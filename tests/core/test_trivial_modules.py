"""src/tui/input + src/renderer/_utils/_css_colors — 门面/空模块冒烟测试。

覆盖：
  - tui.input 门面 re-export（Input/KeyEvent 与 _input 同源）
  - _css_colors 模块可导入（当前为空映射模块）
"""

from __future__ import annotations


def test_tui_input_facade_reexports():
    from src.tui import input as input_facade
    from src.tui._input import Input as RealInput
    from src.tui._input import KeyEvent as RealKeyEvent

    assert input_facade.Input is RealInput
    assert input_facade.KeyEvent is RealKeyEvent
    assert input_facade.__all__ == ["Input", "KeyEvent"]


def test_css_colors_module_importable():
    import src.renderer._utils._css_colors as css_colors

    assert css_colors.__doc__  # 模块存在且可导入
