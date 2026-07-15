"""Layout 模块 — 布局系统。

提供弹性布局容器，支持垂直/水平/弹性三种布局模式。

## 分层架构

```
layout/
├── container.py   — LayoutContainer 抽象基类（Widget 子类）
├── vbox.py        — VBox 垂直布局
├── hbox.py        — HBox 水平布局（含 HAlign 对齐常量）
└── flex.py        — Flex 弹性布局（智能路由层）
```

## 快速使用

```python
from tui_framework.layout import VBox, HBox, Flex
from tui_framework.widgets.base import Widget

# 垂直布局
vbox = VBox(spacing=1)
vbox.add_child(Widget())
vbox.add_child(Widget())

# 水平布局
hbox = HBox(spacing=2, align="top")
hbox.add_child(Widget())
hbox.add_child(Widget())

# 弹性布局
flex = Flex(direction="row", wrap=True, max_width=80)
flex.add_child(Widget(), flex_weight=2)
flex.add_child(Widget(), flex_weight=1)
```
"""

from .container import LayoutContainer
from .vbox import VBox
from .hbox import HBox, HAlign
from .flex import Flex, FlexChild, FlexDirection

__all__ = [
    "LayoutContainer",
    "VBox",
    "HBox",
    "HAlign",
    "Flex",
    "FlexChild",
    "FlexDirection",
]
