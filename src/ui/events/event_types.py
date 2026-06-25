"""事件类型兼容层 — 所有定义已迁移至 src.shared_events.types。

此文件保留作为向后兼容 re-export。新代码请直接导入：
    from src.shared_events.types import DisplayEvent, ...
"""
from src.shared_events.types import *  # noqa: F401, F403
