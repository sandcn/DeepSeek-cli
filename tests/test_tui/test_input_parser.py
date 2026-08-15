"""CSI-u 修饰 Enter 语义对齐测试（L2）。

修复背景（2026-08-15 L2）：``_input_parser._dispatch_csi`` 中
``keycode==13 and modifier in (2,3,5)``（Shift/Ctrl/Alt+Enter，CSI-u 增强
键盘协议）返回 ``kind="char", char="\\n"`` ——被 _dispatch_key_event 当
可打印字符插入缓冲；普通 Enter（0x0d / \\x1b[13;1u）为 ``kind="enter"``
提交。修复：对齐提交语义——修饰 Enter 均返回 ``kind="enter"``（router 优先
消费，未消费走 ``_dispatch_key_event`` ``kind=="enter"`` → ``_enter()`` 提交）。

本测试锁定：修饰 Enter 1/2/3/4/5 边界映射、既有 Shift+Tab / Ctrl 字母映射
回归（不破坏）。
"""

from __future__ import annotations

import pytest

from src.tui._input_parser import InputParser, KeyEvent


# ── L2：修饰 Enter 语义 ───────────────────────────────────

@pytest.mark.parametrize("modifier", [2, 3, 5])
def test_modified_enter_returns_enter_regression(modifier):
    """L2：Shift(2)/Alt(3)/Ctrl(5)+Enter（\\x1b[13;<m>u）返回 kind="enter"
    提交语义（不再作为可打印换行插入缓冲）。"""
    ev = InputParser._dispatch_csi([13, modifier], "u")
    assert isinstance(ev, KeyEvent)
    assert ev.kind == "enter"
    assert ev.modifier == modifier
    assert ev.keycode == 13
    assert ev.raw == f"\x1b[13;{modifier}u".encode()


def test_plain_enter_returns_enter_regression():
    """L2 回归：无修饰 Enter（\\x1b[13;1u）保持既有 enter 语义（modifier=1）。"""
    ev = InputParser._dispatch_csi([13, 1], "u")
    assert ev.kind == "enter"
    assert ev.modifier == 1


def test_modified_enter_modifier4_stays_csi_u_regression():
    """L2 边界：modifier=4（未对齐修饰键）保持 csi_u 原行为（不误改）。"""
    ev = InputParser._dispatch_csi([13, 4], "u")
    assert ev.kind == "csi_u"
    assert ev.modifier == 4
    assert ev.keycode == 13


def test_modified_enter_single_param_returns_csi_u_regression():
    """L2 边界：\\x1b[13u（无 modifier 参数，默认 modifier=1）→ enter。"""
    ev = InputParser._dispatch_csi([13], "u")
    assert ev.kind == "enter"
    assert ev.modifier == 1


# ── 回归：既有 CSI u 映射 ────────────────────────────────

def test_shift_tab_returns_tab_regression():
    """L2 回归：Shift+Tab（\\x1b[9;2u）保持 tab modifier=2（反向补全导航）。"""
    ev = InputParser._dispatch_csi([9, 2], "u")
    assert ev.kind == "tab"
    assert ev.modifier == 2
    assert ev.keycode == 9


def test_ctrl_letter_mapping_regression():
    """L2 回归：Ctrl+字母（\\x1b[<97-122>;5u）保持 _decode_control_char
    解码语义（Ctrl+A=home 等，不因 Enter 分支改动受影响）。"""
    ev = InputParser._dispatch_csi([97, 5], "u")  # Ctrl+A → home
    assert ev.kind == "home"
    ev2 = InputParser._dispatch_csi([119, 5], "u")  # Ctrl+W → delete word left
    assert ev2.kind == "delete"
    assert ev2.modifier == 1


def test_plain_printable_char_regression():
    """L2 回归：无修饰可打印 ASCII（\\x1b[65;1u = 'A'）保持 char 事件。"""
    ev = InputParser._dispatch_csi([65, 1], "u")
    assert ev.kind == "char"
    assert ev.char == "A"
