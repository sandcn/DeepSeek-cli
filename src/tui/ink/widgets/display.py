"""display 门面 — React Ink 风格展示控件（Spinner / ProgressBar / Table /
Badge / Divider / Panel）。

模块边界（2026-08-05 架构优化）：原单一 display.py（560 行）按控件拆分为
独立模块，本文件作为公共门面 re-export 全部符号（旧导入路径
``from src.tui.ink.widgets.display import ...`` 保持不变，测试/外部调用面
兼容）：

  - ``_display_common.py``  — 公共辅助（_color/_resolve_style/_repeat_to_width）
  - ``_spinner.py``         — Spinner（旋转加载动画 + SPINNER_FRAMES）
  - ``_progress.py``        — ProgressBar（进度条）
  - ``_table.py``           — Table（对齐表格）
  - ``_badge_divider.py``   — Badge（徽章）+ Divider（分隔线）
  - ``_panel.py``           — Panel（带标题边框面板）

★ patch 兼容（test_widgets_display.py 锁定）：``import threading`` 保留于本
模块——``patch("src.tui.ink.widgets.display.threading.Timer")`` 修改的是全局
threading 模块对象（单例），``_spinner`` 经 ``import threading`` 引用同一
对象，patch 依然生效。

纯展示控件（无输入路由），输出由 BOX/TEXT 元素树构建。
依赖约束：仅依赖 element / output / core.style / _screen / hooks（Layer 0/1），
无父包依赖。宽度一律用 ``_width.wcswidth_simple``（唯一宽度依据）。
"""

from __future__ import annotations

import threading  # ★ patch 兼容保留（见模块 docstring）

from ._display_common import (
    _color,
    _resolve_style,
    _repeat_to_width,
)
from ._spinner import (
    Spinner,
    SPINNER_FRAMES,
)
from ._progress import ProgressBar
from ._table import Table
from ._badge_divider import Badge, Divider
from ._panel import Panel

__all__ = ["Spinner", "ProgressBar", "Table", "Badge", "Divider", "Panel"]
