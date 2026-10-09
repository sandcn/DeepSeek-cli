"""logs_view — LogsView 会话日志 / 投影浏览器（模态全屏视图，2026-10）。

``/logs`` 命令打开：App 在 ``model.fullscreen == "logs"`` 时经全屏视图注册表
**整屏只渲染本组件**，关闭后恢复完整聊天界面。视图把会话的事实源——仅追加
的 ``SessionLog``——完整呈现：事件流、由事件投影出的模型历史、各投影单元
状态，以及「模型可见即已记录」的一致性校验。

布局（React Ink 左右布局）：
  - 左栏「事件流」：``#seq 图标 类型 时间 摘要``，↑↓/jk 选择
    （ListView 标准控件）；
  - 右栏「详情」：按 ``v`` 循环四种模式——事件详情（选中事件的 data 展开）/
    消息投影（derive_messages 结果）/ 投影状态（各投影单元 snapshot）/
    一致性校验（模型历史与日志投影比对）。

键盘：
  - ↑↓/jk 选择 · g/G 首末 · Enter/l 查看详情 · h 返回列表 · ``v`` 切换右栏模式；
  - ``r`` 刷新（重读会话日志）· ``/`` 搜索 · ``n``/``N``/``p`` 定位匹配 ·
    ``f`` 过滤 · ``?`` 帮助面板 · Esc/Ctrl+H 关闭。

数据源：命令线程构建 ``model.logs_view`` 的 entries / messages / projections /
verify / stats（``src/core/commands/_logs_cmd.py``）。依赖约束：仅依赖 app 同层
与 ink 框架（Layer 0/1），不直接依赖 core 层（数据经状态注入）。
"""

from __future__ import annotations

import json
import time

from src.tui.core.style import Style
from src.tui.ink import TEXT, Column, Row, StyledRun, h, use_input
from src.tui.ink.helpers import truncate_runs, wrap_runs_by_width
from src.tui.ink.hooks import use_effect, use_memo
from src.tui.ink.widgets.listview import ListView

from ._inspector_pane import PaneState, handle_nav, resolve
from ._modal_view import empty_modal_frame, is_modal_close_key, use_modal_scope
from ._view_common import (
    SEARCH_QUERY_MAX,
    build_header_runs,
    char_of,
    handle_search_input,
    jump_match,
    run_search,
    split_panes,
    status_runs,
    viewport_rows,
)

__all__ = [
    "LogsView",
    "_logs_search_text",
    "_event_detail_rows",
    "_message_rows",
    "_projection_rows",
    "_verify_rows",
    "_data_rows",
    "_is_logs_close_key",
    "format_time",
    "PANE_MODES",
    "pane_label",
]

_S_TITLE = Style(fg=45, bold=True)
_S_HINT = Style(fg=242)
_S_SEP = Style(fg=238)
_S_NAME = Style(fg=252)
_S_TIME = Style(fg=110)
_S_SEQ = Style(fg=110, bold=True)
_S_KEY = Style(fg=75)
_S_VALUE = Style(fg=252)
_S_MSG = Style(fg=252)
_S_OK = Style(fg=40, bold=True)
_S_ERR = Style(fg=196, bold=True)
_S_WARN = Style(fg=214)
_S_EVENT = Style(fg=45)
_S_STRUCT = Style(fg=179)
_S_SEL_BG = Style(bg=237)
_S_SEL_MARK = Style(fg=45, bold=True)
_S_INSP_BG = Style(bg=237)
_S_MATCH_BG = Style(bg=236)
_S_MATCH_CUR_BG = Style(bg=25)
_S_STATUS = Style(fg=221)
_S_PROMPT = Style(fg=45, bold=True)

#: 事件类型图标（与命令侧 ``_KIND_LABEL`` 同源文案；未知类型回退 ``•``）。
_KIND_ICON = {
    "turn/start": "\u25b6",
    "turn/end": "\u25a0",
    "step/start": "\u25b8",
    "step/end": "\u25c2",
    "system/message": "\u2699",
    "user/message": "\U0001f464",
    "assistant/message": "\U0001f916",
    "assistant/attempt": "\u21bb",
    "tool/result": "\U0001f527",
    "request/header": "\U0001f4e4",
    "request/context": "\U0001f4e5",
    "session/insert": "\uff0b",
    "session/replace": "\u270e",
    "session/delete": "\uff0d",
    "session/truncate": "\u2702",
    "session/reset": "\u232b",
}

#: 结构变更类事件（着色区分）。
_STRUCTURAL = frozenset({
    "session/insert", "session/replace", "session/delete",
    "session/truncate", "session/reset",
})

#: 消息类事件（role 相关着色）。
_MESSAGE_KIND = {
    "system/message": _S_HINT,
    "user/message": _S_EVENT,
    "assistant/message": _S_MSG,
    "tool/result": _S_STRUCT,
}

#: 右栏模式（``v`` 循环）。
PANE_MODES = ("event", "messages", "projections", "verify")
_PANE_LABELS = {
    "event": "事件详情",
    "messages": "消息投影",
    "projections": "投影状态",
    "verify": "一致性",
}

_KEYMAP = [
    {"group": "浏览", "keys": "\u2191\u2193/jk", "desc": "选择事件"},
    {"group": "浏览", "keys": "l/Enter", "desc": "查看详情"},
    {"group": "浏览", "keys": "h", "desc": "返回列表"},
    {"group": "浏览", "keys": "v", "desc": "切换右栏模式"},
    {"group": "浏览", "keys": "F", "desc": "跟随最新（开/关）"},
    {"group": "操作", "keys": "r", "desc": "刷新日志"},
    {"group": "搜索", "keys": "/", "desc": "搜索事件"},
    {"group": "搜索", "keys": "n/N/p", "desc": "下一/上一匹配"},
    {"group": "搜索", "keys": "f", "desc": "切换过滤"},
    {"group": "通用", "keys": "?", "desc": "帮助面板"},
    {"group": "通用", "keys": "Esc/F12", "desc": "关闭视图"},
]

#: 事件 data 中优先展示的键（顺序即展示顺序）。
_KNOWN_KEYS = (
    "content", "reasoning_content", "tool_calls", "tool_call_id", "name",
    "index", "start", "stop", "length", "message",
)


def pane_label(mode: str) -> str:
    """右栏模式显示名。"""
    return _PANE_LABELS.get(mode, _PANE_LABELS["event"])


def format_time(ts) -> str:
    """时间戳 → ``HH:MM:SS``（异常回退空串）。"""
    try:
        return time.strftime("%H:%M:%S", time.localtime(float(ts)))
    except Exception:
        return ""


def _kind_color(kind: str) -> Style:
    if kind in _STRUCTURAL:
        return _S_STRUCT
    return _MESSAGE_KIND.get(kind, _S_NAME)


def _kind_icon(kind: str) -> str:
    return _KIND_ICON.get(kind, "\u2022")


def _logs_search_text(entry: dict) -> str:
    """事件条目的可搜索文本（类型 + 摘要 + data 序列化）。"""
    if not isinstance(entry, dict):
        return ""
    parts = [
        str(entry.get("type", "")), str(entry.get("label", "")),
        str(entry.get("summary", "")),
    ]
    data = entry.get("data")
    if isinstance(data, dict):
        for value in data.values():
            if isinstance(value, str):
                parts.append(value)
            elif isinstance(value, (int, float, bool)) or value is None:
                parts.append(str(value))
    return " ".join(p for p in parts if p)


def _field_rows(key: str, value, width: int) -> list:
    """单个 data 字段的详情行（多行文本 / JSON 结构化 / 标量）。"""
    rows: list = []
    label = f"{key}: "
    if isinstance(value, str):
        lines = value.split("\n") if value else [""]
        for i, line in enumerate(lines):
            prefix = label if i == 0 else " " * len(label)
            runs = [
                StyledRun(prefix, _S_KEY) if i == 0 else StyledRun(prefix, None),
                StyledRun(line, _S_VALUE),
            ]
            rows.extend(list(l.runs) for l in wrap_runs_by_width(runs, max(1, width)))
        return rows
    if isinstance(value, (dict, list)):
        try:
            text = json.dumps(value, ensure_ascii=False, indent=2, default=str)
        except Exception:
            text = str(value)
        for i, line in enumerate(text.split("\n")):
            prefix = label if i == 0 else " " * len(label)
            runs = [
                StyledRun(prefix, _S_KEY) if i == 0 else StyledRun(prefix, None),
                StyledRun(line, _S_VALUE),
            ]
            rows.extend(list(l.runs) for l in wrap_runs_by_width(runs, max(1, width)))
        return rows
    if isinstance(value, bool):
        value_text = "true" if value else "false"
    elif value is None:
        value_text = "null"
    else:
        value_text = str(value)
    rows.append([
        StyledRun(label, _S_KEY),
        StyledRun(value_text, _S_VALUE),
    ])
    if width > 0:
        rows = [list(l.runs) for l in wrap_runs_by_width(rows[0], max(1, width))]
    return rows


def _data_rows(data, width: int) -> list:
    """事件 data → 详情行（优先键在前，其余键随后）。"""
    if not isinstance(data, dict) or not data:
        return [[StyledRun("(无附加数据)", _S_HINT)]]
    rows: list = []
    seen = set()
    for key in _KNOWN_KEYS:
        if key in data:
            rows.extend(_field_rows(key, data[key], width))
            seen.add(key)
    for key, value in data.items():
        if key not in seen:
            rows.extend(_field_rows(str(key), value, width))
    return rows or [[StyledRun("(无附加数据)", _S_HINT)]]


def _event_detail_rows(entry: dict, right_w: int) -> list:
    """选中事件 → 详情行。"""
    if entry is None:
        return [[StyledRun("无选中事件", _S_HINT)]]
    kind = str(entry.get("type", ""))
    label = str(entry.get("label", ""))
    rows: list = []
    header = [StyledRun(f"{_kind_icon(kind)} {label}", _S_TITLE)]
    for line in wrap_runs_by_width(header, max(1, right_w)):
        rows.append(list(line.runs))
    rows.append([StyledRun("\u2500" * max(1, right_w - 1), _S_SEP)])
    meta = [
        StyledRun(f"# {entry.get('seq', 0)}", _S_SEQ),
        StyledRun(f"  类型 {kind}", _S_TIME),
        StyledRun(f"  时间 {format_time(entry.get('time'))}", _S_TIME),
    ]
    for line in wrap_runs_by_width(meta, max(1, right_w)):
        rows.append(list(line.runs))
    rows.append([StyledRun(" ", None)])
    rows.extend(_data_rows(entry.get("data"), right_w))
    return rows


def _message_rows(messages: list, width: int) -> list:
    """模型历史投影行（每条消息一行摘要）。"""
    rows: list = [[StyledRun(f"\u25b8 消息投影（{len(messages or [])} 条）", _S_TITLE)], [StyledRun(" ", None)]]
    if not messages:
        rows.append([StyledRun("(模型历史为空)", _S_HINT)])
        return rows
    for i, msg in enumerate(messages):
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role", ""))
        color = {"system": _S_HINT, "user": _S_EVENT, "assistant": _S_MSG, "tool": _S_STRUCT}.get(role, _S_NAME)
        summary = str(msg.get("summary", ""))
        runs = [
            StyledRun(f"#{i:<3}", _S_SEQ),
            StyledRun(f"{role:<10}", color),
            StyledRun(summary, _S_VALUE),
        ]
        if msg.get("tool_calls"):
            runs.append(StyledRun(f"  [\u2699 {len(msg.get('tool_calls') or [])}]", _S_WARN))
        if width > 0:
            rows.extend(list(l.runs) for l in wrap_runs_by_width(runs, max(1, width)))
        else:
            rows.append(runs)
    return rows


def _projection_rows(projections: list, width: int) -> list:
    """投影单元状态行。"""
    rows: list = [[StyledRun(f"\u25b8 投影状态（{len(projections or [])} 个）", _S_TITLE)], [StyledRun(" ", None)]]
    if not projections:
        rows.append([StyledRun("(无已注册投影)", _S_HINT)])
        return rows
    for item in projections:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", ""))
        state_text = str(item.get("state_text", ""))
        runs = [
            StyledRun(f"\u25b8 {name}  ", _S_KEY),
            StyledRun(state_text, _S_VALUE),
        ]
        if width > 0:
            rows.extend(list(l.runs) for l in wrap_runs_by_width(runs, max(1, width)))
        else:
            rows.append(runs)
        for state_row in item.get("state_rows") or []:
            sub = [StyledRun("   ", None), StyledRun(str(state_row), _S_HINT)]
            if width > 0:
                rows.extend(list(l.runs) for l in wrap_runs_by_width(sub, max(1, width)))
            else:
                rows.append(sub)
    return rows


def _verify_rows(state, width: int) -> list:
    """一致性校验结果行。"""
    verify_ok = getattr(state, "verify_ok", None)
    verify_text = str(getattr(state, "verify_text", "") or "")
    if verify_ok is True:
        head = [StyledRun("\u2713 一致", _S_OK)]
    elif verify_ok is False:
        head = [StyledRun("\u2717 不一致", _S_ERR)]
    else:
        head = [StyledRun("\u2014 不可校验", _S_WARN)]
    rows: list = [[StyledRun("\u25b8 一致性校验", _S_TITLE)], [StyledRun(" ", None)], head]
    for line in wrap_runs_by_width([StyledRun(verify_text, _S_VALUE)], max(1, width)):
        rows.append(list(line.runs))
    rows.append([StyledRun(" ", None)])
    for label, value in getattr(state, "stats", None) or []:
        runs = [StyledRun(f"  {str(label):<16}", _S_KEY), StyledRun(str(value), _S_VALUE)]
        if width > 0:
            rows.extend(list(l.runs) for l in wrap_runs_by_width(runs, max(1, width)))
        else:
            rows.append(runs)
    return rows


def _is_logs_close_key(event) -> bool:
    """会话日志视图关闭键：Esc / Ctrl+H / F12（再次按下关闭）。"""
    if is_modal_close_key(event):
        return True
    return getattr(event, "kind", "") == "f12"


def _close_view(model, lv) -> None:
    """关闭视图：写终态 + 清 fullscreen（F12 / Esc 共用）。

    命令路径（``/logs`` 阻塞轮询）下命令线程的 cleanup 会检查
    ``fullscreen`` 是否仍指向本视图——已被本函数清空时跳过，无冲突。
    """
    if lv is not None:
        lv.try_set_final("cancel")
    if getattr(model, "fullscreen", "") == "logs":
        model.fullscreen = ""


def LogsView(props) -> object:
    """会话日志 / 投影浏览器组件（模态全屏视图）。"""
    model = props["model"]
    width = props.get("width", 0) or 0
    lv = getattr(model, "logs_view", None)
    visible = bool(lv is not None and lv.visible and not lv.done)
    entries = list(getattr(lv, "entries", None) or []) if lv is not None else []
    messages = list(getattr(lv, "messages", None) or []) if lv is not None else []
    projections = list(getattr(lv, "projections", None) or []) if lv is not None else []
    help_open = bool(getattr(lv, "help_open", False)) if lv is not None else False
    search_mode = bool(getattr(lv, "search_mode", False)) if lv is not None else False
    pattern = (getattr(lv, "search_pattern", "") or "") if lv is not None else ""
    filter_on = bool(getattr(lv, "search_filter", False)) if lv is not None else False
    matches = list(getattr(lv, "search_matches", None) or []) if lv is not None else []
    status_message = (getattr(lv, "status_message", "") or "") if lv is not None else ""
    pane_mode = str(getattr(lv, "pane_mode", "event") or "event") if lv is not None else "event"
    if pane_mode not in PANE_MODES:
        pane_mode = "event"

    allowed = None
    if filter_on and pattern and matches:
        allowed = set(matches)
    index_map = [i for i in range(len(entries)) if allowed is None or i in allowed]
    items = [entries[i] for i in index_map]
    total = len(items)
    sel = 0
    if total:
        try:
            sel = max(0, min(int(getattr(lv, "selected", 0) or 0), total - 1))
        except (TypeError, ValueError):
            sel = 0
    # 跟随最新（实时刷新）：命令线程按会话日志签名自动重建数据，本处让选中
    # 项始终停在末条（新事件实时可见）；用户手动导航后 follow_tail=False，
    # 保持固定阅读位置（``F`` 键重新开启）。
    follow_tail = bool(getattr(lv, "follow_tail", True)) if lv is not None else False
    if follow_tail and total:
        sel = total - 1
    if lv is not None and sel != getattr(lv, "selected", None):
        lv.selected = sel
    entry = items[sel] if total else None

    pane = getattr(lv, "pane", "list") if lv is not None else "list"
    if pane not in ("list", "detail"):
        pane = "list"
    if help_open:
        pane = "detail"

    left_w, right_w = split_panes(width, fallback=(36, 46))
    extra_rows = (1 if search_mode else 0) + (1 if status_message else 0)
    vh = max(4, viewport_rows() - extra_rows)

    if help_open:
        from ._view_common import help_panel_rows

        content_rows = help_panel_rows(
            _KEYMAP, right_w, key_style=_S_WARN, group_style=_S_TIME,
            desc_style=_S_NAME, sep_style=_S_SEP,
        )
    elif pane_mode == "messages":
        content_rows = use_memo(lambda: _message_rows(messages, right_w), (id(messages), len(messages), right_w))
    elif pane_mode == "projections":
        content_rows = use_memo(
            lambda: _projection_rows(projections, right_w), (id(projections), len(projections), right_w),
        )
    elif pane_mode == "verify":
        content_rows = use_memo(
            lambda: _verify_rows(lv, right_w),
            (getattr(lv, "verify_ok", None), getattr(lv, "verify_text", ""), right_w),
        )
    else:
        content_rows = use_memo(lambda: _event_detail_rows(entry, right_w), (id(entry), right_w))
    total_content = len(content_rows)
    cursor, scroll = resolve(
        getattr(lv, "cursor", 0) or 0, getattr(lv, "scroll", 0) or 0,
        total_content, max(1, vh),
    )
    if lv is not None:
        lv.cursor = cursor
        lv.scroll = scroll

    _pane_state = PaneState(
        lambda: getattr(lv, "cursor", 0) or 0,
        lambda v: setattr(lv, "cursor", v),
        lambda: getattr(lv, "scroll", 0) or 0,
        lambda v: setattr(lv, "scroll", v),
    )

    def _row_of(orig: int) -> int:
        try:
            return index_map.index(orig)
        except ValueError:
            return 0

    def _select_row(row: int) -> None:
        """选中某行（用户导航 / 搜索定位）——同时关闭「跟随最新」。"""
        if lv is None:
            return
        lv.selected = row
        lv.cursor = 0
        lv.scroll = 0
        lv.follow_tail = False

    def _handle(event) -> bool:
        if not visible or lv is None:
            return False
        ch = char_of(event)

        verdict = handle_search_input(event, lv, max_len=SEARCH_QUERY_MAX)
        if verdict == "cancel":
            lv.search_mode = False
            lv.search_query = ""
            return True
        if verdict == "run":
            run_search(lv, entries, getattr(lv, "search_query", "") or "",
                       _logs_search_text, on_first=lambda i: _select_row(_row_of(i)),
                       label="匹配")
            return True
        if verdict == "consume":
            return True

        if _is_logs_close_key(event):
            if getattr(lv, "help_open", False):
                lv.help_open = False
                lv.pane = "list"
                lv.cursor = 0
                lv.scroll = 0
                return True
            _close_view(model, lv)
            return True

        if event.kind == "char":
            if ch == "?":
                lv.help_open = not bool(lv.help_open)
                lv.pane = "detail" if lv.help_open else "list"
                lv.cursor = 0
                lv.scroll = 0
                return True
            if ch == "/":
                lv.search_mode = True
                lv.search_query = getattr(lv, "search_pattern", "") or ""
                return True
            if ch in ("n", "N", "p") and (getattr(lv, "search_pattern", "") or ""):
                jump_match(lv, 1 if ch == "n" else -1,
                           on_jump=lambda i: _select_row(_row_of(i)))
                return True
            if ch == "f":
                if pattern and matches:
                    lv.search_filter = not bool(lv.search_filter)
                    lv.status_message = "过滤开启" if lv.search_filter else "过滤关闭"
                else:
                    lv.status_message = "过滤需先搜索且有匹配"
                return True
            if ch == "v":
                # ★ 同批连续按键：读实时 pane_mode（而非渲染期闭包）——
                #   同一输入批内到达的多次 ``v`` 才能逐次前进（闭包值在
                #   下一帧渲染前恒定 → 修复前连按 v 只切一次）。
                current_mode = str(getattr(lv, "pane_mode", "event") or "event")
                if current_mode not in PANE_MODES:
                    current_mode = "event"
                new_mode = PANE_MODES[(PANE_MODES.index(current_mode) + 1) % len(PANE_MODES)]
                lv.pane_mode = new_mode
                lv.cursor = 0
                lv.scroll = 0
                lv.status_message = f"右栏：{pane_label(new_mode)}"
                return True
            if ch == "F":
                lv.follow_tail = not bool(getattr(lv, "follow_tail", True))
                if lv.follow_tail and total:
                    lv.selected = total - 1
                    lv.cursor = 0
                    lv.scroll = 0
                lv.status_message = "跟随最新：开" if lv.follow_tail else "跟随最新：关"
                return True
            if ch == "r":
                lv.refresh_seq += 1
                lv.status_message = "已刷新"
                return True

        if pane == "list":
            if event.kind == "enter" or (event.kind == "char" and ch == "l"):
                lv.pane = "detail"
                lv.help_open = False
                return True
            return False

        if event.kind == "char" and ch == "h":
            if getattr(lv, "help_open", False):
                lv.help_open = False
                return True
            lv.pane = "list"
            return True
        if handle_nav(event, _pane_state, total_content, max(1, vh)):
            return True
        return False

    # 实时刷新：渲染后调用装配注入的刷新器（``model.logs_refresher``）——
    # 会话日志签名变化（新事件 / 消息增删）时重建并写回 ``model.logs_view``，
    # 签名未变则零成本；deps=None → 每帧执行（30Hz，签名检查 O(事件数)）。
    # 视图层不依赖 core 层（刷新逻辑经注入的 callable 承担），因此 F12 在
    # AI 流式输出期间打开同样自动跟进。
    _refresher = getattr(model, "logs_refresher", None)
    use_effect(
        (lambda: _refresher()) if (callable(_refresher) and visible) else None,
        None,
    )
    use_input(_handle, visible)
    use_modal_scope(visible)

    if not visible:
        return empty_modal_frame()

    matched_set = set(matches) if (pattern and matches) else set()
    cur_match_entry = -1
    if matched_set and 0 <= getattr(lv, "search_idx", -1) < len(matches):
        cur_match_entry = matches[lv.search_idx]

    def _render_left(item, idx, is_sel):
        e = item if isinstance(item, dict) else {}
        orig_idx = int(e.get("_index", idx))
        kind = str(e.get("type", ""))
        color = _kind_color(kind)
        runs = [
            StyledRun("\u25b6 " if is_sel else "  ", _S_SEL_MARK if is_sel else None),
            StyledRun(f"#{e.get('seq', 0):<4}", _S_SEQ),
            StyledRun(f"{_kind_icon(kind)} ", color),
            StyledRun(f"{format_time(e.get('time'))} ", _S_TIME),
            StyledRun(str(e.get("summary", "")), color),
        ]
        if left_w > 0:
            runs = truncate_runs(runs, left_w)
        bg = None
        if orig_idx == cur_match_entry:
            bg = _S_MATCH_CUR_BG
        elif orig_idx in matched_set:
            bg = _S_MATCH_BG
        elif is_sel:
            bg = _S_SEL_BG
        if bg is not None:
            runs = [StyledRun(r.text, (r.style or Style()).merge(bg)) for r in runs]
        return h(TEXT, {"styled": runs, "height": 1, "key": f"lg-{idx}"})

    display_items = [{**e, "_index": index_map[idx]} for idx, e in enumerate(items)]

    ledger = h(ListView, {
        "items": display_items,
        "height": vh,
        "width": left_w,
        "cursor": sel if total else 0,
        "renderItem": _render_left,
        "onNavigate": lambda idx: _select_row(int(idx)),
        "focus": visible and pane == "list" and not search_mode,
    })

    right_children: list = []
    window = content_rows[scroll:scroll + max(1, vh)]
    for i, runs in enumerate(window):
        abs_idx = scroll + i
        is_cur = pane == "detail" and abs_idx == cursor
        line_runs = runs if runs else [StyledRun(" ", None)]
        if is_cur:
            line_runs = [
                StyledRun(r.text, (r.style or Style()).merge(_S_INSP_BG)) for r in line_runs
            ]
        right_children.append(h(TEXT, {
            "styled": line_runs, "height": 1, "key": f"lg-f-{abs_idx}",
        }))

    if search_mode:
        hint = "  输入搜索词 · Enter 执行 · Esc 取消"
    elif help_open:
        hint = "  帮助面板 · ? / Esc 关闭"
    elif pane == "detail":
        hint = "  jk 滚动 · h 列表 · v 模式 · r 刷新 · ? 帮助 · Esc 关闭"
    else:
        hint = "  \u2191\u2193/jk 选择 · Enter 详情 · v 模式 · F 跟随 · r 刷新 · / 搜索 · ? 帮助 · Esc/F12 关闭"
    segs = [f" · {total} 事件", f" · {len(messages)} 消息", f" · 右栏 {pane_label(pane_mode)}"]
    if follow_tail:
        segs.append(" · 跟随")
    if filter_on and pattern:
        segs.append(f" · 过滤 {len(matches)}/{len(entries)}")
    header_runs = build_header_runs(
        "\u258d\U0001f5d2 会话日志", _S_TITLE, segs, hint, width,
        hint_style=_S_HINT, sep_style=_S_SEP,
    )

    children: list = [
        h(TEXT, {"styled": header_runs, "height": 1, "key": "lg-header"}),
        h(Row, None, [
            ledger,
            h(TEXT, {"children": "\u2502", "style": _S_SEP, "height": 1}),
            h(Column, {"width": right_w}, right_children),
        ]),
    ]
    parts: list = []
    if pattern:
        n = len(matches)
        idx = getattr(lv, "search_idx", -1)
        parts.append(f"/{pattern}  {(idx + 1) if 0 <= idx < n else 0}/{n}")
    verify_ok = getattr(lv, "verify_ok", None)
    if verify_ok is True:
        parts.append("\u2713 一致")
    elif verify_ok is False:
        parts.append("\u2717 不一致")
    sruns = status_runs(parts, style=_S_STATUS, message=status_message,
                        message_style=_S_OK, width=width)
    if sruns:
        children.append(h(TEXT, {"styled": sruns, "height": 1, "key": "lg-status"}))
    if search_mode:
        q = getattr(lv, "search_query", "") or ""
        children.append(h(TEXT, {
            "children": f"/{q}\u258f", "style": _S_PROMPT, "height": 1, "key": "lg-search",
        }))
    return h(Column, None, children)
