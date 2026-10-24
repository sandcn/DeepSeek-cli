"""TUI / renderer 真彩色（24-bit）输出验证。

TUI 颜色统一出口按终端能力输出：

  - 真彩终端（``COLORTERM`` 含 truecolor/24bit，或 ``TERM`` 含 direct）
    → ``38;2;r;g;b`` / ``48;2;r;g;b``（256 色号按 xterm-256 调色板反查
    精确 RGB）；
  - 不支持真彩（或设置 ``NO_COLOR``）→ 降级 ``38;5;n`` / ``48;5;n``，
    保证旧终端不乱码。

renderer 层（``src/renderer/ansi``）不得依赖 tui，自带同口径的检测实现，
两处结果必须一致（同一环境变量判定）。
"""

from __future__ import annotations

import pytest

from src.renderer.ansi.style import Style as RendererStyle
from src.renderer.ansi.style import _reset_truecolor_cache as _reset_renderer
from src.tui.core.color import TrueColor
from src.tui.core.color import _reset_truecolor_cache as _reset_tui
from src.tui.core.style import Style


def _set_capability(
    monkeypatch: pytest.MonkeyPatch,
    truecolor: bool,
    no_color: bool = False,
) -> None:
    """设置终端能力并清空两侧检测缓存与 Style ANSI 缓存。"""
    if truecolor:
        monkeypatch.setenv("COLORTERM", "truecolor")
    else:
        monkeypatch.delenv("COLORTERM", raising=False)
    if no_color:
        monkeypatch.setenv("NO_COLOR", "1")
    else:
        monkeypatch.delenv("NO_COLOR", raising=False)
    _reset_tui()
    _reset_renderer()
    Style.to_ansi.cache_clear()


# ═══════════════════════════════════════════════════════════
# tui.core.Style — 统一颜色出口
# ═══════════════════════════════════════════════════════════


def test_tui_style_emits_24bit_in_truecolor(monkeypatch) -> None:
    """真彩终端：256 色号按 xterm 调色板反查 RGB，输出 24-bit 序列。"""
    _set_capability(monkeypatch, truecolor=True)
    # xterm-256 45 → (0, 215, 255)；124 → (175, 0, 0)
    assert Style(fg=45).to_ansi() == "\033[38;2;0;215;255m"
    assert Style(bg=124).to_ansi() == "\033[48;2;175;0;0m"


def test_tui_style_falls_back_to_256(monkeypatch) -> None:
    """不支持真彩：保持 256 色序列（向后兼容，不乱码）。"""
    _set_capability(monkeypatch, truecolor=False)
    assert Style(fg=45).to_ansi() == "\033[38;5;45m"
    assert Style(bg=124).to_ansi() == "\033[48;5;124m"


def test_no_color_wins_over_colorterm(monkeypatch) -> None:
    """NO_COLOR 存在即禁用真彩（最高优先级）。"""
    _set_capability(monkeypatch, truecolor=True, no_color=True)
    assert Style(fg=45).to_ansi() == "\033[38;5;45m"


def test_truecolor_value_bypasses_capability(monkeypatch) -> None:
    """显式 TrueColor 值对象恒输出 24-bit（与终端能力无关）。"""
    _set_capability(monkeypatch, truecolor=False)
    assert Style(fg=TrueColor(1, 2, 3)).to_ansi() == "\033[38;2;1;2;3m"
    assert Style(bg=TrueColor(4, 5, 6)).to_ansi() == "\033[48;2;4;5;6m"


def test_style_apply_wraps_with_24bit(monkeypatch) -> None:
    """apply() 走同一条出口（组件渲染热路径）。"""
    _set_capability(monkeypatch, truecolor=True)
    assert Style(fg=45).apply("x") == "\033[38;2;0;215;255mx\033[0m"


# ═══════════════════════════════════════════════════════════
# renderer.ansi.Style — 独立实现（不依赖 tui）
# ═══════════════════════════════════════════════════════════


def test_renderer_style_emits_24bit_in_truecolor(monkeypatch) -> None:
    _set_capability(monkeypatch, truecolor=True)
    assert RendererStyle(fg=45).to_ansi() == "\033[38;2;0;215;255m"
    assert RendererStyle(bg=124).to_ansi() == "\033[48;2;175;0;0m"


def test_renderer_style_falls_back_to_256(monkeypatch) -> None:
    _set_capability(monkeypatch, truecolor=False)
    assert RendererStyle(fg=45).to_ansi() == "\033[38;5;45m"
    assert RendererStyle(bg=124).to_ansi() == "\033[48;5;124m"


def test_renderer_hex_color_keeps_24bit_precision(monkeypatch) -> None:
    """pygments 主题 hex：真彩下保真为 (r, g, b)，降级才转 256 色号。"""
    from src.renderer.ansi.code import _hex_to_color

    _set_capability(monkeypatch, truecolor=True)
    assert _hex_to_color("#ff8800") == (255, 136, 0)

    _set_capability(monkeypatch, truecolor=False)
    assert isinstance(_hex_to_color("#ff8800"), int)


def test_code_highlight_end_to_end_truecolor(monkeypatch) -> None:
    """代码高亮整条链路输出 24-bit（hex → 真彩 Style → ANSI）。"""
    from src.renderer.ansi.code import highlight_code_lines

    _set_capability(monkeypatch, truecolor=True)
    lines = highlight_code_lines(["def f():", "    return 1"], "python")
    raw = "\n".join(line.render() for line in lines)
    assert "\033[38;2;" in raw
    assert "\033[38;5;" not in raw


# ═══════════════════════════════════════════════════════════
# TUI 回灌路径：解析 24-bit ANSI 后仍输出 24-bit
# ═══════════════════════════════════════════════════════════


def test_tui_reparse_24bit_keeps_truecolor(monkeypatch) -> None:
    """renderer 产出的 38;2 串回灌 TUI 解析后保持 24-bit（不降级 256）。"""
    from src.tui.app._ansi_runs import ansi_line_to_runs

    _set_capability(monkeypatch, truecolor=False)
    runs = ansi_line_to_runs("\033[38;2;1;2;3mX\033[0m")
    assert len(runs) == 1
    assert runs[0].style is not None
    assert runs[0].style.fg == TrueColor(1, 2, 3)
    assert runs[0].render() == "\033[38;2;1;2;3mX\033[0m"
