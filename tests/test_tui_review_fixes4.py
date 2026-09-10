"""TUI 全量 review 修复回归测试（2026-09-10 第二轮全量 review）。

覆盖本轮修复的关键行为（每条对应一处 review 问题）：
  1. ``ink/diff.py`` 稳定前缀跳过不再吞掉前缀之上行区间（顶部行更新可见）；
  2. ``ink/_paint_border.py`` clip 空交集哨兵 (0,0,0,0) 全裁剪 / y0<0 不丢可见部分；
  3. ``ink/_paint_canvas.py`` 合并时清理宽字符第二列残留；
  4. ``_width.wcswidth_simple`` 与 ``renderer cjk_display_width`` 区间一致；
  5. ``_width.truncate_width`` ANSI 序列整段穿透（不产生残缺转义）；
  6. ``_screen.TerminalWidthCache.set_dimensions`` 覆盖尺寸不被 TTL 探测替换；
  7. ``trace._slot_live_lines`` 缓存键含 attr（reasoning/content 各自命中）；
  8. ``app._state_types.ChatBlock`` 身份语义（``eq=False``）；
  9. ``core.style.StyleSheet.clear`` 后恢复内置样式集；
 10. ``src.tui.__getattr__`` 对废弃符号抛 AttributeError（hasattr 安全）；
 11. ``_input_orchestrator`` 窗口期非空提交不被静默丢弃（返回用户输入）；
 12. ``user_select._popup_item_rows`` 矮终端下限为 1（不溢出）。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest


# ═══════════════════════════════════════════════════════════
# 1. diff.py — 稳定前缀跳过语义
# ═══════════════════════════════════════════════════════════

def test_first_diff_line_detects_change_before_stable_prefix():
    """前缀之上的行（top header）变化必须被检测到（修复前被跳过）。"""
    from src.tui.ink.diff import first_diff_line
    from src.tui.ink.output import Frame, Line

    shared_prefix = [Line.of("committed-1"), Line.of("committed-2")]
    prev = Frame(
        [Line.of("hdr-a")] + list(shared_prefix) + [Line.of("tail")],
        stable_prefix=shared_prefix,
        stable_prefix_offset=1,
        stable_prefix_len=2,
    )
    new = Frame(
        [Line.of("hdr-b")] + list(shared_prefix) + [Line.of("tail")],
        stable_prefix=shared_prefix,
        stable_prefix_offset=1,
        stable_prefix_len=2,
    )
    assert first_diff_line(prev, new) == 0


def test_first_diff_line_skips_identical_prefix_only():
    """前缀区间相同、其余行相同时仍返回 -1（跳过语义保留）。"""
    from src.tui.ink.diff import first_diff_line
    from src.tui.ink.output import Frame, Line

    shared_prefix = [Line.of("committed-1"), Line.of("committed-2")]
    lines = [Line.of("hdr")] + list(shared_prefix) + [Line.of("tail")]
    prev = Frame(lines, stable_prefix=shared_prefix, stable_prefix_offset=1, stable_prefix_len=2)
    new = Frame(list(lines), stable_prefix=shared_prefix, stable_prefix_offset=1, stable_prefix_len=2)
    assert first_diff_line(prev, new) == -1


# ═══════════════════════════════════════════════════════════
# 2. _paint_border — clip 哨兵 / 负 y0
# ═══════════════════════════════════════════════════════════

def test_paint_border_empty_clip_sentinel_paints_nothing():
    """clip=(0,0,0,0)（全部裁剪哨兵）时边框不绘制。"""
    from src.tui.ink._paint_border import _paint_border

    fiber = SimpleNamespace(
        layout_box=SimpleNamespace(x=0, y=0, w=5, h=3),
        props={"border": 1},
    )
    canvas: list = [None, None, None, None]
    _paint_border(fiber, canvas, 1, clip=(0, 0, 0, 0))
    assert canvas == [None, None, None, None]


def test_paint_border_negative_y_paints_visible_rows():
    """box 顶部在画布上方（y0<0）时底边/左右边可见部分仍绘制。"""
    from src.tui.ink._paint_border import _paint_border

    fiber = SimpleNamespace(
        layout_box=SimpleNamespace(x=0, y=-1, w=4, h=3),
        props={"border": 1},
    )
    canvas: list = [None, None]
    _paint_border(fiber, canvas, 1)
    assert any(row for row in canvas)


# ═══════════════════════════════════════════════════════════
# 3. _paint_canvas — 宽字符第二列残留清理 / 行首零宽
# ═══════════════════════════════════════════════════════════

def test_merge_line_clears_stale_second_column():
    """新宽字符写入列 c 时清理既有 c+1 残字（避免错位拼接）。"""
    from src.tui.ink._paint_canvas import _merge_line, _canvas_row_to_line
    from src.tui.ink.output import Line

    row = {1: ("x", None)}
    line = Line.of("中")
    merged = _merge_line(row, 0, line)
    out = _canvas_row_to_line(merged)
    assert out.plain == "中"


def test_put_char_leading_zero_width_kept():
    """行首零宽字符不被下一字符覆盖（合并到同一键）。"""
    from src.tui.ink._paint_canvas import _line_as_dict
    from src.tui.ink.output import Line

    line = Line.of("\u0301a")
    d = _line_as_dict(line)
    joined = "".join(v[0] for _, v in sorted(d.items()))
    assert joined == "\u0301a"


# ═══════════════════════════════════════════════════════════
# 4. 双宽度函数一致性（_width vs renderer cjk_display_width）
# ═══════════════════════════════════════════════════════════

@pytest.mark.parametrize("cp", [
    0x1100, 0x2E80, 0x3002, 0x3042, 0x4E00, 0xAC00, 0xF900,
    0x2F800, 0x2CEB0, 0x30400, 0x1F600, 0x26A1, 0xFF01, 0x0301, 0x200B,
])
def test_dual_width_functions_agree(cp):
    """两个宽度函数在关键码点上一致（修复前 Ext F/兼容表意补充缺口）。"""
    from src.tui._width import wcswidth_simple
    from src.renderer._utils._display import cjk_display_width

    ch = chr(cp)
    assert wcswidth_simple(ch) == cjk_display_width(ch)


# ═══════════════════════════════════════════════════════════
# 5. truncate_width — ANSI 序列穿透
# ═══════════════════════════════════════════════════════════

def test_truncate_width_keeps_ansi_sequence_intact():
    """ANSI 序列整体保留、不被拦腰截断（修复前产生残缺转义）。"""
    from src.tui._width import truncate_width, wcswidth_simple

    s = "\x1b[31mabcdef"
    out = truncate_width(s, 3)
    assert out.startswith("\x1b[31m")
    assert "\x1b[31" not in out.replace("\x1b[31m", "")
    assert wcswidth_simple(out) == 3


# ═══════════════════════════════════════════════════════════
# 6. TerminalWidthCache.set_dimensions — 覆盖不被 TTL 替换
# ═══════════════════════════════════════════════════════════

def test_terminal_width_cache_set_dimensions_override_persists():
    """set_dimensions 覆盖后，TTL 到期（force_refresh）仍返回覆盖值。"""
    from src.tui._screen import TerminalWidthCache

    cache = TerminalWidthCache(ttl=0.0)  # TTL 立即过期
    cache.set_dimensions(123, 45)
    assert cache.get_width() == 123
    assert cache.get_height() == 45
    cache.force_refresh()
    assert cache.get_width() == 123
    cache.clear_override()
    assert cache.get_width() != 123 or cache.get_width() > 0


# ═══════════════════════════════════════════════════════════
# 7. trace._slot_live_lines — 缓存键含 attr
# ═══════════════════════════════════════════════════════════

def test_slot_live_lines_cache_keyed_by_attr():
    """reasoning 与 content 交错读取不再互相驱逐（修复前恒 miss）。"""
    from src.tui.app.trace import _slot_live_lines

    slot = SimpleNamespace(live_reasoning="r1", live_content="c1")
    first_r = _slot_live_lines(slot, "live_reasoning")
    first_c = _slot_live_lines(slot, "live_content")
    # 再次读取 reasoning 应命中其自身缓存（返回同一列表对象）
    assert _slot_live_lines(slot, "live_reasoning") is first_r
    assert _slot_live_lines(slot, "live_content") is first_c


# ═══════════════════════════════════════════════════════════
# 8. ChatBlock 身份语义
# ═══════════════════════════════════════════════════════════

def test_chat_block_identity_semantics():
    """ChatBlock 为身份对象：内容相同的两个块不相等（eq=False）。"""
    from src.tui.app._state_types import ChatBlock

    a = ChatBlock(kind="content", lines=["x"])
    b = ChatBlock(kind="content", lines=["x"])
    assert a != b
    assert a == a


# ═══════════════════════════════════════════════════════════
# 9. StyleSheet.clear 恢复内置集
# ═══════════════════════════════════════════════════════════

def test_stylesheet_clear_restores_builtins():
    """clear() 后内置样式仍可用（修复前永久丢失）。"""
    from src.tui.core.style import StyleSheet

    before = set(StyleSheet.all_names())
    assert "error" in before
    StyleSheet.clear()
    after = set(StyleSheet.all_names())
    assert "error" in after
    assert after == before


# ═══════════════════════════════════════════════════════════
# 10. src.tui.__getattr__ — AttributeError 语义
# ═══════════════════════════════════════════════════════════

def test_tui_getattr_obsolete_symbol_attribute_error():
    """hasattr/getattr 对废弃符号安全返回（修复前抛 ImportError）。"""
    import src.tui as tui

    assert hasattr(tui, "Box") is False
    assert getattr(tui, "Box", "dflt") == "dflt"
    with pytest.raises(AttributeError):
        tui.Box


# ═══════════════════════════════════════════════════════════
# 11. 输入编排器 — 窗口期非空提交不丢弃
# ═══════════════════════════════════════════════════════════

def test_input_orchestrator_returns_window_submit():
    """窗口期非空提交返回用户输入（修复前静默丢弃）。"""
    from src.tui._input_orchestrator import TuiInputOrchestrator

    class _FakeInput:
        def __init__(self):
            self._queued = "用户窗口期输入"
            self.calls = []

        def get_queued_input(self):
            v, self._queued = self._queued, None
            return v

        def set_buffer(self, text):
            self.calls.append(("set", text))

        def echo(self, text):
            self.calls.append(("echo", text))

    class _FakeMonitor:
        is_alive = True

    orch = TuiInputOrchestrator.__new__(TuiInputOrchestrator)
    fake_input = _FakeInput()
    orch._input = fake_input  # type: ignore[attr-defined]

    result = orch.wait_for_user_input(
        prefill="旧消息", monitor=_FakeMonitor(), input_=fake_input,
    )
    assert result == "用户窗口期输入"


# ═══════════════════════════════════════════════════════════
# 12. user_select 弹窗行数下限
# ═══════════════════════════════════════════════════════════

def test_user_select_popup_rows_lower_bound(monkeypatch):
    """矮终端（h<9）时下限为 1（修复前强制 6 行溢出）。"""
    import src.tui.app.user_select as us

    monkeypatch.setattr(
        "src.tui._screen.TerminalWidthCache.get_default",
        classmethod(lambda cls: SimpleNamespace(get_height=lambda: 4)),
    )
    rows = us._popup_item_rows()
    assert rows == 1
