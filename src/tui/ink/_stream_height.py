"""流式期间文档高度锁定 — 防止内容因瞬时高度变化整体跳动。

背景（2026-10-23 用户报障「上屏流式输出时会有一定机率会闪 / 内容整体
跳动一行后恢复」）：

流式期间文档高度会因**合法的瞬时内容变化**而变短，例如：

  - 工具参数解析进度行（``~ 工具名 …``）在参数接收完成时被删除；
  - 未闭合 Markdown 预览重排（段落被识别为列表/标题/表格时行数可能减少
    1~数行，见 ``AnsiStreamRenderer`` 的预览路径）；
  - 工具卡/子代理卡片内容重排。

本模块内容器框架没有视口（文档自底向上贴屏幕底部），文档变短时渲染器
会切换映射并重写可见区，视觉上就是「内容整体上移/下移一行，下一帧再
回来」。高度锁定把**流式期间的最大文档高度**作为高水位：文档暂时变短时
在**下屏（状态栏/输入区）之前**补空行，把高度补回高水位，下屏位置与
上方内容都不动；等本轮流式结束（``status_active`` 变假）再释放锁定，
高度自然收缩一次。

**严格语义（2026-10-23 用户确认）**：流式期间**行数只增不减**——只要处于
活跃期，任何高度缩短都补白到高水位，不设「小回退容忍上限」（删除行 /
可见区重映射就是闪动的根因）。流式结束（``active=False``）立即释放锁定，
文档允许一次性缩回真实高度。

边界：
  - 只锁「文档总高度」，不改变任何内容行；
  - 浮窗 / 全屏视图 / 模态底部视图 / 未知屏幕高度时**不锁定**（这些形态
    的文档区域语义不同，补白会破坏浮窗锚点与模态布局）。
"""

from __future__ import annotations

from .output import Frame, Line


class StreamHeightLock:
    """流式高度锁定器（纯状态机，无 IO）。

    使用方式（每帧一次，仅在实际**重建帧**时调用）::

        pad = lock.update(active=True, height=len(frame.lines),
                          bottom_y=bottom_area_y)
        if pad:
            frame = pad_frame(frame, bottom_y, pad)

    ``update`` 返回本帧需要补白的行数（0 表示不补）。高水位只在
    ``active=True`` 期间维护；``active=False``、``disabled=True`` 或
    ``reset()`` 时归零（下一轮流式从新的高度重新开始）。

    Attributes:
        floor: 当前高水位（文档总行数）；0 表示未锁定。
        locked: 是否处于锁定（高水位有效）状态。
    """

    __slots__ = ("_floor", "_locked")

    def __init__(self) -> None:
        self._floor = 0
        self._locked = False

    @property
    def floor(self) -> int:
        """当前高水位行数（0 = 未锁定）。"""
        return self._floor

    @property
    def locked(self) -> bool:
        """是否处于锁定状态。"""
        return self._locked

    def reset(self) -> None:
        """释放锁定（下一帧起不再补白）。"""
        self._floor = 0
        self._locked = False

    def update(
        self,
        *,
        active: bool,
        height: int,
        bottom_y: int,
        disabled: bool = False,
    ) -> int:
        """更新锁定状态并返回本帧补白行数。

        Args:
            active: 当前是否处于流式/工具执行活跃期。False 时释放锁定。
            height: 本帧**原始**文档高度（行数）。
            bottom_y: 下屏（状态栏/输入区）在文档中的起始行号（0-based）。
                补白插在该行之前，下屏因此固定在可见区底部。
            disabled: 当前形态不支持锁定时为 True（浮窗/全屏/模态/未知
                屏幕高度）——释放锁定且不补白。

        Returns:
            需要补白的行数（0 = 不补白）。
        """
        if disabled or not active or height <= 0 or bottom_y <= 0:
            self.reset()
            return 0
        if not self._locked:
            self._locked = True
            self._floor = int(height)
            return 0
        height = int(height)
        if height >= self._floor:
            # 内容增长/持平：高水位跟随（不补白）。
            self._floor = height
            return 0
        # 严格只增不减：任何缩短都补白到高水位（不设容忍上限——删除行
        # 会触发可见区重映射，正是用户看到的「闪」）。
        return self._floor - height


def find_bottom_area(root):
    """在 host 树中查找下屏（``dataFloatAnchor="bottom_area"``）fiber。

    下屏是 App 根组件中包裹状态栏 + 输入区的无边框 Box——高度锁定的补白
    必须插在它之前，才能保证下屏贴可见区底部、上方内容不移动。

    Args:
        root: 根 fiber（ROOT 或 APP host）。

    Returns:
        找到的 host fiber；未找到返回 None。
    """
    if root is None:
        return None
    stack = [root]
    seen: set = set()
    while stack:
        fiber = stack.pop()
        if fiber is None or id(fiber) in seen:
            continue
        seen.add(id(fiber))
        if getattr(fiber, "deleted", False):
            continue
        props = getattr(fiber, "props", None)
        # ★ 只返回 **host** fiber：``Box`` 是函数组件，其 props 同样带
        #   ``dataFloatAnchor``，但函数 fiber 没有 layout_box；真正带布局盒的
        #   是它返回的 host ``"box"``（props 透传）。误取函数 fiber 会让
        #   ``layout_box`` 为 None → 补白锚点丢失（高度锁定静默失效）。
        if (
            isinstance(props, dict)
            and props.get("dataFloatAnchor") == "bottom_area"
            and getattr(fiber, "is_host", False)
        ):
            return fiber
        # 深度优先：sibling 链整体压栈（保持遍历完整性），child 最后压栈。
        child = getattr(fiber, "child", None)
        if child is not None:
            stack.append(child)
        sibling = getattr(fiber, "sibling", None)
        while sibling is not None:
            stack.append(sibling)
            sibling = getattr(sibling, "sibling", None)
    return None


def shift_subtree_y(fiber, delta: int) -> None:
    """把 fiber 及其子树的所有 layout_box.y 下移 ``delta`` 行。

    补白插入在下屏之前后，下屏/输入区/光标等布局盒子必须同步下移，否则
    ``position_cursor``（读输入区 ``layout_box.y``）会把光标放到补白之前
    的位置。每帧布局（``layout_tree``）会重新生成 layout_box，本函数只
    修正**本帧**的坐标（只读消费于本帧之后的光标定位/浮窗锚点）。

    Args:
        fiber: 子树根 fiber（下屏 Box）。
        delta: 下移行数（<=0 时无操作）。
    """
    if fiber is None or delta <= 0:
        return
    stack = [fiber]
    seen: set = set()
    while stack:
        node = stack.pop()
        if node is None or id(node) in seen:
            continue
        seen.add(id(node))
        box = getattr(node, "layout_box", None)
        if box is not None:
            box.y = int(box.y) + int(delta)
        child = getattr(node, "child", None)
        while child is not None:
            stack.append(child)
            child = getattr(child, "sibling", None)


def pad_frame(frame: Frame, bottom_y: int, pad: int) -> Frame:
    """返回在 ``bottom_y`` 之前插入 ``pad`` 个空行的新 Frame。

    不原地修改传入 Frame/行列表（``Frame`` 契约：构造后 lines 只读）：
    新建 lines / base_lines 列表，复用原 Line 对象与稳定前缀元数据。

    稳定前缀（committed 内容）位于 ``bottom_y`` 之前，补白不改变其偏移；
    若检测到前缀区间跨过补白位置（异常布局），保守放弃稳定前缀标记，
    让行级 diff 走逐行比较（正确性优先）。

    Args:
        frame: 原始帧。
        bottom_y: 补白插入位置（下屏起始行，0-based）。
        pad: 补白行数（<=0 时原样返回）。

    Returns:
        补白后的新 Frame（或原 Frame）。
    """
    if pad <= 0:
        return frame
    y = max(0, min(int(bottom_y), len(frame.lines)))
    blanks = [Line() for _ in range(int(pad))]
    lines = list(frame.lines[:y]) + blanks + list(frame.lines[y:])
    base = frame.base_lines
    if base is not None:
        base = list(base[:y]) + [Line() for _ in range(int(pad))] + list(base[y:])
    prefix = frame._stable_prefix
    offset = frame._stable_prefix_offset
    plen = frame._stable_prefix_len
    if prefix is not None and offset + plen > y:
        # 前缀跨越补白点（正常布局不会发生）：放弃前缀复用标记。
        prefix, offset, plen = None, 0, 0
    return Frame(
        lines,
        stable_prefix=prefix,
        stable_prefix_offset=offset,
        stable_prefix_len=plen,
        base_lines=base,
    )


__all__ = [
    "StreamHeightLock",
    "find_bottom_area",
    "shift_subtree_y",
    "pad_frame",
]
