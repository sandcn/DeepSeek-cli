"""display 门面 — React Ink 风格展示控件（Badge / Divider / Panel）。

模块边界（2026-08-05 架构优化）：原单一 display.py 按控件拆分为独立模块，
本文件作为公共门面 re-export 剩余符号（旧导入路径
``from src.tui.ink.widgets.display import ...`` 保持不变）：

  - ``_display_common.py``  — 公共辅助（_color/_resolve_style/_repeat_to_width）
  - ``_badge_divider.py``   — Badge（徽章）+ Divider（分隔线）
  - ``_panel.py``           — Panel（带标题边框面板）

**2026-10 架构演进（清理未消费控件）**：Spinner / ProgressBar / Table 在生产
代码与测试中均零引用，已随 `_spinner.py` / `_progress.py` / `_table.py` 删除
（理由见 ``widgets/__init__.py`` docstring）。

纯展示控件（无输入路由），输出由 BOX/TEXT 元素树构建。
依赖约束：仅依赖 element / output / Layer 0 样式与宽度工具，无父包依赖。
"""

from __future__ import annotations

from ._display_common import (
    _color,
    _resolve_style,
    _repeat_to_width,
)
from ._badge_divider import Badge, Divider
from ._panel import Panel

__all__ = [
    "Badge",
    "Divider",
    "Panel",
    # 辅助符号补入 __all__（通配导入不丢失）
    "_color",
    "_resolve_style",
    "_repeat_to_width",
]
