"""终端能力检测 — TrueColor / UTF-8 / Emoji / 256色

检测以下终端能力并缓存为模块级常量：
  1. TrueColor（24-bit 真彩色）支持
  2. UTF-8 编码支持
  3. Emoji 渲染支持
  4. 256 色支持（兼容 xterm-256color 标准）

设计原则：
  - 检测结果在模块首次导入后缓存（通过 ``functools.cache``），后续 O(1) 访问
  - 惰性检测：首次访问对应检测函数时触发计算，避免导入时副作用
  - 双重检测：通过 Blessed Terminal + 环境变量交叉验证确保准确性
  - 安全降级：检测失败时返回保守值（False），不抛异常

使用方式：
    from src.tui.terminal.capabilities import (
        supports_truecolor,
        supports_256color,
        supports_utf8,
        supports_emoji,
        get_capabilities_summary,
    )

    if supports_truecolor():
        # 使用 24-bit 颜色
        pass

    summary = get_capabilities_summary()
    # -> {"truecolor": True, "256color": True, "utf8": True, "emoji": True, "color_count": 256}
"""
from tui_framework.terminal.capabilities import *

__all__ = [
    "supports_truecolor",
    "supports_256color",
    "supports_utf8",
    "supports_emoji",
    "get_capabilities_summary",
]
