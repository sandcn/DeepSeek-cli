"""trace_tools_view — 工具列表详情视图（轨迹 Trace 工具列表 Enter 进入）。

用户需求（2026-08-17）：轨迹 Trace 中选中 **#0 工具列表** 记录按 Enter →
进入**新界面**（模态全屏视图 id ``"trace_tools"``）——左右布局：
  - 左栏「工具列表」：全部工具名（原名，注册顺序），↑↓/PgUp/PgDn/Home/End/
    g/G 上下选择（ListView 标准控件——受控光标 + 虚拟滚动 + 选中整行高亮）；
  - 右栏「参数检查器」：选中工具的参数 schema **树控件**展示
    （``build_tools_params_tree``——``参数 (N 项)`` 容器 + 每个参数的
    类型/描述/必需/默认值/枚举/约束叶子），树行渲染复用
    ``trace_view._tree_node_rows``（缩进 + 展开指示符，与轨迹工具调用
    参数树同语义）。

键盘：
  - ↑↓/PgUp/PgDn/Home/End/g/G：上下选择工具（ListView 消费，导航经
    ``onNavigate`` 写回 ``model.trace_tools_selected``——返回主轨迹再进入
    保持上次选择）；
  - Esc/Ctrl+H：返回主轨迹（``model.fullscreen = "trace"``——TraceView
    恢复；主轨迹再次 Esc/Ctrl+H 关闭整个轨迹视图）；
  - 其余按键（Enter/字符等）不消费——经 ``use_fullscreen`` 模态被 input
    router 吞掉（不落入输入缓冲，杜绝看不见的输入）。

数据源：``trace._tools_schema_list``（与 ``_tools_record`` 同源：
ToolRegistry 注册顺序，TTL 缓存；注册表异常/为空 → 空列表——左栏空态 +
右栏「无可用工具」占位，静默降级零成本）。依赖约束：仅依赖 app 同层
（trace/trace_view——均不依赖本模块，无循环依赖）与 ink 框架（Layer 0/1）。
"""

from __future__ import annotations

from src.tui._input_layout import _wrap_by_width
from src.tui.app.trace import _tools_schema_list, build_tools_params_tree
from src.tui.app.trace_view import _tree_node_rows, _viewport_rows
from src.tui.core.style import Style
from src.tui.ink import (
    TEXT, Column, Row, StyledRun, h, use_fullscreen, use_input, use_memo,
)
from src.tui.ink.helpers import truncate_runs
from src.tui.ink.widgets.listview import ListView

# ── 样式（对齐 trace_view 轨迹视图视觉：亮青标题/暗灰提示/浅蓝小节） ──
_S_TITLE = Style(fg=45, bold=True)        # 视图标题/检查器标题（亮青加粗）
_S_HINT = Style(fg=242)                    # 提示/占位（暗灰）
_S_SEP_ROW = Style(fg=238)                 # 分隔线（深灰）
_S_TEXT = Style(fg=252)                    # 工具名/树叶子文本（亮白）
_S_DIM = Style(fg=242)                     # 元信息/描述（暗灰）
_S_SEL_BG = Style(bg=237)                  # 选中行背景（静态 237，不呼吸）
_S_SEL_MARK = Style(fg=45, bold=True)      # 选中 ▶ 标记（亮青加粗）
_S_SECTION = Style(fg=110, bold=True)      # 参数小节标题（浅蓝加粗）
#: 右栏光标行背景（2026-08-19 用户需求：右边高亮当前行背景色）——与
#: trace_view 检查器同色 237（两视图视觉一致；vim cursorline 语义）
_S_INSP_BG = Style(bg=237)

#: 右栏内容行预算下限（标题 + 元信息 + 省略提示占用后至少保留的行数）
_INSPECTOR_MIN_CONTENT = 4
#: 右栏内容行全量生成上限（2026-08-19 vim 滚动查看——超限截断 + 「内容
#:   过长」提示行，与 trace_view ``_INSPECTOR_MAX_ROWS`` 同语义）
_INSPECTOR_MAX_ROWS = 2000


def _tool_row_runs(name: str, sel: bool, left_w: int) -> list:
    """工具名行 runs（选中行整行背景高亮 + ▶ 标记；宽截断）。

    Args:
        name: 工具名（原名）。
        sel: 是否选中。
        left_w: 左栏宽（>0 时截断；<=0 不截断防御）。
    """
    runs: list = []
    if sel:
        runs.append(StyledRun("\u25b6 ", _S_SEL_MARK))
    else:
        runs.append(StyledRun("  ", None))
    runs.append(StyledRun(name, _S_TEXT))
    if sel:
        runs = [StyledRun(r.text, (r.style or Style()).merge(_S_SEL_BG)) for r in runs]
    return truncate_runs(runs, left_w) if left_w > 0 else runs


def _tree_rows(nodes: list, right_w: int) -> list:
    """树节点列表 → StyledRun 行列表（前序；缩进 + 展开指示符）。

    复用 ``trace_view._tree_node_rows``（轨迹工具调用参数/返回值树同渲染
    管线：层级缩进 + ▾ 展开指示符 + 超宽截断）——右栏参数树与轨迹检查器
    树视觉一致。
    """
    out: list = []
    _tree_node_rows(nodes, max(1, right_w), out)
    return out


def _tools_inspector_content_rows(
    name: str, props_map: dict, required: list, description: str, right_w: int,
) -> list:
    """工具参数检查器**全量内容行**（正序；上限防御）——滚动查看数据源。

    ★ 2026-08-19（vim 面板浏览一致化）：与 trace_view 检查器同模式——
    内容行全量生成（描述 + 分割线 + ``▸ 参数`` 小节 + 参数树行），滚动
    窗口切片显示。返回元素为 ``list[StyledRun]``（分割线/小节标题/树行）
    或 ``str``（描述行）——由 ``_inspector_children`` 统一转 TEXT 元素。

    Args:
        name: 工具名（空串 = 空态——调用方提前返回占位，此处不处理）。
        props_map: 参数名 → 参数定义 dict。
        required: 必需参数名列表。
        description: 工具描述（可为空串）。
        right_w: 右栏宽（换行/截断宽度）。
    """
    right_w = max(1, right_w)
    rows: list = []
    desc_lines = _wrap_by_width(description, right_w) if description else []
    rows.extend(desc_lines)
    if desc_lines:
        rows.append([StyledRun("\u2500" * max(1, right_w - 1), _S_SEP_ROW)])
    rows.append([StyledRun("\u25b8 \u53c2\u6570", _S_SECTION)])
    rows.extend(_tree_rows(
        build_tools_params_tree(props_map or {}, required or []), right_w,
    ))
    if len(rows) > _INSPECTOR_MAX_ROWS:
        rows = rows[:_INSPECTOR_MAX_ROWS]
        rows.append([StyledRun(
            f"\u2026 内容过长，仅显示前 {_INSPECTOR_MAX_ROWS} 行", _S_HINT,
        )])
    return rows


def _inspector_children(
    name: str, props_map: dict, required: list, description: str,
    right_w: int, vh: int, scroll: int = 0,
    content_rows: list | None = None, cursor: int = -1,
) -> list:
    """右栏参数检查器子元素（标题 + 元信息 + 内容行滚动窗口 + 光标行高亮 +
    省略提示）。

    内容顺序：
      1. 标题（工具名，亮青加粗）；
      2. 元信息（``N 个参数 · M 个必需``，暗灰）；
      3. 内容滚动窗口：描述（按栏宽换行）→ 分割线（描述存在时）→
         ``▸ 参数`` 小节标题 → 参数树行（``build_tools_params_tree`` 树形
         展开）。scroll>0 置顶「… 前 N 行省略」、未到尾部后置「… 后 N 行
         省略」（vim/less 滚动语义——焦点移到右栏后滚动查看全部参数）；
         ``cursor``（绝对行索引，-1=不高亮）落在窗口内的行**整行背景
         高亮**（vim cursorline——2026-08-19 用户需求：右边高亮当前行
         背景色）。

    工具名为空（空数据源防御）→ 返回「无可用工具」占位。

    Args:
        name: 工具名（空串 = 空态）。
        props_map: 参数名 → 参数定义 dict（schema parameters.properties）。
        required: 必需参数名列表。
        description: 工具描述（可为空串）。
        right_w: 右栏宽（换行/截断宽度；<=0 外部调用防御）。
        vh: 视口行数预算（内容行数上限）。
        scroll: 内容滚动偏移（0=顶部；越界钳制）。
        content_rows: 预生成的全量内容行（组件 use_memo 传入）；None 时
            内部惰性生成（直接调用/测试兼容）。
        cursor: 内容光标行绝对索引（-1 = 不高亮）。

    Returns:
        list——TEXT 元素列表（检查器子元素）。
    """
    if not name:
        return [h(TEXT, {
            "children": "无可用工具", "style": _S_HINT, "height": 1,
            "key": "tinsp-empty",
        })]
    right_w = max(1, right_w)
    children: list = []
    children.append(h(TEXT, {
        "children": name, "style": _S_TITLE, "height": 1, "key": "tinsp-title",
    }))
    n_req = len([p for p in (required or []) if p in (props_map or {})])
    children.append(h(TEXT, {
        "children": f"{len(props_map or {})} 个参数 · {n_req} 个必需",
        "style": _S_DIM, "height": 1, "key": "tinsp-meta",
    }))
    # ── 内容行（全量生成 → 滚动窗口切片；光标行高亮；省略提示两侧） ──
    if content_rows is None:
        content_rows = _tools_inspector_content_rows(
            name, props_map, required, description, right_w,
        )
    total = len(content_rows)
    try:
        scroll = int(scroll) or 0
    except (TypeError, ValueError, OverflowError):
        scroll = 0
    try:
        cursor = int(cursor) if cursor is not None else -1
    except (TypeError, ValueError, OverflowError):
        cursor = -1
    content_vh = max(_INSPECTOR_MIN_CONTENT, vh - 2)
    if total > content_vh:
        scroll = max(0, min(scroll, total - content_vh))
    else:
        scroll = 0
    if scroll > 0:
        children.append(h(TEXT, {
            "children": f"\u2026 前 {scroll} 行省略",
            "style": _S_HINT, "height": 1, "key": "tinsp-omitted-top",
        }))
    window = content_rows[scroll:scroll + content_vh]
    if scroll + len(window) < total:
        window = window[:max(0, len(window) - 1)]
        bottom_omitted = total - scroll - len(window)
    else:
        bottom_omitted = 0
    for i, seg in enumerate(window):
        abs_idx = scroll + i
        is_cursor = cursor >= 0 and abs_idx == cursor
        if is_cursor and isinstance(seg, list):
            # 树/分割线/小节标题 StyledRun 行——逐 run 合并光标背景色
            seg = [
                StyledRun(r.text, (r.style or Style()).merge(_S_INSP_BG))
                for r in seg
            ]
        if isinstance(seg, list):
            children.append(h(TEXT, {
                "children": "".join(r.text for r in seg) if seg else " ",
                "styled": seg,
                "height": 1,
                "key": f"tinsp-{len(children)}",
            }))
        else:
            # 描述纯文本行——光标行样式合并背景色
            style = _S_DIM
            if is_cursor:
                style = style.merge(_S_INSP_BG)
            children.append(h(TEXT, {
                "children": seg if seg else " ",
                "style": style, "height": 1,
                "key": f"tinsp-{len(children)}",
            }))
    if bottom_omitted:
        children.append(h(TEXT, {
            "children": f"\u2026 后 {bottom_omitted} 行省略",
            "style": _S_HINT, "height": 1, "key": "tinsp-omitted",
        }))
    return children


def TraceToolsView(props) -> object:
    """工具列表详情视图组件（模态全屏视图；App 按 FULLSCREEN_VIEWS 整屏渲染）。

    Props:
        model: AppModel 实例（fullscreen/trace_tools_selected）。
        width: 终端宽度（左右栏宽分配）。

    ★ 2026-08-17（用户需求：轨迹 Trace 工具列表 Enter 进入新界面）：主轨迹
    中选中 #0 工具列表记录按 Enter → ``model.fullscreen = "trace_tools"`` →
    App 整屏渲染本组件（左右布局：左工具名列表 + 右参数树）；Esc/Ctrl+H
    返回主轨迹（``model.fullscreen = "trace"``）。选中工具索引经
    ``model.trace_tools_selected`` 受控（ListView 受控光标 + onNavigate 写回
    ——返回主轨迹再进入保持上次选择）。
    """
    model = props["model"]
    width = props.get("width", 0) or 0
    active = getattr(model, "fullscreen", "") == "trace_tools"

    # ── 数据（use_memo：schema 列表 TTL 缓存内稳定——工具注册装配期完成） ──
    schemas = use_memo(lambda: _tools_schema_list(), ("tools-schema",))
    sel = max(0, min(
        getattr(model, "trace_tools_selected", 0),
        max(0, len(schemas) - 1),
    )) if schemas else 0

    # ── 面板焦点 / 右栏滚动与光标（2026-08-19 vim 面板浏览一致化） ──
    pane = getattr(model, "trace_tools_pane", "ledger") or "ledger"
    if pane not in ("ledger", "inspector"):
        pane = "ledger"
    scroll_raw = getattr(model, "trace_tools_scroll", 0) or 0
    cursor_raw = getattr(model, "trace_tools_cursor", 0) or 0

    # ── 视口 / 栏宽（左栏窄于台账——工具名短；右栏参数树占主体） ──
    vh = _viewport_rows()
    if width > 0:
        left_w = max(18, int(width * 0.32))
        if width - left_w - 1 < 20:
            left_w = max(14, width - 21)
        right_w = max(1, width - left_w - 1)
    else:
        left_w, right_w = 28, 52

    # ── 右栏内容（全量行 + 光标/scroll 渲染期协调 + use_memo 元素树） ──
    name, props_map, required, description = (
        schemas[sel] if schemas else ("", {}, [], "")
    )
    content_rows = use_memo(
        lambda: _tools_inspector_content_rows(
            name, props_map, required, description, right_w,
        ),
        (name, len(props_map or {}), ";".join(map(str, required or [])),
         description, right_w),
    )
    total_content = len(content_rows)
    approx_content_vh = max(_INSPECTOR_MIN_CONTENT, vh - 3)
    # 光标渲染期钳制（写回 model——越界残留收敛；空内容 → 0）
    if total_content:
        cursor = max(0, min(cursor_raw, total_content - 1))
    else:
        cursor = 0
    if cursor != cursor_raw:
        model.trace_tools_cursor = cursor
        cursor_raw = cursor
    # scroll 渲染期协调：钳制 + 跟随光标保持可见（vim 视口语义）
    if total_content > approx_content_vh:
        if scroll_raw < 0:
            scroll_raw = 0
        elif scroll_raw > total_content - approx_content_vh:
            scroll_raw = total_content - approx_content_vh
        if cursor < scroll_raw:
            scroll_raw = cursor
        elif cursor >= scroll_raw + approx_content_vh:
            scroll_raw = cursor - approx_content_vh + 1
    else:
        scroll_raw = 0
    if scroll_raw != (getattr(model, "trace_tools_scroll", 0) or 0):
        model.trace_tools_scroll = scroll_raw
    scroll = scroll_raw
    # 光标参数：仅右栏焦点传入（高亮）；左栏焦点 -1（不高亮）
    cursor_arg = cursor if pane == "inspector" else -1

    # ── 输入（激活期间：Esc/Ctrl+H 返回主轨迹；l/h 面板切换 + 光标移动） ──
    def _scroll_for_cursor(cursor: int, scroll: int) -> int:
        """右栏视口滚动：钳制 + 跟随光标保持可见（vim 视口语义）。"""
        if total_content <= approx_content_vh:
            return 0
        scroll = max(0, min(int(scroll), total_content - approx_content_vh))
        cursor = max(0, min(int(cursor), total_content - 1))
        if cursor < scroll:
            return cursor
        if cursor >= scroll + approx_content_vh:
            return cursor - approx_content_vh + 1
        return scroll

    def _move_cursor(new_cursor: int) -> None:
        """右栏光标移动：写回 cursor + scroll 跟随（保持光标可见）。"""
        if total_content:
            new_cursor = max(0, min(int(new_cursor), total_content - 1))
        else:
            new_cursor = 0
        model.trace_tools_cursor = new_cursor
        model.trace_tools_scroll = _scroll_for_cursor(
            new_cursor, getattr(model, "trace_tools_scroll", 0) or 0,
        )

    def _handle(event) -> bool:
        if not active:
            return False
        pane_now = getattr(model, "trace_tools_pane", "ledger") or "ledger"
        # 返回主轨迹（TraceView 恢复；主轨迹再次 Esc/Ctrl+H 关闭整个视图）
        if event.kind == "escape":
            model.fullscreen = "trace"
            return True
        if event.kind == "ctrl_key" and getattr(event, "char", "") == "\x08":
            model.fullscreen = "trace"
            return True
        # ── 面板切换（vim h/l）与右栏光标（char 单字符） ──
        # ★ 2026-08-19（vim 面板浏览一致化）：左栏焦点 l → 右检查器、
        #   h 已在最左放行；右栏焦点 h → 返回左栏、j/k/↑↓ 移动光标
        #   （当前行背景高亮，视口跟随）、g/G 顶部/底部、PgUp/PgDn 翻页、
        #   Home/End 首末、← 返回左栏。
        ch = getattr(event, "char", "") or ""
        if event.kind == "char" and len(ch) == 1:
            if pane_now == "ledger":
                if ch == "l":
                    model.trace_tools_pane = "inspector"
                    return True
            else:
                if ch == "h":
                    model.trace_tools_pane = "ledger"
                    return True
                cur_cursor = getattr(model, "trace_tools_cursor", 0) or 0
                if ch in ("j", "J"):
                    _move_cursor(cur_cursor + 1)
                    return True
                if ch in ("k", "K"):
                    _move_cursor(cur_cursor - 1)
                    return True
                if ch == "g":
                    _move_cursor(0)
                    return True
                if ch == "G":
                    _move_cursor(total_content)
                    return True
        # ── 右栏焦点：方向键/翻页/首末（ListView focus=False 不消费） ──
        if pane_now == "inspector":
            cur_cursor = getattr(model, "trace_tools_cursor", 0) or 0
            if event.kind == "arrow_down":
                _move_cursor(cur_cursor + 1)
                return True
            if event.kind == "arrow_up":
                _move_cursor(cur_cursor - 1)
                return True
            if event.kind == "page_down":
                _move_cursor(cur_cursor + max(1, approx_content_vh))
                return True
            if event.kind == "page_up":
                _move_cursor(cur_cursor - max(1, approx_content_vh))
                return True
            if event.kind == "home":
                _move_cursor(0)
                return True
            if event.kind == "end":
                _move_cursor(total_content)
                return True
            if event.kind == "arrow_left":
                model.trace_tools_pane = "ledger"
                return True
        # 其余按键不消费——左栏：放行 ListView（j/k/↑↓/PgUp/PgDn/g/G 导航）；
        # 右栏：未消费按键被 use_fullscreen 模态吞掉
        return False

    use_input(_handle, active)
    # ★ 模态全屏视图声明：未消费按键被 input router 吞掉（字符/Enter 不落入
    #   输入缓冲）；关闭后（fullscreen 变化）hook 不激活零影响。
    use_fullscreen(active)

    def _on_navigate(idx: int) -> None:
        """工具列表导航回调（ListView 导航后）：写回选中索引（受控光标）；
        切换工具同时复位右栏滚动/光标（新工具参数从顶部查看）。"""
        model.trace_tools_selected = int(idx)
        model.trace_tools_scroll = 0
        model.trace_tools_cursor = 0

    # ── 渲染 ──
    header_title = "\u258d\u5de5\u5177\u5217\u8868"
    # ★ BEAUTY-36（美化）：提示精简（80 宽终端下为行尾 ─ 分隔线留出空间）。
    if pane == "inspector":
        header_hint = ("  jk/\u2191\u2193 \u6eda\u52a8 \u00b7 h \u5217\u8868 \u00b7 g/G \u9996\u672b \u00b7 "
                       "Esc \u8fd4\u56de")
    else:
        header_hint = ("  \u2191\u2193/jk \u9009\u62e9 \u00b7 l \u8be6\u60c5 \u00b7 g/G \u9996\u672b \u00b7 "
                       "Esc \u8fd4\u56de")
    header_runs = [
        StyledRun(header_title, _S_TITLE),
        StyledRun(f" \u00b7 {len(schemas)} \u4e2a\u5de5\u5177", _S_HINT),
        StyledRun(header_hint, _S_HINT),
    ]
    if width > 0:
        header_runs = truncate_runs(header_runs, width)
    # ★ BEAUTY-36（2026-08-19 美化）：头部行尾 ``─`` 分隔线填充至满宽——
    #   与 TraceView 头部同视觉分层（标题区 / 内容区）。
    if width > 0:
        used = sum(getattr(r, "width", 1) for r in header_runs)
        pad = width - used
        if pad > 0:
            header_runs.append(StyledRun("\u2500" * pad, _S_SEP_ROW))

    # 左栏（工具名列表——ListView 标准控件：受控光标 + 虚拟滚动）
    names = [s[0] for s in schemas]

    def _render_item(item, idx, is_sel):
        return h(TEXT, {
            "key": f"ttools-{idx}",
            "styled": _tool_row_runs(item, bool(is_sel), left_w),
            "height": 1,
        })

    # ★ 2026-08-19（vim 面板浏览）：focus 仅在左栏焦点时激活（右栏焦点
    #   放行 j/k/↑↓ 等给本组件滚动处理）。
    ledger = h(ListView, {
        "items": names,
        "height": vh,
        "width": left_w,
        "cursor": sel,
        "renderItem": _render_item,
        "onNavigate": _on_navigate,
        "focus": active and pane == "ledger",
    })

    # 右栏（参数检查器——use_memo：选中工具/栏宽/视口/滚动/光标变化才重建）
    # ★ P2（review 2026-08-18）：deps 展平原子值——修复前 ``tuple(required or [])``
    #   每帧新建嵌套元组，``_hooks_core._object_is`` 对 tuple 按 is 引用比较恒
    #   False → 右栏检查器每帧全量重建（与 trace.py 各指纹模块「展平原子值」
    #   契约相悖）。required 为参数名 str 列表 → join 单一 str（值比较稳定）。
    right_children = use_memo(
        lambda: _inspector_children(
            name, props_map, required, description, right_w, vh, scroll,
            content_rows, cursor_arg,
        ),
        (name, len(props_map or {}), ";".join(map(str, required or [])),
         description, right_w, vh, scroll, total_content, cursor_arg),
    )

    return h(Column, None, [
        h(TEXT, {"styled": header_runs, "height": 1}),
        h(Row, None, [
            ledger,
            h(TEXT, {"children": "\u2502", "style": _S_SEP_ROW, "height": 1}),
            h(Column, {"width": right_w}, right_children),
        ]),
    ])


__all__ = [
    "TraceToolsView",
    "_inspector_children",
    "_tools_inspector_content_rows",
    "_tool_row_runs",
    "_tree_rows",
]
