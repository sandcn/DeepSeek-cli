"""deepseek-flash（DeepSeek V4.1 Flash）多模态接入测试（2026-09-10）。

V4.1 Flash 原生支持多模态视觉理解，模型名为 ``deepseek-flash``；旧名
``deepseek-v4-flash`` / ``deepseek-v4-flash-vision-exp`` 已下线并路由到
V4.1 Flash。本文件覆盖 read_image 等图像能力在该模型下的完整链路：

- PROVIDERS 模型列表 / token 价格（V4.1 Flash 新价）
- 多模态判定（is_multimodal_model）与 V4 系列判定（is_deepseek_v4_model）
- DeepSeekAdapter 请求注入 thinking 参数（reasoning_effort 生效）
- read_image 门禁放行并返回 image_url content blocks
- 用户消息图片输入 content blocks（BaseAgent.add_user_message）
"""

from __future__ import annotations

import base64
import io

import pytest

from src.config.defaults import PROVIDERS
from src.api.adapters._utils import is_deepseek_v4_model
from src.api.multimodal import (
    is_multimodal_model, clear_multimodal_cache, build_user_content_blocks,
)

_FLASH = "deepseek-flash"
_VISION_OLD = "deepseek-v4-flash-vision-exp"
_V4_FLASH_OLD = "deepseek-v4-flash"


def _png_bytes(w: int = 2, h: int = 2) -> bytes:
    PILImage = pytest.importorskip("PIL.Image")
    img = PILImage.new("RGBA", (w, h), (255, 0, 0, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ── 1. 模型列表 / 定价 ────────────────────────────────

def test_provider_models_include_deepseek_flash():
    """deepseek provider 模型列表包含 deepseek-flash（保留旧名）。"""
    models = PROVIDERS["deepseek"]["models"]
    assert _FLASH in models
    assert _V4_FLASH_OLD in models
    assert _VISION_OLD in models


def test_provider_token_prices_v41_flash():
    """flash 系列 token 价格为 V4.1 Flash 新价（0.3 / 1.2 / cache 0.006）。"""
    prices = PROVIDERS["deepseek"]["token_prices"]
    expected = {"input": 0.3, "output": 1.2, "input_cache_hit": 0.006}
    assert prices[_FLASH] == expected
    assert prices[_V4_FLASH_OLD] == expected
    assert prices[_VISION_OLD] == expected


# ── 2. 能力判定 ───────────────────────────────────────

def test_is_multimodal_model_deepseek_flash():
    """deepseek-flash 与旧名均判定为多模态；deepseek-v4-pro 不是。"""
    clear_multimodal_cache()
    assert is_multimodal_model(_FLASH) is True
    assert is_multimodal_model(_V4_FLASH_OLD) is True
    assert is_multimodal_model(_VISION_OLD) is True
    assert is_multimodal_model("deepseek-v4-pro") is False
    clear_multimodal_cache()


def test_is_deepseek_v4_model_deepseek_flash():
    """deepseek-flash 属 V4 系列（注入 thinking 参数）。"""
    assert is_deepseek_v4_model(_FLASH) is True
    assert is_deepseek_v4_model("deepseek-v3") is False


# ── 3. 适配器 thinking 注入 ───────────────────────────

def test_deepseek_adapter_routes_flash(monkeypatch):
    """get_adapter 将 deepseek-flash 路由到 DeepSeekAdapter。"""
    import src.api._adapter_manager as am
    from src.api.adapters import DeepSeekAdapter
    am._adapter_cache.clear()
    try:
        assert isinstance(am.get_adapter(_FLASH), DeepSeekAdapter)
    finally:
        am._adapter_cache.clear()


def test_deepseek_adapter_injects_thinking(monkeypatch):
    """deepseek-flash 请求注入 thinking 参数（reasoning_effort 生效）。"""
    from src.api.adapters import DeepSeekAdapter
    monkeypatch.setattr(
        "src.api.adapters.deepseek._get_reasoning_effort", lambda: "high",
    )
    adapter = DeepSeekAdapter()
    kwargs = adapter.build_request_kwargs(
        [{"role": "user", "content": "hi"}], _FLASH,
    )
    assert kwargs["thinking"] == {"type": "enabled", "reasoning_effort": "high"}


# ── 4. read_image 集成 ────────────────────────────────

async def test_read_image_allows_deepseek_flash(tmp_path, monkeypatch):
    """read_image 在 deepseek-flash 下放行并返回 image_url content blocks。"""
    pytest.importorskip("PIL")
    from src.tools.read_image import ReadImageFunc
    monkeypatch.setattr(
        "src.tools.read_image._current_model", lambda agent=None: _FLASH,
    )
    p = tmp_path / "t.png"
    p.write_bytes(_png_bytes())
    f = ReadImageFunc(path=str(p))
    out = await f.execute()
    assert not out.startswith("(cannot read ")
    blocks = f.result_blocks
    assert blocks is not None and blocks[1]["type"] == "image_url"
    assert blocks[1]["image_url"]["url"].startswith("data:image/png;base64,")


async def test_read_image_rejects_deepseek_v4_pro(tmp_path, monkeypatch):
    """非多模态模型 deepseek-v4-pro 下 read_image 仍按能力门禁拒绝。"""
    pytest.importorskip("PIL")
    from src.tools.read_image import ReadImageFunc
    monkeypatch.setattr(
        "src.tools.read_image._current_model", lambda agent=None: "deepseek-v4-pro",
    )
    p = tmp_path / "t.png"
    p.write_bytes(_png_bytes())
    out = await ReadImageFunc(path=str(p)).execute()
    assert out.startswith("(cannot read ")
    assert "does not declare image input" in out


# ── 5. 用户消息图片输入 ───────────────────────────────

def test_build_user_content_blocks_deepseek_flash(tmp_path):
    """多模态模型 deepseek-flash + 本地图片 → text + image_url blocks。"""
    p = tmp_path / "a.png"
    p.write_bytes(_png_bytes())
    out = build_user_content_blocks(f"分析 {p} 这张图", _FLASH)
    assert isinstance(out, list)
    assert out[0]["type"] == "text"
    url = out[1]["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")
    assert base64.b64decode(url.split(",", 1)[1]) == p.read_bytes()


def test_agent_add_user_message_deepseek_flash(tmp_path):
    """BaseAgent.add_user_message 在 deepseek-flash 下转为 content blocks。"""
    from src.core.base_agent import BaseAgent
    p = tmp_path / "b.png"
    p.write_bytes(_png_bytes())
    agent = BaseAgent()
    agent.model = _FLASH
    agent.add_user_message(f"看下 {p}")
    msg = agent.messages[-1]
    assert msg["role"] == "user"
    assert isinstance(msg["content"], list)
    assert msg["content"][0]["type"] == "text"
    assert msg["content"][1]["type"] == "image_url"
