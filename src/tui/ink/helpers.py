"""ink 工具函数门面 — ANSI 剥离 / 宽度测量 / 换行截断 / 样式解析 / 边框块。

所有宽度计算统一走 ``_width.wcswidth_simple``（唯一宽度依据）。
ANSI 转义序列不占显示宽度，测量前需先剥离或识别。

模块边界（2026-08-05 架构优化）：原单一 helpers.py（674 行）按职责拆分为
独立模块，本文件作为公共门面 re-export 全部符号（旧导入路径
``from src.tui.ink.helpers import ...`` 保持不变，测试/外部调用面兼容）：

  - ``_ansi_utils.py``    — ANSI 转义剥离/检测/视觉宽度/ASCII 快路径判定
  - ``_runs_utils.py``    — StyledRun 换行/截断（wrap_runs_by_width + truncate 族）
  - ``_style_utils.py``   — TEXT shorthand 样式解析（color/bold/transform → Style）
  - ``_border_box.py``    — 边框块构建（build_border_box）
  - 本文件保留 ``line_to_ansi``（Line → ANSI 字符串，依赖 output.Line）

依赖方向（单向无环）：
  ``_ansi_utils`` → _width
  ``_runs_utils`` → _width / output / _ansi_utils
  ``_style_utils`` → core.style
  ``_border_box`` → output / core.style / _runs_utils
  ``helpers``（本模块，公共门面）→ 全部
"""

from __future__ import annotations

from .output import Line
from ._ansi_utils import (
    _ANSI_RE,
    cursor_control_re,
    strip_ansi,
    has_ansi,
    visual_width,
    _is_plain_ascii_fast,
)
from ._runs_utils import (
    wrap_runs_by_width,
    _first_logical_line_runs,
    truncate_runs,
    truncate_runs_ellipsis,
    truncate_runs_start,
    truncate_runs_middle,
    truncate_line,
    pad_line,
    _runs_total_width,
    _keep_head,
    _keep_tail,
)
from ._style_utils import (
    _parse_color,
    resolve_text_style,
    apply_text_transform,
)
from ._border_box import build_border_box


def line_to_ansi(line: Line) -> str:
    """Line → ANSI 字符串（含行末样式重置）。"""
    return line.render()


__all__ = [
    "strip_ansi",
    "has_ansi",
    "visual_width",
    "wrap_runs_by_width",
    "truncate_runs",
    "truncate_runs_ellipsis",
    "truncate_runs_start",
    "truncate_runs_middle",
    "truncate_line",
    "pad_line",
    "line_to_ansi",
    "build_border_box",
    "resolve_text_style",
    "apply_text_transform",
    "_parse_color",
    "cursor_control_re",
    # ★ P3（review）：__all__ 补全——以下符号已模块级导入（_ansi_utils /
    #   _runs_utils re-export）但未列入 __all__（通配导入丢失）。
    "_ANSI_RE",
    "_is_plain_ascii_fast",
    "_first_logical_line_runs",
    "_runs_total_width",
    "_keep_head",
    "_keep_tail",
]
