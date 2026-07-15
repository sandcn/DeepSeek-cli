"""TUI Framework — 独立的、非全屏的终端 UI 框架。

分层架构（自底向上）:
  core/       — 零业务依赖的内核（颜色、样式、动效、主题、文本工具等）
  terminal/   — 终端能力抽象（适配器、输出目标、窄屏检测）
  events/     — 事件系统（事件总线、事件类型、事件池）
  animation/  — 动画系统（动效组合器、过渡效果）
  widgets/    — 控件库（Widget 基类、标准控件）
  layout/     — 布局系统（VBox/HBox/Flex 容器）

与 src/tui/ 的关系:
  本框架从 src/tui/ 提取零业务依赖模块，通过 Adapter 模式支持渐进式迁移。
  src/tui/ 保持不变，通过 adapter 模块桥接新框架。
"""

__version__ = "0.1.0"

# ---- 框架级公开 API ----
from .framework import Framework
from .core.style import Style, StyledText, StyleSheet
from .core.theme import Theme, THEME, THEMES, set_theme, get_active_theme, list_themes
from .core.animator import AnimatorContext
from .core.effects import EffectRegistry
from .core.color import Color256, TrueColor, RGB
from .widgets.base import Widget, TuiComponent
from .widgets.animated import AnimatedWidget
from .layout import VBox, HBox, Flex, LayoutContainer
from .animation.declarative import effect
from .application import Application

__all__ = [
    "Framework",
    "Style", "StyledText", "StyleSheet",
    "Theme", "THEME", "THEMES", "set_theme", "get_active_theme", "list_themes",
    "AnimatorContext",
    "EffectRegistry",
    "Color256", "TrueColor", "RGB",
    "Widget", "TuiComponent",
    "AnimatedWidget",
    "VBox", "HBox", "Flex", "LayoutContainer",
    "effect",
    "Application",
]
