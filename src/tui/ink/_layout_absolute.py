"""布局绝对定位 — position="absolute" 锚点解析 + 第二遍放置。

模块边界（2026-08-05 架构优化）：从 ``ink/layout.py`` 拆分——绝对定位为
独立布局阶段（正常流测量后的第二遍放置），不参与 ``_measure`` 主循环
（``_measure`` 仅分离 absolute 子节点并置位存在标志，不调用本模块）。

依赖方向：本模块 → sizing（锚点/内边距解析）/ tree（子节点收集）/
transform（子树平移）/ measure（确定尺寸时重新测量）。
"""

from __future__ import annotations

from .fiber import Fiber
from ._layout_sizing import (
    _resolve_padding,
    _resolve_length,
    _abs_int,
)
from ._layout_tree import layout_children
from ._layout_transform import (
    _translate_subtree_x,
    _translate_subtree_y,
)
from ._layout_measure import _measure


def _place_absolute(fiber: Fiber, base: Fiber) -> None:
    """将绝对定位节点（position="absolute"）相对基准容器内容区定位。

    锚点解析（React Ink ``position: absolute`` 语义）：
      - ``left`` → x = 内容区左 + left；``right``（无 left）→ x = 内容区右 - right - w；
      - ``top`` → y = 内容区顶 + top；``bottom``（无 top）→ y = 内容区底 - bottom - h；
      - 无锚点 → 内容区左上；
      - ``width``/``height`` 显式（含 ``"50%"`` 百分比，相对内容区尺寸）→
        固定尺寸；left+right / top+bottom 同时指定且无显式宽/高 → 拉伸；
      - 均无 → 内容自适应（fill=False 测量）。

    放置：有确定尺寸时以 ``_measure`` 重新测量（fill=True——固定尺寸容器
    内部正常 flex 布局），再强制写入最终 w/h；纯内容尺寸时仅平移子树
    （``_translate_subtree_*`` 保证嵌套容器后代坐标正确）。
    """
    base_box = base.layout_box
    if base_box is None:
        return
    pad_l, pad_r, pad_t, pad_b = _resolve_padding(base)
    border = base.props.get("border") or 0
    try:
        border = max(0, int(border))
    except (TypeError, ValueError, OverflowError):
        border = 0
    inner_x = base_box.x + pad_l + border
    inner_y = base_box.y + pad_t + border
    inner_w = max(0, base_box.w - (pad_l + pad_r + 2 * border))
    inner_h = max(0, base_box.h - (pad_t + pad_b + 2 * border))

    left = _abs_int(fiber.props.get("left"))
    right = _abs_int(fiber.props.get("right"))
    top = _abs_int(fiber.props.get("top"))
    bottom = _abs_int(fiber.props.get("bottom"))
    width_prop = fiber.props.get("width")
    height_prop = fiber.props.get("height")
    has_w = width_prop is not None
    has_h = height_prop is not None

    # ── 尺寸解析 ──
    if has_w:
        w = _resolve_length(width_prop, inner_w)
    else:
        # 内容测量（fill=False → 内容自适应宽；显式 height 由 _measure 应用）
        box = _measure(fiber, inner_x, inner_y, inner_w, fill=False)
        w = box.w
    if has_h:
        if isinstance(height_prop, str) and height_prop.endswith("%"):
            try:
                h = max(0, int(inner_h * float(height_prop[:-1]) / 100.0))
            except (TypeError, ValueError, OverflowError):
                h = 0
        else:
            try:
                h = max(0, int(height_prop))
            except (TypeError, ValueError, OverflowError):
                h = 0
    else:
        # 无显式高：内容测量值（has_w 分支已 _measure 过；has_w 且未测量时
        # 重新取 layout_box）
        box = fiber.layout_box
        h = box.h if box is not None else 0
    # 拉伸（left+right / top+bottom 同时指定且无显式宽/高）
    stretch_x = left is not None and right is not None and not has_w
    stretch_y = top is not None and bottom is not None and not has_h
    if stretch_x:
        w = max(0, inner_w - left - right)
    if stretch_y:
        h = max(0, inner_h - top - bottom)

    # ── 锚点解析 ──
    if left is not None:
        x = inner_x + left
    elif right is not None:
        x = inner_x + inner_w - right - w
    else:
        x = inner_x
    if top is not None:
        y = inner_y + top
    elif bottom is not None:
        y = inner_y + inner_h - bottom - h
    else:
        y = inner_y

    # ── 最终放置 ──
    if has_w or has_h or stretch_x or stretch_y:
        # 有确定尺寸 → 重新测量（fill=True——固定尺寸容器内部正常 flex），
        # 再强制写入最终 w/h（修正百分比宽二次缩放误差）
        _measure(fiber, x, y, max(1, w), fill=True)
        cb = fiber.layout_box
        if cb is not None:
            cb.w = w
            cb.h = h
            fiber.layout_box = cb
    else:
        # 纯内容尺寸 → 平移子树到锚点（后代坐标随动）
        box = fiber.layout_box
        if box is not None:
            if box.x != x:
                _translate_subtree_x(fiber, x - box.x)
            if box.y != y:
                _translate_subtree_y(fiber, y - box.y)


def _layout_absolute_pass(root: Fiber) -> None:
    """第二遍布局：绝对定位元素（position="absolute"）相对基准容器放置。

    正常流布局（``_measure``）已跳过 absolute 子节点（不占空间）；本 pass 在
    整树测量完成后沿树遍历，对每个 absolute 节点按其 top/left/right/bottom
    相对**最近的 ``position="relative"`` 祖先**（缺省 root）的内容区定位。
    容器自身 ``position="relative"`` 时作为其子节点的定位基准；嵌套
    absolute 容器内的 relative/absolute 孙节点经递归自然处理。
    """

    def _visit(fiber: Fiber, base: Fiber | None) -> None:
        base_for_children = (
            fiber if fiber.props.get("position") == "relative" else base
        )
        for child in layout_children(fiber):
            _visit(child, base_for_children)
        if fiber.props.get("position") == "absolute" and base is not None:
            _place_absolute(fiber, base)

    _visit(root, root)


__all__ = ["_place_absolute", "_layout_absolute_pass"]
