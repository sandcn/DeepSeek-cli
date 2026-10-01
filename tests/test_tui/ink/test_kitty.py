"""kitty 键盘协议 — 常量、解析（CSI-u 子参数）、key 字段映射测试。"""

from __future__ import annotations

from src.tui._input_parser import (
    InputParser,
    KeyEvent,
    decode_kitty_modifiers,
    _kitty_bits_from_modifier,
    _kitty_event_type,
)
from src.tui.ink._hooks_input import _event_key
from src.tui.ink import kittyFlags, kittyModifiers, resolveFlags, h, TEXT
from src.tui.ink.kitty import (
    encode_modifiers,
    resolve_kitty_options,
    enable_sequence,
    disable_sequence,
)


class _FakeIO:
    """逐字节喂入的假 IO（模拟 InputIO.read_with_timeout）。"""

    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def read_with_timeout(self, fd, timeout):
        if self.pos >= len(self.data):
            return None
        b = self.data[self.pos:self.pos + 1]
        self.pos += 1
        return b


def _parse_csi_u(payload: bytes) -> KeyEvent:
    """解析 ``\\x1b[<payload>u`` 序列。"""
    parser = InputParser(io=_FakeIO(payload + b"u"))
    return parser._read_csi_sequence(None)


# ── 常量 ────────────────────────────────────────────────

def test_kitty_flag_values():
    assert kittyFlags["disambiguateEscapeCodes"] == 1
    assert kittyFlags["reportEventTypes"] == 2
    assert kittyFlags["reportAlternateKeys"] == 4
    assert kittyFlags["reportAllKeysAsEscapeCodes"] == 8
    assert kittyFlags["reportAssociatedText"] == 16


def test_kitty_modifier_values():
    assert kittyModifiers["shift"] == 1
    assert kittyModifiers["alt"] == 2
    assert kittyModifiers["ctrl"] == 4
    assert kittyModifiers["super"] == 8
    assert kittyModifiers["hyper"] == 16
    assert kittyModifiers["meta"] == 32
    assert kittyModifiers["capsLock"] == 64
    assert kittyModifiers["numLock"] == 128


def test_resolve_flags():
    assert resolveFlags(["reportEventTypes", "bogus"]) == 2
    assert resolveFlags(None) == 0
    assert resolveFlags(["disambiguateEscapeCodes", "reportEventTypes"]) == 3


def test_encode_modifiers_and_sequences():
    assert encode_modifiers(0) == 1
    assert encode_modifiers(8) == 9
    assert enable_sequence(3) == "\x1b[>3u"
    assert enable_sequence(0) == "\x1b[>1u"
    assert disable_sequence() == "\x1b[<u"


def test_resolve_kitty_options():
    assert resolve_kitty_options(None) == -1
    assert resolve_kitty_options(False) == -1
    assert resolve_kitty_options(True) == 1
    assert resolve_kitty_options({"mode": "disabled"}) == -1
    assert resolve_kitty_options({"mode": "enabled", "flags": ["reportEventTypes"]}) == 2
    assert resolve_kitty_options({"mode": "auto"}) == 1


# ── 解析 ────────────────────────────────────────────────

def test_standard_csi_u_plain_char():
    ev = _parse_csi_u(b"97;1")
    assert ev.kind == "char" and ev.char == "a"
    assert ev.kitty_bits == 0
    assert ev.event_type == ""


def test_extended_csi_u_alternate_keys():
    # \x1b[97:65;9u → code=97, shifted=65, modifier=9 (super)
    ev = _parse_csi_u(b"97:65;9")
    assert ev.kind == "csi_u" and ev.keycode == 97
    assert ev.modifier == 9
    assert ev.kitty_bits == 8


def test_csi_u_event_type_subparam():
    # \x1b[97;5:2u → ctrl+a 映射为 home（modifier=0），但 kitty 位来自原始修饰值 5
    ev = _parse_csi_u(b"97;5:2")
    assert ev.event_type == "repeat"
    assert ev.kind == "home"
    assert ev.kitty_bits == 4  # ctrl 位


def test_event_type_release():
    ev = _parse_csi_u(b"97;1:3")
    assert ev.event_type == "release"


# ── key 字段映射 ────────────────────────────────────────

def test_event_key_super_and_event_type():
    ev = _parse_csi_u(b"97;9:2")
    key = _event_key(ev)
    assert key["super"] is True
    assert key["eventType"] == "repeat"
    assert key["ctrl"] is False


def test_event_key_caps_lock_num_lock():
    # modifier = 1 + capsLock(64) + numLock(128) = 193
    ev = _parse_csi_u(b"97;193")
    key = _event_key(ev)
    assert key["capsLock"] is True
    assert key["numLock"] is True


def test_event_key_plain_event_defaults():
    key = _event_key(KeyEvent(kind="char", char="x"))
    assert key["super"] is False
    assert key["hyper"] is False
    assert key["capsLock"] is False
    assert key["numLock"] is False
    assert key["eventType"] is None


def test_decode_kitty_modifiers():
    mods = decode_kitty_modifiers(1 + 2 + 8)
    assert mods["shift"] and mods["alt"] and mods["super"]
    assert not mods["ctrl"]


def test_kitty_bits_from_modifier():
    assert _kitty_bits_from_modifier(1) == 0
    assert _kitty_bits_from_modifier(9) == 8
    assert _kitty_bits_from_modifier("bad") == 0


def test_kitty_event_type_helper():
    assert _kitty_event_type([[97], [5, 2]]) == "repeat"
    assert _kitty_event_type([[97]]) == ""
    assert _kitty_event_type([]) == ""


def test_kitty_key_mapping_end_to_end_via_router():
    """经 input router 端到端：解析出的 kitty 事件 → use_input handler 收到 super。"""
    from src.tui.ink import use_input
    from tests.test_tui.ink._harness import Harness

    captured = {}

    def Comp(props):
        def _handler(event) -> bool:
            captured.update(_event_key(event))
            return True

        use_input(_handler)
        return h(TEXT, {"children": "x"})

    harness = Harness(20)
    harness.render(h(Comp, {}))
    router = harness.reconciler._input_router_cache[1]
    assert router(_parse_csi_u(b"97;9")) is True
    assert captured["super"] is True
    assert captured["ctrl"] is False
