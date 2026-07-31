"""TUI 统一配置 — 门面模块，所有实现委托至 ._config。"""

from __future__ import annotations

from ._config import TuiConfig, ConfigBase

__all__ = ["ConfigBase", "TuiConfig"]
