#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for src/api/renderer/states.py — RenderEngine 状态 dataclass

覆盖内容：
  1. _CodeBlockState — 代码块渲染状态
  2. _DetailsState — Details 折叠块状态
  3. _TodoState — 任务列表进度状态
"""

import pytest

from src.api.renderer.states import (
    _CodeBlockState,
    _DetailsState,
    _TodoState,
)


# ═══════════════════════════════════════════════════════════════
# 1. _CodeBlockState — 代码块渲染状态
# ═══════════════════════════════════════════════════════════════

class TestCodeBlockState:
    """_CodeBlockState dataclass 测试"""

    def test_defaults(self):
        """无参构造应使用正确默认值"""
        state = _CodeBlockState()
        assert state.lang == ''
        assert state.line_num == 0
        assert state.indented is False
        assert state.highlight_lines == []

    def test_custom_values(self):
        """自定义参数应正确设置"""
        state = _CodeBlockState(
            lang='python',
            line_num=42,
            indented=True,
            highlight_lines=[3, 7, 10],
        )
        assert state.lang == 'python'
        assert state.line_num == 42
        assert state.indented is True
        assert state.highlight_lines == [3, 7, 10]

    def test_partial_custom(self):
        """部分参数自定义"""
        state = _CodeBlockState(lang='rust')
        assert state.lang == 'rust'
        assert state.line_num == 0  # 默认值
        assert state.indented is False  # 默认值

    def test_mutable_default_not_shared(self):
        """highlight_lines 默认空列表不共享"""
        a = _CodeBlockState()
        b = _CodeBlockState()
        a.highlight_lines.append(1)
        assert len(a.highlight_lines) == 1
        assert len(b.highlight_lines) == 0

    def test_assign_empty_lang(self):
        """lang 设为空字符串"""
        state = _CodeBlockState(lang='')
        assert state.lang == ''

    def test_assign_empty_highlight_lines(self):
        """highlight_lines 设为空列表"""
        state = _CodeBlockState(highlight_lines=[])
        assert state.highlight_lines == []

    def test_large_highlight_lines(self):
        """highlight_lines 含大量行号"""
        lines = list(range(1, 101))
        state = _CodeBlockState(highlight_lines=lines)
        assert len(state.highlight_lines) == 100

    def test_negative_line_num(self):
        """line_num 可为负数（表示未知行号）"""
        state = _CodeBlockState(line_num=-1)
        assert state.line_num == -1

    def test_mutate_after_creation(self):
        """创建后修改字段应生效"""
        state = _CodeBlockState()
        state.lang = 'javascript'
        state.line_num = 10
        state.indented = True
        state.highlight_lines = [1, 5]
        assert state.lang == 'javascript'
        assert state.line_num == 10
        assert state.indented is True
        assert state.highlight_lines == [1, 5]


# ═══════════════════════════════════════════════════════════════
# 2. _DetailsState — Details 折叠块状态
# ═══════════════════════════════════════════════════════════════

class TestDetailsState:
    """_DetailsState dataclass 测试"""

    def test_defaults(self):
        """无参构造 depth 应为 0"""
        state = _DetailsState()
        assert state.depth == 0

    def test_custom_depth(self):
        """自定义 depth"""
        state = _DetailsState(depth=3)
        assert state.depth == 3

    def test_zero_depth(self):
        """depth 为 0"""
        state = _DetailsState(depth=0)
        assert state.depth == 0

    def test_max_depth(self):
        """较大的 depth 值"""
        state = _DetailsState(depth=100)
        assert state.depth == 100

    def test_negative_depth(self):
        """depth 可为负数"""
        state = _DetailsState(depth=-1)
        assert state.depth == -1

    def test_mutate_depth(self):
        """创建后修改 depth"""
        state = _DetailsState()
        state.depth = 5
        assert state.depth == 5

    def test_multiple_instances_independent(self):
        """多个实例互不干扰"""
        a = _DetailsState(depth=1)
        b = _DetailsState(depth=2)
        assert a.depth == 1
        assert b.depth == 2
        a.depth = 10
        assert a.depth == 10
        assert b.depth == 2


# ═══════════════════════════════════════════════════════════════
# 3. _TodoState — 任务列表进度状态
# ═══════════════════════════════════════════════════════════════

class TestTodoState:
    """_TodoState dataclass 测试"""

    def test_defaults(self):
        """无参构造应使用正确默认值"""
        state = _TodoState()
        assert state.total == 0
        assert state.done == 0
        assert state.active is False

    def test_custom_values(self):
        """自定义参数应正确设置"""
        state = _TodoState(total=10, done=5, active=True)
        assert state.total == 10
        assert state.done == 5
        assert state.active is True

    def test_partial_custom(self):
        """部分参数自定义"""
        state = _TodoState(active=True)
        assert state.active is True
        assert state.total == 0
        assert state.done == 0

    def test_total_and_done_equal(self):
        """total 等于 done"""
        state = _TodoState(total=5, done=5)
        assert state.total == 5
        assert state.done == 5

    def test_done_exceeds_total(self):
        """done 超过 total（边界场景）"""
        state = _TodoState(total=3, done=5)
        assert state.done > state.total

    def test_zero_total(self):
        """total 为 0"""
        state = _TodoState(total=0, done=0, active=True)
        assert state.total == 0
        assert state.done == 0

    def test_large_values(self):
        """大数值"""
        state = _TodoState(total=1000000, done=500000)
        assert state.total == 1000000
        assert state.done == 500000

    def test_active_false(self):
        """active 为 False"""
        state = _TodoState(active=False)
        assert state.active is False

    def test_mutate_after_creation(self):
        """创建后修改字段"""
        state = _TodoState()
        state.total = 5
        state.done = 3
        state.active = True
        assert state.total == 5
        assert state.done == 3
        assert state.active is True

    def test_progress_computation(self):
        """手动计算进度（外部逻辑的验证）"""
        state = _TodoState(total=10, done=7)
        progress = state.done / state.total if state.total > 0 else 0.0
        assert progress == 0.7

    def test_zero_progress(self):
        """total > 0 但 done == 0"""
        state = _TodoState(total=10, done=0)
        assert state.done == 0
        assert state.total > 0

    def test_complete_progress(self):
        """total == done > 0"""
        state = _TodoState(total=5, done=5)
        assert state.done == state.total
