"""editmsg — 消息选择弹窗组件（EditMsgSelectPopup，独立于 UserSelectPopup）。

★ 2026-08-18（用户需求：editmsg 与 user_select 不能用同一份代码）：
/editmsg 消息选择从 user_select 协议（``model.user_select`` +
``UserSelectPopup`` + ``bottom_view="user_select"``）拆分为**独立实现**：

  - 独立状态：``model.editmsg_select``（``EditMsgSelectState``，
    src/tui/app/_state_types.py）；
  - 独立底部视图：``bottom_view="editmsg"``（``app.BOTTOM_VIEWS`` 注册）；
  - 独立组件：``EditMsgSelectPopup``（本文件）；
  - **每条消息只显示一行**：``options`` 为单行摘要（message_editor
    ``_user_msg_summary`` 生成），组件按单行渲染并截断防超宽。

★ 2026-10-07（editmsg 增强：更好的显示 / 操作）：
  - **消息全文预览**：列表下方预览区显示当前选中消息完整内容
    （``previews`` 数据源，按可用高度截断）；
  - **弹窗内搜索过滤**：``/`` 进入搜索输入（字符累积 / 退格 / Enter 保留
    过滤 / Esc 清除），列表只显示匹配消息（子串匹配，忽略大小写）；
  - **导航增强**：``PgUp``/``PgDn`` 翻页、``Home``/``End`` 首末（``g``/``G``
    由列表控件提供）；
  - **统计**：标题显示 ``(n/total)``（过滤时 ``n`` 为过滤视图内位置，
    ``total`` 为过滤后条数）。

组件与编辑器轮询线程通信协议（跨线程安全，GIL 原子字段；与 user_select
工具协议同构但独立实现，不共用代码）：
  - 编辑器：设置 ``model.editmsg_select``（visible=True, seq+1）→
    request_bottom_redraw；App 组件以 ``key=seq`` 渲染本组件（seq 变化
    强制重挂载，重置内部 use_state）。
  - 组件：控件回调更新内部 state + 写 ``es.selected``；提交/取消经
    ``es.try_set_final`` 原子终态写入（first-write-wins，锁内检查 done）。
  - 编辑器：轮询 ``es.done``（带 deadline 超时），读 selected 后清理
    ``model.editmsg_select = EditMsgSelectState()`` 并 request_bottom_redraw。

依赖约束：仅依赖 app 同层（model/input_area）与 ink 框架（Layer 0/1），
无 tools 层反向依赖。
"""

from __future__ import annotations

import logging

from src.tui.core.style import Style
from src.tui._width import wcswidth_simple
from src.tui.app.input_area import _truncate_width
from src.tui.ink import TEXT, h, Column, StyledRun, use_input
from src.tui.ink.helpers import truncate_runs
from src.tui.ink.hooks import use_ref, use_state, use_memo
from src.tui.ink.widgets.interactive import SelectInput

from ._ansi_convert import _convert_ansi_row
from ._modal_view import empty_modal_frame, use_modal_scope

_logger = logging.getLogger(__name__)

__all__ = ["EditMsgSelectPopup", "_filter_indices"]

#: 弹窗标题色（亮青加粗，与 UserSelectPopup 标题视觉同源但独立定义）
_S_TITLE = Style(fg=45, bold=True)
#: 高亮行背景色（静态 237——弹窗不呼吸）
_S_SEL_BG = Style(bg=237)
#: 提示行静态色（浅蓝 110——弹窗不呼吸）
_S_DESC = Style(fg=110)
#: 预览区样式（暗灰——次要信息，不抢列表焦点）
_S_PREVIEW = Style(fg=245)
#: 预览区标题样式（浅蓝）
_S_PREVIEW_TITLE = Style(fg=110)
#: 搜索输入行样式（亮青加粗）
_S_SEARCH = Style(fg=45, bold=True)
#: 列表项默认文本样式（亮白；★ 2026-10-07 第三批：命中高亮改造为 styled 渲染）
_S_ITEM = Style(fg=252)
#: 搜索命中子串高亮（亮黄加粗 + 暗底——精确到子串而非整行）
_S_HIT = Style(fg=214, bold=True, bg=238)

#: 预览区最大行数（超出滚动/截断——弹窗空间有限）。
_PREVIEW_MAX_ROWS = 6
#: 过滤文本长度上限（渲染行按宽度截断，无上限累积只浪费内存）。
_FILTER_MAX = 100
#: 预览滚动步长（``[``/``]`` 每次滚动的行数）。
_PREVIEW_SCROLL_STEP = 3
#: 预览渲染缓存上限（markdown 渲染结果按内容哈希缓存——切回同一消息零重渲染）。
_PREVIEW_CACHE_MAX = 16
_preview_rows_cache: dict = {}


def _highlight_runs(text: str, query: str, base_style=None, hl_style=None) -> list:
    """文本 → StyledRun 列表（命中的 ``query`` 子串加高亮样式，忽略大小写）。

    ★ 2026-10-07 第三批（editmsg 搜索命中高亮）：弹窗内搜索时列表命中的
    子串以高亮样式（``_S_HIT``）显示（非整行背景，更精确）。空 query →
    单 run（零成本）。
    """
    text = str(text or "")
    if not query:
        return [StyledRun(text, base_style)]
    low = str(query).lower()
    if not low:
        return [StyledRun(text, base_style)]
    tlow = text.lower()
    runs: list = []
    i = 0
    n = len(text)
    while i < n:
        j = tlow.find(low, i)
        if j < 0:
            runs.append(StyledRun(text[i:], base_style))
            break
        if j > i:
            runs.append(StyledRun(text[i:j], base_style))
        runs.append(StyledRun(text[j:j + len(low)], hl_style))
        i = j + len(low)
    return runs or [StyledRun(text, base_style)]


def _preview_rows(text: str, width: int) -> list:
    """预览文本 → StyledRun 行列表（markdown/代码高亮渲染；失败回退纯文本）。

    ★ 2026-10-07 第三批（editmsg 预览区增强）：复用聊天区 markdown 渲染管线
    （``apply._render_markdown_lines``）——预览长消息时标题/粗体/代码块等
    格式化显示（与消息区渲染一致）。结果按 ``(内容哈希, 宽度)`` 缓存，
    切回同一消息零重渲染。
    """
    width = max(1, int(width))
    text = str(text or "")
    key = (hash(text), len(text), width)
    cached = _preview_rows_cache.get(key)
    if cached is not None:
        return cached
    from src.tui.app.apply import _render_markdown_lines
    try:
        ansi_lines = _render_markdown_lines(text, max(width, 20))
    except Exception:
        ansi_lines = []
    rows: list = []
    for aline in ansi_lines:
        rows.extend(_convert_ansi_row(aline, width, "content"))
    if not rows:
        for ln in (text.splitlines() or [""]):
            rows.append([StyledRun(ln, _S_PREVIEW)])
    if len(_preview_rows_cache) >= _PREVIEW_CACHE_MAX:
        _preview_rows_cache.clear()
    _preview_rows_cache[key] = rows
    return rows


def _editmsg_item_rows() -> int:
    """消息选择弹窗行数上限（超屏防护）。

    ★ 模态底部视图：EditMsgSelectPopup 独立为底部视图——弹窗打开时状态栏/
    输入区**不渲染**，可用高度 = 终端高 - 顶部标题栏 1 - 弹窗标题 1 - 弹窗
    提示行 1 ≈ ``h - 3``（与 UserSelectPopup 的高度预算语义相同，独立实现
    ——editmsg 与 user_select 不共用代码）。

    ★ P3-1 修复（矮终端溢出）：下限从 6 收紧到 1——修复前 ``max(6, h-3)``
    在矮终端（h < 9）强制 6 行，弹窗溢出屏幕底部（无滚动）。下限 1 时调用
    处 ``min(total, rows)`` 自然钳制；异常/未知高度回退 12。

    Returns:
        选项最大渲染行数（≥1）。
    """
    try:
        from src.tui._screen import TerminalWidthCache
        h = TerminalWidthCache.get_default().get_height()
        if h and h > 0:
            return max(1, h - 3)
        return 12
    except Exception:
        return 12


def _filter_indices(options: list, query: str) -> list:
    """过滤后保留的消息索引列表（子串匹配，忽略大小写；空 query=全量）。

    Args:
        options: 单行摘要列表。
        query: 过滤文本。

    Returns:
        list[int]——保留项在 options 中的原索引（顺序一致）；query 为空时
        返回全部索引。
    """
    if not query:
        return list(range(len(options or [])))
    low = str(query).lower()
    return [
        i for i, text in enumerate(options or [])
        if low in str(text).lower()
    ]


def _clamp_pos(pos: int, total: int) -> int:
    """视图位置钳制（total<=0 → 0）。"""
    if total <= 0:
        return 0
    try:
        return max(0, min(int(pos), total - 1))
    except (TypeError, ValueError):
        return 0


def EditMsgSelectPopup(props) -> object:
    """React Ink 消息选择弹窗组件（/editmsg 专用，独立于 UserSelectPopup）。

    Props:
        model: AppModel 实例（读 ``model.editmsg_select``）。
        width: 终端宽度。

    Returns:
        Column（弹窗行）或空 TEXT（不可见时零高度）。
    """
    model = props["model"]
    width = props.get("width", 80)
    es = getattr(model, "editmsg_select", None)
    # options 为空（异常状态）时弹窗无可交互选项——静默不可见
    # （编辑器轮询 deadline 超时兜底，不会永久卡死）。
    visible = bool(
        es is not None and es.visible and not es.done
        and getattr(es, "options", None)
    )

    # ── hooks（无条件调用，保持 fiber hook 顺序稳定） ──
    # 初始值从 model.editmsg_select 读取（App 以 key=seq 强制重挂载，
    # seq 变化 → fiber 重建 → use_state 重新初始化）。
    selected, set_selected = use_state(es.selected if es is not None else 0)
    # ★ 2026-08-18（连续弹出显示错乱修复 · 组件级双保险，与 UserSelectPopup
    #   同机制）：es 实例变化（清理后重新打开产生新 EditMsgSelectState 对象）
    #   时本帧即以新 es.selected 计算高亮，并排队 set_selected 让下一帧 state
    #   收敛——即使调和器因 key 复用（seq 修复后理论不会发生）保留旧 fiber，
    #   也不残留旧选中（标题 (n/N) 与高亮行显示正确）。
    es_ref = use_ref(None)
    fresh_es = es_ref.current is not es
    if fresh_es:
        es_ref.current = es
        if es is not None and selected != es.selected:
            set_selected(es.selected)
    # ★ 模态底部视图声明（与 UserSelectPopup 同机制）：visible 时独占键盘
    #   输入——未消费按键被 input router 吞掉（不落入输入缓冲；输入区已
    #   不渲染）。visible=False 时 hook 不参与路由（零影响）。
    use_modal_scope(visible, fullscreen=False)
    # ★ P2（review 2026-08-22）：use_memo 无条件调用（移到 early return 前）
    #   ——修复前 use_memo 仅在 visible 分支调用，违反「unconditional hooks」
    #   契约（下方 ``if not visible: return`` 早退后 use_memo 被跳过；若日后
    #   在该 return 与 use_memo 之间新增 hook 将触发 HookStateError 或 hook
    #   顺序错乱）。options/total 提前计算（es 可能为 None——防御）。
    options = list(es.options) if (es is not None and getattr(es, "options", None)) else []
    previews = list(getattr(es, "previews", None) or []) if es is not None else []
    query = (getattr(es, "filter", "") or "") if es is not None else ""
    search_mode = bool(getattr(es, "search_mode", False)) if es is not None else False
    total_all = len(options)
    # ★ 2026-10-07（弹窗内搜索过滤）：keep = 过滤后保留的原索引列表；
    #   SelectInput 的 items 为过滤视图（索引 = 视图位置），提交/高亮时
    #   经 keep 换算回**原始消息索引**（es.selected 语义 = 原始索引，编辑器
    #   据此选择 user_msgs）。
    keep = _filter_indices(options, query)
    total = len(keep)
    # ★ P2（review）：弹窗行数纳入 deps——修复前仅 ``[total]``，终端 resize
    #   （item 数不变）后可见行数不刷新（弹窗高度陈旧）；同构的
    #   ``user_select._popup_item_rows()`` 为每帧直调。此处按高度入 deps
    #   保持 use_memo 缓存收益且响应 resize。
    _rows = _editmsg_item_rows()
    # 预览区行数（有预览数据且行数预算允许时显示；搜索模式下让位给输入行）
    preview_h = 0
    if previews and not search_mode:
        preview_h = max(2, min(_PREVIEW_MAX_ROWS, _rows // 3))
    list_rows_budget = use_memo(lambda: _rows, [total, _rows])
    list_limit = max(1, min(total, list_rows_budget - (preview_h + (1 if preview_h else 0))))

    if not visible:
        return empty_modal_frame()

    # ── 当前选中（原始索引 → 视图位置） ──
    if fresh_es and es is not None:
        try:
            cur_orig = max(0, min(int(es.selected or 0), total_all - 1))
        except (TypeError, ValueError):
            cur_orig = max(0, min(selected, total_all - 1))
    else:
        try:
            cur_orig = max(0, min(int(getattr(es, "selected", 0) or 0), total_all - 1))
        except (TypeError, ValueError):
            cur_orig = 0
    if total_all <= 0:
        cur_orig = 0
    if keep and cur_orig in keep:
        view_pos = keep.index(cur_orig)
    else:
        view_pos = 0
        # 过滤后原选中不在视图内 → 选中首项（同步回 es，编辑器读取一致）
        if keep and es is not None:
            es.selected = keep[0]
            cur_orig = keep[0]
    title = es.title or "选择要编辑的消息"
    rows: list = []

    # 标题行（对齐补全弹窗：▍ + 模式图标 + 标题 + (n/total)；静态色不呼吸）
    title_style = _S_TITLE
    count_seg = f" ({view_pos + 1}/{total})" if total else " (0/0)"
    if query:
        count_seg += f" \u00b7 \u8fc7\u6ee4 {total}/{total_all}"
    title_disp = f" \u258d \u25b6 {title}{count_seg}"
    if wcswidth_simple(title_disp) > width:
        prefix = " \u258d \u25b6 "
        suffix = count_seg
        budget = max(1, width - wcswidth_simple(prefix) - wcswidth_simple(suffix))
        title_disp = prefix + _truncate_width(title, budget) + suffix
    rows.append(h(TEXT, {
        "children": title_disp,
        "style": title_style,
        "textWrap": "truncate-end",
        "key": "em-title",
    }))

    # ── 协议回调（first-write-wins，独立实现不共用 UserSelectState） ──
    def _commit(result, action: str) -> None:
        """提交终态（first-write-wins：done 已置位则放弃覆盖）。"""
        es.try_set_final(action, result)

    def _on_select(item) -> None:
        # 单选 Enter：经 try_set_final 原子终态写入（编辑器超时已置位则
        # 放弃覆盖——first-write-wins）。
        # ★ P2-4 修复（闭包 cur 陈旧）：SelectInput 事件期经 selected_ref
        #   （即时值）选中正确的 item 传入——result 直接取 item["label"]
        #   （权威显示文本），es.selected 取 item["value"]（**原始消息索引**，
        #   过滤视图下与视图位置不同）；修复前用渲染帧闭包 ``cur`` 判定
        #   ``0 <= cur < total``，同批多按键无重渲染时 cur 陈旧，es.result
        #   可能与 es.selected 不一致（协议数据漂移，未来消费方即踩坑）。
        try:
            label = str(item["label"])
        except (TypeError, KeyError):
            # ★ P3（review）：不静默吞异常——item 非订阅结构时记录 debug
            #   （result 退化为空 → 提交空结果，须可观测）。
            _logger.debug("editmsg_select 提交结果构建异常，退化为空结果", exc_info=True)
            label = ""
        try:
            orig = int(item["value"])
        except (TypeError, KeyError, ValueError):
            orig = cur_orig
        if 0 <= orig < total_all:
            es.selected = orig
        else:
            orig = cur_orig
        _commit([label], "confirmed")

    def _on_cancel(*_args) -> None:
        _commit([], "cancel")

    def _on_highlight(pos: int) -> None:
        # 导航变化：视图位置 → 原始索引写回 es.selected（组件内部 state 由
        # 控件维护；es 为跨线程权威值）
        try:
            idx = int(pos)
        except (TypeError, ValueError):
            return
        if not (0 <= idx < len(keep)):
            return
        es.selected = keep[idx]
        set_selected(keep[idx])
        # ★ 2026-10-07 第三批：切换选中消息 → 预览区滚动复位（从顶部查看新消息）
        es.preview_scroll = 0

    # ── 选项控件（SelectInput 标准控件，单选；受控 index=过滤视图位置） ──
    items = [{"label": options[i], "value": i} for i in keep]
    # 每条消息只显示一行：单行选项 → 行数预算即可见项数（交互仍可导航到
    # 隐藏项）。
    # ★ P3-4 修复（高度查询 memo 化）+ P2（review 2026-08-22）：
    #   ``_editmsg_item_rows`` 的 use_memo 已上移至 early return 前无条件
    #   调用（见上方 hooks 顺序修复），此处仅保留渲染说明。

    def _render_item(item, idx, is_sel):
        """单行渲染：▶ 前缀 + 摘要（★ 2026-10-07 第三批：命中子串高亮）。

        ``query``（弹窗内搜索文本）非空时，摘要中命中的子串以 ``_S_HIT``
        高亮（精确到子串，非整行背景）；超宽截断不拆 CJK（``truncate_runs``）。
        """
        prefix = " \u25b6  " if is_sel else "    "
        body_w = (width - 4) if width and width > 0 else 40
        runs = [StyledRun(prefix, _S_TITLE if is_sel else None)]
        runs.extend(_highlight_runs(str(item["label"]), query, _S_ITEM, _S_HIT))
        runs = truncate_runs(runs, max(1, body_w + 4))
        if is_sel:
            runs = [
                StyledRun(r.text, (r.style or Style()).merge(_S_SEL_BG))
                for r in runs
            ]
        return h(TEXT, {
            "children": "".join(r.text for r in runs) if runs else " ",
            "styled": runs, "height": 1, "key": f"em-item-{idx}",
        })

    # ── 弹窗级按键（搜索输入 / 翻页 / 首末） ──
    def _current_view_pos() -> int:
        """当前选中在过滤视图中的位置（实时读 es——同批连续按键不陈旧）。"""
        try:
            cur = int(getattr(es, "selected", 0) or 0)
        except (TypeError, ValueError):
            cur = 0
        if keep and cur in keep:
            return keep.index(cur)
        return 0

    def _move_view_pos(new_pos: int) -> bool:
        if not keep:
            return False
        new_pos = _clamp_pos(new_pos, total)
        if new_pos == _current_view_pos():
            return False
        es.selected = keep[new_pos]
        set_selected(keep[new_pos])
        # ★ 2026-10-07 第三批：切换选中消息 → 预览区滚动复位
        es.preview_scroll = 0
        return True

    def _scroll_preview(delta: int) -> bool:
        """``[``/``]``：滚动当前消息全文预览（± ``_PREVIEW_SCROLL_STEP`` 行）。

        无预览数据 / 预览不足一屏 / 已在边界 → 返回 False（放行，不消费）。
        """
        if not (previews and 0 <= cur_orig < len(previews) and preview_h):
            return False
        pw = max(1, width if width and width > 0 else 40)
        total_prev = len(_preview_rows(str(previews[cur_orig] or ""), pw))
        if total_prev <= preview_h:
            return False
        try:
            cur = int(getattr(es, "preview_scroll", 0) or 0)
        except (TypeError, ValueError):
            cur = 0
        new = max(0, min(cur + delta * _PREVIEW_SCROLL_STEP, total_prev - preview_h))
        if new == cur:
            return False
        es.preview_scroll = new
        return True

    def _handle_extra(event) -> bool:
        if not visible or es is None:
            return False
        # 搜索输入模式：独占按键（列表控件 focus=False，不参与导航）
        if getattr(es, "search_mode", False):
            if event.kind == "escape":
                es.search_mode = False
                es.filter = ""
                return True
            if event.kind == "enter":
                es.search_mode = False
                return True
            if event.kind == "backspace":
                cur_q = getattr(es, "filter", "") or ""
                if cur_q:
                    es.filter = cur_q[:-1]
                return True
            if event.kind == "char":
                ch = getattr(event, "char", "") or ""
                if ch and "\n" not in ch and "\r" not in ch:
                    cur_q = getattr(es, "filter", "") or ""
                    if len(cur_q) < _FILTER_MAX:
                        es.filter = cur_q + ch
                return True
            return True
        kind = event.kind
        ch = getattr(event, "char", "") or ""
        if kind == "char" and ch == "/":
            es.search_mode = True
            es.filter = ""
            return True
        # ★ 2026-10-07 第三批（预览区滚动）：[ / ] 滚动当前消息全文预览。
        if kind == "char" and ch in ("[", "]"):
            return _scroll_preview(1 if ch == "]" else -1)
        if kind == "page_up":
            return _move_view_pos(_current_view_pos() - max(1, list_limit))
        if kind == "page_down":
            return _move_view_pos(_current_view_pos() + max(1, list_limit))
        if kind == "home":
            return _move_view_pos(0)
        if kind == "end":
            return _move_view_pos(total - 1)
        return False

    use_input(_handle_extra, visible)

    rows.append(h(SelectInput, {
        "key": f"em-select-{getattr(es, 'seq', 0)}",
        "items": items,
        "initialIndex": view_pos,
        "index": view_pos,
        "limit": list_limit,
        "onSelect": _on_select,
        "onCancel": _on_cancel,
        "onHighlight": _on_highlight,
        "renderItem": _render_item,
        "consumeAll": True,
        "focus": bool(visible and not search_mode),
    }))

    # ── 预览区（当前选中消息全文；markdown 渲染 + 滚动） ──
    #   ★ 2026-10-07 第三批（editmsg 预览区增强）：预览文本经 markdown 渲染
    #   （标题/粗体/代码高亮，与消息区同管线）；``[``/``]`` 滚动查看全文
    #   （``es.preview_scroll``），标题显示当前窗口位置 ``(起-止/总行)``。
    if preview_h and 0 <= cur_orig < len(previews):
        preview = str(previews[cur_orig] or "")
        pw = max(1, width if width and width > 0 else 40)
        prows = _preview_rows(preview, pw)
        total_prev = len(prows)
        limit = preview_h
        try:
            pscroll = int(getattr(es, "preview_scroll", 0) or 0)
        except (TypeError, ValueError):
            pscroll = 0
        pscroll = max(0, min(pscroll, max(0, total_prev - limit)))
        if es is not None and pscroll != getattr(es, "preview_scroll", None):
            es.preview_scroll = pscroll
        title_txt = "\u2500 \u9884\u89c8 "
        if total_prev > limit:
            title_txt += (
                f"({pscroll + 1}-{min(pscroll + limit, total_prev)}/{total_prev}) "
            )
        title_txt += "\u2500" * max(0, pw - wcswidth_simple(title_txt))
        rows.append(h(TEXT, {
            "children": title_txt, "style": _S_PREVIEW_TITLE,
            "textWrap": "truncate-end", "height": 1, "key": "em-preview-title",
        }))
        window = prows[pscroll:pscroll + limit]
        for i, rline in enumerate(window):
            runs = (
                list(rline) if isinstance(rline, list)
                else [StyledRun(str(rline), _S_PREVIEW)]
            )
            runs = truncate_runs(runs, pw)
            if not runs:
                runs = [StyledRun(" ", None)]
            rows.append(h(TEXT, {
                "children": "".join(r.text for r in runs) if runs else " ",
                "styled": runs, "height": 1,
                "key": f"em-preview-{i}",
            }))
        if total_prev > limit:
            rows.append(h(TEXT, {
                "children": (
                    f"  \u2026 {pscroll + len(window)}/{total_prev} \u884c"
                    " \u00b7 [ / ] \u6eda\u52a8\u9884\u89c8"
                ),
                "style": _S_DESC, "textWrap": "truncate-end", "height": 1,
                "key": "em-preview-more",
            }))

    # ── 底部行（搜索输入行 / 提示行；静态色不呼吸，窄屏单行截断） ──
    if search_mode:
        q = getattr(es, "filter", "") or ""
        disp = "/" + q + "\u258f"
        if width and width > 0:
            disp = _truncate_width(disp, width)
        rows.append(h(TEXT, {
            "children": disp,
            "style": _S_SEARCH,
            "textWrap": "truncate-end",
            "key": "em-search",
        }))
        hint = "  \u8f93\u5165\u8fc7\u6ee4\u6587\u672c \u00b7 Enter \u4fdd\u7559\u8fc7\u6ee4 \u00b7 Esc \u6e05\u9664"
    elif query:
        hint = (
            f"  /{query}  \u8fc7\u6ee4 {total}/{total_all} \u00b7 \u2191\u2193/jk \u9009\u62e9"
            " \u00b7 / \u6539\u8fc7\u6ee4 \u00b7 Esc \u6e05\u9664\u5e76\u53d6\u6d88"
        )
    else:
        hint = (
            "  \u2191\u2193/jk \u9009\u62e9 \u00b7 PgUp/PgDn \u7ffb\u9875 \u00b7 g/G \u9996\u672b"
            " \u00b7 [ ] \u9884\u89c8 \u00b7 Enter \u7f16\u8f91 \u00b7 / \u641c\u7d22"
            " \u00b7 Esc \u53d6\u6d88"
        )
    rows.append(h(TEXT, {
        "children": hint,
        "style": _S_DESC,
        "textWrap": "truncate-end",
        "key": "em-hint",
    }))

    return h(Column, None, rows)
