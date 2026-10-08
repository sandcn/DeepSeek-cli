"""export_view — ExportView 导出向导视图（模态全屏视图，2026-10）。

``/export``（无参数）在有活跃 ChatUI 时打开：App 在 ``model.fullscreen ==
"export"`` 时经全屏视图注册表**整屏只渲染本组件**，关闭后恢复完整聊天界面。
向导选择导出格式 / 范围 / 输出路径后执行导出。

布局（单栏表单）：
  - ``格式``（Enter 在 md / json 间切换）；
  - ``范围``（Enter 在 全部 / 仅用户 / 仅助手 间切换）；
  - ``路径``（Enter 进入输入；留空用默认时间戳名）；
  - ``执行导出``（Enter 执行）。

键盘：↑↓/jk 选择行 · Enter 执行该行动作 · Esc 关闭；路径输入态：字符输入 /
Backspace / Enter 提交 / Esc 取消。

数据源：命令线程执行导出（``build_markdown`` / JSON）并回写 ``message`` /
``error``；导出经 ``applied_seq`` 回传。
"""

from __future__ import annotations

from src.tui.core.style import Style
from src.tui.ink import TEXT, Column, StyledRun, h, use_input

from ._modal_view import empty_modal_frame, is_modal_close_key, use_modal_scope
from ._view_common import build_header_runs, char_of, viewport_rows

__all__ = ["ExportView"]

_S_TITLE = Style(fg=45, bold=True)
_S_HINT = Style(fg=242)
_S_SEP = Style(fg=238)
_S_LABEL = Style(fg=75)
_S_VALUE = Style(fg=252)
_S_SEL = Style(fg=45, bold=True)
_S_SEL_BG = Style(bg=237)
_S_OK = Style(fg=40, bold=True)
_S_ERR = Style(fg=196, bold=True)
_S_PROMPT = Style(fg=45, bold=True)
_S_FOCUS = Style(fg=214, bold=True)

_FORMAT_OPTIONS = ["md", "json"]
_SCOPE_OPTIONS = ["all", "user", "assistant"]
_SCOPE_LABEL = {"all": "全部", "user": "仅用户", "assistant": "仅助手"}

#: 表单行数（格式 / 范围 / 路径 / 执行）。
_FORM_ROWS = 4


def _cycle(options: list, current: str) -> str:
    try:
        idx = options.index(current)
    except ValueError:
        idx = -1
    return options[(idx + 1) % len(options)]


def ExportView(props) -> object:
    """导出向导视图组件（模态全屏视图）。"""
    model = props["model"]
    width = props.get("width", 0) or 0
    ev = getattr(model, "export_view", None)
    visible = bool(ev is not None and ev.visible and not ev.done)
    fmt = (getattr(ev, "format", "md") or "md") if ev is not None else "md"
    scope = (getattr(ev, "scope", "all") or "all") if ev is not None else "all"
    path = (getattr(ev, "path", "") or "") if ev is not None else ""
    message = (getattr(ev, "message", "") or "") if ev is not None else ""
    error = (getattr(ev, "error", "") or "") if ev is not None else ""
    editing = bool(getattr(ev, "editing", False)) if ev is not None else False

    sel = 0
    if ev is not None:
        try:
            sel = max(0, min(int(getattr(ev, "selected", 0) or 0), _FORM_ROWS - 1))
        except (TypeError, ValueError):
            sel = 0
        if sel != getattr(ev, "selected", None):
            ev.selected = sel

    def _apply() -> None:
        if ev is None:
            return
        ev.applied = {"action": "export", "format": fmt, "scope": scope, "path": path}
        ev.applied_seq += 1
        ev.message = "正在导出…"
        ev.error = ""

    def _handle(event) -> bool:
        if not visible or ev is None:
            return False
        ch = char_of(event)

        # ── 路径输入态 ──
        if getattr(ev, "editing", False):
            if event.kind == "escape":
                ev.editing = False
                return True
            if event.kind == "backspace":
                v = ev.path or ""
                if v:
                    ev.path = v[:-1]
                return True
            if event.kind == "enter":
                ev.editing = False
                return True
            if event.kind == "char":
                if ch and "\n" not in ch and "\r" not in ch:
                    v = ev.path or ""
                    if len(v) < 200:
                        ev.path = v + ch
                return True
            return True

        if is_modal_close_key(event):
            ev.try_set_final("cancel")
            return True

        if event.kind == "arrow_down" or (event.kind == "char" and ch == "j"):
            ev.selected = (sel + 1) % _FORM_ROWS
            return True
        if event.kind == "arrow_up" or (event.kind == "char" and ch == "k"):
            ev.selected = (sel - 1) % _FORM_ROWS
            return True
        if event.kind == "home" or (event.kind == "char" and ch == "g"):
            ev.selected = 0
            return True
        if event.kind == "end" or (event.kind == "char" and ch == "G"):
            ev.selected = _FORM_ROWS - 1
            return True
        if event.kind == "enter":
            if sel == 0:
                ev.format = _cycle(_FORMAT_OPTIONS, fmt)
            elif sel == 1:
                ev.scope = _cycle(_SCOPE_OPTIONS, scope)
            elif sel == 2:
                ev.editing = True
            else:
                _apply()
            return True
        if event.kind == "char" and ch == "e":
            _apply()
            return True
        return False

    use_input(_handle, visible)
    use_modal_scope(visible)

    if not visible:
        return empty_modal_frame()

    vh = max(6, viewport_rows())

    def _row(idx: int, label: str, value: str) -> list:
        is_sel = (idx == sel and not editing)
        runs = [
            StyledRun("\u25b6 " if is_sel else "  ", _S_SEL if is_sel else None),
            StyledRun(f"{label:<8}", _S_LABEL),
            StyledRun(value, _S_SEL if is_sel else _S_VALUE),
        ]
        if is_sel and idx < 2:
            runs.append(StyledRun("   (Enter 切换)", _S_HINT))
        elif is_sel and idx == 2:
            runs.append(StyledRun("   (Enter 编辑)", _S_HINT))
        if is_sel:
            runs = [StyledRun(r.text, (r.style or Style()).merge(_S_SEL_BG)) for r in runs]
        return runs

    path_display = path if path else "(默认: chat_export_<时间戳>." + fmt + ")"
    rows = [
        _row(0, "格式", fmt),
        _row(1, "范围", _SCOPE_LABEL.get(scope, scope)),
        _row(2, "路径", path_display),
        [
            StyledRun("\u25b6 " if sel == 3 else "  ", _S_SEL if sel == 3 else None),
            StyledRun("执行导出" if sel != 3 else "\u25b6 执行导出", _S_OK if sel == 3 else _S_VALUE),
            StyledRun("   (Enter 执行)", _S_HINT),
        ],
    ]

    header_runs = build_header_runs(
        "\u258d\U0001f4e4 导出向导", _S_TITLE,
        [f" · {fmt.upper()}", f" · {_SCOPE_LABEL.get(scope, scope)}"],
        "  ↑↓/jk 选择 · Enter 执行 · Esc 关闭", width,
        hint_style=_S_HINT, sep_style=_S_SEP,
    )

    children: list = [
        h(TEXT, {"styled": header_runs, "height": 1, "key": "ev-header"}),
        h(TEXT, {"children": " ", "height": 1, "key": "ev-sp"}),
    ]
    for i, runs in enumerate(rows):
        children.append(h(TEXT, {"styled": runs, "height": 1, "key": f"ev-{i}"}))
    children.append(h(TEXT, {"children": " ", "height": 1, "key": "ev-sp2"}))
    children.append(h(TEXT, {
        "children": "\u2500" * (width if width > 0 else 40), "style": _S_SEP,
        "height": 1, "key": "ev-sep",
    }))
    children.append(h(TEXT, {
        "children": "  说明: 路径留空时自动生成时间戳文件名；路径必须位于当前目录下。",
        "style": _S_HINT, "height": 1, "key": "ev-note",
    }))
    if error:
        children.append(h(TEXT, {"children": "  ✖ " + error, "style": _S_ERR, "height": 1, "key": "ev-err"}))
    elif message:
        children.append(h(TEXT, {"children": "  " + message, "style": _S_OK, "height": 1, "key": "ev-msg"}))
    if editing:
        children.append(h(TEXT, {
            "children": f"路径: {path}\u258f", "style": _S_PROMPT, "height": 1, "key": "ev-path",
        }))
    return h(Column, None, children)
