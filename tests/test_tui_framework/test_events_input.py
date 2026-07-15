"""test_events_input.py — 终端输入事件测试。

测试范围：
- KeyPressEvent / MouseEvent / ResizeEvent / FocusEvent 创建与属性
- InputEvent 联合类型
- SGR 鼠标转义序列解析
- InputReader 按键解析（mock Blessed Keystroke）
- InputReader 降级处理
"""

from __future__ import annotations

import pytest

from tui_framework.events.event_types import (
    KeyPressEvent,
    MouseEvent,
    ResizeEvent,
    FocusEvent,
    InputEvent,
    DisplayEvent,
)
from tui_framework.events.input_reader import (
    InputReader,
    _parse_sgr_mouse,
)


# ═══════════════════════════════════════════════════════════
# KeyPressEvent
# ═══════════════════════════════════════════════════════════

class TestKeyPressEvent:
    """KeyPressEvent 创建与属性测试。"""

    def test_default_creation(self):
        """默认创建：所有字段为默认值。"""
        e = KeyPressEvent()
        assert e.key == ""
        assert e.ctrl is False
        assert e.alt is False
        assert e.shift is False
        assert e.raw == ""
        assert isinstance(e, DisplayEvent)

    def test_printable_char(self):
        """可打印字符按键。"""
        e = KeyPressEvent(key="a")
        assert e.key == "a"
        assert e.ctrl is False
        assert e.alt is False

    def test_ctrl_modifier(self):
        """Ctrl 修饰键。"""
        e = KeyPressEvent(key="c", ctrl=True)
        assert e.key == "c"
        assert e.ctrl is True
        assert e.alt is False

    def test_alt_modifier(self):
        """Alt 修饰键。"""
        e = KeyPressEvent(key="x", alt=True)
        assert e.alt is True
        assert e.ctrl is False

    def test_shift_modifier(self):
        """Shift 修饰键。"""
        e = KeyPressEvent(key="A", shift=True)
        assert e.shift is True

    def test_combined_modifiers(self):
        """组合修饰键。"""
        e = KeyPressEvent(key="s", ctrl=True, shift=True)
        assert e.ctrl is True
        assert e.shift is True
        assert e.alt is False

    def test_raw_sequence(self):
        """原始 ANSI 序列字段。"""
        e = KeyPressEvent(key="up", raw="\033[A")
        assert e.key == "up"
        assert e.raw == "\033[A"

    def test_frozen(self):
        """frozen dataclass：不可修改。"""
        e = KeyPressEvent(key="enter")
        with pytest.raises(Exception):
            e.key = "escape"  # type: ignore[misc]

    def test_function_key(self):
        """功能键名称。"""
        for fk in ("f1", "f5", "f12", "up", "down", "left", "right",
                    "enter", "escape", "backspace", "tab", "delete",
                    "home", "end", "page_up", "page_down", "insert"):
            e = KeyPressEvent(key=fk)
            assert e.key == fk


# ═══════════════════════════════════════════════════════════
# MouseEvent
# ═══════════════════════════════════════════════════════════

class TestMouseEvent:
    """MouseEvent 创建与属性测试。"""

    def test_default_creation(self):
        """默认创建。"""
        e = MouseEvent()
        assert e.x == 0
        assert e.y == 0
        assert e.button == ""
        assert e.action == ""
        assert e.ctrl is False
        assert e.alt is False
        assert e.shift is False

    def test_left_click(self):
        """左键单击。"""
        e = MouseEvent(x=10, y=5, button="left", action="click")
        assert e.x == 10
        assert e.y == 5
        assert e.button == "left"
        assert e.action == "click"

    def test_right_click(self):
        """右键单击。"""
        e = MouseEvent(x=1, y=1, button="right", action="click")
        assert e.button == "right"

    def test_scroll(self):
        """滚轮事件。"""
        e = MouseEvent(button="wheel_up", action="scroll_up")
        assert e.button == "wheel_up"
        assert e.action == "scroll_up"

    def test_with_modifiers(self):
        """带修饰键的鼠标事件。"""
        e = MouseEvent(x=3, y=4, button="left", action="click",
                       ctrl=True, shift=True)
        assert e.ctrl is True
        assert e.shift is True
        assert e.alt is False

    def test_frozen(self):
        """frozen dataclass。"""
        e = MouseEvent(x=1, y=1, button="left", action="click")
        with pytest.raises(Exception):
            e.x = 999  # type: ignore[misc]


# ═══════════════════════════════════════════════════════════
# ResizeEvent / FocusEvent
# ═══════════════════════════════════════════════════════════

class TestResizeEvent:
    """ResizeEvent 测试。"""

    def test_creation(self):
        e = ResizeEvent(width=80, height=24)
        assert e.width == 80
        assert e.height == 24

    def test_frozen(self):
        e = ResizeEvent(width=120, height=40)
        with pytest.raises(Exception):
            e.width = 80  # type: ignore[misc]


class TestFocusEvent:
    """FocusEvent 测试。"""

    def test_focus_gained(self):
        """获得焦点（默认值）。"""
        e = FocusEvent(widget_id="btn_1")
        assert e.widget_id == "btn_1"
        assert e.gained is True  # 默认 True

    def test_focus_lost(self):
        """失去焦点。"""
        e = FocusEvent(widget_id="input_1", gained=False)
        assert e.widget_id == "input_1"
        assert e.gained is False

    def test_frozen(self):
        e = FocusEvent(widget_id="w1")
        with pytest.raises(Exception):
            e.widget_id = "w2"  # type: ignore[misc]


# ═══════════════════════════════════════════════════════════
# InputEvent 联合类型
# ═══════════════════════════════════════════════════════════

class TestInputEvent:
    """InputEvent 联合类型测试。"""

    def test_keypress_is_input_event(self):
        """KeyPressEvent 是 InputEvent 的子类型。"""
        e = KeyPressEvent(key="a")
        # 运行时 isinstance 检查（使用 typing.get_type_hints 或直接判断）
        assert isinstance(e, KeyPressEvent)
        assert isinstance(e, DisplayEvent)

    def test_mouse_is_display_event(self):
        """MouseEvent 继承 DisplayEvent。"""
        e = MouseEvent()
        assert isinstance(e, DisplayEvent)

    def test_resize_is_display_event(self):
        e = ResizeEvent()
        assert isinstance(e, DisplayEvent)

    def test_focus_is_display_event(self):
        e = FocusEvent()
        assert isinstance(e, DisplayEvent)


# ═══════════════════════════════════════════════════════════
# SGR 鼠标转义序列解析
# ═══════════════════════════════════════════════════════════

class TestSGRMouseParsing:
    """_parse_sgr_mouse() 测试。"""

    def test_left_click(self):
        e = _parse_sgr_mouse("\033[<0;10;20M")
        assert e is not None
        assert e.button == "left"
        assert e.action == "click"
        assert e.x == 10
        assert e.y == 20

    def test_right_click(self):
        e = _parse_sgr_mouse("\033[<2;8;12M")
        assert e is not None
        assert e.button == "right"
        assert e.action == "click"

    def test_middle_click(self):
        e = _parse_sgr_mouse("\033[<1;5;7M")
        assert e is not None
        assert e.button == "middle"

    def test_scroll_up(self):
        e = _parse_sgr_mouse("\033[<64;15;30M")
        assert e is not None
        assert e.button == "wheel_up"
        assert e.action == "scroll_up"

    def test_scroll_down(self):
        e = _parse_sgr_mouse("\033[<65;15;30M")
        assert e is not None
        assert e.button == "wheel_down"
        assert e.action == "scroll_down"

    def test_drag(self):
        e = _parse_sgr_mouse("\033[<32;8;12M")
        assert e is not None
        assert e.button == "left"
        assert e.action == "drag"

    def test_release(self):
        e = _parse_sgr_mouse("\033[<3;1;1m")
        assert e is not None
        assert e.action == "release"

    def test_modifiers_ctrl_shift(self):
        """Ctrl+Shift+左键: 0 + 4 + 16 = 20。"""
        e = _parse_sgr_mouse("\033[<20;5;10M")
        assert e is not None
        assert e.ctrl is True
        assert e.shift is True
        assert e.alt is False
        assert e.button == "left"

    def test_modifiers_alt(self):
        """Alt+左键: 0 + 8 = 8。"""
        e = _parse_sgr_mouse("\033[<8;5;10M")
        assert e is not None
        assert e.alt is True
        assert e.ctrl is False

    def test_scroll_with_shift(self):
        """Shift+滚轮上: 64 + 4 = 68。"""
        e = _parse_sgr_mouse("\033[<68;15;30M")
        assert e is not None
        assert e.button == "wheel_up"
        assert e.action == "scroll_up"
        assert e.shift is True

    def test_invalid_sequence(self):
        """无效序列返回 None。"""
        assert _parse_sgr_mouse("not a mouse event") is None
        assert _parse_sgr_mouse("") is None
        assert _parse_sgr_mouse("\033[A") is None  # 上箭头，非鼠标

    def test_release_event_via_press(self):
        """M 为按下，但按钮码为释放码 (3) 时仍应识别为 release。"""
        # 某些终端在释放时也用 M 而非 m
        e = _parse_sgr_mouse("\033[<3;1;1M")
        assert e is not None
        assert e.action == "release"


# ═══════════════════════════════════════════════════════════
# InputReader — 按键解析
# ═══════════════════════════════════════════════════════════

class MockKeystroke:
    """模拟 Blessed Keystroke 对象。"""

    def __init__(self, char="", code=0, is_sequence=False):
        self._char = char
        self.code = code
        self.is_sequence = is_sequence

    def __str__(self):
        return self._char

    def __bool__(self):
        return bool(self._char) or self.is_sequence


class TestInputReaderKeyParsing:
    """InputReader._keystroke_to_event() 测试。"""

    @pytest.fixture
    def reader(self):
        return InputReader()

    def _make_key(self, char="", code=0, is_sequence=False):
        return MockKeystroke(char=char, code=code, is_sequence=is_sequence)

    def test_printable_char(self, reader):
        key = self._make_key(char="a")
        e = reader._keystroke_to_event(key)
        assert e.key == "a"
        assert e.ctrl is False

    def test_enter_key(self, reader):
        key = self._make_key(char="\n")
        e = reader._keystroke_to_event(key)
        assert e.key == "enter"

    def test_tab_key(self, reader):
        key = self._make_key(char="\t")
        e = reader._keystroke_to_event(key)
        assert e.key == "tab"

    def test_escape_key(self, reader):
        key = self._make_key(char="\x1b")
        e = reader._keystroke_to_event(key)
        assert e.key == "escape"

    def test_backspace_key(self, reader):
        key = self._make_key(char="\x7f")
        e = reader._keystroke_to_event(key)
        assert e.key == "backspace"

    def test_space_key(self, reader):
        key = self._make_key(char=" ")
        e = reader._keystroke_to_event(key)
        assert e.key == "space"

    def test_raw_field_preserved(self, reader):
        raw_seq = "\033[A"
        key = self._make_key(char=raw_seq, code=261, is_sequence=True)
        e = reader._keystroke_to_event(key)
        assert e.raw == raw_seq


# ═══════════════════════════════════════════════════════════
# InputReader — 降级处理 & 边界条件
# ═══════════════════════════════════════════════════════════

class TestInputReaderDegradation:
    """InputReader 降级与边界测试。"""

    def test_blessed_available_property(self):
        reader = InputReader()
        # 在有 blessed 的环境中应为 True
        assert reader.blessed_available is True

    def test_mouse_disabled_by_default(self):
        reader = InputReader()
        assert reader.mouse_enabled is False

    def test_read_mouse_invalid_input(self):
        reader = InputReader()
        assert reader.read_mouse("not mouse") is None
        assert reader.read_mouse("") is None
        assert reader.read_mouse("\033[A") is None  # 箭头键，非鼠标

    def test_timestamp_set(self):
        """所有事件应有自动生成的时间戳。"""
        e = KeyPressEvent(key="a")
        assert e.timestamp > 0
        e2 = MouseEvent()
        assert e2.timestamp > 0
        e3 = ResizeEvent()
        assert e3.timestamp > 0
        e4 = FocusEvent()
        assert e4.timestamp > 0

    def test_source_field(self):
        """source 字段默认为空字符串。"""
        e = KeyPressEvent(key="x")
        assert e.source == ""

    def test_source_custom(self):
        """可自定义 source 字段。"""
        e = KeyPressEvent(key="x", source="input_loop")
        assert e.source == "input_loop"


# ═══════════════════════════════════════════════════════════
# InputReader — read_input (combined)
# ═══════════════════════════════════════════════════════════

class TestInputReaderReadInput:
    """InputReader.read_input() 测试。"""

    def test_mouse_sequence_detected(self, monkeypatch):
        """read_input 应能检测鼠标序列并返回 MouseEvent。"""
        reader = InputReader()

        class FakeKeyMouse:
            is_sequence = True
            code = 0

            def __str__(self):
                return "\033[<0;10;20M"

            def __bool__(self):
                return True

        # Mock blessed 可用
        monkeypatch.setattr(reader, "_check_blessed", lambda: True)

        # Mock term.inkey 返回假鼠标事件
        class FakeTerm:
            def inkey(self, timeout=None):
                return FakeKeyMouse()

        monkeypatch.setattr(reader, "_get_terminal", lambda: FakeTerm())

        result = reader.read_input(timeout=0.1)
        assert result is not None
        assert isinstance(result, MouseEvent)
        assert result.button == "left"
        assert result.action == "click"
        assert result.x == 10
        assert result.y == 20

    def test_key_sequence_detected(self, monkeypatch):
        """read_input 应能检测普通按键并返回 KeyPressEvent。"""
        reader = InputReader()

        class FakeKey:
            is_sequence = False

            def __str__(self):
                return "x"

            def __bool__(self):
                return True

        monkeypatch.setattr(reader, "_check_blessed", lambda: True)

        class FakeTerm:
            def inkey(self, timeout=None):
                return FakeKey()

        monkeypatch.setattr(reader, "_get_terminal", lambda: FakeTerm())

        result = reader.read_input(timeout=0.1)
        assert result is not None
        assert isinstance(result, KeyPressEvent)
        assert result.key == "x"
