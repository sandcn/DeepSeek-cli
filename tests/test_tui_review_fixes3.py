"""TUI 第三轮 review 修复（2026-08-22）回归测试。

覆盖本轮 review（P0-P3）关键修复：
  1. P0  _text_input 光标字符重复（test_tui_textinput_cursor.py 单文件）
  2. P1  _completion_engine._complete_path("") 空前缀返回空
  3. P1  _apply_completion 对 /config set（尾随空格）词边界对齐
  4. P2  Style 拒绝非 int/TrueColor 色号（float 等）
  5. P2  search_input/breadcrumbs/tabs 的 _normalize_* 拒绝 str/bytes
  6. P2  _repeat_to_width 零宽字符不崩溃（补空格）
  7. P2  _desc_column_width 19/20 边界单调（修复非单调跳变）
  8. P2  _sync_locked_height 提取（_completion_height 副作用分离）
  9. P3  _border_box width==3 顶/底行补右角 ┐/┘
  10. P3 _input_layout._wrap_by_width 内部展开制表符
  11. P3 _truncate_to_width 参数化 strip（codeblock 复用）
  12. P3 lerp_color 越界 a + 非有限 t 顺序
  13. P3 hex_to_rgb 非 hex 上下文 ValueError
  14. P3 DisplayEventBus.subscribe 非类型 event_type
  15. P3 _content_str 未知 dict 类型摘要
"""

from __future__ import annotations

import pytest

from src.tui.core.style import Style


def _text_of(line) -> str:
    """Line 的纯文本（runs 拼接）。"""
    return "".join(getattr(r, "text", "") for r in getattr(line, "runs", []))


# ── P1: _complete_path("") 空前缀返回空 ──
def test_complete_path_empty_prefix_returns_empty():
    from src.tui._completion_engine import CompletionEngine
    engine = CompletionEngine()
    assert engine._complete_path("") == []


# ── P1: /config set 尾随空格词边界对齐 ──
def test_apply_completion_config_set_trailing_space():
    from src.tui._completion import _apply_completion
    result = _apply_completion("/config set ", "set api_base_url", -3, "set")
    assert result == "/config set api_base_url"


# ── P2: Style 拒绝 float/str 色号 ──
def test_style_rejects_float_fg():
    with pytest.raises(ValueError):
        Style(fg=45.5)
    with pytest.raises(ValueError):
        Style(bg="45")
    # bool 仍被拒绝（bool 是 int 子类）
    with pytest.raises(ValueError):
        Style(fg=True)
    # 合法 int 通过
    Style(fg=45, bg=23)


# ── P2: _normalize_* 拒绝 str/bytes ──
def test_normalize_items_reject_str():
    from src.tui.ink.widgets.search_input import _normalize_items as _search
    from src.tui.ink.widgets.breadcrumbs import _normalize_items as _breadcrumbs
    from src.tui.ink.widgets.tabs import _normalize_tabs
    assert _search("abc") == []
    assert _breadcrumbs("abc") == []
    assert _normalize_tabs("abc") == []


# ── P2: _repeat_to_width 零宽字符补齐不崩溃 ──
def test_repeat_to_width_zero_width_no_crash():
    from src.tui.ink.widgets._display_common import _repeat_to_width
    assert _repeat_to_width("\u200b", 5) == "     "


# ── P2: _desc_column_width 19/20 边界单调 ──
def test_desc_column_width_monotonic_19_20():
    from src.tui._input_metrics import _desc_column_width
    assert _desc_column_width(19) == 8
    assert _desc_column_width(20) == 8
    # 12~20 无跳变（均匀近似单调）
    for w in range(12, 21):
        assert 0 <= _desc_column_width(w) <= w - 1


# ── P2: _sync_locked_height 提取存在且行为 ──
def test_sync_locked_height_extracted():
    from src.tui._input_metrics import _sync_locked_height
    from types import SimpleNamespace
    c = SimpleNamespace(locked_height=0)
    out = _sync_locked_height(c, locked=0, need=5)
    assert out == 5 and c.locked_height == 5
    # 小幅减少保持（只增不减）
    out2 = _sync_locked_height(c, locked=5, need=4)
    assert out2 == 5 and c.locked_height == 5
    # 大幅减少允许缩小
    out3 = _sync_locked_height(c, locked=5, need=1)
    assert out3 == 1 and c.locked_height == 1


# ── P3: _border_box width==3 顶/底行补右角 ──
def test_border_box_width3_corners():
    from src.tui.ink._border_box import build_border_box
    from src.tui.core.style import Style
    lines = build_border_box([], [], width=3, status="open", border_style=Style(fg=23))
    top = _text_of(lines[0])
    assert "\u2510" in top  # ┐
    closed = build_border_box([], [], width=3, status="done", border_style=Style(fg=23))
    bottom = _text_of(closed[-1])
    assert "\u2518" in bottom  # ┘


# ── P3: _wrap_by_width 内部展开制表符 ──
def test_wrap_by_width_expands_tab():
    from src.tui._input_layout import _wrap_by_width
    # "a" 占 1 列 → \t 跳到第 4 列 tab stop → 补 3 空格 → "a   b"
    out = _wrap_by_width("a\tb", 10)
    assert out == ["a   b"]


# ── P3: _truncate_to_width 参数化 strip ──
def test_truncate_to_width_strip_param():
    from src.tui.ink.widgets._display_common import _truncate_to_width
    text = "\x1b[31mhello\x1b[0m"
    # strip_ansi_seq=True 剥离 ANSI 后按可见字符截断
    r = _truncate_to_width(text, 20, True)
    assert r == "hello"


# ── P3: lerp_color 越界 a + 非有限 t 顺序 ──
def test_lerp_color_range_before_nonfinite():
    from src.tui.core.color import lerp_color
    # a 越界且 t 为 NaN → 范围校验优先（抛 ValueError，而非返回越界 a）
    with pytest.raises(ValueError):
        lerp_color(300, 45, float("nan"))
    # 合法范围 + 非有限 t → 返回 a
    assert lerp_color(45, 60, float("nan")) == 45


# ── P3: hex_to_rgb 非 hex 上下文 ValueError ──
def test_hex_to_rgb_invalid_context():
    from src.tui.core.color import hex_to_rgb
    with pytest.raises(ValueError):
        hex_to_rgb("#gg8800")


# ── P3: DisplayEventBus.subscribe 非类型 event_type ──
def test_event_bus_subscribe_non_type_rejected():
    from src.tui.events.event_bus import DisplayEventBus
    bus = DisplayEventBus()
    with pytest.raises(TypeError):
        bus.subscribe(lambda e: None, event_type=object())


# ── P3: _content_str 未知 dict 类型摘要 ──
def test_content_str_unknown_dict_summary():
    from src.tui.pipeline.message_display import _content_str
    out = _content_str([{"type": "tool_use", "name": "calculator"}])
    assert "[工具调用: calculator]" in out
