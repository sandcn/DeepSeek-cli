"""输入区按宽换行宽字符拆分越界钳制测试（L1）。

修复背景（2026-08-15）：``_wrap_by_width`` 首字符超宽分支原 ``if idx == 0:
idx = 1``——max_width=1 且首字符 CJK（宽 2）时 ``w=0, idx=0`` 强制拆出
宽 2 行 > max_width，破坏行宽不变量。修复：``idx==0`` 时先判首字符宽度，
若 ``wcswidth_simple(remaining[0]) > max_width``（最小 1 列预算仍放不下）则
跳过该字符（宁可窄不可宽，不产生超宽行；每轮至少推进 1 字符无死循环）；
否则保持 ``idx=1``。调用方 ``_compute_input_layout`` 以 ``or [""]`` 兜底空段。
"""

from __future__ import annotations

import pytest

from src.tui._input_layout import (
    _wrap_by_width,
    _compute_cursor_visual_pos,
)
from src.tui._width import wcswidth_simple


def test_wrap_by_width_cjk_max_width_one_regression():
    """L1：max_width=1 且输入 CJK（宽 2）不产生超宽行——宁可窄不可宽，
    字符被跳过（空段由调用方 ``or [""]`` 兜底）。"""
    lines = _wrap_by_width("가나", 1)
    # 所有行宽 <= 1（行宽不变量）
    for ln in lines:
        assert wcswidth_simple(ln) <= 1, f"{ln!r} 宽 {wcswidth_simple(ln)} > 1"
    # 字符全部跳过 → 空段（返回 [""] 而非产生宽 2 行）
    assert lines == [""]


def test_wrap_by_width_cjk_max_width_two_regression():
    """L1：max_width=2 且首字符 CJK 正常拆分——每行宽 <= 2，内容完整。"""
    lines = _wrap_by_width("가나", 2)
    assert lines == ["가", "나"]
    for ln in lines:
        assert wcswidth_simple(ln) <= 2, f"{ln!r} 宽 {wcswidth_simple(ln)} > 2"


def test_wrap_by_width_max_width_zero_regression():
    """L1 边界：max_width=0 无有效列宽返回 []（不拆行、不产生超宽单行）。"""
    assert _wrap_by_width("abc", 0) == []


def test_wrap_by_width_ascii_mixed_regression():
    """L1：ASCII 混合（"ab가c", 3）正常拆分不变（内容完整、行宽不变量）。"""
    lines = _wrap_by_width("ab가c", 3)
    assert "".join(lines) == "ab가c"
    for ln in lines:
        assert wcswidth_simple(ln) <= 3, f"{ln!r} 宽 {wcswidth_simple(ln)} > 3"


def test_compute_cursor_visual_pos_normal_regression():
    """L1 回归：既有正常路径（光标视觉位置计算）不受影响。"""
    # 纯 ASCII
    assert _compute_cursor_visual_pos("hello", 2, 10) == (0, 2)
    # CJK 多行换行（max_width=4：每个 CJK 宽 2，一行最多 2 个）
    assert _compute_cursor_visual_pos("가나다라", 3, 4) == (1, 2)
    # 含 \n 多逻辑行（cursor_pos=3 指向第二逻辑行 "cd" 的首字符 "c"）
    assert _compute_cursor_visual_pos("ab\ncd", 3, 10) == (1, 0)
