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

坐标与可视区（2026-10-21 修复）：
  浮窗坐标是**文档坐标**，而渲染器只能重写屏幕可见行（``renderer`` 把首
  差异行钳到 ``_screen_offset``）。绝对坐标 / ``doc_*`` 锚点的窗口在长文档
  里可能落在可视区上方 → 永远看不见。``apply_floating_layout`` 因此接受
  ``viewport_height``：给出视口高度时把窗口**钳进可视区**（只会向下移动，
  不会把底部框上方的循环浮窗顶出原位）。未给视口高度（headless 整帧渲染 /
  公共 ``renderToString``）时不钳制，按文档坐标输出。

空 id / 重复 id：
  ``dataFloatWindow`` 用 **``is not None`` 判定**（不是真值判定）——空串
  也是合法标记，保证「标记存在」与「是否定位」口径一致；重复/空 id 的
  过滤在 ``app.floating.floating_layer_elements`` 完成。
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


def _float_cache(root_fiber) -> dict:
    """浮窗查找缓存（挂在 root fiber 上，跨帧复用；结构损坏时重建）。

    Returns:
        ``{"input": fiber|缺失, "anchors": {anchor: fiber}}``。
    """
    cache = getattr(root_fiber, "_float_anchor_cache", None)
    if not isinstance(cache, dict):
        cache = {}
        root_fiber._float_anchor_cache = cache
    if not isinstance(cache.get("anchors"), dict):
        cache["anchors"] = {}
    return cache


def _cached(cache: dict, key: str, check) -> object | None:
    """取缓存 fiber 并校验（未删除 + ``check(fiber)`` 为真），否则丢弃。"""
    fiber = cache.get(key)
    if fiber is None:
        return None
    if getattr(fiber, "deleted", False) or not getattr(fiber, "is_host", False):
        cache.pop(key, None)
        return None
    try:
        if not check(fiber):
            cache.pop(key, None)
            return None
    except Exception:  # pragma: no cover - 属性异常按失效处理
        cache.pop(key, None)
        return None
    return fiber


def _resolve_row(value, *, doc_height: int, input_top: int,
                 input_bottom: int, bottom_area_top: int = -1) -> int | None:
    """解析行锚点 → 文档行号；无法解析返回 None（调用方回退，记 debug）。

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
    parsed = _as_int(key)
    if parsed is None:
        # ★ 可诊断性（2026-10-21）：非法锚点（如 ``3.7`` / 拼写错误）此前
        #   静默回退到调用方默认值，浮窗"莫名出现在文档底部"难以定位。
        _logger.debug("浮窗行锚点无法解析（回退默认锚点）: %r", value)
    return parsed


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
    if parsed is None:
        # ★ 可诊断性（2026-10-21）：非法列锚点（拼写错误等）此前静默右对齐。
        _logger.debug("浮窗列锚点无法解析（回退右对齐）: %r", value)
        return width - win_w
    return parsed


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
            # ★ is not None（不是真值判定）：空串 id 也是合法浮窗标记——
            #   修复前 ``dataFloatWindow=""`` 既不被收集也不被隐藏，导致它
            #   既不定位也不进合成层（停在左上角且被前缀拼接截断）。
            and f.props.get(FLOAT_PROP) is not None
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
    """查找带 ``dataFloatAnchor == anchor`` 标记的 host fiber（首个，带缓存）。

    输入区最上方的分隔线（CPU/MEM 行）由 ``InputArea`` 打上
    ``input_separator`` 标记——浮窗 ``input_top`` 锚点据此精确定位到分隔线
    （输入区容器首行可能是不可见的补全弹窗空行，直接用容器 y 会差 1 行）。

    ★ 缓存（2026-10-21）：锚点 fiber 跨帧稳定（调和器按 key 复用），每帧
    重新全树 DFS 纯属浪费（浮窗开启时每帧 2 次）。缓存挂在 root fiber 上，
    命中前校验 ``deleted`` 与标记 props；未命中（含首次 / 标记视图切换后）
    才 DFS。与 ``_find_committed_chat`` 的缓存契约一致。
    """
    if root_fiber is None:
        return None
    cache = _float_cache(root_fiber)
    anchors = cache["anchors"]
    hit = _cached(
        anchors, anchor,
        lambda f: f.props.get("dataFloatAnchor") == anchor,
    )
    if hit is not None:
        return hit
    pushed: set = set()
    stack = [root_fiber]
    pushed.add(id(root_fiber))
    while stack:
        f = stack.pop()
        if getattr(f, "deleted", False):
            continue
        if f.is_host and f.props.get("dataFloatAnchor") == anchor:
            anchors[anchor] = f
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


def _find_input_fiber(root_fiber):
    """输入区 fiber（带缓存，语义同 ``_cursor.find_input_fiber``）。"""
    if root_fiber is None:
        return None
    cache = _float_cache(root_fiber)
    hit = _cached(
        cache, "input",
        lambda f: f.type == "input-area" or bool(f.props.get("dataInputArea")),
    )
    if hit is not None:
        return hit
    fiber = find_input_fiber(root_fiber)
    if fiber is not None:
        cache["input"] = fiber
    return fiber


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
    fiber = _find_input_fiber(root_fiber)  # 带缓存（每帧免全树 DFS）
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


def apply_floating_layout(root_fiber, width: int, viewport_height: int = 0) -> int:
    """布局后修正全部浮窗坐标（就地平移子树）。返回定位的浮窗数量。

    **幂等**：目标坐标只由 props + 文档几何决定（不读当前 ``layout_box``），
    重复调用只是再算一次同样的 ``(x, y)`` → 位移为 0。因此所有帧构建入口
    （``components.render_frame`` 内部统一调用 + 直接调用）都能安全重复执行。

    只修改 ``layout_box`` 坐标（不重新测量、不改变文档高度）。浮窗行超出文档
    顶部 / 底部时由绘制阶段的行越界检查自然裁剪。

    **锚点缺失时的隐藏规则**：

      - ``bottom_area`` 锚点需要底部框（状态栏 + 输入区 / 模态底部视图）——
        整屏视图无底部框时窗口隐藏；
      - ``input_top``/``input_bottom``/默认 锚点需要输入区——模态底部视图 /
        整屏视图无输入区时窗口隐藏（避免贴到文档底部与弹窗重叠错位）；
      - 绝对坐标 / 文档锚点的窗口照常显示。

    隐藏的窗口本帧标记 ``_float_hidden``：整树绘制与合成层都跳过；锚点恢复
    （输入区/底部框重新渲染）后下一帧自动回到原位、原内容原样还原。

    Args:
        root_fiber: 组件树根（ROOT / host 根均可）。
        width: 文档宽度（终端列宽）。
        viewport_height: 终端可见高度（行）。> 0 时把窗口**钳进可视区**——
            渲染器只能重写可见行（``renderer`` 把首差异行钳到
            ``_screen_offset``），文档坐标落在可视区上方的窗口否则永不显示。
            0（默认，headless 整帧渲染）表示不钳制。

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
    # 可视区首行（文档坐标）：窗口不得高于它（否则写不到终端）。
    viewport_h = max(0, int(viewport_height or 0))
    view_min_y = 0
    if viewport_h > 0 and doc_height > viewport_h:
        view_min_y = doc_height - viewport_h
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
            box = fiber.layout_box
            if rect is None or box is None:
                # 无法解析（无布局盒 / 零尺寸）→ 隐藏。修复前直接 continue：
                # 窗口既不定位也不隐藏，停在上一次（或默认 0,0）位置。
                fiber._float_hidden = True
                continue
            x, y = rect
            win_h = max(1, int(box.h))
            # ★ 可视区钳制（2026-10-21）：只向下移动（不把底部锚定的窗口顶出
            #   原位）；窗口比视口还高时优先保证底部可见（顶行可被裁掉）。
            max_y = max(0, doc_height - win_h)
            if y > max_y:
                y = max_y
            if view_min_y and y < view_min_y:
                y = min(view_min_y, max_y)
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
