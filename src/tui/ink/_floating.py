"""浮窗定位 — 绝对定位浮窗的锚点解析与布局后坐标修正（ink 层）。

设计（用户需求 2026-10-21：「通用的浮动窗口」）：

  - 浮窗本质是组件树中的 ``position="absolute"`` 容器（``dataFloatWindow``
    标记），**不占文档高度**——正常流布局（``_measure``）把它分离，
    ``_layout_absolute_pass`` 先按默认锚点（left/top=0）放置；
  - 本模块在**整棵树布局完成后**（``reconciler.render`` 之后、
    ``components.render_frame`` 之前）读取浮窗自身的锚点 props，解析出
    目标矩形并把整棵浮窗子树平移到目标位置；
  - 浮窗可锚定「底部框最上方的分隔线」等语义行（``input_top`` 等），
    并以**底边为基准向上展开**（窗口底行 = 锚点行 + offset）；也支持
    绝对行列坐标——任意位置显示；
  - 浮窗仍是普通组件树：内部可用任意 ink 组件/hooks；关闭后组件从树中
    移除，下一帧 ``render_frame`` 重建**不含浮窗**的整帧，行级 diff 自动
    重写被覆盖的行 → 原内容原样还原（不破坏原内容、不残留）。
  - 多个浮窗按渲染顺序叠加（App 层按优先级排序：高优先级在后 → 后绘制
    覆盖先绘制），可叠在同一位置。

本模块**只依赖 ink 内部模块**（fiber / 布局变换 / 光标模块的输入区查找），
不依赖 ``src.tui.app``（避免 ink → app → ink 循环依赖）。

浮窗 props 约定（由 ``src/tui/app/floating.py`` 写入）：
  - ``dataFloatWindow``: 浮窗 id（字符串标记，收集浮窗用）
  - ``floatX``: 左边界列——int / ``"left"`` / ``"center"`` / ``"right"``
  - ``floatBottom``: 窗口**底行**锚点——int / ``"input_top"`` /
    ``"input_bottom"`` / ``"doc_bottom"`` / ``"doc_top"``
  - ``floatTop``: 窗口**顶行**锚点（与 ``floatBottom`` 同时给出时优先）
  - ``floatOffsetX`` / ``floatOffsetY``: 额外偏移（列 / 行）
"""

from __future__ import annotations

import logging

from ._cursor import find_input_fiber
from ._layout_transform import _translate_subtree_x, _translate_subtree_y
from ._layout_tree import _skip_function

_logger = logging.getLogger(__name__)

#: 浮窗标记 prop（App 层写入窗口 id）
FLOAT_PROP = "dataFloatWindow"

#: 行锚点取值（小写归一化后匹配）
_ANCHOR_INPUT_TOP = frozenset({"input_top", "input-top", "input"})
_ANCHOR_INPUT_BOTTOM = frozenset({"input_bottom", "input-bottom"})
_ANCHOR_DOC_BOTTOM = frozenset({"doc_bottom", "doc-bottom", "bottom"})
_ANCHOR_DOC_TOP = frozenset({"doc_top", "doc-top", "top"})
#: 底部框（状态栏 + 输入区 / 模态底部视图）顶部锚点——浮窗底行放在其上方
#: 一行时绝不遮挡底部框（2026-10-21 用户需求）。
_ANCHOR_BOTTOM_AREA = frozenset({
    "bottom_area", "bottom-area", "bottom_area_top", "bottom_area-top",
})

#: 列锚点取值
_X_LEFT = frozenset({"left", "start", "flex-start"})
_X_CENTER = frozenset({"center", "middle"})
_X_RIGHT = frozenset({"right", "end", "flex-end"})


def _as_int(value, default: int | None = None) -> int | None:
    """把 props 值转为 int（bool/None/异常值回退 default）。"""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _resolve_row(value, *, doc_height: int, input_top: int,
                 input_bottom: int, bottom_area_top: int = -1) -> int | None:
    """解析行锚点 → 文档行号；无法解析返回 None（调用方回退）。

    Args:
        value: int 行号或锚点名（``bottom_area`` / ``input_top`` /
            ``input_bottom`` / ``doc_bottom`` / ``doc_top``）。
        doc_height: 文档总行数。
        input_top: 输入区顶行（文档坐标）。
        input_bottom: 输入区底行（文档坐标，含）。
        bottom_area_top: 底部框（状态栏 + 输入区）顶行；-1 表示不存在。
    """
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if value is None:
        return None
    key = str(value).strip().lower()
    if key in _ANCHOR_BOTTOM_AREA:
        return bottom_area_top if bottom_area_top >= 0 else None
    if key in _ANCHOR_INPUT_TOP:
        return input_top
    if key in _ANCHOR_INPUT_BOTTOM:
        return input_bottom
    if key in _ANCHOR_DOC_BOTTOM:
        return max(0, doc_height - 1)
    if key in _ANCHOR_DOC_TOP:
        return 0
    return _as_int(key)


def _resolve_x(value, *, width: int, win_w: int) -> int:
    """解析列锚点 → 窗口左边界列（未钳制）。

    Args:
        value: int 列号或 ``left`` / ``center`` / ``right``。
        width: 终端/文档宽度。
        win_w: 浮窗宽度。
    """
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    key = str(value).strip().lower() if value is not None else "right"
    if key in _X_LEFT:
        return 0
    if key in _X_CENTER:
        return (width - win_w) // 2
    if key in _X_RIGHT:
        return width - win_w
    parsed = _as_int(key)
    return parsed if parsed is not None else width - win_w


def _collect_float_fibers(root_fiber, include_hidden: bool = False) -> list:
    """收集组件树中浮窗 host fiber（**绘制顺序**，即树中顺序）。

    显式栈迭代（与 ``_cursor.find_input_fiber`` 一致）；过滤已删除 fiber。
    浮窗数量少、树中顺序即绘制顺序（后者覆盖前者 = 高优先级在上）。

    Args:
        root_fiber: 组件树根。
        include_hidden: True 时包含本帧标记为隐藏的窗口（布局阶段用于复位
            ``_float_hidden``）；False（默认）只返回可绘制窗口（合成层用）。
    """
    if root_fiber is None:
        return []
    pushed: set = set()
    stack = [root_fiber]
    pushed.add(id(root_fiber))
    found: list = []
    while stack:
        f = stack.pop()
        if getattr(f, "deleted", False):
            continue
        if (
            f.is_host
            and f.props.get(FLOAT_PROP)
            and (include_hidden or not getattr(f, "_float_hidden", False))
        ):
            found.append(f)
        # sibling 链逆序压栈（恢复从左到右顺序），child 最后压入（先处理）
        sibs = []
        s = f.sibling
        while s is not None and id(s) not in pushed:
            pushed.add(id(s))
            sibs.append(s)
            s = s.sibling
        for i in range(len(sibs) - 1, -1, -1):
            stack.append(sibs[i])
        if f.child is not None and id(f.child) not in pushed:
            pushed.add(id(f.child))
            stack.append(f.child)
    return found


def _find_anchor_fiber(root_fiber, anchor: str):
    """查找带 ``dataFloatAnchor == anchor`` 标记的 host fiber（首个）。

    输入区最上方的分隔线（CPU/MEM 行）由 ``InputArea`` 打上
    ``input_separator`` 标记——浮窗 ``input_top`` 锚点据此精确定位到分隔线
    （输入区容器首行可能是不可见的补全弹窗空行，直接用容器 y 会差 1 行）。
    """
    if root_fiber is None:
        return None
    pushed: set = set()
    stack = [root_fiber]
    pushed.add(id(root_fiber))
    while stack:
        f = stack.pop()
        if getattr(f, "deleted", False):
            continue
        if f.is_host and f.props.get("dataFloatAnchor") == anchor:
            return f
        sibs = []
        s = f.sibling
        while s is not None and id(s) not in pushed:
            pushed.add(id(s))
            sibs.append(s)
            s = s.sibling
        for i in range(len(sibs) - 1, -1, -1):
            stack.append(sibs[i])
        if f.child is not None and id(f.child) not in pushed:
            pushed.add(id(f.child))
            stack.append(f.child)
    return None


def _document_height(root_fiber) -> int:
    """文档总行数（host 根布局盒高度；无布局时 0）。"""
    host_root = _skip_function(root_fiber) or root_fiber
    box = getattr(host_root, "layout_box", None)
    return max(0, int(getattr(box, "h", 0) or 0))


def _input_row_range(root_fiber) -> tuple[int, int]:
    """输入区文档行范围 ``(top, bottom)``；无输入区时回退文档底 ``(h-1, h-1)``。

    ``top`` 优先取输入区**最上方的分隔线**（``InputArea`` 标记
    ``dataFloatAnchor="input_separator"`` 的 CPU/MEM 分隔线行）——供通用
    ``input_top`` 锚点使用；标记缺失（旧组件树/测试桩）时回退输入区容器
    首行。``bottom`` 为输入区容器末行（模式行）。
    """
    fiber = find_input_fiber(root_fiber)
    box = getattr(fiber, "layout_box", None) if fiber is not None else None
    if box is None:
        return -1, -1
    container_top = int(getattr(box, "y", 0) or 0)
    height = max(1, int(getattr(box, "h", 0) or 0))
    container_bottom = container_top + height - 1
    top = container_top
    anchor = _find_anchor_fiber(root_fiber, "input_separator")
    anchor_box = getattr(anchor, "layout_box", None)
    if anchor_box is not None:
        top = int(getattr(anchor_box, "y", container_top) or container_top)
    return top, container_bottom


def _bottom_area_top(root_fiber) -> int:
    """底部框（状态栏 + 输入区 / 模态底部视图）顶部行；不存在返回 -1。

    App 给整个底部区 Column 打 ``dataFloatAnchor="bottom_area"`` 标记——
    浮窗 ``bottom="bottom_area"`` + ``offset_y=-1`` 时底行落在其上方一行，
    向上展开，**绝不覆盖底部框**（用户需求）。
    """
    anchor = _find_anchor_fiber(root_fiber, "bottom_area")
    box = getattr(anchor, "layout_box", None)
    if box is None:
        return -1
    return int(getattr(box, "y", -1) or -1)


def resolve_window_rect(fiber, *, width: int, doc_height: int,
                        input_top: int, input_bottom: int,
                        bottom_area_top: int = -1) -> tuple[int, int] | None:
    """按浮窗 props 解析目标左边界列 / 顶行（未钳制）。

    Returns:
        ``(x, y)``；窗口无布局盒或宽度为 0 时返回 None（不定位）。
    """
    box = getattr(fiber, "layout_box", None)
    if box is None or box.w <= 0 or box.h <= 0:
        return None
    props = fiber.props
    win_w = int(box.w)
    win_h = int(box.h)

    # ── 水平：左边界 + 偏移，再钳制到可见范围（窗口比屏幕宽时贴左）──
    x = _resolve_x(props.get("floatX"), width=width, win_w=win_w)
    x += _as_int(props.get("floatOffsetX"), 0) or 0
    max_x = max(0, width - win_w)
    x = max(0, min(x, max_x))

    # ── 垂直：顶行优先；否则底行锚点 + 向上展开 ──
    top_val = props.get("floatTop")
    if top_val is not None:
        row = _resolve_row(
            top_val, doc_height=doc_height,
            input_top=input_top, input_bottom=input_bottom,
            bottom_area_top=bottom_area_top,
        )
        if row is None:
            row = input_top
        y = row
    else:
        anchor = props.get("floatBottom")
        if anchor is None:
            anchor = "doc_bottom"
        row = _resolve_row(
            anchor, doc_height=doc_height,
            input_top=input_top, input_bottom=input_bottom,
            bottom_area_top=bottom_area_top,
        )
        if row is None:
            row = doc_height - 1
        # 底行锚点：窗口最后一行落在 row 上；向上展开
        y = row - win_h + 1
    y += _as_int(props.get("floatOffsetY"), 0) or 0
    return x, y


def _anchor_kind(props) -> str:
    """窗口锚点依赖类型：``"input"`` / ``"bottom_area"`` / ``"doc"``。

    锚点缺失时默认 ``input``（历史语义 ``input_top``）。
    """
    top = props.get("floatTop")
    anchor = top if top is not None else props.get("floatBottom")
    if anchor is None:
        return "input"
    key = str(anchor).strip().lower()
    if key in _ANCHOR_BOTTOM_AREA:
        return "bottom_area"
    if key in _ANCHOR_INPUT_TOP or key in _ANCHOR_INPUT_BOTTOM:
        return "input"
    return "doc"


def apply_floating_layout(root_fiber, width: int) -> int:
    """布局后修正全部浮窗坐标（就地平移子树）。返回定位的浮窗数量。

    由渲染帧在 ``reconciler.render``（内部完成布局）之后调用；只修改
    ``layout_box`` 坐标（不重新测量、不改变文档高度）。浮窗行超出文档顶部
    /底部时由 ``_paint`` 的行越界检查自然裁剪。

    **锚点缺失时的隐藏规则**：

      - ``bottom_area`` 锚点需要底部框（状态栏 + 输入区 / 模态底部视图）——
        整屏视图无底部框时窗口隐藏；
      - ``input_top``/``input_bottom``/默认 锚点需要输入区——模态底部视图 /
        整屏视图无输入区时窗口隐藏（避免贴到文档底部与弹窗重叠错位）；
      - 绝对坐标 / 文档锚点的窗口照常显示。

    隐藏的窗口本帧标记 ``_float_hidden``：整树绘制与合成层都跳过；锚点恢复
    （输入区/底部框重新渲染）后下一帧自动回到原位、原内容原样还原。

    异常安全：单个窗口解析/平移失败只记 debug，不影响其它窗口与主界面。
    """
    windows = _collect_float_fibers(root_fiber, include_hidden=True)
    if not windows:
        return 0
    width = max(1, int(width or 0))
    doc_height = _document_height(root_fiber)
    input_top, input_bottom = _input_row_range(root_fiber)
    has_input = input_top >= 0
    bottom_area_top = _bottom_area_top(root_fiber)
    has_bottom_area = bottom_area_top >= 0
    if not has_input:
        # 无输入区：文档锚点（doc_bottom/doc_top）/绝对坐标窗口仍可解析
        input_top = max(0, doc_height - 1)
        input_bottom = max(0, doc_height - 1)
    applied = 0
    for fiber in windows:
        try:
            # 每帧复位隐藏标记（上一帧隐藏的窗口在锚点恢复后重新显示）
            fiber._float_hidden = False
            kind = _anchor_kind(fiber.props)
            if kind == "input" and not has_input:
                # 输入区不存在（模态底部视图 / 整屏视图）→ 隐藏，避免贴底
                fiber._float_hidden = True
                continue
            if kind == "bottom_area" and not has_bottom_area:
                # 底部框不存在（整屏视图）→ 隐藏
                fiber._float_hidden = True
                continue
            rect = resolve_window_rect(
                fiber, width=width, doc_height=doc_height,
                input_top=input_top, input_bottom=input_bottom,
                bottom_area_top=bottom_area_top,
            )
            if rect is None:
                continue
            x, y = rect
            box = fiber.layout_box
            dx = int(x - box.x)
            dy = int(y - box.y)
            if dx:
                _translate_subtree_x(fiber, dx)
            if dy:
                _translate_subtree_y(fiber, dy)
            applied += 1
        except Exception:
            _logger.debug(
                "浮窗定位异常 id=%r", fiber.props.get(FLOAT_PROP), exc_info=True,
            )
    return applied


__all__ = [
    "FLOAT_PROP",
    "apply_floating_layout",
    "resolve_window_rect",
    "_collect_float_fibers",
]
