"""Style 不可变样式描述器 — 统一样式管理和 ANSI 序列构建。

提供：
  - Style:        不可变样式描述器，封装前景色/背景色/加粗/斜体/暗淡/下划线
  - StyledText:   带样式的文本片段，一件渲染
  - StyleSheet:   命名样式注册表，类似 BreathPalette 的集中管理模式

设计原则：
  - 不可变：Style 为冻结 dataclass，所有合并操作返回新实例
  - 纯函数：to_ansi()/apply() 无副作用，结果可缓存
  - 延迟导入：from_theme() 方法体内延迟加载 THEME，避免模块加载顺序依赖
  - 零依赖：仅使用标准库，不依赖 src/tui/ 上层模块
"""
from tui_framework.core.style import *

__all__: list[str] = [
    "Style",
    "StyledText",
    "StyleSheet",
]
