"""usage_view — UsageView 用量仪表盘视图（模态全屏视图，2026-10）。

``/usage``（或 ``/cost`` / ``/context`` 有 ChatUI 时）打开：App 在
``model.fullscreen == "usage"`` 时经全屏视图注册表**整屏只渲染本组件**，
关闭后恢复完整聊天界面。视图可视化 token 用量 / 上下文窗口 / 费用统计。

布局（单栏滚动）：
  - 每个统计区块：``▸ 区块标题 ───…`` + 若干 ``标签  值 [进度条 百分比]`` 行；
  - 视口按终端高度自适应，只渲染可见行；光标行整行背景高亮。

键盘：
  - ↑↓/jk 滚动 · PgUp/PgDn 翻页 · Home/End 或 g/G 首末 · ``r`` 刷新 ·
    ``?`` 帮助面板 · Esc/Ctrl+H 关闭。

数据源：命令线程构建 ``model.usage_view.sections``（``core.stats`` token 快照 /
``core.context_manager`` 上下文使用率 / ``TOKEN_PRICES`` 费用计算）。
"""

from __future__ import annotations

from src.tui.core.style import Style
from src.tui.ink import TEXT, Column, StyledRun, h, use_input
from src.tui.ink.helpers import truncate_runs, wrap_runs_by_width

from ._modal_view import empty_modal_frame, is_modal_close_key, use_modal_scope
from ._view_common import (
    build_header_runs,
    char_of,
    status_runs,
    viewport_rows,
)

__all__ = ["UsageView", "_content_rows", "_bar_runs"]

_S_TITLE = Style(fg=45, bold=True)
_S_HINT = Style(fg=242)
_S_SEP = Style(fg=238)
_S_GROUP = Style(fg=110, bold=True)
_S_LABEL = Style(fg=75)
_S_VALUE = Style(fg=252)
_S_OK = Style(fg=40)
_S_WARN = Style(fg=214)
_S_ERR = Style(fg=196)
_S_BAR_FILL = Style(fg=40)
_S_BAR_MID = Style(fg=214)
_S_BAR_LOW = Style(fg=196)
_S_BAR_EMPTY = Style(fg=238)
_S_CUR_BG = Style(bg=237)

_KEYMAP = [
    {"group": "浏览", "keys": "↑↓/jk", "desc": "滚动"},
    {"group": "浏览", "keys": "PgUp/PgDn", "desc": "翻页"},
    {"group": "浏览", "keys": "g/G", "desc": "首/末"},
    {"group": "操作", "keys": "r", "desc": "刷新统计"},
    {"group": "通用", "keys": "?", "desc": "帮助面板"},
    {"group": "通用", "keys": "Esc", "desc": "关闭视图"},
]

#: 进度条宽度（字符数）。
_BAR_WIDTH = 20


def _value_style(kind: str) -> Style:
    if kind == "error":
        return _S_ERR
    if kind == "warn":
        return _S_WARN
    if kind == "ok":
        return _S_OK
    return _S_VALUE


def _bar_runs(ratio: float) -> list:
    """进度条 runs（按比例填充；>90% 红、>70% 黄、其余绿）。"""
    try:
        r = max(0.0, min(1.0, float(ratio)))
    except (TypeError, ValueError):
        r = 0.0
    filled = int(round(r * _BAR_WIDTH))
    filled = max(0, min(_BAR_WIDTH, filled))
    fill_style = _S_BAR_FILL
    if r >= 0.9:
        fill_style = _S_BAR_LOW
    elif r >= 0.7:
        fill_style = _S_BAR_MID
    return [
        StyledRun("\u2588" * filled, fill_style),
        StyledRun("\u2591" * (_BAR_WIDTH - filled), _S_BAR_EMPTY),
        StyledRun(f" {r * 100:.1f}%", _S_VALUE),
    ]


def _content_rows(sections: list, width: int) -> list:
    """统计区块 → 内容行（``list[list[StyledRun]]``）。"""
    rows: list = []
    for sec in sections or []:
        if not isinstance(sec, dict):
            continue
        title = str(sec.get("title", ""))
        prefix = f"\u25b8 {title} "
        pad = max(0, width - len(prefix) - 1) if width > 0 else 0
        rows.append([
            StyledRun(prefix, _S_GROUP),
            StyledRun("\u2500" * pad, _S_SEP),
        ])
        for item in sec.get("rows") or []:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                label, value = item[0], item[1]
                kind = item[2] if len(item) > 2 else ""
                ratio = item[3] if len(item) > 3 else None
            elif isinstance(item, dict):
                label = item.get("label", "")
                value = item.get("value", "")
                kind = item.get("kind", "")
                ratio = item.get("bar")
            else:
                continue
            runs = [
                StyledRun(f"  {str(label):<14}", _S_LABEL),
                StyledRun(str(value), _value_style(str(kind))),
            ]
            if ratio is not None:
                runs.append(StyledRun("  ", None))
                runs.extend(_bar_runs(ratio))
            if width > 0:
                rows.extend(list(line.runs) for line in wrap_runs_by_width(runs, width))
            else:
                rows.append(runs)
        rows.append([StyledRun(" ", None)])
    if not rows:
        rows.append([StyledRun("(暂无统计)", _S_HINT)])
    return rows


def UsageView(props) -> object:
    """用量仪表盘视图组件（模态全屏视图）。"""
    model = props["model"]
    width = props.get("width", 0) or 0
    uv = getattr(model, "usage_view", None)
    visible = bool(uv is not None and uv.visible and not uv.done)
    sections = list(getattr(uv, "sections", None) or []) if uv is not None else []
    help_open = bool(getattr(uv, "help_open", False)) if uv is not None else False

    if help_open:
        from ._view_common import help_panel_rows

        content_rows = help_panel_rows(
            _KEYMAP, width, key_style=_S_WARN, group_style=_S_GROUP,
            desc_style=_S_VALUE, sep_style=_S_SEP,
        )
    else:
        content_rows = _content_rows(sections, width if width > 0 else 80)
    total = len(content_rows)
    vh = max(4, viewport_rows())
    try:
        scroll = int(getattr(uv, "scroll", 0) or 0)
    except (TypeError, ValueError):
        scroll = 0
    max_scroll = max(0, total - vh)
    scroll = max(0, min(scroll, max_scroll))
    if uv is not None and scroll != getattr(uv, "scroll", None):
        uv.scroll = scroll

    def _handle(event) -> bool:
        if not visible or uv is None:
            return False
        ch = char_of(event)
        if is_modal_close_key(event):
            if getattr(uv, "help_open", False):
                uv.help_open = False
                return True
            uv.try_set_final("cancel")
            return True
        if event.kind == "char" and ch == "?":
            uv.help_open = not bool(uv.help_open)
            uv.scroll = 0
            return True
        if event.kind == "char" and ch == "r":
            uv.refresh_seq += 1
            uv.status_message = "已刷新"
            return True
        if event.kind == "arrow_down" or (event.kind == "char" and ch == "j"):
            uv.scroll = min(max_scroll, scroll + 1)
            return True
        if event.kind == "arrow_up" or (event.kind == "char" and ch == "k"):
            uv.scroll = max(0, scroll - 1)
            return True
        if event.kind == "page_down":
            uv.scroll = min(max_scroll, scroll + vh)
            return True
        if event.kind == "page_up":
            uv.scroll = max(0, scroll - vh)
            return True
        if event.kind == "home" or (event.kind == "char" and ch == "g"):
            uv.scroll = 0
            return True
        if event.kind == "end" or (event.kind == "char" and ch == "G"):
            uv.scroll = max_scroll
            return True
        return False

    use_input(_handle, visible)
    use_modal_scope(visible)

    if not visible:
        return empty_modal_frame()

    if help_open:
        hint = "  帮助面板 · ? / Esc 关闭"
    else:
        hint = "  ↑↓/jk 滚动 · PgUp/PgDn 翻页 · r 刷新 · ? 帮助 · Esc 关闭"
    header_runs = build_header_runs(
        "\u258d\U0001f4ca 用量仪表盘", _S_TITLE,
        [f" · {total} 行", (f" · {scroll + 1}-{min(total, scroll + vh)}/{total}" if total > vh else "")],
        hint, width, hint_style=_S_HINT, sep_style=_S_SEP,
    )

    children: list = [
        h(TEXT, {"styled": header_runs, "height": 1, "key": "uv-header"}),
    ]
    window = content_rows[scroll:scroll + vh]
    for i, runs in enumerate(window):
        children.append(h(TEXT, {
            "styled": runs if runs else [StyledRun(" ", None)],
            "height": 1, "key": f"uv-{scroll + i}",
        }))
    status_message = (getattr(uv, "status_message", "") or "") if uv is not None else ""
    sruns = status_runs([], style=_S_HINT, message=status_message,
                        message_style=_S_OK, width=width)
    if sruns:
        children.append(h(TEXT, {"styled": sruns, "height": 1, "key": "uv-status"}))
    return h(Column, None, children)
