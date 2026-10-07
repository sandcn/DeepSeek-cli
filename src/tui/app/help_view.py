"""help_view — HelpView 帮助速查视图（模态全屏视图，2026-10-07）。

**F1** 或 **Ctrl+/** 打开/关闭：App 在 ``model.fullscreen == "help"`` 时经
全屏视图注册表 **整屏只渲染本组件**（消息区/顶部标题栏/状态栏/输入区全部
不显示），关闭后恢复完整聊天界面。

布局（单栏滚动）：
  - 头部行：标题 + 统计（命令数/快捷键数）+ 操作提示 + 搜索状态 +
    ``─`` 填充至满宽；
  - 内容区：分组命令列表（分组标题行带折叠指示符 ▾/▸ + 命令行，名称列对齐、
    描述分色、别名弱化）+ 空行 + 快捷键速查（两列按显示宽度对齐）；视口按
    终端高度自适应，滚动窗口只渲染可见行；
  - 光标行整行背景高亮（vim cursorline 语义）。

键盘：
  - ↑↓/jk 移动 · PgUp/PgDn 翻页 · Home/End 或 g/G 首末；
  - **Enter / 空格** 折叠/展开光标所在分组（分组标题行）；
  - **/** 进入搜索输入 · 输入中 Backspace 删除 · Enter 确认 · Esc 取消 ·
    确认后 **n / N** 在匹配行间前后跳转（环绕）；
  - Esc / Ctrl+H / F1 / Ctrl+/ 关闭（其余按键被模态吞掉，不落入输入缓冲）。

数据源：核心命令插件注册表（``<command_core>._iter_help_commands``——分组/
描述/别名）与 ``SHORTCUT_ROWS``（快捷键单一真源）——与 ``/help`` 文本输出
同源，避免两处漂移。依赖约束：仅依赖 app 同层（_modal_view）与 ink 框架
（Layer 0/1）；核心命令数据经函数内延迟 import（避免模块级循环依赖）。
"""

from __future__ import annotations

from src.tui.core.style import Style
from src.tui.ink import TEXT, Column, StyledRun, h, use_input, use_memo, use_state
from src.tui.ink.helpers import truncate_runs
from src.tui._screen import wcswidth_simple

from ._modal_view import empty_modal_frame, is_modal_close_key, use_modal_scope

__all__ = ["HelpView", "_help_rows", "_help_sections"]

# ── 样式（静态色——浏览界面，不呼吸，diff 零输出） ──
_S_TITLE = Style(fg=45, bold=True)     # 视图标题（亮青加粗）
_S_HINT = Style(fg=242)                # 提示/统计（暗灰）
_S_SEP = Style(fg=238)                 # 分隔线（深灰）
_S_GROUP = Style(fg=110, bold=True)    # 分组标题（浅蓝加粗）
_S_CMD = Style(fg=45)                  # 命令名（亮青）
_S_DESC = Style(fg=252)                # 描述/说明（亮白）
_S_ALIAS = Style(fg=242)               # 别名（暗灰）
_S_KEY = Style(fg=214)                 # 快捷键键名（橙黄）
_S_CUR_BG = Style(bg=237)              # 光标行背景
_S_MATCH = Style(fg=0, bg=221)         # 搜索匹配高亮（黑字金底）
_S_SEARCH = Style(fg=221)              # 搜索输入态文本（金）

#: 命令名称列最小/最大宽度（与 /help 文本输出同口径）。
_CMD_COL_MIN = 8
_CMD_COL_MAX = 20
#: 头部预留行数（视图内容可见行 = 终端高 - 头部 1 行）。
_VIEWPORT_RESERVED = 1


def _viewport_rows() -> int:
    """内容区可见行数（终端高度自适应；无高度上下文回退 16）。"""
    try:
        from src.tui._screen import TerminalWidthCache

        h_ = TerminalWidthCache.get_default().get_height()
        return max(4, int(h_) - _VIEWPORT_RESERVED)
    except Exception:
        return 16


def _group_header(label: str, width: int, marker: str = "") -> list:
    """分组标题行：``── [marker] 标题 `` + ``─`` 填充至满宽。

    Args:
        label: 分组显示名。
        width: 行宽预算。
        marker: 折叠指示符（``▾`` 展开 / ``▸`` 折叠；空串不显示）。
    """
    text = f"\u2500\u2500 {marker}{label} " if marker else f"\u2500\u2500 {label} "
    pad = max(0, width - _disp_len(text)) if width > 0 else 0
    runs = [StyledRun(text, _S_GROUP)]
    if pad:
        runs.append(StyledRun("\u2500" * pad, _S_SEP))
    return runs


def _disp_len(text: str) -> int:
    """文本显示宽度（东亚宽/全角按 2 列）。"""
    try:
        return wcswidth_simple(text)
    except Exception:
        return len(text)


def _sec(kind: str, group: str, text: str, runs: list) -> dict:
    """构建一个帮助行段（结构化：类型/所属分组/纯文本/渲染 runs）。"""
    return {"kind": kind, "group": group, "text": text, "runs": runs}


def _help_sections(width: int) -> list:
    """构建帮助速查全量行段（结构化）。

    Args:
        width: 行宽预算（>0 时每行截断至该宽度，保持行级 diff 宽度不变量）。

    Returns:
        行段列表——每项 ``{"kind", "group", "text", "runs"}``；
        ``kind`` ∈ ``spacer`` / ``group_header`` / ``cmd`` / ``shortcut``。
    """
    from src.core.internal.commands._command_core import (
        GROUP_LABELS,
        GROUP_ORDER,
        SHORTCUT_ROWS,
        _iter_help_commands,
    )

    entries = _iter_help_commands()
    groups: dict = {}
    for group, name, desc, aliases in entries:
        groups.setdefault(group, []).append((name, desc, aliases))

    sections: list = [_sec("spacer", "", " ", [StyledRun(" ", None)])]
    if groups:
        order = [g for g in GROUP_ORDER if g in groups]
        order += [g for g in sorted(groups) if g not in GROUP_ORDER]
        cmd_w = max(len("/" + n) for items in groups.values() for n, _, _ in items)
        cmd_w = max(_CMD_COL_MIN, min(cmd_w, _CMD_COL_MAX))
        for group in order:
            label = GROUP_LABELS.get(group, group)
            sections.append(_sec(
                "group_header", group, label,
                _group_header(label, width, "\u25be "),
            ))
            for name, desc, aliases in sorted(groups[group], key=lambda t: t[0]):
                cmd = "/" + name
                pad = " " * (cmd_w - len(cmd) + 2)
                runs = [
                    StyledRun("  " + cmd, _S_CMD),
                    StyledRun(pad, None),
                    StyledRun(desc, _S_DESC),
                ]
                if aliases:
                    runs.append(StyledRun(
                        "  " + " ".join("/" + a for a in aliases), _S_ALIAS,
                    ))
                sections.append(_sec(
                    "cmd", group, "".join(r.text for r in runs), runs,
                ))
        pad_exit = " " * (cmd_w - len("exit") + 2)
        exit_runs = [
            StyledRun("  exit", _S_CMD),
            StyledRun(pad_exit, None),
            StyledRun("退出", _S_DESC),
        ]
        sections.append(_sec(
            "cmd", "general", "".join(r.text for r in exit_runs), exit_runs,
        ))
    else:
        empty_runs = [StyledRun("  (暂无可用命令)", _S_HINT)]
        sections.append(_sec("cmd", "", "(暂无可用命令)", empty_runs))

    sections.append(_sec("spacer", "", " ", [StyledRun(" ", None)]))
    sections.append(_sec(
        "group_header", "shortcuts", "快捷键",
        _group_header("快捷键", width, "\u25be "),
    ))
    key_w = max((len(k) for row in SHORTCUT_ROWS for k, _ in row), default=0)
    left_w = 0
    for row in SHORTCUT_ROWS:
        key, desc = row[0]
        left_w = max(left_w, key_w + 3 + _disp_len(desc))
    for row in SHORTCUT_ROWS:
        runs: list = []
        for idx, (key, desc) in enumerate(row):
            pad = " " * (key_w - len(key) + 3)
            if idx == 0:
                gap = left_w - (key_w + 3 + _disp_len(desc)) + 2
                runs.append(StyledRun("  ", None))
                runs.append(StyledRun(key, _S_KEY))
                runs.append(StyledRun(pad, None))
                runs.append(StyledRun(desc, _S_DESC))
                runs.append(StyledRun(" " * max(2, gap), None))
            else:
                runs.append(StyledRun(key, _S_KEY))
                runs.append(StyledRun(pad, None))
                runs.append(StyledRun(desc, _S_DESC))
        sections.append(_sec(
            "shortcut", "shortcuts", "".join(r.text for r in runs), runs,
        ))

    if width > 0:
        for sec in sections:
            sec["runs"] = truncate_runs(sec["runs"], width) if sec["runs"] else sec["runs"]
    # 稳定行标识（供渲染 key——不随可见列表下标漂移，避免 id() 复用风险）
    for idx, sec in enumerate(sections):
        sec["idx"] = idx
    return sections


def _help_rows(width: int) -> list:
    """构建帮助速查全量内容行（``list[list[StyledRun]]``，兼容旧接口）。

    与 ``_help_sections`` 同源（后者为结构化视图，供搜索/折叠使用）。
    """
    return [sec["runs"] for sec in _help_sections(width)]


def _visible_sections(sections: list, collapsed, query: str) -> list:
    """按折叠集合与搜索关键词过滤行段。

    - 搜索（``query`` 非空）：只保留 ``text`` 含关键词的行（不区分大小写），
      此时忽略折叠（保证匹配项可见）；
    - 无搜索：隐藏被折叠分组下的 ``cmd`` / ``shortcut`` 行（分组标题仍显示，
      指示符变为 ``▸``）。
    """
    q = (query or "").strip().lower()
    out: list = []
    for sec in sections:
        kind = sec["kind"]
        group = sec["group"]
        if q:
            if q in sec["text"].lower():
                out.append(sec)
            continue
        if kind in ("cmd", "shortcut") and group in collapsed:
            continue
        if kind == "group_header" and group in collapsed:
            # 折叠指示符 ▾ → ▸（浅拷贝，不污染 sections 缓存）
            runs = list(sec["runs"])
            if runs:
                runs[0] = StyledRun(
                    runs[0].text.replace("\u25be", "\u25b8"), runs[0].style,
                )
            out.append({**sec, "runs": runs})
            continue
        out.append(sec)
    return out


def _is_help_close_key(event) -> bool:
    """帮助视图关闭键：Esc / Ctrl+H / F1 / Ctrl+/。"""
    if is_modal_close_key(event):
        return True
    kind = getattr(event, "kind", "")
    if kind == "f1":
        return True
    return kind == "ctrl_key" and getattr(event, "char", "") == "\x1f"


def HelpView(props) -> object:
    """帮助速查视图组件（模态全屏视图；App 按 FULLSCREEN_VIEWS 整屏渲染）。"""
    model = props["model"]
    width = props.get("width", 0) or 0
    visible = getattr(model, "fullscreen", "") == "help"

    sections = use_memo(lambda: _help_sections(width), (width,))
    collapsed, set_collapsed = use_state(frozenset())
    query, set_query = use_state("")
    searching, set_searching = use_state(False)
    search_buf, set_search_buf = use_state("")
    cursor, set_cursor = use_state(0)
    scroll, set_scroll = use_state(0)

    rows = _visible_sections(sections, collapsed, query)
    total = len(rows)
    vh = max(1, _viewport_rows())

    # 渲染期局部归一化（不写 state——渲染保持纯函数；真实状态由事件处理器钳制）
    cur = max(0, min(int(cursor or 0), total - 1)) if total else 0
    max_scroll = max(0, total - vh)
    off = max(0, min(int(scroll or 0), max_scroll))
    if cur < off:
        off = cur
    elif cur >= off + vh:
        off = cur - vh + 1

    def _move_to(new_cursor: int) -> None:
        new_cursor = max(0, min(new_cursor, total - 1)) if total else 0
        set_cursor(new_cursor)
        if new_cursor < off:
            set_scroll(new_cursor)
        elif new_cursor >= off + vh:
            set_scroll(new_cursor - vh + 1)

    def _jump_match(step: int) -> None:
        """在可见行中跳到下一个/上一个匹配（环绕）。"""
        if not query or total <= 0:
            return
        q = query.lower()
        for delta in range(1, total + 1):
            idx = (cur + delta * step) % total
            if q in rows[idx]["text"].lower():
                _move_to(idx)
                return

    def _toggle_collapse() -> None:
        """折叠/展开光标所在分组（仅当光标在分组标题行）。"""
        if total <= 0:
            return
        sec = rows[cur]
        if sec["kind"] != "group_header":
            return
        group = sec["group"]
        if not group:
            return
        if group in collapsed:
            set_collapsed(lambda c: frozenset(g for g in c if g != group))
        else:
            set_collapsed(lambda c: frozenset(set(c) | {group}))

    def _handle(event) -> bool:
        if not visible:
            return False
        kind = getattr(event, "kind", "")
        ch = getattr(event, "char", "") or ""
        # ── 搜索输入态（独占按键；其余键吞掉） ──
        if searching:
            if kind == "escape" or (kind == "ctrl_key" and ch == "\x1b"):
                set_searching(False)
                set_search_buf("")
                return True
            if kind == "enter":
                set_query(search_buf)
                set_searching(False)
                set_cursor(0)
                set_scroll(0)
                return True
            if kind == "backspace":
                set_search_buf(lambda b: b[:-1])
                return True
            if kind == "char" and ch and ch.isprintable():
                set_search_buf(lambda b: b + ch)
                return True
            return True
        if _is_help_close_key(event):
            model.fullscreen = ""
            return True
        if kind == "char" and ch == "/":
            set_searching(True)
            set_search_buf(query)
            return True
        if kind == "char" and ch == "n" and query:
            _jump_match(1)
            return True
        if kind == "char" and ch == "N" and query:
            _jump_match(-1)
            return True
        if kind == "enter" or (kind == "char" and ch == " "):
            _toggle_collapse()
            return True
        if kind == "arrow_up" or (kind == "char" and ch == "k"):
            _move_to(cur - 1)
            return True
        if kind == "arrow_down" or (kind == "char" and ch == "j"):
            _move_to(cur + 1)
            return True
        if kind == "page_up":
            _move_to(cur - vh)
            return True
        if kind == "page_down":
            _move_to(cur + vh)
            return True
        if kind == "home" or (kind == "char" and ch == "g"):
            _move_to(0)
            return True
        if kind == "end" or (kind == "char" and ch == "G"):
            _move_to(total - 1)
            return True
        return False

    use_input(_handle, visible)
    # 模态全屏视图声明：visible 期间未消费按键被 input router 吞掉（字符/Enter
    # 不落入输入缓冲）；关闭后（visible=False）hook 不激活零影响。
    use_modal_scope(visible)

    if not visible:
        return empty_modal_frame()

    # ── 头部（标题 + 统计 + 提示 + ─ 填充至满宽） ──
    cmd_count = sum(1 for sec in sections if sec["kind"] == "cmd")
    shortcut_count = _shortcut_count()
    if searching:
        header_runs = [
            StyledRun("\u258d帮助速查", _S_TITLE),
            StyledRun("  搜索: ", _S_HINT),
            StyledRun(search_buf + "\u258f", _S_SEARCH),
            StyledRun("  Enter 确认 · Esc 取消", _S_HINT),
        ]
    else:
        match_hint = ""
        if query:
            match_count = sum(1 for sec in rows if query.lower() in sec["text"].lower())
            match_hint = f"  /{query} ({match_count} 匹配, n/N 跳转)"
        header_runs = [
            StyledRun("\u258d帮助速查", _S_TITLE),
            StyledRun(
                f" \u00b7 {cmd_count} \u547d\u4ee4 \u00b7 {shortcut_count} \u5feb\u6377\u952e",
                _S_HINT,
            ),
            StyledRun(match_hint, _S_SEARCH if query else _S_HINT),
            StyledRun(
                "  \u2191\u2193/jk \u9009\u62e9 \u00b7 Enter \u6298\u53e0 \u00b7 / \u641c\u7d22"
                " \u00b7 Esc \u5173\u95ed",
                _S_HINT,
            ),
        ]
    if width > 0:
        header_runs = truncate_runs(header_runs, width)
        used = sum(getattr(r, "width", 1) for r in header_runs)
        pad = width - used
        if pad > 1:
            header_runs.append(StyledRun(" " + "\u2500" * (pad - 1), _S_SEP))

    children: list = [
        h(TEXT, {"styled": header_runs, "height": 1, "key": "hv-header"}),
    ]
    window = rows[off:off + vh]
    for i, sec in enumerate(window):
        abs_idx = off + i
        line_runs = _highlight_matches(sec["runs"], query) if query else sec["runs"]
        if not line_runs:
            line_runs = [StyledRun(" ", None)]
        if abs_idx == cur:
            line_runs = [
                StyledRun(r.text, (r.style or Style()).merge(_S_CUR_BG))
                for r in line_runs
            ]
        children.append(h(TEXT, {
            "styled": line_runs, "height": 1, "key": f"hv-{sec['idx']}",
        }))
    return h(Column, None, children)


def _highlight_matches(runs: list, query: str) -> list:
    """在 runs 中高亮搜索关键词（大小写不敏感，保序拆分 run）。

    无匹配/空关键词时原样返回（同引用，零重建）。
    """
    q = (query or "").strip()
    if not q:
        return runs
    lower_q = q.lower()
    out: list = []
    for run in runs:
        text = run.text
        if not text:
            out.append(run)
            continue
        lower_text = text.lower()
        idx = lower_text.find(lower_q)
        if idx < 0:
            out.append(run)
            continue
        pos = 0
        while idx >= 0:
            if idx > pos:
                out.append(StyledRun(text[pos:idx], run.style))
            out.append(StyledRun(text[idx:idx + len(q)], _S_MATCH))
            pos = idx + len(q)
            idx = lower_text.find(lower_q, pos)
        if pos < len(text):
            out.append(StyledRun(text[pos:], run.style))
    return out


def _shortcut_count() -> int:
    """快捷键条目数（头部统计用；数据源与 /help 同源）。"""
    from src.core.internal.commands._command_core import SHORTCUT_ROWS

    return sum(len(row) for row in SHORTCUT_ROWS)
