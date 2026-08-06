"""布局坐标变换 — 子树平移 / reflow 重排。

模块边界（2026-08-05 架构优化）：从 ``ink/layout.py`` 拆分——坐标变换为
纯几何操作（修改 layout_box 坐标，无测量/换行副作用），独立成模块供
flexbox 分布（``_layout_flex``）/绝对定位（``_layout_absolute``）/
测量主循环（``_layout_measure``）共享。
"""

from __future__ import annotations

from .fiber import Fiber
from ._layout_sizing import _resolve_padding
from ._layout_tree import layout_children


def _reflow_subtree(fiber: Fiber, new_y: int, new_x: int | None = None) -> None:
    """递归重排 fiber 子树孙节点坐标（flexShrink 高度修改后使用，方向1）。

    flexShrink 修改直接子节点高度后仅重排直接子节点 y——孙节点 y 在 shrink
    前按原高度推算，shrink 后陈旧（下一帧 ``_measure`` 才会按新高度重排）。
    本函数在 shrink 路径内逐层将孙节点坐标累加重排（与 flexGrow 分支的
    ``cb.y = cursor_y`` 重排语义一致；仅本帧 shrink 路径内生效，``_measure``
    仍是布局唯一真源）。

    BUG-3（方向3 修复）：区分 flexDirection——column 容器子节点纵向堆叠
    （y 累加），row 容器子节点横向排列（x 累加、y 保持内边距基准）。修复前
    一律按纵向堆叠，row 容器 flexShrink 后子节点被错误竖排。

    Args:
        fiber: 待重排的 fiber（其 layout_box 非 None）。
        new_y: 该 fiber 的新 y 坐标。
        new_x: 该 fiber 的新 x 坐标（None 表示保持原 x）。
        ★ P3-8 说明（review 方向）：column 分支不传 ``new_x``——**column 仅
        重排 y，x 由测量阶段确定**（``_measure`` 已按最终 x 测量，重排只修正
        纵向堆叠后的 y；传入 new_x 会覆盖测量阶段的 x 布局结果）。
    """
    cb = fiber.layout_box
    if cb is None:
        return
    cb.y = new_y
    if new_x is not None:
        cb.x = new_x
    fiber.layout_box = cb
    pad_l, pad_r, pad_t, pad_b = _resolve_padding(fiber)
    # ★ 健壮性（PERF-12 同批）：``fiber.props.get("border", 0)`` 在 props 显式
    #   传 ``None``（键存在但值为 None）时返回 None → ``if border:`` 为 False
    #   → border 保持 None → ``cursor_x = cb.x + pad_l + border`` 崩溃。统一
    #   用 ``or 0`` 兜底（None/0 归 0；非法值走 try/except 归 0）。
    border = fiber.props.get("border") or 0
    if border:
        try:
            border = max(0, int(border))
        except (TypeError, ValueError, OverflowError):
            border = 0
    margin = fiber.props.get("margin") or 0
    if margin:
        try:
            margin = max(0, int(margin))
        except (TypeError, ValueError, OverflowError):
            margin = 0
    gap = fiber.props.get("gap")
    if gap is not None:
        try:
            spacing = max(0, int(gap))
        except (TypeError, ValueError, OverflowError):
            spacing = margin
    else:
        spacing = margin
    direction = fiber.props.get("flexDirection", "column")
    if direction == "row":
        # row：横向排列——子节点 x 累加，y 保持内边距基准（纵向偏移由
        # alignItems 承担；与 _measure row 分支语义一致）。
        cursor_x = cb.x + pad_l + border
        row_children = layout_children(fiber)
        for i, child in enumerate(row_children):
            _reflow_subtree(child, new_y + pad_t + border, cursor_x)
            ccb = child.layout_box
            if ccb is not None:
                cursor_x += ccb.w
                # ★ P3-7 修复（review 方向）：最后子节点后不计 spacing——与
                #   ``_measure`` row 分支（``if i < n_children - 1: cursor_x +=
                #   spacing``）一致。修复前无条件累加 spacing（局部变量无
                #   副作用——最后子节点后 cursor_x 不再被消费——但语义误导，
                #   未来若复用 cursor_x 会多出间隔）。
                if i < len(row_children) - 1:
                    cursor_x += spacing
    else:
        # column：纵向堆叠——子节点 y 累加（默认方向，与既有语义一致）。
        cursor_y = new_y + pad_t + border
        for child in layout_children(fiber):
            _reflow_subtree(child, cursor_y)
            ccb = child.layout_box
            if ccb is not None:
                cursor_y += ccb.h + spacing


def _translate_subtree_y(fiber: Fiber, delta_y: int) -> None:
    """整体平移 fiber 子树（含自身）的 y 坐标（方向3 探针复用修复）。

    探针测量（fill=False column 容器）把子树全部按 ``inner_y`` 测量（y 重叠），
    主循环复用盒时须将整棵子树平移到 ``cursor_y``——仅更新自身 box 会导致
    第 2+ 个子节点的后代 y 停留在 ``inner_y`` 基准（与首个子树重叠）。
    本函数保持 w/h/x 不变，只平移 y（delta_y 为相对偏移，可为负）。

    方向3（BUG-2 关联修复）：**不遍历子树根自身的 sibling 链**——调用方以
    单个直接子节点为参数（探针复用 / alignItems 偏移），仅须平移该子节点及
    其**全部后代**；遍历子树根自身 sibling 会把后续兄弟节点一并平移（其后
    再被循环各自平移 → 重复偏移）。
    ★ BUG-14 修复：**遍历后代 sibling 链**（``child + child.sibling``）——
    修复前仅递归 ``fiber.child``（首子链），嵌套容器内第 2+ 个子节点
    （child 的 sibling）停留在旧坐标 → alignItems/alignSelf/探针复用偏移
    后嵌套多子容器文本/边框错位（确定性渲染错误，见
    ``test_translate_subtree_multi_child``）。

    Args:
        fiber: 待平移的子树根（其 layout_box 非 None）。
        delta_y: y 偏移量（像素/行）。
    """
    if fiber is None:
        return
    if fiber.layout_box is not None:
        cb = fiber.layout_box
        cb.y += delta_y
        fiber.layout_box = cb
    child = fiber.child
    while child is not None:
        _translate_subtree_y(child, delta_y)
        child = child.sibling


def _translate_subtree_x(fiber: Fiber, delta_x: int) -> None:
    """整体平移 fiber 子树（含自身）的 x 坐标（alignItems/alignSelf 偏移修复）。

    column alignItems（center/flex-end）与 alignSelf 对子节点做横向偏移时，
    只改子容器自身 layout_box.x 会令其后代停留在原 x 基准（嵌套容器内
    TEXT/边框错位——TEXT 按未偏移 x 绘制、边框按偏移后 x 绘制）。整棵子树
    平移 delta_x 保持后代相对位置不变。

    本函数保持 w/h/y 不变，只平移 x（delta_x 为相对偏移，可为负）。不遍历
    子树根自身的 sibling 链（仅平移参数指定子树及全部后代，与
    ``_translate_subtree_y`` 一致）。★ BUG-14：遍历后代 sibling 链
    （``child + child.sibling``）——修复前仅递归首子链，嵌套多子容器错位。

    Args:
        fiber: 待平移的子树根（其 layout_box 非 None）。
        delta_x: x 偏移量（像素/列）。
    """
    if fiber is None:
        return
    if fiber.layout_box is not None:
        cb = fiber.layout_box
        cb.x += delta_x
        fiber.layout_box = cb
    child = fiber.child
    while child is not None:
        _translate_subtree_x(child, delta_x)
        child = child.sibling


__all__ = ["_reflow_subtree", "_translate_subtree_y", "_translate_subtree_x"]
