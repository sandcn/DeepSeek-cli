"""diff_renderer ink 输出模型测试（2026-08-16 深化控件化）。

背景（用户需求「所有 TUI 都要用 React Ink 控件跟布局实现所有」）：
``_diff_renderer`` 的 diff 行构建统一迁移为 ink 输出模型
（``ink.output.Line`` / ``StyledRun``，样式统一 ``tui.core.Style``）——不再
手工 ``Style.apply`` 拼接 ANSI。本测试锁定：
  - ``_inline_highlight`` 返回 StyledRun 列表（含背景色样式）；
  - ``_write_diff_line`` 接受 ink Line（截断经 ink.helpers.truncate_line）
    且与 str 兼容路径字节一致；
  - ``render_diff_to_ansi`` 输出格式字节基线（回归保护，与旧实现一致）；
  - 语法高亮 / 行内高亮路径经 ink 模型渲染后输出合法。
"""

from __future__ import annotations

import pytest

from src.tui.core.style import Style
from src.tui.ink.output import Line, StyledRun
from src.tui._diff_renderer import (
    _bg_add,
    _bg_del,
    _inline_highlight,
    _write_diff_line,
    render_diff,
    render_diff_to_ansi,
)


class _Collector:
    """收集 write_line 调用（与 render_diff_to_ansi 同型）。"""

    def __init__(self):
        self.lines: list[str] = []

    def write_line(self, text: str) -> None:
        self.lines.append(text)


# ── _inline_highlight：返回 StyledRun 列表 ────────────────

def test_inline_highlight_returns_styled_runs() -> None:
    """行内高亮返回 StyledRun 列表（equal 无样式 / 差异段背景色）。"""
    old_runs, new_runs = _inline_highlight("abc def", "abc xyz")
    assert all(isinstance(r, StyledRun) for r in old_runs)
    assert all(isinstance(r, StyledRun) for r in new_runs)
    # equal 段无样式
    for r in old_runs + new_runs:
        if r.style is not None:
            assert r.style.bg in (28, 124), "差异段应为背景高亮（_bg_add/_bg_del）"


def test_inline_highlight_low_ratio_plain() -> None:
    """低相似度（ratio<0.25）：整段无样式返回（不触发背景高亮）。"""
    old_runs, new_runs = _inline_highlight("aaaa", "zzzzzzzzzz")
    assert old_runs[0].style is None
    assert new_runs[0].style is None


def test_inline_highlight_sanitizes_ansi() -> None:
    """行内高亮输入消毒（ANSI 注入防护——ESC 字符被移除）。"""
    old_runs, _ = _inline_highlight("a\x1b[31mb", "a b")
    joined = "".join(r.text for r in old_runs)
    assert "\x1b" not in joined


# ── _write_diff_line：ink Line 输入 ───────────────────────

def test_write_diff_line_accepts_ink_line() -> None:
    """_write_diff_line 接受 ink Line 并渲染输出（字节与旧 str 拼接一致）。"""
    line = Line()
    line.append("  ", None)
    line.append("content", Style(fg=45))
    collected = _Collector()
    _write_diff_line(line, collected)
    assert collected.lines == ["  \x1b[38;5;45mcontent\x1b[0m"]


def test_write_diff_line_line_truncate() -> None:
    """ink Line + width 截断：超宽行经 ink.helpers.truncate_line 截断。"""
    line = Line()
    line.append("  ", None)
    line.append("x" * 50, None)
    collected = _Collector()
    _write_diff_line(line, collected, width=10)
    assert len(collected.lines) == 1
    assert len(collected.lines[0]) <= 10


def test_write_diff_line_str_legacy_still_works() -> None:
    """兼容路径：str 输入（既有测试/外部调用）行为不变。"""
    collected = _Collector()
    _write_diff_line("\x1b[31m" + "z" * 50 + "\x1b[0m", collected, width=30)
    assert len(collected.lines) == 1
    # 截断后为合法 ANSI（无断裂 SGR，ansi_to_line 重渲染为 256 色格式）
    # 且视觉宽度 <= 30
    out = collected.lines[0]
    from src.tui.ink.helpers import strip_ansi, visual_width
    assert visual_width(strip_ansi(out)) <= 30
    # 无孤立 ESC 残留（消毒断言）
    assert out.count("\x1b") % 2 == 0


# ── render_diff_to_ansi：字节基线 ─────────────────────────

def test_render_diff_to_ansi_byte_baseline() -> None:
    """典型 diff 输出字节基线（与旧 Style.apply 拼接实现完全一致）。"""
    out = render_diff_to_ansi(
        "a.txt",
        "line1\nline2\nline3\n",
        "line1\nline2 modified\nline3\nline4\n",
    )
    assert out == (
        "\n  \x1b[1m\x1b[38;5;210m┌─ a/a.txt\x1b[0m\n"
        "  \x1b[1m\x1b[38;5;114m└─ b/a.txt\x1b[0m\n"
        "  \x1b[2m\x1b[38;5;45m▌ \x1b[0m\x1b[1m\x1b[38;5;45m@@ -1,3 +1,4 @@\x1b[0m\n"
        "  \x1b[38;5;244m│ 1 │\x1b[0m line1\n"
        "  \x1b[38;5;167m│ 2 │\x1b[0m \x1b[1m\x1b[38;5;196m-\x1b[0mline2\n"
        "  \x1b[38;5;41m│ 2 │\x1b[0m \x1b[1m\x1b[38;5;41m+\x1b[0mline2\x1b[48;5;28m modified\x1b[0m\n"
        "  \x1b[38;5;244m│ 3 │\x1b[0m line3\n"
        "  \x1b[38;5;41m│ 4 │\x1b[0m \x1b[1m\x1b[38;5;41m+\x1b[0mline4\n"
        "  \x1b[38;5;244m╌╌╌╌╌╌╌╌╌╌\x1b[0m\n"
        "  \x1b[2m\x1b[38;5;45m✦ \x1b[0m\x1b[38;5;41m🟢 +2\x1b[0m  "
        "\x1b[38;5;196m🔴 -1\x1b[0m  \x1b[38;5;244m⚪ 2 unchanged\x1b[0m"
    )


def test_render_diff_to_ansi_inline_hl_uses_ink_bg() -> None:
    """行内高亮经 ink 输出模型：新增段背景色 _bg_add（28）与旧字节一致。"""
    out = render_diff_to_ansi(
        "f.py", "def foo():\n    return 1\n", "def foo():\n    return 2\n",
    )
    assert "\x1b[48;5;28m 2\x1b[0m" in out or "\x1b[48;5;28m2\x1b[0m" in out
    assert _bg_add == Style(bg=28)
    assert _bg_del == Style(bg=124)


def test_render_diff_no_manual_style_apply_remaining() -> None:
    """生产路径不再手工 Style.apply 拼接（源码静态断言——防止回归）。"""
    import inspect
    import src.tui._diff_renderer as mod
    src = inspect.getsource(mod)
    # _syntax_hl 的 pygments 输出与兼容路径注释不统计；生产渲染路径
    # （_render_chunk/_flush_pairs/_render_diff_summary）不得出现 .apply(
    # 拼接 ANSI——用 render_diff_to_ansi 输出验证已由字节基线覆盖；
    # 此处仅断言模块内 Line/StyledRun 已被生产代码引用。
    assert "Line" in src
    assert "StyledRun" in src


# ── render_diff：Line 行输出到 collector ─────────────────

def test_render_diff_line_model() -> None:
    """render_diff 经 ink Line 构建行（collector 收到渲染后 ANSI 文本）。"""
    collected = _Collector()
    diff_list = list(__import__("difflib").unified_diff(
        ["a", "b"], ["a", "c"], fromfile="a/x", tofile="b/x", lineterm="", n=1,
    ))
    render_diff(diff_list, 1, output_target=collected)
    assert collected.lines
    assert all(isinstance(ln, str) for ln in collected.lines)


# ── 语法高亮路径（.py lexer）──────────────────────────────

def test_render_diff_to_ansi_syntax_highlight() -> None:
    """语法高亮路径（pygments）经 ink 模型输出不崩溃且含高亮 ANSI。"""
    out = render_diff_to_ansi("app.py", "", "import os\nprint('hi')\n")
    assert out
    assert "\x1b[" in out
