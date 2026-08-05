"""interactive 门面 — React Ink 风格交互控件（SelectInput / TextInput /
MultiSelect / ConfirmInput / Toggle）。

模块边界（2026-08-05 架构优化）：原单一 interactive.py（623 行）按控件拆分
为独立模块，本文件作为公共门面 re-export 全部符号（旧导入路径
``from src.tui.ink.widgets.interactive import ...`` 保持不变，测试/外部
调用面兼容）：

  - ``_interactive_common.py`` — 公共辅助（_call/_color/_normalize_items/
                                 _visible_window/_clamp_index/_hashable）
  - ``_select_input.py``       — SelectInput（单选列表）
  - ``_text_input.py``         — TextInput（单行文本输入）
  - ``_multi_select.py``       — MultiSelect（多选列表）
  - ``_confirm_toggle.py``     — ConfirmInput（y/n 确认）+ Toggle（开关）

基于 ``use_input`` + ``use_state`` 实现，按键事件结构对齐框架 KeyEvent：
  - kind: "char" | "enter" | "backspace" | "delete" | "arrow_up" | "arrow_down"
          | "arrow_left" | "arrow_right" | "home" | "end" | "escape" | ...
  - char: kind="char" 时的可打印字符（含粘贴整段文本）

与 React Ink 生态控件（ink-select-input / ink-text-input / ink-multi-select /
ink-confirm-input）API 对齐：
  - 控件为函数组件（props 传入 + 内部 state）；
  - 父组件渲染时传入回调（onSelect/onChange/onSubmit/onConfirm），控件经
    ``use_input`` 消费按键并触发回调；
  - ``focus=False`` 时控件不参与输入路由（零行为变化，事件放行旧路径）。

依赖约束：仅依赖 element / output / core.style / _screen / hooks（Layer 0/1），
无父包依赖。
"""

from __future__ import annotations

from ._interactive_common import (
    _call,
    _color,
    _normalize_items,
    _visible_window,
    _clamp_index,
    _hashable,
)
from ._select_input import SelectInput
from ._text_input import TextInput
from ._multi_select import MultiSelect
from ._confirm_toggle import ConfirmInput, Toggle

__all__ = ["SelectInput", "TextInput", "MultiSelect", "ConfirmInput", "Toggle"]
