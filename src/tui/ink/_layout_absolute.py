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

    def _measure_abs(x: int, y: int, avail_w: int, fill: bool, w_override: int):
        """以解析宽测量（百分比 width 归一化为整数，防 _measure 二次缩放）。

        2026-08-06：``_measure`` 内部对 ``width="50%"`` 再次按 ``avail_w``
        解析 → 宽度二次缩放（w*0.5）→ 内容按错误宽度换行 → 高度偏大。
        测量前临时把 ``props.width`` 替换为已解析整数 ``w_override``。
        ★ P3-12 机制说明（review 方向）：替换采用**新建 props dict 换引用**
        （``{**fiber.props, "width": w_override}``）而非原地修改——``_measure``
        的 props 引用级缓存（``mc[1] is fiber.props``）对**新引用必然 miss**
        （容器不缓存、TEXT 缓存键为 props 引用），避免「旧 props 引用 + 新
        width 值」的脏命中（旧引用命中会跳过百分比归一化、宽度二次缩放）；
        测量后 ``finally`` 恢复原 props 引用（缓存键回原引用，不残留）。
        ★ P3-11 修复（review 方向）：``w_override`` 显式参数传递——修复前
        内嵌函数通过闭包引用外部 ``w``（隐式依赖，重构时易错）；改为显式
        传参（调用点语义清晰：百分比宽场景用已解析整数覆盖 props.width）。
        """
        if has_w and isinstance(width_prop, str) and width_prop.endswith("%"):
            saved = fiber.props
            fiber.props = {**fiber.props, "width": w_override}
            try:
                return _measure(fiber, x, y, avail_w, fill)
            finally:
                fiber.props = saved
        return _measure(fiber, x, y, avail_w, fill)

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
    elif has_w:
        # ★ P1 修复（review 方向）：显式 width、无显式 height——先以固定宽
        #   测量内容高度（fill=False——内容自适应高）。修复前直接读
        #   ``fiber.layout_box``（可能为 None）→ h=0 → 最终放置又把
        #   fill=True 重测出的正确高度覆盖为 0（absolute 元素显式 width
        #   无显式 height 时高度恒为 0）。
        box = _measure_abs(inner_x, inner_y, max(1, w), fill=False, w_override=w)
        h = box.h
    else:
        # 无显式高（无显式宽时上方已内容测量）：复用测量结果
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
        # 再强制写入最终 w/h（修正百分比宽二次缩放误差）。
        # ★ P1 修复（review 方向）：cb.h 仅在显式 height 或纵向拉伸时覆盖——
        #   无显式 height（内容高度已由测量推导）时保留 fill=True 重测的
        #   高度，避免把内容高度覆盖为 0。
        _measure_abs(x, y, max(1, w), fill=True, w_override=w)
        cb = fiber.layout_box
        if cb is not None:
            cb.w = w
            if has_h or stretch_y:
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
