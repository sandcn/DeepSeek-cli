"""_view_common — 新增全屏视图共享的状态基类与纯函数工具。

本模块为 2026-10 新增的一批全屏视图（sessions / changes / theme / skill /
mcp / usage / search / outline / keymap / notify / export）提供**与具体数据
无关**的通用骨架：

  - :class:`ListViewState`：通用视图状态基类（Layer 0，定义于
    ``_state_types.py``；本模块 re-export 供各视图统一引入）；
  - :func:`viewport_rows` / :func:`split_panes`：终端尺寸推导（高度自适应、
    左右栏宽度分配）；
  - :func:`find_matches` / :func:`run_search` / :func:`jump_match`：子串搜索
    的匹配 / 定位（所有视图同一套 vim 风格 n/N/p 语义）；
  - :func:`handle_search_input`：搜索输入模式按键处理（字符/退格/回车/Esc）；
  - :func:`build_header_runs` / :func:`status_runs` / :func:`help_panel_rows`：
    头部行 / 底部状态行 / 帮助面板行的纯渲染；
  - :func:`char_of` / :func:`is_close_key`：按键读取与统一关闭键判定。

设计约束：仅依赖 ink 框架（Layer 0/1）与 tui 核心样式——不依赖 tools 层，
也不依赖任何具体 app 视图（避免循环依赖）。样式由调用方注入（各视图配色
不同），本模块不定义全局样式常量。
"""

from __future__ import annotations

from typing import Callable, Iterable, Optional

from src.tui._width import truncate_width, wcswidth_simple
from src.tui.ink import TEXT, StyledRun, h
from src.tui.ink.helpers import truncate_runs, truncate_runs_ellipsis

# ListViewState 是 Layer 0 纯状态容器（_state_types.py）；本模块 re-export 供
# 各视图统一从 _view_common 引入（渲染工具与状态基类同源）。
from ._state_types import ListViewState  # noqa: F401

__all__ = [
    "ListViewState",
    "viewport_rows",
    "split_panes",
    "find_matches",
    "run_search",
    "jump_match",
    "handle_search_input",
    "build_header_runs",
    "status_runs",
    "help_panel_rows",
    "char_of",
    "is_close_key",
    "clamp_index",
    "pane_divider",
    "pad_to_width",
    "SEARCH_QUERY_MAX",
]

#: 搜索输入缓冲上限（渲染行按宽度截断，无上限累积只浪费内存）。
SEARCH_QUERY_MAX = 200
#: 搜索历史上限（各视图共享）。
SEARCH_HISTORY_MAX = 50
#: 视图可见行预算预留（头部 1 行 + 底部余量）。
VIEWPORT_RESERVED = 2
#: 视图可见行下限。
VIEWPORT_MIN = 8


def viewport_rows(reserved: int = VIEWPORT_RESERVED, minimum: int = VIEWPORT_MIN) -> int:
    """视图可见行数（终端高度自适应；无高度上下文回退 16）。"""
    try:
        from src.tui._screen import TerminalWidthCache

        h_ = TerminalWidthCache.get_default().get_height()
        return max(int(minimum), int(h_) - int(reserved))
    except Exception:
        return 16


def split_panes(
    width: int,
    *,
    left_ratio: float = 0.34,
    min_left: int = 18,
    min_right: int = 24,
    fallback: tuple = (30, 50),
) -> tuple[int, int]:
    """左右栏宽度分配（左栏按比例，剩余给右栏，保证最小宽度）。

    Args:
        width: 终端总宽（<=0 时返回 ``fallback``）。
        left_ratio: 左栏占比。
        min_left: 左栏最小宽度。
        min_right: 右栏最小宽度。
        fallback: 无宽度上下文时的 ``(left, right)``。

    Returns:
        ``(left_w, right_w)``，满足 ``left_w + 1 + right_w == width``（宽度足够时）。
    """
    if width <= 0:
        return fallback
    left_w = max(int(min_left), int(width * left_ratio))
    if width - left_w - 1 < int(min_right):
        left_w = max(int(min_left) // 2, width - int(min_right) - 1)
    right_w = max(1, width - left_w - 1)
    return left_w, right_w


#: 竖直分隔列字符（U+2502）。
PANE_DIVIDER_CHAR = "\u2502"


def pane_divider(height: int, style) -> object:
    """左右栏之间的**竖直分隔列**（填充 ``height`` 行）。

    ★ P1 修复（review）：修复前各双栏视图用
    ``h(TEXT, {"children": "│", "style": ..., "height": 1})`` 作分隔符——
    TEXT 高度仅 1 行，而所在 ``Row`` 的高度由更高的一栏决定（列表 ``vh`` 行 /
    右栏预览多行），故**只有第一行**渲染出 "│"，其余行的分隔线缺失，左右栏
    内容直接相邻（视觉上两栏边界消失；实测 /theme、/trace、/logs、/keymap、
    /plugin、/sessions 等 13 处视图全部命中）。本函数生成与栏高同高的分隔列：
    内容为 ``height`` 行 "│"（``\\n`` 分隔），显式 ``height`` 保证 min-height
    语义下高度一致。

    Args:
        height: 分隔列行数（= Row 的高度，通常为视图可视行数 ``vh``）。
        style: 分隔线样式。

    Returns:
        TEXT 元素（1 列宽、``height`` 行高）。
    """
    try:
        rows = max(1, int(height))
    except (TypeError, ValueError, OverflowError):
        rows = 1
    return h(TEXT, {
        "children": "\n".join([PANE_DIVIDER_CHAR] * rows),
        "style": style,
        "height": rows,
        "key": "pane-divider",
    })


def pad_to_width(text, columns: int, *, min_pad: int = 1) -> str:
    """按**显示宽度**把 ``text`` 右填充到 ``columns`` 列（CJK/emoji 计 2 列）。

    ★ P2 修复（review）：修复前多处用 f-string ``{label:<14}`` 填充——Python
    的 ``<`` 对齐按**字符数**而非显示宽度，CJK 标签（如 "输入（未命中）" 8 字符
    / 16 列）与 ASCII 标签填充后显示宽度不等，同一列区里的值起点错位（/usage
    实测错位最多 6 列）。本函数按 ``wcswidth_simple`` 计算显示宽度补齐。

    标签自身宽于 ``columns`` 时不截断（由调用方决定是否截断），仍保证至少
    ``min_pad`` 个空格分隔。

    Args:
        text: 原始文本（非 str 自动 ``str()``）。
        columns: 目标显示列宽。
        min_pad: 最小间隔空格数（默认 1）。

    Returns:
        填充后的字符串。
    """
    s = str(text)
    gap = int(columns) - wcswidth_simple(s)
    if gap < int(min_pad):
        gap = int(min_pad)
    return s + " " * gap


def clamp_index(value, total: int) -> int:
    """把索引钳制到 ``[0, total-1]``（total<=0 返回 0）。"""
    if total <= 0:
        return 0
    try:
        return max(0, min(int(value), total - 1))
    except (TypeError, ValueError):
        return 0


def find_matches(items: Iterable, pattern: str, text_of: Callable) -> list:
    """子串匹配（忽略大小写）→ 匹配项索引列表（空模式 → 空列表）。

    Args:
        items: 待匹配条目序列。
        pattern: 搜索文本（空串返回空列表）。
        text_of: ``text_of(item) -> str`` 条目 → 可搜索文本。
    """
    if not pattern:
        return []
    low = str(pattern).lower()
    out: list = []
    for i, item in enumerate(items or []):
        try:
            text = text_of(item)
        except Exception:
            text = ""
        if low in str(text or "").lower():
            out.append(i)
    return out


def run_search(state: ListViewState, items: list, pattern: str,
               text_of: Callable, *, on_first: Optional[Callable[[int], None]] = None,
               label: str = "匹配") -> None:
    """执行搜索：写 ``search_pattern/search_matches/search_idx`` + 状态提示。

    Args:
        state: 视图状态（``ListViewState`` 子类）。
        items: 主列表条目列表。
        pattern: 搜索文本。
        text_of: 条目 → 可搜索文本。
        on_first: 命中的第一项索引回调（如定位选中行），可选。
        label: 状态提示词。
    """
    state.search_mode = False
    state.search_pattern = pattern or ""
    found = find_matches(items, state.search_pattern, text_of)
    state.search_matches = found
    if found:
        state.search_idx = 0
        if on_first is not None:
            on_first(found[0])
        state.status_message = f"{len(found)} 个{label}"
    else:
        state.search_idx = -1
        state.status_message = f"无{label}：{pattern}" if pattern else ""


def jump_match(state: ListViewState, delta: int, *,
               on_jump: Optional[Callable[[int], None]] = None) -> None:
    """在匹配列表中环绕切换（n/N/p）并回调定位。"""
    found = list(getattr(state, "search_matches", None) or [])
    if not found:
        return
    idx = getattr(state, "search_idx", -1)
    n = len(found)
    if idx < 0:
        new_idx = 0 if delta > 0 else n - 1
    else:
        new_idx = (idx + delta) % n
    state.search_idx = new_idx
    if on_jump is not None:
        on_jump(found[new_idx])


def handle_search_input(event, state: ListViewState, *,
                        max_len: int = SEARCH_QUERY_MAX) -> str:
    """搜索输入模式按键处理。

    Returns:
        ``"cancel"`` — Esc 取消（调用方清理搜索输入态）；
        ``"run"`` — Enter 执行（调用方读取 ``state.search_query`` 跑搜索）；
        ``"consume"`` — 其余按键已消费（调用方直接返回 True）；
        ``""`` — 非搜索输入模式的键（``state.search_mode`` 为 False）。
    """
    if not getattr(state, "search_mode", False):
        return ""
    kind = getattr(event, "kind", "")
    ch = getattr(event, "char", "") or ""
    if kind == "escape" or (kind == "ctrl_key" and ch == "\x1b"):
        return "cancel"
    if kind == "backspace":
        q = getattr(state, "search_query", "") or ""
        if q:
            state.search_query = q[:-1]
        return "consume"
    if kind == "enter":
        return "run"
    if kind == "char":
        if ch and "\n" not in ch and "\r" not in ch:
            q = getattr(state, "search_query", "") or ""
            if len(q) < int(max_len):
                state.search_query = q + ch
        return "consume"
    return "consume"


def char_of(event) -> str:
    """读取事件字符（非 char 事件返回空串）。"""
    if getattr(event, "kind", "") != "char":
        return ""
    return getattr(event, "char", "") or ""


def is_close_key(event) -> bool:
    """统一关闭键判定：Esc / Ctrl+H（``\\x08``）/ Ctrl+C（``\\x03``）。"""
    kind = getattr(event, "kind", "")
    if kind == "escape":
        return True
    if kind == "ctrl_key":
        return getattr(event, "char", "") in ("\x08", "\x03")
    return False


def build_header_runs(title: str, title_style, segments: list, hint: str,
                      width: int, *, hint_style, sep_style) -> list:
    """头部行 runs：``▍标题`` + 统计段 + 操作提示 + ``─`` 填充至满宽。

    Args:
        title: 标题文本（含图标）。
        title_style: 标题样式。
        segments: 统计段文本列表（原样拼接，调用方自带分隔符）。
        hint: 操作提示（前置两空格；空串不渲染）。
        width: 行宽预算（>0 时截断并填充）。
        hint_style: 提示样式。
        sep_style: 分隔线样式。
    """
    runs = [StyledRun(title, title_style)]
    for seg in segments or []:
        if seg:
            runs.append(StyledRun(seg, hint_style))
    if hint:
        runs.append(StyledRun(hint, hint_style))
    if width > 0:
        # ★ P3 修复（review）：超宽时经 ``truncate_runs_ellipsis`` 截断并补
        #   省略号——修复前用 ``truncate_runs`` 硬截断，末尾操作提示被拦腰
        #   切开（实测 "Esc 关闭" → "Esc"、/theme 头部 "Esc 恢复原主题" →
        #   "Esc 恢复原主"、/logs "F 跟随 · r 刷新" → "F 跟随 · r"），用户
        #   看不到关闭/恢复等关键提示；未超宽时行为不变（原样返回 + ─ 填充）。
        runs = truncate_runs_ellipsis(runs, width)
        used = sum(getattr(r, "width", 1) for r in runs)
        pad = width - used
        if pad > 0:
            runs.append(StyledRun("\u2500" * pad, sep_style))
    return runs


def status_runs(parts: list, *, style, message: str = "", message_style=None,
                width: int = 0) -> Optional[list]:
    """底部状态行 runs（各段 `` · `` 连接 + 操作反馈消息）。

    无任何段与消息时返回 None（调用方不渲染状态行）。
    """
    segs = [str(p) for p in (parts or []) if p]
    if message:
        segs.append(str(message))
    if not segs:
        return None
    text = "  \u00b7  ".join(segs)
    if width > 0:
        # ★ P3 修复（review）：按**显示宽度**截断——修复前 ``text[:width]``
        #   按字符数截断，含 CJK 的消息（如删除确认提示携带长标题）实际显示
        #   宽度可达 2×width，超出终端列宽触发自动换行/布局漂移。
        text = truncate_width(text, width)
    return [StyledRun(text, message_style if message else style)]


def help_panel_rows(keymap_entries: list, width: int, *,
                    key_style, group_style, desc_style, sep_style,
                    empty_text: str = "(\u5feb\u6377\u952e\u901f\u67e5\u8868\u672a\u6ce8\u518c)") -> list:
    """帮助面板（键位速查）内容行——委托 ``_keymap_pane.keymap_panel_rows``。"""
    from ._keymap_pane import keymap_panel_rows

    return keymap_panel_rows(
        keymap_entries, width,
        key_style=key_style, group_style=group_style,
        desc_style=desc_style, sep_style=sep_style,
        empty_text=empty_text,
    )
