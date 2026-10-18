"""read_image 工具加载/放行由模型档案「多模态读图」开关控制。

用户需求（2026-10-19）：
- ``/models`` 模型档案新增 ``multimodal``（true/false）字段；
- false → read_image 不进入该模型的 tool schemas，执行也被拒绝；
- true → 加载 read_image 并允许读取图片；
- 原来的按模型名模式推断（``is_multimodal_model``）已从 read_image 移除。

本文件覆盖：
1. ``config.model_profiles.model_supports_multimodal`` 的档案判定；
2. ``tools.tool_policy.filter_schemas_for_model`` 的加载过滤；
3. ``Agent._get_active_tools`` 在运行期模型切换后的动态过滤；
4. ``read_image`` 执行门禁读取真实档案字段（false 拒绝 / true 放行）。
"""

from __future__ import annotations

import io

import pytest

pytest.importorskip("PIL")

import src.config.model_profiles as mp
from src.core.agent import Agent
from src.tools.read_image import ReadImageFunc
from src.tools.tool_policy import filter_schemas_for_model


@pytest.fixture
def isolated_rc(monkeypatch, tmp_path):
    """隔离 RC 配置文件（临时目录）——保存/读取不污染真实用户配置。"""
    from src.config import loader as cfg_loader
    from src.config import defaults as cfg_defaults

    rc_file = tmp_path / "chatrc.json"
    monkeypatch.setattr(cfg_loader, "RC_FILE", rc_file)
    monkeypatch.setattr(cfg_loader, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cfg_loader, "LOG_FILE", tmp_path / "audit.log")
    monkeypatch.setattr(cfg_loader, "_RC_LOADED", False)
    monkeypatch.setattr(cfg_loader, "_RC", None)
    monkeypatch.setattr(cfg_defaults, "RC_FILE", rc_file)
    monkeypatch.setattr(cfg_defaults, "CONFIG_DIR", tmp_path)
    return rc_file


def _schema(name: str) -> dict:
    return {"type": "function", "function": {"name": name}}


def _names(schemas) -> list[str]:
    return [s["function"]["name"] for s in schemas]


def _write_png(path) -> str:
    from PIL import Image

    img = Image.new("RGB", (2, 2), (255, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    path.write_bytes(buf.getvalue())
    return str(path)


# ── 1. 档案判定 ────────────────────────────────────────

def test_model_supports_multimodal_reads_profile_flag(isolated_rc):
    mp.save_profiles([
        {"model": "text-model", "multimodal": "false"},
        {"model": "vision-model", "multimodal": "true"},
    ])
    assert mp.model_supports_multimodal("vision-model") is True
    assert mp.model_supports_multimodal("text-model") is False
    assert mp.model_supports_multimodal("unknown-model") is False


def test_missing_multimodal_defaults_false(isolated_rc):
    """旧配置无该字段 → 默认 false（未声明即不加载 read_image）。"""
    mp.save_profiles([{"model": "legacy-model"}])
    assert mp.load_profiles()[0]["multimodal"] == "false"
    assert mp.model_supports_multimodal("legacy-model") is False


# ── 2. tool schemas 加载过滤 ───────────────────────────

def test_filter_schemas_removes_read_image_when_false(isolated_rc):
    mp.save_profiles([{"model": "text-model", "multimodal": "false"}])
    schemas = [_schema("read"), _schema("read_image"), _schema("grep")]
    filtered = filter_schemas_for_model(schemas, "text-model")
    assert _names(filtered) == ["read", "grep"]


def test_filter_schemas_keeps_read_image_when_true(isolated_rc):
    mp.save_profiles([{"model": "vision-model", "multimodal": "true"}])
    schemas = [_schema("read"), _schema("read_image")]
    filtered = filter_schemas_for_model(schemas, "vision-model")
    assert _names(filtered) == ["read", "read_image"]


def test_filter_schemas_unknown_model_hides_read_image():
    schemas = [_schema("read"), _schema("read_image")]
    assert _names(filter_schemas_for_model(schemas, "no-such-model")) == ["read"]


# ── 3. Agent 运行期模型切换动态过滤 ────────────────────

def test_agent_active_tools_follows_model_switch(isolated_rc):
    mp.save_profiles([
        {"model": "text-model", "multimodal": "false"},
        {"model": "vision-model", "multimodal": "true"},
    ])
    agent = Agent.__new__(Agent)
    agent.tools = [_schema("read"), _schema("read_image")]
    agent.model = "text-model"
    assert _names(agent._get_active_tools()) == ["read"]
    # 运行期切换模型（/model、/models、Ctrl+N 均同步 agent.model）
    agent.model = "vision-model"
    assert _names(agent._get_active_tools()) == ["read", "read_image"]
    # 切回不支持图像输入的模型 → read_image 再次移除
    agent.model = "text-model"
    assert _names(agent._get_active_tools()) == ["read"]


# ── 4. read_image 执行门禁（真实档案字段） ─────────────

async def test_read_image_rejected_when_profile_false(tmp_path, monkeypatch, isolated_rc):
    mp.save_profiles([{"model": "vision-model", "multimodal": "false"}])
    monkeypatch.setattr(
        "src.tools.read_image._current_model", lambda agent=None: "vision-model",
    )
    path = _write_png(tmp_path / "t.png")
    out = await ReadImageFunc(path=path).execute()
    assert out.startswith("(cannot read ")
    assert "does not declare image input" in out
    assert "多模态读图=false" in out
    assert "set 多模态读图=true" in out


async def test_read_image_allowed_when_profile_true(tmp_path, monkeypatch, isolated_rc):
    mp.save_profiles([{"model": "vision-model", "multimodal": "true"}])
    monkeypatch.setattr(
        "src.tools.read_image._current_model", lambda agent=None: "vision-model",
    )
    path = _write_png(tmp_path / "t.png")
    f = ReadImageFunc(path=path)
    out = await f.execute()
    assert not out.startswith("(cannot read ")
    assert f.result_blocks is not None
    assert f.result_blocks[1]["type"] == "image_url"


async def test_read_image_flag_change_takes_effect_immediately(
    tmp_path, monkeypatch, isolated_rc,
):
    """运行期在 /models 修改开关后，无需重启即可生效。"""
    monkeypatch.setattr(
        "src.tools.read_image._current_model", lambda agent=None: "vision-model",
    )
    path = _write_png(tmp_path / "t.png")
    mp.save_profiles([{"model": "vision-model", "multimodal": "false"}])
    assert (await ReadImageFunc(path=path).execute()).startswith("(cannot read ")
    mp.save_profiles([{"model": "vision-model", "multimodal": "true"}])
    f = ReadImageFunc(path=path)
    assert not (await f.execute()).startswith("(cannot read ")
    assert f.result_blocks is not None
