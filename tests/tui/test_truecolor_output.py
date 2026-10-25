"""TUI / renderer 真彩色（24-bit）输出验证。

2026-10-25 用户需求：TUI 颜色**全部**真彩色，删除原来的 256 色降级。

契约：

  - 颜色输出**恒为** 24-bit：``38;2;r;g;b`` / ``48;2;r;g;b``——调色板索引
    （int）按 xterm-256 调色板反查精确 RGB，不再有 ``38;5``/``48;5`` 分支；
  - ``NO_COLOR`` 存在时禁用颜色（输出空串）；
  - renderer 层（``src/renderer/ansi``）不得依赖 tui，自带同语义实现；
  - renderer 产出的 24-bit ANSI 回灌 TUI 解析后仍输出 24-bit。
"""

from __future__ import annotations

import pytest

from src.renderer.ansi.style import Style as RendererStyle
from src.renderer.ansi.style import _reset_color_disabled_cache as _reset_renderer
from src.tui.core.color import Color256, TrueColor
from src.tui.core.color import _reset_color_disabled_cache as _reset_tui
from src.tui.core.style import Style


def _set_no_color(monkeypatch: pytest.MonkeyPatch, disabled: bool) -> None:
    """设置 NO_COLOR 并清空两侧开关缓存与 Style ANSI 缓存。"""
    if disabled:
        monkeypatch.setenv("NO_COLOR", "1")
    else:
        monkeypatch.delenv("NO_COLOR", raising=False)
    _reset_tui()
    _reset_renderer()
    Style.to_ansi.cache_clear()


# ═══════════════════════════════════════════════════════════
# tui.core.Style — 统一颜色出口（恒真彩）
# ═══════════════════════════════════════════════════════════


def test_tui_style_emits_24bit(monkeypatch) -> None:
    """调色板索引按 xterm 调色板反查 RGB，恒输出 24-bit 序列。"""
    _set_no_color(monkeypatch, False)
    # xterm-256 45 → (0, 215, 255)；124 → (175, 0, 0)
    assert Style(fg=45).to_ansi() == "\033[38;2;0;215;255m"
    assert Style(bg=124).to_ansi() == "\033[48;2;175;0;0m"


def test_tui_style_never_emits_256(monkeypatch) -> None:
    """即使终端不声明真彩能力，也不再降级 256 色。"""
    _set_no_color(monkeypatch, False)
    monkeypatch.delenv("COLORTERM", raising=False)
    monkeypatch.setenv("TERM", "dumb")
    Style.to_ansi.cache_clear()
    assert Style(fg=45).to_ansi() == "\033[38;2;0;215;255m"
    assert "\033[38;5;" not in Style(fg=45).to_ansi()


def test_no_color_disables_color_output(monkeypatch) -> None:
    """NO_COLOR 存在即禁用颜色（输出空串，最高优先级）。"""
    _set_no_color(monkeypatch, True)
    assert Style(fg=45).to_ansi() == ""
    assert Style(bg=124).to_ansi() == ""
    assert Style(fg=TrueColor(1, 2, 3)).to_ansi() == ""


def test_truecolor_value_emits_24bit(monkeypatch) -> None:
    """显式 TrueColor 值对象恒输出 24-bit。"""
    _set_no_color(monkeypatch, False)
    assert Style(fg=TrueColor(1, 2, 3)).to_ansi() == "\033[38;2;1;2;3m"
    assert Style(bg=TrueColor(4, 5, 6)).to_ansi() == "\033[48;2;4;5;6m"


def test_color256_value_emits_24bit(monkeypatch) -> None:
    """Color256 值对象（调色板索引）输出 24-bit，__str__ 同源。"""
    _set_no_color(monkeypatch, False)
    assert Color256(45).__str__() == "\033[38;2;0;215;255m"
    assert Color256(45).to_rgb == (0, 215, 255)


def test_style_apply_wraps_with_24bit(monkeypatch) -> None:
    """apply() 走同一条出口（组件渲染热路径）。"""
    _set_no_color(monkeypatch, False)
    assert Style(fg=45).apply("x") == "\033[38;2;0;215;255mx\033[0m"


# ═══════════════════════════════════════════════════════════
# renderer.ansi.Style — 独立实现（不依赖 tui）
# ═══════════════════════════════════════════════════════════


def test_renderer_style_emits_24bit(monkeypatch) -> None:
    _set_no_color(monkeypatch, False)
    assert RendererStyle(fg=45).to_ansi() == "\033[38;2;0;215;255m"
    assert RendererStyle(bg=124).to_ansi() == "\033[48;2;175;0;0m"


def test_renderer_no_color_disables_color(monkeypatch) -> None:
    _set_no_color(monkeypatch, True)
    assert RendererStyle(fg=45).to_ansi() == ""


def test_renderer_hex_color_keeps_24bit_precision(monkeypatch) -> None:
    """pygments 主题 hex：恒保真为 (r, g, b)，不再降级 256 色号。"""
    from src.renderer.ansi.code import _hex_to_color

    _set_no_color(monkeypatch, False)
    assert _hex_to_color("#ff8800") == (255, 136, 0)


def test_code_highlight_end_to_end_truecolor(monkeypatch) -> None:
    """代码高亮整条链路输出 24-bit（hex → 真彩 Style → ANSI）。"""
    from src.renderer.ansi.code import highlight_code_lines

    _set_no_color(monkeypatch, False)
    lines = highlight_code_lines(["def f():", "    return 1"], "python")
    raw = "\n".join(line.render() for line in lines)
    assert "\033[38;2;" in raw
    assert "\033[38;5;" not in raw


def test_math_hex_color_keeps_24bit_precision(monkeypatch) -> None:
    """Markdown/LaTeX 的 hex 颜色同样保真 24-bit。"""
    from src.renderer.ansi._math_style import color_bg, color_style

    _set_no_color(monkeypatch, False)
    assert color_bg("#ff8800") == (255, 136, 0)
    assert color_style("#ff8800").to_ansi() == "\033[38;2;255;136;0m"


# ═══════════════════════════════════════════════════════════
# TUI 回灌路径：解析 ANSI 后仍输出 24-bit
# ═══════════════════════════════════════════════════════════


def test_tui_reparse_24bit_keeps_truecolor(monkeypatch) -> None:
    """renderer 产出的 38;2 串回灌 TUI 解析后保持 24-bit。"""
    from src.tui.app._ansi_runs import ansi_line_to_runs

    _set_no_color(monkeypatch, False)
    runs = ansi_line_to_runs("\033[38;2;1;2;3mX\033[0m")
    assert len(runs) == 1
    assert runs[0].style is not None
    assert runs[0].style.fg == TrueColor(1, 2, 3)
    assert runs[0].render() == "\033[38;2;1;2;3mX\033[0m"


def test_tui_reparse_256_input_upgrades_to_24bit(monkeypatch) -> None:
    """外部 256 色输入（如旧渲染产物）解析后输出升级为 24-bit。"""
    from src.tui.app._ansi_runs import ansi_line_to_runs

    _set_no_color(monkeypatch, False)
    runs = ansi_line_to_runs("\033[38;5;45mX\033[0m")
    assert len(runs) == 1
    assert runs[0].render() == "\033[38;2;0;215;255mX\033[0m"
