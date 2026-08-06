"""widgets — React Ink 风格控件库（交互 + 展示）。

- ``interactive.py`` — SelectInput / TextInput / MultiSelect / ConfirmInput
  （基于 ``use_input`` + ``use_state`` 的交互控件）；
- ``display.py`` — Spinner / ProgressBar / Table / Badge / Divider（纯展示）。

用法示例::

    from src.tui.ink import h
    from src.tui.ink.widgets import SelectInput, ProgressBar

    h(SelectInput, {"items": ["a", "b"], "onSelect": on_select})
    h(ProgressBar, {"percent": 0.6, "width": 20})
"""

from __future__ import annotations

from .interactive import (
    SelectInput,
    TextInput,
    MultiSelect,
    ConfirmInput,
    Toggle,
)
from .checkbox import Checkbox
from .display import (
    Spinner,
    ProgressBar,
    Table,
    Badge,
    Divider,
    Panel,
    # ★ P3（review）：SPINNER_FRAMES 同名异义说明——本文件导出的
    #   SPINNER_FRAMES 为 **dict**（动画预设名 → 帧串，来自 .display →
    #   ._spinner）；而 ``.spinner`` 模块（InlineSpinner）也导出同名
    #   **str** 类型符号（braille 帧序列）。因 tests/test_tui/ink/
    #   test_widgets_display.py 直接依赖本导出（``"dots" in SPINNER_FRAMES``
    #   dict 语义），按「改动会影响测试则只加注释不改名」原则保持导出名
    #   不变，仅注明语义差异（避免调用方误用 str 语义）。
    SPINNER_FRAMES,
)
from .tree import Tree
from .listview import ListView
from .focus import FocusGroup, Key
from .menu import Menu
from .search_input import SearchInput
from .tabs import Tabs
from .breadcrumbs import Breadcrumbs
from .layout import (
    Row,
    Column,
    Box,
    Text,
    Flex,
    Spacer,
    Center,
    Stack,
    HStack,
    VStack,
    Grid,
    ZStack,
)
from .radio import RadioList
from .codeblock import CodeBlock
from .spinner import InlineSpinner
from .gradient import Gradient
from .staticlines import StaticLines

__all__ = [
    "SelectInput",
    "TextInput",
    "MultiSelect",
    "ConfirmInput",
    "Toggle",
    "Checkbox",
    "Spinner",
    "ProgressBar",
    "Table",
    "Badge",
    "Divider",
    "Panel",
    "SPINNER_FRAMES",
    "Tree",
    "ListView",
    "FocusGroup",
    "Key",
    "Menu",
    "SearchInput",
    "Tabs",
    "Breadcrumbs",
    # 新增标准控件
    "RadioList",
    "CodeBlock",
    "InlineSpinner",
    "Gradient",
    "StaticLines",
    "Row",
    "Column",
    "Box",
    "Text",
    "Flex",
    "Spacer",
    "Center",
    "Stack",
    "HStack",
    "VStack",
    "Grid",
    "ZStack",
]
