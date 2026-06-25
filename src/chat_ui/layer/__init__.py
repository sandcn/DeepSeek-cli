"""TUI 层级渲染系统。

提供分层渲染能力：
- Layer 枚举定义层级
- LayerManager 管理各层 buffer
- Compositor 合并层级输出
- IncrementalLayerRenderer 增量输出到终端
"""

from .types import Layer, LayerBuffer, DEFAULT_LAYER, MAX_LAYERS
from .manager import LayerManager
from .compositor import Compositor
from .renderer import IncrementalLayerRenderer

__all__ = [
    "Layer",
    "LayerBuffer",
    "DEFAULT_LAYER",
    "MAX_LAYERS",
    "LayerManager",
    "Compositor",
    "IncrementalLayerRenderer",
]
