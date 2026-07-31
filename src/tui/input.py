"""Input — 统一 TUI 输入管理（门面模块，所有实现委托至 ._input）。"""

from __future__ import annotations

from ._input import Input, KeyEvent

__all__ = ["Input", "KeyEvent"]
