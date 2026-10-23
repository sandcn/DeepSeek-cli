"""widgets — React Ink 风格控件库（交互 + 展示）。

- ``interactive.py`` — SelectInput / TextInput / MultiSelect（基于 ``use_input``
  + ``use_state`` 的交互控件）；
- ``display.py`` — Badge / Divider / Panel（纯展示控件）。

用法示例::

    from src.tui.ink import h
    from src.tui.ink.widgets import SelectInput

    h(SelectInput, {"items": ["a", "b"], "onSelect": on_select})

**2026-10 架构演进（清理未消费控件）**：``Breadcrumbs`` / ``Checkbox`` /
``CodeBlock`` / ``CollapsibleCodeBlock`` / ``ConfirmInput`` / ``Toggle`` /
``FocusGroup`` / ``Key`` / ``Menu`` / ``ProgressBar`` / ``RadioList`` /
``SearchInput`` / ``Spinner`` / ``InlineSpinner`` / ``Table`` / ``Tabs`` /
``Tree`` / ``Viewport`` / ``MultiPanel`` 在**生产代码与测试中均零引用**
（实测 import 图与断言扫描），已删除——保留它们只会扩大框架表面积、增加
「看起来能用但无人验证」的维护负担。需要时从 git 历史恢复，并在恢复的同一
提交里补上使用方与测试。
"""

from __future__ import annotations

from .interactive import (
    SelectInput,
    TextInput,
    MultiSelect,
)
from .display import (
    Badge,
    Divider,
    Panel,
)
from .listview import ListView
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
from .gradient import Gradient
from .staticlines import StaticLines

__all__ = [
    "SelectInput",
    "TextInput",
    "MultiSelect",
    "Badge",
    "Divider",
    "Panel",
    "ListView",
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
