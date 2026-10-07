"""_welcome — 空状态欢迎卡内容构建（聊天区空态与启动 splash 共用）。

设计（2026-10-07 空状态/启动界面重构；2026-10-07 卡片化增强）：
  - 顶部标题栏（``TopHeader``）已展示品牌与版本，欢迎卡**不再重复品牌行**
    （旧版 splash 打印 ``✦ v2.2.0``、欢迎屏再打印一次渐变品牌行，三者视觉
    冗余）；
  - 欢迎卡为「**运行环境信息卡** + 操作引导」：
      边框标题行  ``╭─ 运行环境 ──…──╮``
      信息行      ``│ ◆ 标签  值   │``（模型 / 模式 / 主题 / 目录 / 分支 /
                  上下文——值为空则跳过该行）
      空行        ``│             │``
      引导行      ``│ › 直接输入消息开始对话 │`` / ``│ › /help … │``
      边框底行    ``╰────…────╯``
  - 卡片宽度自适应终端宽度（``width``）；宽度不足以容纳边框时自动回退为
    **无边框平铺**（窄屏安全，行宽不变量始终成立）。

单一真源：欢迎卡内容仅在本模块构建，``chat_view._welcome_rows``（聊天区空态
实时渲染）与 ``apply._do_splash``（启动提交块）各自转换为自己的行表示
（``StyledRun`` / ``AnsiLine``），保证两处内容一致、不漂移。

依赖约束：仅依赖 core/style（Layer 0 样式）与函数内延迟 import 的运行期查询，
无循环依赖。
"""

from __future__ import annotations

import os
import time
import unicodedata

from src.tui.core.style import Style

__all__ = [
    "GUIDE_ROWS",
    "environment_info",
    "welcome_card_rows",
    "display_width",
    "truncate_segments",
]

# ── 样式 ──
_S_DOT = Style(fg=45, bold=True)     # ◆ / › 引导符号（亮青加粗）
_S_LABEL = Style(fg=242)             # 信息标签（暗灰）
_S_VALUE = Style(fg=252)             # 信息值（亮白）
_S_LEAD = Style(fg=252)              # 首行引导（亮白）
_S_HINT = Style(fg=242)              # 其余引导（暗灰）
_S_BORDER = Style(fg=238)            # 卡片边框（深灰）
_S_TITLE = Style(fg=45, bold=True)   # 卡片标题（亮青加粗）

#: 操作引导行（首行为亮白，其余暗灰）。
GUIDE_ROWS: tuple = (
    "直接输入消息开始对话",
    "/help 查看命令 · /model 选择模型 · /theme 主题",
    "Tab 补全 · Ctrl+N 切换模型 · Ctrl+H 轨迹视图 · F1 帮助",
)

# ── 卡片化参数 ──
#: 卡片左右外边距（保持与旧版平铺一致的视觉缩进）。
_CARD_INDENT = "  "
_CARD_TRAIL = "  "
#: 卡片标题文本（边框标题行内）。
_CARD_TITLE = "运行环境"
#: 卡片最小总宽（低于此宽度回退无边框平铺——边框本身已占 6 列，再窄无意义）。
_CARD_MIN_WIDTH = 24

# ── Git 分支缓存（TTL；避免渲染线程每帧读文件） ──
_GIT_TTL = 5.0
#: ``[cwd, timestamp, branch]``
_GIT_CACHE: list = ["", 0.0, ""]


def display_width(text: str) -> int:
    """文本显示宽度（东亚宽/全角字符按 2 列计）。"""
    width = 0
    for ch in text:
        width += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return width


def _seg_width(segments) -> int:
    """段列表 ``[(text, style), ...]`` 的总显示宽度。"""
    return sum(display_width(text) for text, _ in segments)


def truncate_segments(segments: list, max_width: int) -> list:
    """按显示宽度截断 ``[(text, style), ...]`` 段列表（不拆宽字符）。"""
    if max_width <= 0:
        return []
    out: list = []
    used = 0
    for text, style in segments:
        if not text:
            continue
        buf = []
        for ch in text:
            w = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
            if used + w > max_width:
                break
            buf.append(ch)
            used += w
        if buf:
            out.append(("".join(buf), style))
        if used >= max_width:
            break
    return out


def _current_mode_label() -> str:
    """当前主 Agent 运行模式标签（失败回退空串）。"""
    try:
        from src.prompt_builder.builder import get_mode
        from src.prompt_builder.modes import resolve_mode_label

        return str(resolve_mode_label(get_mode()) or "")
    except Exception:
        return ""


def _current_theme() -> str:
    """当前配色主题名（失败回退空串）。"""
    try:
        from src.tui.core._theme import _read_theme

        return str(_read_theme() or "")
    except Exception:
        return ""


def _short_cwd() -> str:
    """当前工作目录（家目录前缀缩写为 ``~``）。"""
    try:
        cwd = os.getcwd()
    except Exception:
        return ""
    home = os.path.expanduser("~")
    if home and home != "~" and cwd.startswith(home):
        rest = cwd[len(home):]
        cwd = "~" + rest if rest else "~"
    return cwd


def _git_branch() -> str:
    """当前 Git 分支名（失败/非仓库返回空串）。

    detached HEAD 时取提交短 hash（前 8 位）。结果按 ``(cwd, 5s)`` 缓存——
    渲染线程每帧调用，避免逐帧读 ``.git/HEAD`` 的磁盘 I/O。
    """
    try:
        cwd = os.getcwd()
    except Exception:
        return ""
    now = time.monotonic()
    if _GIT_CACHE[0] == cwd and now - _GIT_CACHE[1] < _GIT_TTL:
        return _GIT_CACHE[2]
    branch = ""
    try:
        head = os.path.join(cwd, ".git", "HEAD")
        with open(head, "r", encoding="utf-8", errors="replace") as fh:
            content = fh.read().strip()
        if content.startswith("ref:"):
            branch = content.rsplit("/", 1)[-1].strip()
        elif content:
            branch = content[:8]
    except Exception:
        branch = ""
    _GIT_CACHE[0] = cwd
    _GIT_CACHE[1] = now
    _GIT_CACHE[2] = branch
    return branch


def _context_capacity() -> str:
    """上下文窗口容量显示文本（如 ``1M`` / ``60k``；不可用时返回空串）。"""
    try:
        from src.config.proxy import config

        tokens = int(config.get("max_context_tokens", 0) or 0)
    except Exception:
        return ""
    if tokens <= 0:
        return ""
    if tokens >= 1_000_000:
        # 千进位到 M：1_000_000 → "1M"、1_500_000 → "1.5M"（:g 去掉 ".0"）
        return f"{tokens / 1_000_000:g}M tokens"
    if tokens >= 1000:
        return f"{tokens // 1000}k tokens"
    return f"{tokens} tokens"


def environment_info(model) -> list:
    """运行环境信息 ``[(标签, 值), ...]``（值为空的信息项跳过）。"""
    items: list = []
    status = getattr(model, "status", None)
    name = getattr(status, "model_name", "") if status is not None else ""
    if name:
        items.append(("模型", str(name)))
    mode = _current_mode_label()
    if mode:
        items.append(("模式", mode))
    theme = _current_theme()
    if theme:
        items.append(("主题", theme))
    cwd = _short_cwd()
    if cwd:
        items.append(("目录", cwd))
    branch = _git_branch()
    if branch:
        items.append(("分支", branch))
    capacity = _context_capacity()
    if capacity:
        items.append(("上下文", capacity))
    return items


def _body_rows(model) -> list:
    """欢迎卡内容行（无边框）——信息行 + 空行 + 引导行。"""
    rows: list = []
    for label, value in environment_info(model):
        rows.append([
            ("\u25c6 ", _S_DOT),
            (f"{label}  ", _S_LABEL),
            (value, _S_VALUE),
        ])
    if rows:
        rows.append([(" ", None)])
    for idx, guide in enumerate(GUIDE_ROWS):
        rows.append([
            ("\u203a ", _S_DOT),
            (guide, _S_LEAD if idx == 0 else _S_HINT),
        ])
    return rows


def _frame_card(body: list, width: int) -> list:
    """把内容行包裹为带边框卡片（宽度恒 = ``width``）。

    布局：``  ╭─ 运行环境 ──…─╮`` / ``  │ 内容… │`` / ``  ╰────…────╯``。
    内容超宽时按内宽截断（不拆 CJK）；行宽恒 = width（行级 diff 宽度不变量）。

    Args:
        body: 无边框内容行（``_body_rows`` 产物）。
        width: 卡片总宽（含左右外边距）。

    Returns:
        带边框行列表（每行 ``[(text, style), ...]``）。
    """
    inner_total = width - len(_CARD_INDENT) - len(_CARD_TRAIL)
    inner_w = inner_total - 4  # "│ " + " │"
    title = f" {_CARD_TITLE} "
    top_dash = max(0, inner_total - 2 - display_width(title))
    rows: list = [[
        (_CARD_INDENT, None),
        ("\u256d", _S_BORDER),
        (title, _S_TITLE),
        ("\u2500" * top_dash, _S_BORDER),
        ("\u256e", _S_BORDER),
        (_CARD_TRAIL, None),
    ]]
    for seg in body:
        seg = truncate_segments(seg, inner_w)
        used = _seg_width(seg)
        row: list = [(_CARD_INDENT, None), ("\u2502 ", _S_BORDER)]
        row.extend(seg)
        pad = inner_w - used
        if pad > 0:
            row.append((" " * pad, None))
        row.append((" \u2502", _S_BORDER))
        row.append((_CARD_TRAIL, None))
        rows.append(row)
    rows.append([
        (_CARD_INDENT, None),
        ("\u2570", _S_BORDER),
        ("\u2500" * (inner_total - 2), _S_BORDER),
        ("\u256f", _S_BORDER),
        (_CARD_TRAIL, None),
    ])
    return rows


def welcome_card_rows(model, width: int = 0) -> list:
    """构建欢迎卡行（``[[(text, style|None), ...], ...]``）。

    Args:
        model: AppModel 实例（读取模型名/运行环境）。
        width: 行宽预算（>0 时卡片化并按显示宽度截断；宽度不足以容纳边框时
            回退无边框平铺）。<=0 时不做构建期截断（行宽由渲染期文档宽度
            防线统一钳制）。

    Returns:
        行列表——卡片（边框标题 + 信息卡 + 空行 + 引导行 + 边框底）或
        窄屏时的无边框平铺；信息项为空时跳过该行（不显示空值），也跳过
        其后空行（避免连续空行）。
    """
    body = _body_rows(model)
    rows: list
    if width and width >= _CARD_MIN_WIDTH:
        rows = _frame_card(body, width)
    else:
        rows = [[("  ", None)] + seg for seg in body]
    if width and width > 0:
        rows = [truncate_segments(row, width) for row in rows]
        rows = [row if row else [(" ", None)] for row in rows]
    return rows
