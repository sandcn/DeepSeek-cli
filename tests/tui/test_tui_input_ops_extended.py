"""输入区体验优化 + 操作快捷键单元测试（2026-10-07）。

覆盖：
  - 模式行图标（◇ 空模式 / ▸ 简单模式 / ▣ 标准模式）与行宽不变量；
  - 占位提示轮播条目增强与周期切换；
  - Ctrl+Z / Ctrl+Y 键位绑定、控制字符解码与分发动作。
"""

from __future__ import annotations

from src.tui.app.input_area import (
    _MODE_ICONS,
    _PLACEHOLDER_ROTATE_SECONDS,
    _PLACEHOLDER_ROTATION,
    _build_mode_line,
    _placeholder_rotate,
)


def _text(line) -> str:
    return "".join(r.text for r in line.runs)


# ── 模式行图标 ──────────────────────────────────────────────


def test_mode_line_icons_present():
    for mode, icon in _MODE_ICONS.items():
        text = _text(_build_mode_line(80, mode))
        assert icon in text, f"{mode} 应显示图标 {icon}"


def test_mode_line_width_invariant_with_icons():
    for width in (80, 40, 24, 10):
        for mode in ("empty", "simple", "standard"):
            assert _build_mode_line(width, mode).width == width


def test_mode_label_still_present():
    text = _text(_build_mode_line(80, "empty"))
    assert "空模式" in text
    text = _text(_build_mode_line(80, "standard"))
    assert "标准模式" in text


# ── 占位提示轮播 ────────────────────────────────────────────


def test_placeholder_rotation_entries():
    assert len(_PLACEHOLDER_ROTATION) >= 6


def test_placeholder_rotation_cycles():
    first = _placeholder_rotate(0.0)
    second = _placeholder_rotate(_PLACEHOLDER_ROTATE_SECONDS)
    assert first != second
    wrap = _placeholder_rotate(
        _PLACEHOLDER_ROTATE_SECONDS * len(_PLACEHOLDER_ROTATION)
    )
    assert wrap == first


# ── 撤销/重做快捷键绑定 ─────────────────────────────────────


def test_ctrl_z_y_bindings_registered():
    from src.tui._keybindings import resolve_binding

    assert resolve_binding("\x1a") == "undo"
    assert resolve_binding("\x19") == "redo"


def test_decode_ctrl_z_and_y():
    from src.tui._input_parser import InputParser

    for byte, ch in ((0x1a, "\x1a"), (0x19, "\x19")):
        event = InputParser._decode_control_char(byte)
        assert event.kind == "ctrl_key"
        assert event.char == ch


def test_dispatcher_routes_undo_redo():
    from src.tui._input_dispatcher import InputDispatcher

    d = InputDispatcher.__new__(InputDispatcher)
    calls: list = []

    class _Editor:
        def _undo(self):
            calls.append("undo")

        def _redo(self):
            calls.append("redo")

    d._buffer_editor = _Editor()
    d._dispatch_ctrl_action("undo")
    d._dispatch_ctrl_action("redo")
    assert calls == ["undo", "redo"]
