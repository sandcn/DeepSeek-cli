"""
【向下兼容 shim】原 escape_monitor.py 已拆分为 escape_monitor/ 包。

保留此文件作为 re-export 入口，确保所有已存在的
  from src.api.escape_monitor import XXX
  import src.api.escape_monitor as em
仍然兼容。
"""

from .escape_monitor import *  # noqa: F401, F403
