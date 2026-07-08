"""pipeline_filters — Token 流过滤器插件。

内置过滤器：
  - HeadingAnchorFilter: 收集标题 TOC 条目
  - TokenStreamOptimizer: 合并连续段落/空行 Token，减少冗余输出
"""

from .heading_anchor import HeadingAnchorFilter
from .stream_optimizer import TokenStreamOptimizer

__all__ = [
    "HeadingAnchorFilter",
    "TokenStreamOptimizer",
]
