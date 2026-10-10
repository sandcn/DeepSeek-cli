"""changes_view — ChangesView 文件变更审查器视图（模态全屏视图，2026-10）。

``/changes`` 命令（或 **F11**）打开：App 在 ``model.fullscreen == "changes"``
时经全屏视图注册表**整屏只渲染本组件**，关闭后恢复完整聊天界面。视图审查
文件沙盒中被修改过的文件（diff + 回滚），并浏览沙盒的历史与统计。

布局（React Ink 左右布局）：
  - 左栏「文件列表」：变更标签（新建/删除/修改/无变化）+ 路径 + 修改次数；
    左栏模式（``t`` 目录树 / ``m`` 消息维度 / 再按回文件列表）：
      * **file**（默认）文件列表；
      * **tree** 按目录分组的树（组头行不可选）；
      * **message** 按消息索引分组（消息 N · K 文件 · M 次修改）。
  - 右栏「详情」：按 ``d`` 在 **diff**（差异）与 **history**（单文件历史
    时间线）之间切换；``i`` 打开 **stats**（统计区块）；``r`` 打开
    **preview**（回滚预览）。

键盘：
  - 左栏：↑↓/jk 选择 · Enter/l 查看 · g/G 首末 · ``t`` 目录树 · ``m`` 消息
    维度 · ``s`` 排序 · ``T`` 类型过滤；
  - 右栏：jk/↑↓ 滚动 · h 返回左栏；
  - 操作：``x`` 回滚该文件（再按确认）· ``u`` 撤销回滚 · ``X`` 回滚全部
    （再按确认）· ``R`` 回滚到消息索引 · ``r`` 回滚预览；
  - 复制/导出：``y`` 复制路径 · ``c`` 复制差异 · ``w`` 导出 Markdown ·
    ``W`` 导出 JSON；
  - 通用：``/`` 搜索 · ``n``/``N``/``p`` 定位 · ``f`` 过滤 · ``?`` 帮助 ·
    Esc/Ctrl+H 关闭。

数据源：命令线程构建 ``model.changes_view`` 的 entries / messages / sections
（``core.commands._sandbox_cmd``）；回滚等操作经 ``applied_seq`` 回传给命令
线程执行（保持数据层与 UI 线程分离）。视图渲染期经 ``model.sandbox_refresher``
实时刷新（沙盒数据签名变化时重建）——流式输出期间打开同样自动跟进。
"""

from __future__ import annotations

from src.tui.core.style import Style
from src.tui.ink import TEXT, Column, Row, StyledRun, h, use_input
from src.tui.ink.helpers import truncate_runs, wrap_runs_by_width
from src.tui.ink.hooks import use_effect, use_memo
from src.tui.ink.widgets.listview import ListView

from ._inspector_pane import PaneState, handle_nav, resolve
from ._modal_view import (
    close_fullscreen_view,
    empty_modal_frame,
    is_modal_close_key,
    use_modal_scope,
)
from ._view_common import (
    SEARCH_QUERY_MAX,
    build_header_runs,
    char_of,
    handle_search_input,
    jump_match,
    pane_divider,
    run_search,
    split_panes,
    status_runs,
    viewport_rows,
)
from .sandbox_common import (
    MAX_DIFF_ROWS,
    SORT_MODES,
    TYPE_FILTERS,
    S_GROUP,
    S_HINT,
    S_INSP_BG,
    S_LABEL,
    S_MATCH_BG,
    S_MATCH_CUR_BG,
    S_META,
    S_OK,
    S_PATH,
    S_PROMPT,
    S_SEP,
    S_SEL_BG,
    S_SEL_MARK,
    S_STATUS,
    S_TAG,
    S_TITLE,
    S_WARN,
    change_search_text,
    change_tag_style,
    diff_rows,
    entry_sort_key,
    file_entry_signature,
    fmt_time,
    history_rows,
    is_help_char,
    line_delta,
    message_detail_rows,
    message_search_text,
    message_signature,
    record_signature,
    resync_search,
    sections_signature,
    sort_mode_label,
    stats_rows,
    sync_search_matches,
)

__all__ = [
    "ChangesView",
    "_change_search_text",
    "_message_search_text",
    "_detail_rows",
    "_detail_deps",
    "_preview_rows",
    "_message_detail_rows",
    "_tree_items",
    "_type_match",
    "_base_ordered",
    "_history_deps",
    "_message_deps",
    "_preview_deps",
    "_panel_deps",
]

_KEYMAP = [
    {"group": "浏览", "keys": "↑↓/jk", "desc": "选择文件"},
    {"group": "浏览", "keys": "l/Enter", "desc": "查看详情"},
    {"group": "浏览", "keys": "h", "desc": "返回文件列表"},
    {"group": "模式", "keys": "t", "desc": "目录树视图"},
    {"group": "模式", "keys": "m", "desc": "消息维度视图"},
    {"group": "模式", "keys": "s", "desc": "排序（路径/次数/最近/索引）"},
    {"group": "模式", "keys": "d", "desc": "右栏：差异 / 历史时间线"},
    {"group": "模式", "keys": "i", "desc": "统计面板"},
    {"group": "操作", "keys": "x", "desc": "回滚该文件（再按确认）"},
    {"group": "操作", "keys": "u", "desc": "撤销回滚"},
    {"group": "操作", "keys": "X", "desc": "回滚全部（再按确认）"},
    {"group": "操作", "keys": "R", "desc": "回滚到消息索引"},
    {"group": "操作", "keys": "r", "desc": "回滚预览"},
    {"group": "操作", "keys": "y", "desc": "复制文件路径"},
    {"group": "操作", "keys": "c", "desc": "复制差异"},
    {"group": "操作", "keys": "w/W", "desc": "导出变更报告 md/json"},
    {"group": "搜索", "keys": "/", "desc": "搜索"},
    {"group": "搜索", "keys": "n/N/p", "desc": "下一/上一匹配"},
    {"group": "搜索", "keys": "f", "desc": "切换过滤"},
    {"group": "搜索", "keys": "T", "desc": "变更类型过滤"},
    {"group": "通用", "keys": "?", "desc": "帮助面板"},
    {"group": "通用", "keys": "Esc", "desc": "关闭视图"},
]

#: 预览模式最大渲染行数。
_MAX_PREVIEW_ROWS = 400


def _change_search_text(entry: dict) -> str:
    """文件条目搜索文本（路径 + 变更标签 + 工具）。

    单一真源在 ``sandbox_common.change_search_text``（别名保持历史引用面）。
    """
    return change_search_text(entry)


def _message_search_text(entry: dict) -> str:
    """消息条目搜索文本（消息索引 + 文件路径 + 工具）。

    单一真源在 ``sandbox_common.message_search_text``（别名保持历史引用面）。
    """
    return message_search_text(entry)


def _base_ordered(cv, entries: list, messages: list) -> tuple:
    """当前视图模式 / 排序 / 类型过滤下的**基准列表**与搜索文本函数。

    ★ 单一真源（2026-10）：渲染期与 ``s``/``T``/``t``/``m`` 切换处理共用本
    函数——切换基准列表后必须按**新基准列表**重算搜索匹配下标（旧实现只改
    模式，过滤态显示的是旧下标对应的另一条目）。
    """
    view_mode = str(getattr(cv, "view_mode", "file") or "file")
    if view_mode == "message":
        return list(messages), _message_search_text
    sort_mode = str(getattr(cv, "sort_mode", "path") or "path")
    ordered = entry_sort_key(entries, sort_mode)
    type_filter = str(getattr(cv, "type_filter", "") or "")
    if type_filter:
        ordered = [e for e in ordered if _type_match(e, type_filter)]
    return ordered, _change_search_text


def _type_match(entry: dict, flt: str) -> bool:
    """变更类型过滤匹配（``""`` = 全部；``"目录"`` = 目录类；其余按标签前缀）。"""
    if not flt:
        return True
    if not isinstance(entry, dict):
        return False
    if flt == "目录":
        return bool(entry.get("is_dir"))
    return str(entry.get("change_label", "")).startswith(flt)


def _tree_items(ordered: list, index_map: list) -> list:
    """文件条目 → 目录树行（组头不可选 + 文件行）。

    ``ordered`` 为排序后的基准列表，``index_map`` 为「视图位置 → ordered
    索引」映射；文件行携带 ``_index``（ordered 索引）供匹配高亮定位。
    """
    groups: dict = {}
    for view_pos, orig in enumerate(index_map):
        entry = ordered[orig] if 0 <= orig < len(ordered) else None
        if not isinstance(entry, dict):
            continue
        norm = str(entry.get("path", "")).replace("\\", "/")
        cut = norm.rfind("/")
        group = norm[:cut] if cut > 0 else "(根目录)"
        groups.setdefault(group, []).append((orig, entry))
    items: list = []
    for group in sorted(groups):
        members = groups[group]
        items.append({"_group": True, "name": group, "count": len(members)})
        for orig, entry in sorted(members, key=lambda p: str(p[1].get("path", ""))):
            items.append({**entry, "_index": orig})
    return items


def _detail_deps(entry, right_w: int) -> tuple:
    """``_detail_rows`` 的 useMemo 依赖（**值驱动**，避免陈旧缓存）。

    ★ 修复（2026-10）：改用 ``file_entry_signature``——修复前遗漏 ``tools``
    （同文件后续用别的工具修改时右栏工具列表显示陈旧内容）。
    """
    if not isinstance(entry, dict):
        return (None, right_w)
    return (file_entry_signature(entry), right_w)


def _detail_rows(entry: dict, right_w: int) -> list:
    """选中文件 → 差异内容行（``list[list[StyledRun]]``）。"""
    if entry is None:
        return [[StyledRun("无选中文件", S_HINT)]]
    rows: list = []
    path = str(entry.get("path", ""))
    label = str(entry.get("change_label", ""))
    header = [
        StyledRun(path, S_TITLE),
        StyledRun(f"  {label}", change_tag_style(label)),
    ]
    for line in wrap_runs_by_width(header, max(1, right_w)):
        rows.append(list(line.runs))
    tools = entry.get("tools") or []
    meta = [
        StyledRun(f"{entry.get('records', 0)} 次修改", S_META),
        StyledRun(f"  消息 {entry.get('message_index', '')}", S_META),
        StyledRun(f"  {line_delta(entry.get('before'), entry.get('after'))}", S_META),
    ]
    if tools:
        meta.append(StyledRun(f"  {'/'.join(str(t) for t in tools)}", S_META))
    for line in wrap_runs_by_width(meta, max(1, right_w)):
        rows.append(list(line.runs))
    rows.append([StyledRun("\u2500" * max(1, right_w - 1), S_SEP)])
    rows.extend(diff_rows(path, entry.get("before"), entry.get("after"), right_w))
    return rows


def _history_deps(entry, right_w: int) -> tuple:
    """``history_rows`` 的缓存依赖（文件条目签名 + 每条历史记录签名）。

    ★ 修复（2026-10）：此前只取「历史条数」——同条数下记录内容变化（该文件
    被再次修改为不同内容、回滚后再改回）不会失效，右栏历史时间线显示陈旧
    内容。现按每条记录的值签名判定。
    """
    if not isinstance(entry, dict):
        return (None, right_w)
    history = entry.get("history") or []
    return (
        file_entry_signature(entry),
        tuple(record_signature(h) for h in history),
        right_w,
    )


def _preview_deps(entry, right_w: int) -> tuple:
    """``_preview_rows`` 的缓存依赖（值驱动）。"""
    if not isinstance(entry, dict):
        return (None, right_w)
    return (file_entry_signature(entry), right_w)


def _panel_deps(entry, sections, right_w: int) -> tuple:
    """右栏内容缓存的**完整**依赖（文件 / 消息 / 历史 / 预览 / 统计 + 宽度）。

    单 memo 结构（``_content_rows``）下所有模式共用一份依赖——为避免"未使用
    模式的依赖变化导致无谓重算"与"使用模式的依赖遗漏"，各模式依赖各自完整
    计算后并列（计算均为纯值提取，开销与一帧内的字符串指纹相当）。
    """
    return (
        _detail_deps(entry, right_w),
        _message_deps(entry, right_w),
        _history_deps(entry, right_w),
        _preview_deps(entry, right_w),
        sections_signature(sections),
    )


def _preview_rows(entry: dict, right_w: int) -> list:
    """回滚预览行（将写入的目标内容）。"""
    if entry is None:
        return [[StyledRun("无选中文件", S_HINT)]]
    rows: list = [[StyledRun("\u258d 回滚预览（将写入的内容）", S_TITLE)], [
        StyledRun("\u2500" * max(1, right_w - 1), S_SEP),
    ]]
    path = str(entry.get("path", ""))
    before = entry.get("before")
    after = entry.get("after")
    rows.append([StyledRun(path, S_PATH)])
    cur_text = "(文件不存在)" if after is None else f"{len(str(after).splitlines())} 行"
    tgt_text = "(删除)" if before is None else f"{len(str(before).splitlines())} 行"
    rows.append([
        StyledRun(f"当前 {cur_text} \u2192 目标 {tgt_text}", S_META),
    ])
    rows.append([StyledRun(" ", None)])
    if before is None:
        rows.append([StyledRun("目标状态：文件将被删除", S_WARN)])
        return rows
    lines = str(before).split("\n")
    for i, line in enumerate(lines):
        if i >= _MAX_PREVIEW_ROWS:
            rows.append([StyledRun(f"\u2026 其余 {len(lines) - i} 行省略", S_HINT)])
            break
        rows.append([StyledRun(line, S_PATH)])
    return rows


def _message_deps(entry, right_w: int) -> tuple:
    """消息条目缓存依赖（值驱动，含子记录内容签名）。

    ★ 修复（2026-10）：此前依赖仅 ``(index, 变更条数, 右栏宽)``——同消息下
    文件内容变化但条数不变时右栏 diff 陈旧。
    """
    if not isinstance(entry, dict):
        return (None, right_w)
    return (message_signature(entry), right_w)


def _message_detail_rows(entry: dict, right_w: int) -> list:
    """消息条目 → 该消息下全部文件变更（委托 ``sandbox_common``）。"""
    return message_detail_rows(entry, right_w)


def _rows_plain_text(rows: list) -> str:
    """内容行 → 纯文本（复制差异用）。"""
    out: list = []
    for runs in rows or []:
        out.append("".join(getattr(r, "text", "") for r in runs))
    return "\n".join(out)


def ChangesView(props) -> object:
    """文件变更审查器视图组件（模态全屏视图）。"""
    model = props["model"]
    width = props.get("width", 0) or 0
    cv = getattr(model, "changes_view", None)
    visible = bool(cv is not None and cv.visible and not cv.done)
    entries = list(getattr(cv, "entries", None) or []) if cv is not None else []
    messages = list(getattr(cv, "messages", None) or []) if cv is not None else []
    sections = list(getattr(cv, "sections", None) or []) if cv is not None else []
    help_open = bool(getattr(cv, "help_open", False)) if cv is not None else False
    search_mode = bool(getattr(cv, "search_mode", False)) if cv is not None else False
    restore_mode = bool(getattr(cv, "restore_mode", False)) if cv is not None else False
    pattern = (getattr(cv, "search_pattern", "") or "") if cv is not None else ""
    filter_on = bool(getattr(cv, "search_filter", False)) if cv is not None else False
    matches = list(getattr(cv, "search_matches", None) or []) if cv is not None else []
    status_message = (getattr(cv, "status_message", "") or "") if cv is not None else ""
    revert_confirm = (getattr(cv, "revert_confirm", "") or "") if cv is not None else ""
    revert_all_confirm = bool(getattr(cv, "revert_all_confirm", False)) if cv is not None else False
    export_message = (getattr(cv, "export_message", "") or "") if cv is not None else ""
    view_mode = str(getattr(cv, "view_mode", "file") or "file") if cv is not None else "file"
    if view_mode not in ("file", "tree", "message"):
        view_mode = "file"
    detail_mode = str(getattr(cv, "detail_mode", "diff") or "diff") if cv is not None else "diff"
    if detail_mode not in ("diff", "history", "stats", "preview"):
        detail_mode = "diff"
    sort_mode = str(getattr(cv, "sort_mode", "path") or "path") if cv is not None else "path"
    type_filter = str(getattr(cv, "type_filter", "") or "") if cv is not None else ""

    # ── 基准列表（文件 / 消息） + 排序 + 类型过滤（与切换处理单一真源） ──
    ordered, search_text_of = _base_ordered(cv, entries, messages)

    allowed = None
    # ★ 修复（2026-10）：沙盒数据实时刷新会重建基准列表（流式输出期间工具继续
    #   写文件），旧匹配下标随之失效 → 过滤 / 高亮 / n 定位错位。渲染期同步
    #   一次（结果未变时零写回开销）。
    matches = sync_search_matches(cv, ordered, search_text_of)
    filter_on = bool(getattr(cv, "search_filter", False)) if cv is not None else False
    if filter_on and pattern and matches:
        allowed = set(matches)
    index_map = [i for i in range(len(ordered)) if allowed is None or i in allowed]
    filtered = [ordered[i] for i in index_map]

    if view_mode == "tree":
        display_items = _tree_items(ordered, index_map)
    else:
        display_items = [{**e, "_index": index_map[idx]} for idx, e in enumerate(filtered)]

    total = len(display_items)
    sel = 0
    if total:
        try:
            sel = max(0, min(int(getattr(cv, "selected", 0) or 0), total - 1))
        except (TypeError, ValueError):
            sel = 0
    if cv is not None and sel != getattr(cv, "selected", None):
        cv.selected = sel
    raw_entry = display_items[sel] if total else None
    entry = raw_entry if isinstance(raw_entry, dict) and not raw_entry.get("_group") else None

    pane = getattr(cv, "pane", "list") if cv is not None else "list"
    if pane not in ("list", "detail"):
        pane = "list"
    if help_open:
        pane = "detail"

    left_w, right_w = split_panes(width, fallback=(34, 48))
    extra_rows = (1 if search_mode else 0) + (1 if status_message else 0)
    vh = max(4, viewport_rows() - extra_rows)

    # ── 右栏内容（**单一** use_memo：hook 调用必须无条件且数量恒定）──
    # ★ 修复（2026-10）：修复前 help / stats / message / history / preview /
    #   diff 六个分支各自 ``use_memo``——按 ``?`` 打开帮助时分支切换，hook
    #   序列随之变化 → 违反 Rules of Hooks（``HookStateError: hook 类型不一致``）
    #   → 视图渲染异常，「按 ? 没有帮助面板」。现收敛为**一个** memo，把模式
    #   标志（help_open / view_mode / detail_mode）与内容签名一起纳入依赖：
    #   hook 数量恒定，切换模式即重算，未变化的模式不重算。
    def _content_rows() -> list:
        if help_open:
            from ._view_common import help_panel_rows

            return help_panel_rows(
                _KEYMAP, right_w, key_style=S_WARN, group_style=S_TAG,
                desc_style=S_PATH, sep_style=S_SEP,
            )
        if detail_mode == "stats":
            # ★ 统计面板先于「消息视图」分支——统计是全局数据，任何视图模式
            #   都适用（修复前消息模式按 ``i`` 被消息详情抢先，按键无效）。
            return stats_rows(sections, right_w)
        if view_mode == "message":
            return _message_detail_rows(entry, right_w)
        if detail_mode == "history":
            return history_rows(entry, right_w)
        if detail_mode == "preview":
            return _preview_rows(entry, right_w)
        return _detail_rows(entry, right_w)

    content_rows = use_memo(_content_rows, (
        help_open, view_mode, detail_mode,
        _panel_deps(entry, sections, right_w),
    ))
    total_content = len(content_rows)
    cursor, scroll = resolve(
        getattr(cv, "cursor", 0) or 0, getattr(cv, "scroll", 0) or 0,
        total_content, max(1, vh),
    )
    if cv is not None:
        cv.cursor = cursor
        cv.scroll = scroll

    _pane_state = PaneState(
        lambda: getattr(cv, "cursor", 0) or 0,
        lambda v: setattr(cv, "cursor", v),
        lambda: getattr(cv, "scroll", 0) or 0,
        lambda v: setattr(cv, "scroll", v),
    )

    def _row_of(orig: int) -> int:
        for i, it in enumerate(display_items):
            if isinstance(it, dict) and it.get("_index") == orig:
                return i
        return 0

    def _select_row(row: int) -> None:
        if cv is None:
            return
        cv.selected = row
        cv.cursor = 0
        cv.scroll = 0
        cv.status_message = ""

    def _apply(report: dict) -> None:
        if cv is None:
            return
        cv.applied = report
        cv.applied_seq += 1

    def _target_entry() -> dict:
        return entry if isinstance(entry, dict) else {}

    def _resync_matches() -> int:
        """基准列表变化后重算搜索匹配（值驱动，返回匹配数）。"""
        new_ordered, new_text_of = _base_ordered(cv, entries, messages)
        return resync_search(cv, new_ordered, new_text_of)

    def _with_match_status(base: str) -> str:
        """状态提示：有搜索模式时附加重算后的匹配数。"""
        if not (getattr(cv, "search_pattern", "") or ""):
            return base
        return f"{base} · 匹配 {_resync_matches()}"

    def _handle(event) -> bool:
        if not visible or cv is None:
            return False
        ch = char_of(event)

        # ── 回滚到消息索引：输入模式 ──
        if getattr(cv, "restore_mode", False):
            if event.kind == "escape":
                cv.restore_mode = False
                cv.restore_value = ""
                cv.status_message = "已取消"
                return True
            if event.kind == "backspace":
                v = getattr(cv, "restore_value", "") or ""
                cv.restore_value = v[:-1]
                return True
            if event.kind == "enter":
                raw = (getattr(cv, "restore_value", "") or "").strip()
                cv.restore_mode = False
                # ★ 修复（2026-10）：此前用 ``raw.lstrip("-").isdigit()``——负号
                #   被接受，``restore_to_message(-1)`` 会把**全部文件**恢复到最初
                #   状态（等于隐式「回滚全部」），属危险的输入校验漏洞。现仅接受
                #   非负整数（消息索引语义）。
                if raw.isdigit():
                    _apply({"action": "restore-message", "index": int(raw)})
                    cv.status_message = f"正在回滚到消息 {int(raw)}…"
                else:
                    cv.status_message = "请输入非负的消息索引（数字）"
                cv.restore_value = ""
                return True
            if event.kind == "char":
                if ch.isdigit() and len(getattr(cv, "restore_value", "") or "") < 9:
                    cv.restore_value = (getattr(cv, "restore_value", "") or "") + ch
                return True
            return True

        verdict = handle_search_input(event, cv, max_len=SEARCH_QUERY_MAX)
        if verdict == "cancel":
            cv.search_mode = False
            cv.search_query = ""
            return True
        if verdict == "run":
            run_search(cv, ordered, getattr(cv, "search_query", "") or "",
                       search_text_of, on_first=lambda i: _select_row(_row_of(i)),
                       label="匹配")
            return True
        if verdict == "consume":
            return True

        # ★ F11 = 文件变更审查器专属开关键：**打开后同样用于关闭**，且必须
        #   由本视图消费事件（否则会被 ``use_fullscreen`` 模态吞掉——既不
        #   触发关闭逻辑，也落不到快捷键回调，表现为「按 F11 关不掉」）。
        if getattr(event, "kind", "") == "f11":
            close_fullscreen_view(model, cv, "changes")
            return True

        if is_modal_close_key(event):
            if getattr(cv, "help_open", False):
                cv.help_open = False
                cv.pane = "list"
                cv.cursor = 0
                cv.scroll = 0
                return True
            close_fullscreen_view(model, cv, "changes")
            return True

        if event.kind == "char":
            if is_help_char(ch):
                cv.help_open = not bool(cv.help_open)
                cv.pane = "detail" if cv.help_open else "list"
                cv.cursor = 0
                cv.scroll = 0
                return True
            if ch == "/":
                cv.search_mode = True
                cv.search_query = getattr(cv, "search_pattern", "") or ""
                return True
            if ch in ("n", "N", "p") and (getattr(cv, "search_pattern", "") or ""):
                jump_match(cv, 1 if ch == "n" else -1,
                           on_jump=lambda i: _select_row(_row_of(i)))
                return True
            if ch == "f":
                if pattern and matches:
                    cv.search_filter = not bool(cv.search_filter)
                    cv.status_message = (
                        f"过滤开启：{len(matches)} 项" if cv.search_filter else "过滤关闭"
                    )
                else:
                    cv.status_message = "过滤需先搜索且有匹配"
                return True
            if ch == "t":
                cv.view_mode = "file" if view_mode == "tree" else "tree"
                cv.selected = 0
                cv.cursor = 0
                cv.scroll = 0
                base = "目录树视图" if cv.view_mode == "tree" else "文件列表"
                cv.status_message = _with_match_status(base)
                return True
            if ch == "m":
                cv.view_mode = "file" if view_mode == "message" else "message"
                cv.selected = 0
                cv.cursor = 0
                cv.scroll = 0
                base = "消息维度视图" if cv.view_mode == "message" else "文件列表"
                cv.status_message = _with_match_status(base)
                return True
            if ch == "s":
                idx = SORT_MODES.index(sort_mode) if sort_mode in SORT_MODES else 0
                nxt = SORT_MODES[(idx + 1) % len(SORT_MODES)]
                cv.sort_mode = nxt
                cv.selected = 0
                cv.cursor = 0
                cv.scroll = 0
                cv.status_message = _with_match_status(f"排序：{sort_mode_label(nxt)}")
                return True
            if ch == "T":
                if view_mode == "message":
                    cv.status_message = "消息视图下不适用类型过滤（按 m 回文件列表）"
                    return True
                idx = TYPE_FILTERS.index(type_filter) if type_filter in TYPE_FILTERS else 0
                nxt = TYPE_FILTERS[(idx + 1) % len(TYPE_FILTERS)]
                cv.type_filter = nxt
                cv.selected = 0
                cv.cursor = 0
                cv.scroll = 0
                cv.status_message = _with_match_status(f"类型过滤：{nxt or '全部'}")
                return True
            if ch == "d":
                if view_mode == "message":
                    cv.status_message = "消息视图下不适用历史时间线（按 m 回文件列表）"
                    return True
                cv.detail_mode = "history" if detail_mode == "history" else "diff"
                cv.cursor = 0
                cv.scroll = 0
                cv.status_message = "右栏：历史时间线" if cv.detail_mode == "history" else "右栏：差异"
                return True
            if ch == "i":
                cv.detail_mode = "stats" if detail_mode != "stats" else "diff"
                cv.pane = "detail"
                cv.cursor = 0
                cv.scroll = 0
                cv.status_message = "统计面板" if cv.detail_mode == "stats" else "右栏：差异"
                return True
            if ch == "r":
                if view_mode == "message":
                    cv.status_message = "消息视图下不适用回滚预览（按 m 回文件列表）"
                    return True
                cv.detail_mode = "preview" if detail_mode != "preview" else "diff"
                cv.pane = "detail"
                cv.cursor = 0
                cv.scroll = 0
                cv.status_message = "回滚预览" if cv.detail_mode == "preview" else "右栏：差异"
                return True
            if ch == "R":
                cv.restore_mode = True
                cv.restore_value = ""
                cv.status_message = "输入消息索引后回车回滚"
                return True
            if ch == "x" and entry is not None:
                if view_mode == "message":
                    # ★ 修复（2026-10）：消息条目没有 ``path``，此前按 ``x`` 会
                    #   进入「回滚 ''」流程（提示「再按 x 确认回滚「」」→
                    #   回滚失败）。改为提示正确入口。
                    cv.status_message = "消息视图下按 R 回滚到该消息（x 仅文件视图）"
                    return True
                path = str(entry.get("path", ""))
                if revert_confirm == path:
                    _apply({"action": "revert", "path": path})
                    # ★ 一致性修复（2026-10）：确认后立即清确认态（与流水视图
                    #   一致）——刷新器缺失时不会因残留确认态被下一次 x 误触发。
                    cv.revert_confirm = ""
                    cv.status_message = "正在回滚…"
                else:
                    cv.revert_confirm = path
                    cv.status_message = f"再按 x 确认回滚「{path}」"
                return True
            if ch == "u":
                target = _target_entry()
                path = str(target.get("path", "") or "")
                _apply({"action": "undo-revert", "path": path})
                cv.status_message = "正在撤销回滚…"
                return True
            if ch == "X":
                if revert_all_confirm:
                    _apply({"action": "revert-all"})
                    cv.status_message = "正在回滚全部…"
                    cv.revert_all_confirm = False
                else:
                    cv.revert_all_confirm = True
                    cv.status_message = "再按 X 确认回滚全部文件"
                return True
            if ch == "y" and entry is not None:
                from src.tui._screen import set_clipboard

                if view_mode == "message":
                    cv.status_message = "消息视图下无单一路径可复制（按 m 回文件列表）"
                    return True
                path = str(entry.get("path", ""))
                if path and set_clipboard(path):
                    cv.status_message = f"已复制路径：{path}"
                else:
                    cv.status_message = "复制失败"
                return True
            if ch == "c":
                from src.tui._screen import set_clipboard

                text = _rows_plain_text(content_rows)
                if text and set_clipboard(text):
                    cv.status_message = f"已复制差异（{len(text)} 字符）"
                else:
                    cv.status_message = "无可复制内容"
                return True
            if ch in ("w", "W"):
                from .sandbox_export import write_export

                fmt = "md" if ch == "w" else "json"
                try:
                    path = write_export(entries, fmt)
                    cv.export_message = f"已导出：{path}"
                except Exception:
                    cv.export_message = "导出失败"
                return True

        if pane == "list":
            if event.kind == "enter" or (event.kind == "char" and ch == "l"):
                cv.pane = "detail"
                cv.help_open = False
                return True
            return False

        if event.kind == "char" and ch == "h":
            if getattr(cv, "help_open", False):
                cv.help_open = False
                return True
            cv.pane = "list"
            return True
        if handle_nav(event, _pane_state, total_content, max(1, vh)):
            return True
        return False

    # 实时刷新：渲染期调用装配注入的刷新器（沙盒数据签名未变时零成本）。
    _refresher = getattr(model, "sandbox_refresher", None)
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
    if matched_set and 0 <= getattr(cv, "search_idx", -1) < len(matches):
        cur_match_entry = matches[cv.search_idx]

    def _render_left(item, idx, is_sel):
        e = item if isinstance(item, dict) else {}
        if e.get("_group"):
            runs = [
                StyledRun("\u25be ", S_GROUP),
                StyledRun(str(e.get("name", "")), S_GROUP),
                StyledRun(f"  ({e.get('count', 0)})", S_HINT),
            ]
            if left_w > 0:
                runs = truncate_runs(runs, left_w)
            return h(TEXT, {"styled": runs, "height": 1, "key": f"cv-{idx}"})

        orig_idx = int(e.get("_index", idx))
        if view_mode == "message":
            runs = [
                StyledRun("\u25b6 " if is_sel else "  ", S_SEL_MARK if is_sel else None),
                StyledRun(f"消息 {e.get('index', '')}", S_TITLE),
                StyledRun(f"  {e.get('count', 0)} 次", S_HINT),
                StyledRun(f"  {e.get('files', 0)} 文件", S_TAG),
                StyledRun(f"  {fmt_time(e.get('time'))}", S_META),
            ]
        else:
            path = str(e.get("path", ""))
            if view_mode == "tree":
                norm = path.replace("\\", "/")
                name = norm[norm.rfind("/") + 1:] if "/" in norm else norm
            else:
                name = path
            label = str(e.get("change_label", ""))
            runs = [
                StyledRun("\u25b6 " if is_sel else ("    " if view_mode == "tree" else "  "),
                          S_SEL_MARK if is_sel else None),
                StyledRun(f"[{label}] ", change_tag_style(label)),
                StyledRun(name, S_PATH),
                StyledRun(f"  \u00d7{e.get('records', 0)}", S_HINT),
            ]
        if left_w > 0:
            runs = truncate_runs(runs, left_w)
        bg = None
        if revert_confirm and str(e.get("path", "")) == revert_confirm:
            bg = S_MATCH_CUR_BG
        elif orig_idx == cur_match_entry:
            bg = S_MATCH_CUR_BG
        elif orig_idx in matched_set:
            bg = S_MATCH_BG
        elif is_sel:
            bg = S_SEL_BG
        if bg is not None:
            runs = [StyledRun(r.text, (r.style or Style()).merge(bg)) for r in runs]
        return h(TEXT, {"styled": runs, "height": 1, "key": f"cv-{idx}"})

    ledger = h(ListView, {
        "items": display_items,
        "height": vh,
        "width": left_w,
        "cursor": sel if total else 0,
        "renderItem": _render_left,
        "onNavigate": lambda idx: _select_row(int(idx)),
        "isSelectable": lambda it: not (isinstance(it, dict) and it.get("_group")),
        "focus": visible and pane == "list" and not search_mode and not restore_mode,
    })

    right_children: list = []
    window = content_rows[scroll:scroll + max(1, vh)]
    for i, runs in enumerate(window):
        abs_idx = scroll + i
        is_cur = pane == "detail" and abs_idx == cursor
        line_runs = runs if runs else [StyledRun(" ", None)]
        if is_cur:
            line_runs = [
                StyledRun(r.text, (r.style or Style()).merge(S_INSP_BG)) for r in line_runs
            ]
        right_children.append(h(TEXT, {
            "styled": line_runs, "height": 1, "key": f"cv-f-{abs_idx}",
        }))

    mode_label = {"file": "文件", "tree": "目录树", "message": "消息"}.get(view_mode, "文件")
    detail_label = {"diff": "差异", "history": "历史", "stats": "统计", "preview": "预览"}.get(detail_mode, "差异")
    if view_mode == "message" and detail_mode != "stats":
        # ★ 修复（2026-10）：消息视图右栏显示「该消息下全部文件变更」，此前沿用
        #   ``detail_mode`` 的「差异」标签，与实际内容不符。
        detail_label = "消息详情"
    if search_mode:
        hint = "  输入搜索词 · Enter 执行 · Esc 取消"
    elif restore_mode:
        hint = "  输入消息索引 · Enter 执行 · Esc 取消"
    elif help_open:
        hint = "  帮助面板 · ? / Esc 关闭"
    elif pane == "detail":
        hint = "  jk 滚动 · h 列表 · d 历史 · i 统计 · r 预览 · ? 帮助 · Esc 关闭"
    else:
        hint = "  \u2191\u2193/jk 选择 · Enter 差异 · x 回滚 · t 树 · m 消息 · s 排序 · w 导出 · ? 帮助"
    # ★ 修复（2026-10）：消息视图下列表单位是「条消息」——此前统一显示
    #   「N 个文件」，与左栏内容（消息索引）不符。
    unit = "条消息" if view_mode == "message" else "个文件"
    segs = [f" · {total} {unit}", f" · 视图 {mode_label}", f" · 右栏 {detail_label}"]
    if type_filter:
        segs.append(f" · 类型 {type_filter}")
    if filter_on and pattern:
        segs.append(f" · 过滤 {len(matches)}/{len(ordered)}")
    header_runs = build_header_runs(
        "\u258d\U0001f4dd 文件变更审查", S_TITLE, segs, hint, width,
        hint_style=S_HINT, sep_style=S_SEP,
    )

    children: list = [
        h(TEXT, {"styled": header_runs, "height": 1, "key": "cv-header"}),
        h(Row, None, [
            ledger,
            pane_divider(max(vh, len(right_children)), S_SEP),
            h(Column, {"width": right_w}, right_children),
        ]),
    ]

    parts: list = []
    if pattern:
        n = len(matches)
        idx = getattr(cv, "search_idx", -1)
        parts.append(f"/{pattern}  {(idx + 1) if 0 <= idx < n else 0}/{n}")
    sruns = status_runs(
        parts, style=S_STATUS, message=status_message,
        message_style=S_WARN if ("确认" in status_message or "失败" in status_message) else S_OK,
        width=width,
    )
    if sruns:
        children.append(h(TEXT, {"styled": sruns, "height": 1, "key": "cv-status"}))
    if export_message:
        children.append(h(TEXT, {
            "styled": [StyledRun(export_message, S_OK)], "height": 1, "key": "cv-export",
        }))
    if restore_mode:
        q = getattr(cv, "restore_value", "") or ""
        children.append(h(TEXT, {
            "children": f"回滚到消息: {q}\u258f", "style": S_PROMPT,
            "height": 1, "key": "cv-restore",
        }))
    if search_mode:
        q = getattr(cv, "search_query", "") or ""
        children.append(h(TEXT, {
            "children": f"/{q}\u258f", "style": S_PROMPT, "height": 1, "key": "cv-search",
        }))
    return h(Column, None, children)
