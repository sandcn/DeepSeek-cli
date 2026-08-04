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
from .spinner import InlineSpinner, SPINNER_FRAMES as _INLINE_SPINNER_FRAMES
from .gradient import Gradient

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
