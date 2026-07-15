"""Blessed Terminal 单例管理 — 全局共享 Blessed Terminal 实例。

Blessed 的 Terminal 实例封装了终端类型检测、能力查询、颜色支持等。
整个应用应共享同一 Terminal 实例，避免重复开销。

使用方式：
    from src.tui.terminal.blessed import get_terminal
    term = get_terminal()
    term.move_xy(0, 0)
    term.clear_eol()

设计决策：
  - 惰性初始化：首次调用 get_terminal() 时才创建 Terminal 实例
  - 测试可重置：reset_terminal() 允许测试环境重建实例
  - stream 参数保持默认（sys.__stdout__），但调用方仍使用自己的
    sys.__stdout__ 写入（经过 _StdoutLineTracker 包装）。
    Blessed 仅用于生成序列字符串（move_xy, clear_eol 等）。
  - 零开销：get_terminal() 返回 cached 实例，O(1) 复杂度。
"""
from tui_framework.terminal.blessed import *

__all__ = [
    "get_terminal",
    "reset_terminal",
    "blessed_available",
]
