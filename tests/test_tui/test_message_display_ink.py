"""message_display 兜底直写路径 — ink 输出模型渲染测试。

背景（2026-08-16 用户需求「所有 TUI 都要用 React Ink 控件跟布局实现所有」）：
``pipeline/message_display.display_messages``（非 ChatUI 兜底直写路径）的
渲染行统一迁移为 ink 输出模型（``ink.output.Line`` / ``StyledRun``）——
兜底路径与 TUI 界面渲染共用同一输出模型。本测试锁定：
  - ``_display_line`` 返回 ink Line 且渲染字节与旧纯文本输出一致；
  - ``display_messages`` 兜底输出行为不变（含异常跳过 / 截断 / 空内容）。
"""

from __future__ import annotations

import io
import sys

import pytest

from src.tui.pipeline.message_display import (
    MessageDisplayContext,
    RoleConfig,
    _display_line,
    display_messages,
)
from src.tui.ink.output import Line, StyledRun


class _FakeStdout:
    """捕获 write/flush 的假 stdout（模拟无 TTY / 管道）。"""

    def __init__(self, fail_after: int | None = None):
        self.buf = io.StringIO()
        self._writes = 0
        self._fail_after = fail_after

    def write(self, text: str) -> int:
        self._writes += 1
        if self._fail_after is not None and self._writes > self._fail_after:
            raise OSError("pipe closed")
        return self.buf.write(text)

    def flush(self) -> None:
        pass

    @property
    def value(self) -> str:
        return self.buf.getvalue()


# ── _display_line：ink 输出模型 ───────────────────────────

def test_display_line_returns_ink_line() -> None:
    """_display_line 返回 ink Line（StyledRun 行，输出模型统一）。"""
    line = _display_line("\u25cf", "user", "hello")
    assert isinstance(line, Line)
    assert all(isinstance(r, StyledRun) for r in line.runs)
    assert line.plain == "  \u25cf [user] hello"


def test_display_line_render_matches_legacy_text() -> None:
    """渲染字节与旧纯文本输出一致（``  {icon} [{role}] {preview}``）。"""
    line = _display_line("\u25cf", "user", "hello")
    assert line.render() == "  \u25cf [user] hello"
    line2 = _display_line("\u2699", "tool", "run ls")
    assert line2.render() == "  \u2699 [tool] run ls"


# ── display_messages：兜底直写行为 ────────────────────────

def test_display_messages_writes_lines(monkeypatch) -> None:
    """正常路径：逐消息一行输出（含角色图标/角色名/预览）。"""
    fake = _FakeStdout()
    monkeypatch.setattr(sys, "__stdout__", fake)
    display_messages([
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "世界"},
        {"role": "tool", "content": "输出"},
    ])
    assert fake.value == "  \u25cf [user] 你好\n  \u25c6 [assistant] 世界\n  \u2699 [tool] 输出\n"


def test_display_messages_skips_empty_and_non_dict(monkeypatch) -> None:
    """防御：空 content / 非 dict 元素跳过（不输出也不崩溃）。"""
    fake = _FakeStdout()
    monkeypatch.setattr(sys, "__stdout__", fake)
    display_messages([
        {"role": "user", "content": "   "},
        "not-a-dict",
        None,
        {"role": "user", "content": "ok"},
    ])
    assert fake.value == "  \u25cf [user] ok\n"


def test_display_messages_truncates_long_content(monkeypatch) -> None:
    """长消息截断（_DISPLAY_PREVIEW_MAX_LEN=120，超长补省略号）。"""
    fake = _FakeStdout()
    monkeypatch.setattr(sys, "__stdout__", fake)
    long_text = "x" * 500
    display_messages([{"role": "user", "content": long_text}])
    # 120 上限：118 内容 + "..."
    assert fake.value == "  \u25cf [user] " + "x" * 117 + "...\n"


def test_display_messages_write_failure_skips_continue(monkeypatch) -> None:
    """写失败（管道关闭）跳过当前消息继续，不中断整个循环（BUG-60）。"""
    fake = _FakeStdout(fail_after=1)
    monkeypatch.setattr(sys, "__stdout__", fake)
    display_messages([
        {"role": "user", "content": "first"},
        {"role": "user", "content": "second"},
        {"role": "user", "content": "third"},
    ])
    # 第一次写入成功，之后失败 → 后续消息跳过，仅首条输出
    assert fake.value == "  \u25cf [user] first\n"


def test_display_messages_context_from_messages() -> None:
    """MessageDisplayContext 构建（system 消息排除、非 dict 防御）。"""
    ctx = MessageDisplayContext.from_messages([
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "u"},
        "junk",
        {"role": "assistant", "content": "a"},
    ])
    assert [m.get("role") for m in ctx.data] == ["user", "assistant"]


def test_display_messages_role_config_default() -> None:
    """RoleConfig 默认图标为 ?（未注册角色）。"""
    assert RoleConfig().icon == "?"
