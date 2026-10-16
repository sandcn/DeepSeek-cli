"""Palette 槽位一致性校验回归（P0：模块级 assert → 显式 ValueError）。

★ 修复（P0）：``src/tui/core/_theme.py`` 原用模块级 ``assert`` 校验
  ``_PALETTE_SLOTS ⊆ Palette 字段集``——``assert`` 在 ``python -O`` 下被剥离，
  槽位表漂移不再被拦截。现抽为 ``_validate_palette_slots`` 并在导入期调用
  （显式 ``ValueError``），既可测又不依赖解释器优化开关。
"""

from __future__ import annotations

from dataclasses import fields

import pytest

from src.tui.core import _theme
from src.tui.core._theme import Palette, _PALETTE_SLOTS, _validate_palette_slots


def test_builtin_palette_slots_are_subset_of_palette_fields():
    """内置槽位表必须 ⊆ Palette 字段集（导入期已校验，此处固化契约）。"""
    assert set(_PALETTE_SLOTS) <= {f.name for f in fields(Palette)}


def test_validate_palette_slots_accepts_valid_input():
    _validate_palette_slots(["a", "b"], ["a", "b", "c"])


def test_validate_palette_slots_rejects_unknown_slot():
    with pytest.raises(ValueError) as exc:
        _validate_palette_slots(["a", "zzz"], ["a"])
    assert "zzz" in str(exc.value)


def test_validate_palette_slots_is_not_assert_based():
    """校验必须真实抛错（不受 ``-O`` 影响）——用显式异常类而非 AssertionError。"""
    with pytest.raises(ValueError):
        _validate_palette_slots(["missing"], [])


def test_module_imports_without_optimization_dependence():
    """模块导入后槽位校验函数可用（导入期已成功执行一次）。"""
    assert callable(_theme._validate_palette_slots)
