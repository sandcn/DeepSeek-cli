"""sandbox_stats_view — SandboxStatsView 文件沙盒概览视图（模态全屏视图，2026-10）。

``/sandbox`` 命令打开：App 在 ``model.fullscreen == "sandbox"`` 时经全屏视图
注册表**整屏只渲染本组件**，关闭后恢复完整聊天界面。视图汇总文件沙盒
（``SandboxManager``）的统计（概览 / 变更类型 / 工具分布 / 缓存与回滚），
并提供沙盒管理与子视图入口。

布局（单栏滚动）：
  - 每个统计区块：``▸ 区块标题 ───…`` + 若干 ``标签  值`` 行；
  - 视口按终端高度自适应，只渲染可见行；光标行整行背景高亮。

键盘：
  - ↑↓/jk 滚动 · PgUp/PgDn 翻页 · Home/End 或 g/G 首末；
  - ``r`` 刷新 · ``c`` 清空沙盒（再按确认）· ``h`` 消息维度历史视图 ·
    ``l`` 变更记录流水视图 · ``?`` 帮助面板 · Esc/Ctrl+H 关闭。

数据源：命令线程构建 ``model.sandbox_view.sections``（``core.commands._sandbox_cmd``）；
操作经 ``applied_seq`` 回传给命令线程执行；渲染期经 ``model.sandbox_refresher``
实时刷新。
"""

from __future__ import annotations

from src.tui.ink import TEXT, Column, StyledRun, h, use_input
from src.tui.ink.hooks import use_effect

from ._modal_view import (
    close_fullscreen_view,
    empty_modal_frame,
    is_modal_close_key,
    use_modal_scope,
)
from ._view_common import (
    build_header_runs,
    char_of,
    help_panel_rows,
    status_runs,
    viewport_rows,
)
from .sandbox_common import (
    S_GROUP,
    S_HINT,
    S_OK,
    S_SEP,
    S_TITLE,
    S_VALUE,
    S_WARN,
    stats_rows,
)

__all__ = ["SandboxStatsView"]

_KEYMAP = [
    {"group": "浏览", "keys": "↑↓/jk", "desc": "滚动"},
    {"group": "浏览", "keys": "PgUp/PgDn", "desc": "翻页"},
    {"group": "浏览", "keys": "g/G", "desc": "首/末"},
    {"group": "操作", "keys": "r", "desc": "刷新统计"},
    {"group": "操作", "keys": "c", "desc": "清空沙盒（再按确认）"},
    {"group": "操作", "keys": "h", "desc": "消息维度历史视图"},
    {"group": "操作", "keys": "l", "desc": "变更记录流水视图"},
    {"group": "通用", "keys": "?", "desc": "帮助面板"},
    {"group": "通用", "keys": "Esc", "desc": "关闭视图"},
]


def _open_sub_view(model, view_id: str) -> None:
    """打开沙盒子视图：先经刷新器构建数据，再切换 ``fullscreen``。"""
    refresher = getattr(model, "sandbox_refresher", None)
    if callable(refresher):
        try:
            refresher(force=True)
        except Exception:
            pass
    model.fullscreen = view_id


def SandboxStatsView(props) -> object:
    """文件沙盒概览视图组件（模态全屏视图）。"""
    model = props["model"]
    width = props.get("width", 0) or 0
    sv = getattr(model, "sandbox_view", None)
    visible = bool(sv is not None and sv.visible and not sv.done)
    sections = list(getattr(sv, "sections", None) or []) if sv is not None else []
    help_open = bool(getattr(sv, "help_open", False)) if sv is not None else False
    clear_confirm = bool(getattr(sv, "clear_confirm", False)) if sv is not None else False
    status_message = (getattr(sv, "status_message", "") or "") if sv is not None else ""

    if help_open:
        content_rows = help_panel_rows(
            _KEYMAP, width if width > 0 else 80,
            key_style=S_WARN, group_style=S_GROUP, desc_style=S_VALUE,
            sep_style=S_SEP,
        )
    else:
        content_rows = stats_rows(sections, width if width > 0 else 80)
    total = len(content_rows)
    vh = max(4, viewport_rows() - (1 if status_message else 0))
    try:
        scroll = int(getattr(sv, "scroll", 0) or 0)
    except (TypeError, ValueError):
        scroll = 0
    max_scroll = max(0, total - vh)
    scroll = max(0, min(scroll, max_scroll))
    if sv is not None and scroll != getattr(sv, "scroll", None):
        sv.scroll = scroll

    def _handle(event) -> bool:
        if not visible or sv is None:
            return False
        ch = char_of(event)
        # ★ F11 是沙盒视图族开关：在任一沙盒视图内按 F11 即关闭（事件须由
        #   视图消费，否则被 use_fullscreen 模态吞掉）。
        if getattr(event, "kind", "") == "f11":
            close_fullscreen_view(model, sv, "sandbox")
            return True
        if is_modal_close_key(event):
            if getattr(sv, "help_open", False):
                sv.help_open = False
                return True
            close_fullscreen_view(model, sv, "sandbox")
            return True
        if event.kind == "char" and ch == "?":
            sv.help_open = not bool(sv.help_open)
            sv.scroll = 0
            return True
        if event.kind == "char" and ch == "r":
            sv.refresh_seq += 1
            sv.status_message = "已刷新"
            return True
        if event.kind == "char" and ch == "c":
            if clear_confirm:
                sv.applied = {"action": "clear"}
                sv.applied_seq += 1
                sv.status_message = "正在清空沙盒…"
                sv.clear_confirm = False
            else:
                sv.clear_confirm = True
                sv.status_message = "再按 c 确认清空全部沙盒记录"
            return True
        if event.kind == "char" and ch == "h":
            _open_sub_view(model, "sandbox_history")
            return True
        if event.kind == "char" and ch == "l":
            _open_sub_view(model, "sandbox_records")
            return True
        if event.kind == "arrow_down" or (event.kind == "char" and ch == "j"):
            sv.scroll = min(max_scroll, scroll + 1)
            return True
        if event.kind == "arrow_up" or (event.kind == "char" and ch == "k"):
            sv.scroll = max(0, scroll - 1)
            return True
        if event.kind == "page_down":
            sv.scroll = min(max_scroll, scroll + vh)
            return True
        if event.kind == "page_up":
            sv.scroll = max(0, scroll - vh)
            return True
        if event.kind == "home" or (event.kind == "char" and ch == "g"):
            sv.scroll = 0
            return True
        if event.kind == "end" or (event.kind == "char" and ch == "G"):
            sv.scroll = max_scroll
            return True
        return False

    # 实时刷新（沙盒数据签名未变时零成本）。
    _refresher = getattr(model, "sandbox_refresher", None)
    use_effect(
        (lambda: _refresher()) if (callable(_refresher) and visible) else None,
        None,
    )
    use_input(_handle, visible)
    use_modal_scope(visible)

    if not visible:
        return empty_modal_frame()

    if help_open:
        hint = "  帮助面板 · ? / Esc 关闭"
    else:
        hint = "  ↑↓/jk 滚动 · r 刷新 · c 清空 · h 历史 · l 流水 · ? 帮助 · Esc 关闭"
    header_runs = build_header_runs(
        "\u258d\U0001f4e6 文件沙盒", S_TITLE,
        [f" · {total} 行", (f" · {scroll + 1}-{min(total, scroll + vh)}/{total}" if total > vh else "")],
        hint, width, hint_style=S_HINT, sep_style=S_SEP,
    )

    children: list = [
        h(TEXT, {"styled": header_runs, "height": 1, "key": "sb-header"}),
    ]
    window = content_rows[scroll:scroll + vh]
    for i, runs in enumerate(window):
        children.append(h(TEXT, {
            "styled": runs if runs else [StyledRun(" ", None)],
            "height": 1, "key": f"sb-{scroll + i}",
        }))
    sruns = status_runs(
        [], style=S_HINT, message=status_message,
        message_style=(S_WARN if "确认" in status_message or "失败" in status_message else S_OK),
        width=width,
    )
    if sruns:
        children.append(h(TEXT, {"styled": sruns, "height": 1, "key": "sb-status"}))
    return h(Column, None, children)
