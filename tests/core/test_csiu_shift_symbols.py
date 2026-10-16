"""增强键盘协议（CSI u）Shift 符号解析测试（2026-10 修复）。

背景：kitty / WezTerm / iTerm2 / Windows Terminal 等终端启用「report all
keys as escape codes」后，Shift+键 发送 ``\\x1b[<base>[:<shifted>];2u``。
修复前此类事件落入 ``csi_u`` no-op 被静默丢弃——Shift 符号（``?`` ``:``
``@`` ``{`` …）完全打不出来，模态视图的 ``?`` 帮助键因此失效。
"""

from __future__ import annotations

import pytest


def _csi(params, terminator="u", groups=None):
    from src.tui._input_parser import InputParser

    return InputParser._dispatch_csi(list(params), terminator, groups)


# ── shifted_printable_char 纯函数 ──────────────────────


@pytest.mark.parametrize("base,shifted,shift,expected", [
    (47, 63, True, "?"),      # / + Shift，alternate key 上报 '?'
    (47, 0, True, "?"),       # / + Shift，未上报 → US 映射
    (47, 0, False, "/"),      # 无 Shift → 原字符
    (59, 0, True, ":"),       # ; + Shift
    (50, 0, True, "@"),       # 2 + Shift
    (49, 0, True, "!"),       # 1 + Shift
    (97, 0, True, "A"),       # a + Shift → 大写
    (97, 65, True, "A"),      # alternate key 上报 'A'
    (91, 0, True, "{"),       # [ + Shift
    (27, 0, True, ""),        # Esc（非可打印）
    (0, 0, True, ""),         # 空 keycode
    (0, 63, True, "?"),       # 只有 shifted keycode
])
def test_shifted_printable_char(base, shifted, shift, expected):
    from src.tui._input_parser import shifted_printable_char

    assert shifted_printable_char(base, shifted, shift) == expected


def test_us_shift_map_from_registry():
    from src.tui._input_parser import _us_shift_map

    table = _us_shift_map()
    assert table["/"] == "?"
    assert table["1"] == "!"
    assert table[";"] == ":"


# ── _dispatch_csi（CSI u 分支） ────────────────────────


@pytest.mark.parametrize("params,groups,char,modifier", [
    ([47, 2], None, "?", 2),                       # Shift+/（无 alternate）
    ([47, 63, 2], [[47, 63], [2]], "?", 2),        # Shift+/（alternate key）
    ([63, 2], None, "?", 2),                       # keycode 即 '?'
    ([59, 2], None, ":", 2),                       # Shift+;
    ([50, 2], None, "@", 2),                       # Shift+2
    ([50, 64, 2], [[50, 64], [2]], "@", 2),        # Shift+2（alternate key）
    ([97, 65, 2], [[97, 65], [2]], "A", 2),        # Shift+a
    ([97, 2], None, "A", 2),                       # Shift+a（无 alternate）
    ([91, 123, 2], [[91, 123], [2]], "{", 2),      # Shift+[
])
def test_csi_u_shift_symbols_become_chars(params, groups, char, modifier):
    ev = _csi(params, "u", groups)
    assert ev.kind == "char"
    assert ev.char == char
    assert ev.modifier == modifier


def test_csi_u_shift_alt_is_alt_char():
    ev = _csi([47, 63, 4], "u", [[47, 63], [4]])   # Shift+Alt+/
    assert ev.kind == "alt_char"
    assert ev.char == "?"


def test_csi_u_capslock_bit_does_not_block_char():
    # modifier = 1 + shift(1) + capsLock(64) = 66
    ev = _csi([47, 2 + 64], "u")
    assert ev.kind == "char"
    assert ev.char == "?"


@pytest.mark.parametrize("modifier", [5, 6, 7, 9, 12])   # Ctrl / Ctrl+Shift / Super…
def test_csi_u_ctrl_like_modifiers_keep_csi_u(modifier):
    ev = _csi([47, modifier], "u")
    assert ev.kind == "csi_u"


def test_csi_u_plain_printable_unchanged():
    ev = _csi([97, 1], "u")            # 无修饰 'a'（既有行为）
    assert (ev.kind, ev.char) == ("char", "a")
    ev = _csi([63, 1], "u")
    assert (ev.kind, ev.char) == ("char", "?")


def test_csi_u_non_printable_with_shift_stays_csi_u():
    ev = _csi([27, 2], "u")            # Shift+Esc
    assert ev.kind == "csi_u"


# ── 端到端：真实字节 → 事件 ────────────────────────────


@pytest.mark.parametrize("raw,tail,char", [
    (b"\x1b[47;2u", b"[47;2u", "?"),
    (b"\x1b[47:63;2u", b"[47:63;2u", "?"),
    (b"\x1b[63;2u", b"[63;2u", "?"),
])
def test_parse_sequence_shift_symbol_bytes(raw, tail, char):
    import os

    from src.tui._input_parser import InputParser

    r, w = os.pipe()
    os.write(w, tail)
    os.close(w)
    try:
        ev = InputParser().parse_sequence(r)
    finally:
        os.close(r)
    assert ev.kind == "char"
    assert ev.char == char
    assert ev.raw == raw
