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
)
from .display import (
    Spinner,
    ProgressBar,
    Table,
    Badge,
    Divider,
    SPINNER_FRAMES,
)
from .layout import (
    Row,
    Column,
    Center,
    Stack,
    HStack,
    VStack,
    Grid,
    ZStack,
)

__all__ = [
    "SelectInput",
    "TextInput",
    "MultiSelect",
    "ConfirmInput",
    "Spinner",
    "ProgressBar",
    "Table",
    "Badge",
    "Divider",
    "SPINNER_FRAMES",
    "Row",
    "Column",
    "Center",
    "Stack",
    "HStack",
    "VStack",
    "Grid",
    "ZStack",
]
