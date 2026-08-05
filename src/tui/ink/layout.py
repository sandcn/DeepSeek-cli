"""布局 — flexbox 子集 + 文本换行（公共门面）。

模块边界（2026-08-05 架构优化）：原单一 layout.py（1903 行）按职责拆分为
独立模块，本文件作为公共门面保留 `LayoutBox` / `layout_tree` 入口并
re-export 全部符号（旧导入路径 ``from src.tui.ink.layout import ...``
保持不变，测试/外部调用面兼容）：

  - ``_layout_sizing.py``     — 尺寸解析（width/height/padding/flexGrow/flexShrink）
  - ``_layout_tree.py``       — 布局树遍历（host 子节点收集 / function 链下降）
  - ``_layout_transform.py``  — 坐标变换（子树平移 / reflow 重排）
  - ``_layout_flex.py``       — flexbox 分布（余数分配 / row justifyContent）
  - ``_layout_measure.py``    — 测量核心（``_measure`` / 行宽 / 对齐 / 收缩）
  - ``_layout_absolute.py``   — 绝对定位（第二遍放置）

依赖方向（单向无环）：
  ``_layout_sizing`` → fiber / _width
  ``_layout_tree`` → fiber
  ``_layout_transform`` → tree / sizing
  ``_layout_flex`` → transform
  ``_layout_measure`` → sizing/tree/transform/flex
  ``_layout_absolute`` → sizing/tree/transform/measure
  ``layout``（本模块，公共门面）→ 全部
"""

from __future__ import annotations

from .fiber import Fiber
from ._layout_sizing import (
    _resolve_length,
    _resolve_width,
    _clamp_width,
    _resolve_height,
    _flex_grow,
    _flex_shrink,
    _resolve_padding,
    _abs_int,
    _apply_aspect_ratio,
)
from ._layout_tree import (
    _skip_function,
    layout_children,
)
from ._layout_transform import (
    _reflow_subtree,
    _translate_subtree_y,
    _translate_subtree_x,
)
from ._layout_flex import (
    _distribute_extra,
    _reflow_row_justify,
)
from ._layout_measure import (
    LayoutBox,
    _runs_natural_width,
    wrap_text_lines,
    _apply_text_align,
    _shrink_row_children,
    _measure,
)
from ._layout_absolute import (
    _place_absolute,
    _layout_absolute_pass,
)


def layout_tree(root_fiber: Fiber, width: int) -> int:
    """布局整棵 host 树。

    两阶段：
      1. ``_measure`` 正常流布局（absolute 子节点不占空间）；
      2. ``_layout_absolute_pass`` 绝对定位元素第二遍定位。

    ★ 性能（PERF-17）：绝对定位第二遍**快速路径**——``_measure`` 整树遍历
    时若检测到任何 ``position="absolute"`` 节点则置位 root 标志
    （``_has_absolute_present``）；无 absolute 节点的组件树（绝大多数——
    App/ChatView 等业务组件不用绝对定位）跳过第二遍整树遍历（省 ~4.6ms/
    帧，1000+ 节点树）。absolute 节点必为其父容器的直接子节点（``_measure``
    容器分支的 ``has_abs`` 检测覆盖），故「容器检测到 absolute → 置位」即可
    捕获树中全部 absolute 节点（display:none 子树不测量、其内 absolute 不可见
    无需定位，语义一致）。

    Args:
        root_fiber: 根 fiber（ROOT 或 APP host）。
        width: 文档宽度（终端列宽）。

    Returns:
        文档总高度（行数）。
    """
    root = _skip_function(root_fiber) or root_fiber
    # ★ 性能（PERF-15）：每帧布局前复位 committed-chat 存在标志——_measure
    #   整树遍历时若遇到 committed-chat host 则置位（供 render_frame 的
    #   _find_committed_chat 快速路径 O(1) 判定；无 committed-chat 的组件树
    #   每帧零 DFS——修复前 _find_committed_chat 对纯 TEXT 树每帧全量 DFS）。
    root._committed_chat_present = False
    # ★ 性能（PERF-17）：每帧布局前复位 absolute 存在标志——_measure 容器
    #   分支检测到 absolute 子节点时置位（见 _measure）；无 absolute 节点
    #   跳过第二遍绝对定位遍历。
    root._has_absolute_present = False
    box = _measure(root, 0, 0, width)
    if getattr(root, "_has_absolute_present", False):
        _layout_absolute_pass(root)
    return box.h


__all__ = [
    "LayoutBox",
    "layout_tree",
    "layout_children",
    "wrap_text_lines",
    "_measure",
    "_skip_function",
    "_runs_natural_width",
    "_resolve_length",
    "_resolve_width",
    "_clamp_width",
    "_resolve_height",
    "_flex_grow",
    "_flex_shrink",
    "_resolve_padding",
    "_apply_aspect_ratio",
    "_apply_text_align",
    "_shrink_row_children",
    "_distribute_extra",
    "_reflow_row_justify",
    "_reflow_subtree",
    "_translate_subtree_y",
    "_translate_subtree_x",
    "_place_absolute",
    "_layout_absolute_pass",
    "_abs_int",
]
