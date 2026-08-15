"""布局 flexbox 分布 — flexGrow/flexShrink 余数分配 + row justifyContent。

模块边界（2026-08-05 架构优化）：从 ``ink/layout.py`` 拆分——flexbox
分布为独立算法（权重分配 / 间隔重排），不依赖测量（``_measure`` 仅调用
本模块的纯函数）。注意：``_shrink_row_children``（row 超宽收缩，调用
``_measure`` 重新测量）保留在 ``_layout_measure``（避免 measure ↔ flex
模块循环）。
"""

from __future__ import annotations

from typing import Callable

from .fiber import Fiber
from ._layout_transform import _translate_subtree_x


def _compute_weight_shares(weights: list[int], total_extra: int) -> tuple[int, list[int]]:
    """按权重计算余数分配份额：返回 ``(per, extra_shares)``。

      - ``per = total_extra // sum(weights)``（每个权重单位的基础份额）；
      - ``extra_shares``：余数（total_extra % sum(weights)）的分配份额——
        前 remainder 个权重 >0 节点各 +1；余数超过权重节点数时，超出部分
        按权重单位分配（每个权重单位至多 +1），保证余数**不丢失**。
        （修复前按「加权节点索引 < remainder」逐节点 +1：remainder 可大于
        权重节点数（如 extra=5、权重 [10,1] → remainder=5、节点仅 2 个），
        导致余数丢失（欠分配 3 行）。）

    权重总和 <= 0 或 total_extra <= 0 时返回 ``(0, [0] * len(weights))``。

    供 ``_distribute_extra``（column 高度）与 ``_layout_measure`` 的 row
    flexGrow 宽度分配共用，避免两处余数逻辑漂移。
    """
    total_weight = sum(weights)
    if total_weight <= 0 or total_extra <= 0:
        return 0, [0] * len(weights)
    per = total_extra // total_weight
    remainder = total_extra % total_weight
    extra_shares = [0] * len(weights)
    weighted_idx = 0
    n_weighted = 0
    for i, w in enumerate(weights):
        if w > 0:
            if weighted_idx < remainder:
                extra_shares[i] += 1
            weighted_idx += 1
            n_weighted += 1
    overflow = remainder - n_weighted
    if overflow > 0:
        # 超出部分按权重单位分配：节点 i 覆盖权重单位
        # [prefix, prefix+w)；单位编号 < overflow 的 +1（权重 0 节点不参与）。
        prefix = 0
        for i, w in enumerate(weights):
            if w <= 0:
                continue
            extra_shares[i] += max(0, min(w, overflow - prefix))
            prefix += w
    return per, extra_shares


def _distribute_extra(
    children: list[Fiber],
    weight_fn: Callable[[Fiber], int],
    total_extra: int,
    inner_y: int,
    margin: int,
    direction: int = 1,
    clamp_min: int | None = None,
) -> None:
    """按权重分配余数给子节点高度并重排 y（flexGrow/flexShrink 共用，方向1）。

    统一 flexGrow 与 flexShrink 的余数分配逻辑（差异封装——两处相似循环
    收敛为单一 helper，满足「先封装差异再做功能」）：

      - ``per = total_extra // sum(weights)``；
      - 余数按 ``weighted_idx < remainder`` 分配给**权重 >0 的节点**（按权重
        节点序列计索引，非原 children 索引——原实现按 ``i < remainder`` 分配，
        权重 0 节点也会得分，权重不符）；
      - 权重 0 节点不参与分配（不增减高度），但 y 坐标仍参与重排；
      - 分配后重排子节点 y（累加 margin），保证无重叠（与 flexGrow 分支
        既有重排语义一致）。

    Args:
        children: 直接 host 子节点。
        weight_fn: 权重解析函数（``_flex_grow`` 或 ``_flex_shrink``）。
        total_extra: 待分配的总余数（>0 才有意义）。
        inner_y: 容器内边距后的起始 y（统一从 inner_y 重排）。
        margin: 子节点间距（每子累计）。
        direction: 分配方向——``1`` 增加高度（flexGrow）；``-1`` 缩减高度
            （flexShrink）。
        clamp_min: 缩减钳制下限（flexShrink 每子至少保留 1 行传 ``1``；
            flexGrow 传 None 不钳制）。
    """
    weights = [weight_fn(child) for child in children]
    total_weight = sum(weights)
    if total_weight <= 0 or total_extra <= 0:
        return
    per, extra_shares = _compute_weight_shares(weights, total_extra)
    cursor = inner_y
    remaining = total_extra  # ★ P3-9：剩余分配量跟踪（delta 钳制上限基准）
    for i, child in enumerate(children):
        cb = child.layout_box
        if weights[i] > 0:
            delta = per * weights[i] + extra_shares[i]
            if delta > 0:
                if direction > 0:
                    # ★ P3-9 修复（review 方向）：``cb.h += delta`` 无上限
                    #   钳制——增加 ``delta = min(delta, remaining)`` 防御性
                    #   钳制。正常路径 delta 总和恰为 total_extra（per + 余数
                    #   分配已收敛，见上），钳制不影响结果；仅防御未来权重
                    #   解析/余数分配改动引入超发（保证 flexGrow 不会把子节点
                    #   高度撑过总余数）。flexShrink 方向不钳制（clamp_min
                    #   下限已约束单子缩减量，remaining 语义不同）。
                    delta = min(delta, remaining)
                    cb.h += delta
                    remaining -= delta
                else:
                    # 每子至少保留 1 行（钳制 ≥1）
                    cb.h = max(clamp_min if clamp_min is not None else 1, cb.h - delta)
        cb.y = cursor
        child.layout_box = cb
        cursor += cb.h + margin


def _reflow_row_justify(
    children: list[Fiber],
    justify: str,
    start_x: int,
    margin: int,
    extra: int,
) -> None:
    """row justifyContent 重排子节点 x（space-between/space-around/space-evenly）。

    方向1（完善 flexbox）：横向主轴剩余宽度分布——与 column justifyContent
    （纵向，已实现）对称。三种模式均从 ``start_x`` 起重排 x（忽略 grow/align
    已产生的偏移；调用点在 row flexGrow 之后，grow 消费剩余则 extra≈0 不触发）：

      - ``space-between``：首子靠左、末子靠右，中间等间隔（gaps = n-1）；
      - ``space-evenly``：含边缘等间隔（slots = n+1）；
      - ``space-around``：每子两侧等半间隔（边缘半间隔、中间整间隔，2n 单位）。

    余数（extra % slots）逐个加到前若干个间隔上（视觉差 ≤1 列，可接受）。

    ★ BUG-15（review 方向）：x 重排后整棵子树平移（``_place_child_x`` 经
    ``_translate_subtree_x``）——修复前直接 ``cb.x = cx`` 仅改直接子节点，
    嵌套容器内后代 x 陈旧 → 文本与边框错位。

    Args:
        children: 直接 host 子节点（已测量，layout_box 非 None）。
        justify: space-between / space-around / space-evenly。
        start_x: 内边距后的起始 x。
        margin: 子节点间距（每子累计）。
        extra: 待分配的剩余宽度（>0 才有意义）。
    """

    def _place_child_x(child: Fiber, cx: int) -> None:
        """放置子节点到目标 x 并平移整棵子树（后代随动）。"""
        cb = child.layout_box
        delta = cx - cb.x
        if delta:
            _translate_subtree_x(child, delta)
        else:
            cb.x = cx
            child.layout_box = cb

    n = len(children)
    if n == 0:
        return
    if justify == "space-between":
        # ★ P3-10 说明（review 方向）：n==1 时 gaps=0（无间隔）——CSS flexbox
        #   space-between 语义：单子节点时无间隔可分配（子节点贴 start_x，
        #   不居中/不偏移；``per/rem`` 经 ``if gaps else 0`` 兜底避免除零）。
        gaps = n - 1
        per = extra // gaps if gaps else 0
        rem = extra % gaps if gaps else 0
        cx = start_x
        for i, child in enumerate(children):
            cb = child.layout_box
            _place_child_x(child, cx)
            cx += cb.w
            if i < gaps:
                cx += margin + per + (1 if i < rem else 0)
    elif justify == "space-evenly":
        slots = n + 1
        per = extra // slots
        rem = extra % slots
        gaps = [per] * slots
        for i in range(rem):
            gaps[i] += 1
        cx = start_x + gaps[0]
        for i, child in enumerate(children):
            cb = child.layout_box
            _place_child_x(child, cx)
            cx += cb.w + margin + gaps[i + 1]
    else:  # space-around：2n 半间隔（边缘半间隔、中间整间隔）
        half_units = 2 * n
        per = extra // half_units
        rem = extra % half_units
        gaps = [per if i in (0, n) else per * 2 for i in range(n + 1)]
        for i in range(rem):
            gaps[i % (n + 1)] += 1
        cx = start_x + gaps[0]
        for i, child in enumerate(children):
            cb = child.layout_box
            _place_child_x(child, cx)
            cx += cb.w + margin + gaps[i + 1]


__all__ = ["_distribute_extra", "_compute_weight_shares", "_reflow_row_justify"]
