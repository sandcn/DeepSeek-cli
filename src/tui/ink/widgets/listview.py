"""listview — React Ink 风格虚拟滚动列表（ListView）。

大列表渲染优化：只渲染可见窗口内的项（``height`` 视口 + 滚动 offset）——
渲染行数 = O(视口)，与 items 总数无关。复用 Column 布局 + 显式高度裁剪
（超出视口的项不创建 Element，避免大列表每帧全量布局）。

功能：
  - up/down 移动光标（自动滚出视口：光标越过视口边界时滚动 offset）；
  - home/end 跳到首/末项；enter 触发 ``onSelect(item, index)``；
  - ``initialIndex`` 初始光标；items 变化时光标/offset 钳制。

★ 全面控件化（2026-08-16 方案B）：控件扩展支持 TraceView 台账——
新增 ``cursor``（受控光标）、``onNavigate``（光标变化回调）、
``page_up/page_down``（翻页）、``g/G``（首末，与 home/end 等价）、
``renderItem(item, index, isSelected)`` 三参签名（选中态注入）、
items 中 ``None`` 项为不可选分隔行（导航自动跳过，不触发 onSelect）。
未传新 props 时行为与旧版完全一致（零回归）。

依赖约束：仅依赖 element / output / core.style / hooks / widgets.layout
（Layer 0/1），无父包依赖。
"""

from __future__ import annotations

import logging

from src.tui.core.style import Style
from ..element import TEXT, Element, h
from ..hooks import use_state, use_input, use_ref
from ..widgets.layout import Column
# ★ 公共纯辅助收敛（2026-08-05 架构优化）：_clamp_index 原本地定义——收敛
#   至 _widget_common 单一真源。
from ._widget_common import _clamp_index, _call

_logger = logging.getLogger(__name__)

__all__ = ["ListView"]


def _is_selectable(item) -> bool:
    """项是否可选（None 为不可选分隔行——TraceView 轮次分隔语义）。"""
    return item is not None


def _build_rows(items, render_item, offset_shown: int, cursor_shown: int,
                viewport_h: int, highlight_style) -> list:
    """构建视口内可见行元素（模块级纯辅助，P3 review 2026-08-19 提取）。

    仅渲染 ``[offset_shown, offset_shown+viewport_h)`` 内的项（虚拟化）；
    选中行（``i == cursor_shown``）在返回元素无 style/styled 时注入
    highlightStyle（与旧版内联实现逐行一致）。

    降级（P2-9 / P2 review 2026-08-19）：renderItem 抛异常 → warning 日志 +
    占位文本（str(item)）；返回 None → 空 TEXT（不渲染字面 "None"）。
    """
    rows: list = []
    total = len(items)
    for i in range(offset_shown, min(total, offset_shown + viewport_h)):
        item = items[i]
        is_sel = i == cursor_shown
        try:
            child = render_item(item, i, is_sel)
        except Exception:
            _logger.warning("ListView renderItem 异常，降级为占位文本", exc_info=True)
            child = h(TEXT, {"children": str(item), "height": 1})
        if isinstance(child, Element):
            cp = dict(child.props)
            if is_sel and "style" not in cp and "styled" not in cp:
                cp["style"] = highlight_style
            cp.setdefault("key", f"lv-{i}")
            rows.append(Element(child.type, cp, child.children))
        elif child is None:
            rows.append(h(TEXT, {
                "children": "",
                "style": highlight_style if is_sel else None,
                "key": f"lv-{i}", "height": 1,
            }))
        else:
            rows.append(h(TEXT, {
                "children": str(child),
                "style": highlight_style if is_sel else None,
                "key": f"lv-{i}", "height": 1,
            }))
    return rows


def ListView(props: dict) -> Element:
    """React Ink 风格虚拟滚动列表控件（``ink-listview`` 对齐）。

    Props:
        items: 列表项（任意类型；经 ``renderItem`` 渲染；None 项为不可选
            分隔行——导航自动跳过，不参与选择）。
        height: 可见行数（视口高度，默认 10）。
        renderItem: ``(item, index, isSelected) -> Element``——项渲染函数
            （缺省 ``lambda item, i: h(TEXT, {"children": str(item), "height": 1})``；
            第三参 isSelected 提供选中态，选中行注入 highlightStyle 仅在
            返回元素无 style/styled 时发生——与旧版一致）。
        focus: 是否参与输入路由（默认 True）。
        initialIndex: 初始光标下标（默认 0）。仅**首帧渲染**生效（use_state
            初始值语义）；首帧 items 为空（异步候选未达）时固化 0，后续
            到达的 initialIndex 静默失效——异步 items 场景请用受控 cursor。
        cursor: 受控光标下标（提供时渲染期用该值——外部控制；导航仍更新
            内部 state 并经 onNavigate 回调同步外部；None 时用内部 state）。
        onNavigate: ``(index) -> None``——光标变化回调（导航后触发；
            None 时忽略）。
        onSelect: ``(item, index) -> None``——Enter 选择回调（不可选项不
            触发）。
        highlightStyle: 光标行样式（默认 ``Style(fg=6)`` cyan）。

    行为（与常见 React 列表选择控件对齐）：
      - arrow_up/arrow_down（跳过不可选项）移动光标（越过视口边界自动滚动）；
      - page_up/page_down 翻页（跳过不可选项）；home/end/g/G 跳首/末；
      - enter 触发 ``onSelect(items[cursor], cursor)``。

    Returns:
        Column 元素（高度 = ``height`` 视口，仅渲染可见窗口内的项）。
    """
    raw_items = props.get("items")
    # ★ 健壮性（渲染错误防御）：items 不可迭代（None/标量/对象）时回退空列表
    #   ——修复前 ``list(props.get("items", []) or [])`` 对不可迭代的 items
    #   （如 float/bool）抛 TypeError，ListView 渲染崩溃。
    # ★ 性能（O(N²) 优化）：items 已为 list 时直接引用（内部只读遍历）——
    #   修复前 ``list(raw_items)`` 每帧复制整个 items（TraceView 台账大列表
    #   下每帧 O(N) 复制，随渲染帧数累积）；生成器/可迭代仍 list() 化。
    if raw_items is None:
        items = []
    elif isinstance(raw_items, list):
        items = raw_items
    elif hasattr(raw_items, "__iter__") and not isinstance(raw_items, (str, bytes)):
        items = list(raw_items)
    else:
        items = []
    try:
        viewport_h = max(1, int(props.get("height", 10)))
    except (TypeError, ValueError, OverflowError):
        viewport_h = 10
    render_item = props.get("renderItem")
    if render_item is None:
        # ★ P3（review）：默认渲染器对 None 分隔行渲染空文本（模块语义
        #   「None=不可选分隔行」），而非字面 "None"。
        render_item = lambda item, i, is_sel=None: h(TEXT, {
            "children": "" if item is None else str(item), "height": 1,
        })
    on_select = props.get("onSelect")
    on_navigate = props.get("onNavigate")
    focus = bool(props.get("focus", True))
    # ★ P3（review）：highlightStyle 改 ``is not None`` 判断——修复前 ``or``
    #   把显式空 Style()（falsy）当默认替换。
    highlight_style_prop = props.get("highlightStyle")
    highlight_style = highlight_style_prop if highlight_style_prop is not None else Style(fg=6)
    try:
        initial_index = max(0, int(props.get("initialIndex", 0)))
    except (TypeError, ValueError, OverflowError):
        initial_index = 0
    # ★ 受控光标（方案B）：cursor prop 提供时渲染期用该值（外部控制——
    #   TraceView 经 model.trace_selected 受控）；None 时用内部 state。
    cursor_prop = props.get("cursor")
    controlled = cursor_prop is not None
    if controlled:
        try:
            cursor_prop = int(cursor_prop)
        except (TypeError, ValueError, OverflowError):
            cursor_prop = None
            controlled = False
    total = len(items)

    cursor, set_cursor = use_state(_clamp_index(initial_index, total))
    offset, set_offset = use_state(0)
    # ★ ref 镜像（同批连续按键修复）：handler 读 ref 而非闭包 state。
    cursor_ref = use_ref(cursor)
    offset_ref = use_ref(offset)
    if controlled:
        # ★ 修复（受控 ref 基准，2026-08-19「轨迹 Trace 按上键不移动」）：
        #   受控模式渲染期基准**无条件统一为外部受控值**（cursor_prop）——
        #   修复前无条件 ``cursor_ref.current = cursor``（内部 state）覆盖：
        #   尾部跟随场景（TraceView ``trace_selected=-1`` → cursor prop=
        #   末行）内部 state 恒为初始 0（无人导航、set_cursor 从未提交），
        #   每帧渲染把 ref 拉回首行 → handler 基准=首行 → 按上键无处可移
        #   返回 False → 事件被 use_fullscreen 模态吞掉（用户看到按上键
        #   完全不动）。
        #   语义（P3 review 2026-08-18 同批连续导航 + 2026-08-19 修正）：
        #   渲染后基准=受控值（外部经 onNavigate 写回或直接改值，下一帧
        #   生效）；**批内**（同输入批、无中间渲染）基准=``_move`` 推进值
        #   ——连续按键沿推进值计算，不回退受控旧值。
        cursor_ref.current = cursor_prop
    else:
        cursor_ref.current = cursor
    offset_ref.current = offset

    def _move(new: int, base: int) -> bool:
        """内部移动光标（跳过不可选项）；返回是否实际移动。

        Args:
            new: 目标下标（调用方已确保可选）。
            base: 移动前光标基准（``cursor_ref.current`` 的钳制值——受控/
                非受控统一，不区分模式）。
        """
        if new == base:
            return False
        cursor_ref.current = new
        set_cursor(new)
        if on_navigate is not None:
            _call(on_navigate, new)
        return True

    def _step(delta: int, page: bool = False, base: int | None = None) -> bool:
        """从基准光标沿 delta 方向移动到下一可选（可选项）；不可达返回 False。

        ★ P3（review 2026-08-19，翻页边缘钳制）：翻页（page=True）目标越过
        列表边缘时**钳制到边缘最近可选项**（距末项 2 行按 PgDn → 跳到末项）
        ——与 home/end 的跳边缘语义一致；修复前整体失败返回 False（事件
        放行被模态吞掉，用户看到「近边缘翻页完全不动」）。单步（page=False）
        边缘不可达仍返回 False（上键首项/下键末项放行语义保持）。
        """
        cur = _clamp_index(base if base is not None else cursor_ref.current, total)
        if delta > 0:
            step = max(1, page * viewport_h)
            i = cur + step
            while i < total and not _is_selectable(items[i]):
                i += 1
            if i >= total:
                if not page:
                    return False
                # 翻页越界 → 钳制到最后一个可选项（仍不可达=已在边缘 → False）
                i = total - 1
                while i >= 0 and not _is_selectable(items[i]):
                    i -= 1
                if i < 0 or i <= cur:
                    return False
            # 翻页时若越过一个不可选区落在可选项上即可（跳过多项）
            return _move(i, cur)
        else:
            step = max(1, page * viewport_h)
            i = cur - step
            while i >= 0 and not _is_selectable(items[i]):
                i -= 1
            if i < 0:
                if not page:
                    return False
                # 翻页越界 → 钳制到第一个可选项
                i = 0
                while i < total and not _is_selectable(items[i]):
                    i += 1
                if i >= total or i >= cur:
                    return False
            return _move(i, cur)

    def _jump(to_end: bool, base: int | None = None) -> bool:
        """跳转到首/末（to_end 二值语义；跳过不可选项找最近可选项）。"""
        if total == 0:
            return False
        if not to_end:
            i = 0
            while i < total and not _is_selectable(items[i]):
                i += 1
            if i >= total:
                return False
            return _move(i, base if base is not None else cursor_ref.current)
        i = total - 1
        while i >= 0 and not _is_selectable(items[i]):
            i -= 1
        if i < 0:
            return False
        return _move(i, base if base is not None else cursor_ref.current)

    def _handle(event) -> bool:
        if not focus or total == 0:
            return False
        # ★ 受控/非受控统一基准：``cursor_ref.current``（P3 review 2026-08-18）
        #   ——受控 prop 变化经渲染期同步块写入 ref；同批连续导航 ref 保持
        #   ``_move`` 推进值（修复前受控模式恒读 prop，同批第二次导航从旧
        #   基准计算净移动 1 行）。
        base_cur = cursor_ref.current
        cur = _clamp_index(base_cur, total)
        cur_offset = offset_ref.current
        moved = False
        if event.kind == "arrow_up":
            # ★ P3（review）：视口边界按键空转——已在首项时按上键不移动却
            #   set_state + 消费事件。移动无效返回 False（不消费）。
            moved = _step(-1, base=base_cur)
        elif event.kind == "arrow_down":
            moved = _step(1, base=base_cur)
        elif event.kind == "page_up":
            moved = _step(-1, page=True, base=base_cur)
        elif event.kind == "page_down":
            moved = _step(1, page=True, base=base_cur)
        elif event.kind == "home":
            moved = _jump(False, base_cur)
        elif event.kind == "end":
            moved = _jump(True, base_cur)
        elif event.kind == "char" and event.char in ("g", "G"):
            # vim 风格：g 首 / G 末（TraceView 台账既有语义）
            moved = _jump(event.char == "G", base_cur)
        elif event.kind == "char" and event.char in ("j", "J", "k", "K"):
            # vim 风格：j/J 下、k/K 上（与 SelectInput ``_nav_for_char``
            # 大小写等效语义一致——TraceView 台账 / TraceToolsView 工具
            # 列表 vim 导航，2026-08-19 用户需求「像 vim 一样」）。
            # 多字符（粘贴流）整体不匹配（单字符判定），放行——与
            # g/G 处理一致。
            moved = _step(1 if event.char in ("j", "J") else -1, base=base_cur)
        elif event.kind == "enter":
            # ★ 方案B：onSelect 未提供时 enter 放行（返回 False 不消费——
            #   TraceView 台账 Enter 提交消息的放行语义）；提供时消费并回调。
            if _is_selectable(items[cur]) and on_select is not None:
                # ★ P3（review 2026-08-19）：回调经 ``_call`` 统一（与
                #   on_navigate 同一异常处理路径），warning 级日志可观测。
                _call(on_select, items[cur], cur)
                return True
            if on_select is None:
                return False
            return True
        else:
            return False
        if not moved:
            return False
        # 光标移出视口 → 滚动 offset（保持光标可见）
        new_cur = cursor_ref.current
        if new_cur < cur_offset:
            cur_offset = new_cur
        elif new_cur >= cur_offset + viewport_h:
            cur_offset = new_cur - viewport_h + 1
        offset_ref.current = cur_offset
        set_offset(cur_offset)
        return True

    use_input(_handle, focus)

    # 渲染期钳制（items 收缩后光标/offset 越界防护；受控模式用 cursor_prop）
    if controlled:
        cursor_shown = _clamp_index(cursor_prop, total)
    else:
        cursor_shown = _clamp_index(cursor, total)
    max_offset = max(0, total - viewport_h)
    offset_shown = min(offset, max_offset) if total > viewport_h else 0
    if cursor_shown < offset_shown:
        offset_shown = cursor_shown
    if cursor_shown >= offset_shown + viewport_h and total > viewport_h:
        offset_shown = max(0, cursor_shown - viewport_h + 1)
    # 可见行构建（P3 review 2026-08-19 提取为模块级 _build_rows 纯辅助）
    rows = _build_rows(items, render_item, offset_shown, cursor_shown,
                       viewport_h, highlight_style)
    # ★ 标准布局：Column 显式 height 视口裁剪（超出部分不渲染——虚拟化）
    return h(Column, {"height": viewport_h, "width": props.get("width")}, rows)
