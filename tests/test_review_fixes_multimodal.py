"""多模态 review 修复回归测试（2026-08-22）。

集中覆盖：
- tools/__init__ ``__all__`` 与导入符号一致（含 README 工具计数）
- loader.update_config 嵌套 RC 键被写成非 dict（performance.*）时健壮
- view_model.format_config_text 键列按显示宽度对齐（CJK）
- _width.truncate_width 公共工具（不拆 CJK）
- chat_msgs._content_to_text 对 image_url 只输出 [图片] 占位（不泄露 base64）
"""

from __future__ import annotations

import re
import unittest.mock
from pathlib import Path

import pytest

import src.tools as tools


# ── tools/__init__ 导出一致性 ────────────────────────────

def _imported_tool_names() -> set:
    """解析 tools/__init__.py 的 from .X import Y as Z 导入符号。"""
    src = Path(tools.__file__).read_text(encoding="utf-8")
    names = set()
    for m in re.finditer(r"^from \.(\w+) import (\w+)(?: as (\w+))?$", src, re.M):
        names.add(m.group(3) or m.group(2))
    return names


def test_tools_all_covers_imported():
    """__all__ 必须覆盖所有导入的工具类符号。"""
    assert set(tools.__all__) >= _imported_tool_names()


def test_tools_all_no_extra_tool_names():
    """__all__ 不含未在上方导入的工具类（允许 Func/注册表符）。"""
    allowed = _imported_tool_names() | {"Func", "get_tools", "register_tool"}
    assert set(tools.__all__) <= allowed


def test_tools_exported_count_matches_readme():
    """README 声明 19 个内置工具；__all__ 工具类数量应一致（不含 Func）。"""
    tool_names = _imported_tool_names() - {"Func"}
    assert len(tool_names) == 19


# ── loader.update_config 嵌套非 dict 防御 ────────────────

def test_update_config_non_dict_intermediate_key(monkeypatch, tmp_path):
    """performance.http_client 被写成字符串时，update_config 仍健壮写入。"""
    from src.config import loader
    rc = {"performance": {"http_client": "oops-not-a-dict"}}
    monkeypatch.setattr(loader, "_RC", rc)
    monkeypatch.setattr(loader, "_RC_LOADED", True)
    monkeypatch.setattr(loader, "RC_FILE", tmp_path / "chatrc.json")
    loader.update_config("HTTP_CONNECT_TIMEOUT", 30)
    assert rc["performance"]["http_client"]["connect_timeout"] == 30
    assert isinstance(rc["performance"]["http_client"], dict)


# ── view_model.format_config_text 键列显示宽度对齐 ────────

def test_format_config_text_cjk_alignment():
    """CJK 键与 ASCII 键在 '=' 前显示宽度一致（按显示宽补齐）。"""
    from src.config.view_model import format_config_text
    from src.tui._width import wcswidth_simple
    entries = [
        {"path": "性能", "value_text": "1", "sensitive": False, "desc": "a"},
        {"path": "ab", "value_text": "2", "sensitive": False, "desc": "b"},
    ]
    text = format_config_text(entries)
    lines = [ln for ln in text.splitlines()[1:] if " = " in ln]
    assert len(lines) == 2
    widths = [wcswidth_simple(ln.split(" = ", 1)[0]) for ln in lines]
    assert widths[0] == widths[1]


# ── _width.truncate_width 公共工具 ───────────────────────

def test_truncate_width_ascii():
    from src.tui._width import truncate_width
    assert truncate_width("abcdef", 3) == "abc"
    assert truncate_width("abcdef", 100) == "abcdef"
    assert truncate_width("abcdef", 0) == ""
    assert truncate_width("abcdef", -1) == ""


def test_truncate_width_cjk():
    from src.tui._width import truncate_width
    # 2 个 CJK 显示宽 4；预算 3 只放得下 1 个 CJK（不拆半数）
    assert truncate_width("测试", 3) == "测"


# ── chat_msgs._content_to_text image_url 占位 ────────────

def test_chat_msgs_content_to_text_image_placeholder_only():
    from src.chat_msgs import _content_to_text
    content = [
        {"type": "text", "text": "看图"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,zz"}},
    ]
    t = _content_to_text(content)
    assert "看图" in t
    assert "[图片]" in t
    assert "base64" not in t
    assert "data:image" not in t


# ── observability context.chars 统计多模态 list content ──

class _FakePort:
    def __init__(self):
        self.gauges = {}
    def gauge(self, name, val):
        self.gauges[name] = val
    def counter(self, *a, **k):
        pass
    def histogram(self, *a, **k):
        pass


class _FakeAgent:
    """模拟真实 Agent：get_observability_port 为实例方法（验证 _resolve 修复）。"""
    def __init__(self, messages):
        self.messages = messages
        self._port = _FakePort()
    def get_observability_port(self):
        return self._port


class _FakeCtx:
    def __init__(self, agent, usage):
        self.agent = agent
        self.usage = usage


async def test_observability_chars_counts_list_content():
    """after_model_call 对多模态 list content 统计纯文本长度（不含 base64）。"""
    from src.core.middleware.observability import _AsyncObservabilityMiddleware
    agent = _FakeAgent([
        {"role": "user", "content": "abc"},
        {"role": "tool", "content": [
            {"type": "text", "text": "图片是"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,zz"}},
        ]},
    ])
    ctx = _FakeCtx(agent, {"input": 1, "output": 1})
    await _AsyncObservabilityMiddleware().after_model_call(ctx)
    # content_to_text(["图片是", 图片块]) → "图片是 [图片]"（8 字）+ "abc"（3 字）
    assert agent._port.gauges["context.chars"] == 11


# ── _export_cmd 拒绝覆盖已存在文件（与 chat_msgs 一致） ──

def test_export_resolve_output_rejects_existing_file(tmp_path, monkeypatch):
    """显式导出路径已存在 → 返回 None 并输出错误（避免覆盖）。"""
    from src.core.commands import _export_cmd
    fake_out = unittest.mock.Mock()
    monkeypatch.setattr(_export_cmd, "_out", fake_out)
    existing = tmp_path / "out.md"
    existing.write_text("x", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert _export_cmd._resolve_output_path("out.md") is None
    assert fake_out.write.called


# ── chat_msgs.save_session 路径校验（P1 修复） ──────────

def test_save_session_rejects_path_traversal(monkeypatch, tmp_path):
    """save_session 对含路径穿越的 session_id 回退自动生成 id（不越权写盘）。"""
    import re as _re
    from src import chat_msgs
    monkeypatch.setattr(chat_msgs, "CHAT_MSGS_DIR", tmp_path)
    monkeypatch.setattr("src.paths.CHAT_MSGS_DIR", tmp_path)
    sid = chat_msgs.save_session(
        [{"role": "user", "content": "hi"}], "m", session_id="../evil",
    )
    assert sid and sid != "../evil"
    assert _re.match(r"^[a-zA-Z0-9_-]+$", sid)
    # 未越权写入当前目录外的 ".." 文件
    assert not (tmp_path.parent / "evil.json").exists()
