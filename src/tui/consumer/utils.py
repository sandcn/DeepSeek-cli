"""向后兼容存根 — 从 engine/ 重导出。

原模块已迁移至 src/tui/engine/utils.py。
"""

from __future__ import annotations

from ..engine.utils import _cmd_name, _emergency_write, _truncate_msg

__all__ = ["_cmd_name", "_emergency_write", "_truncate_msg"]
