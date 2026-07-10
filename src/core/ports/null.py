"""通用空端口实现 — 向后兼容导入层

【已废弃】具体实现在 src.core.adapters.null 中。
此处保留为向后兼容的 re-export。
新代码请直接从 src.core.adapters.null 导入。
"""

from __future__ import annotations

from ..adapters.null import _NullPort, _NullOutputPort  # noqa: F401
