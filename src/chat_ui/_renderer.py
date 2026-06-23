"""TuiRenderer - Legacy 渲染器薄包装"""

from ._renderer_legacy import TuiRenderer as _LegacyTuiRenderer
from ._render_state import _RenderState

# 重新导出 Legacy 的 TuiRenderer 保持向后兼容
# 所有公开符号从 _renderer_legacy 重导出
TuiRenderer = _LegacyTuiRenderer
RenderEngine = _LegacyTuiRenderer
ContentRenderer = _LegacyTuiRenderer

__all__ = ["TuiRenderer", "RenderEngine", "ContentRenderer", "_RenderState"]
