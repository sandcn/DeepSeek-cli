"""_welcome — 空状态欢迎卡内容构建（聊天区空态与启动 splash 共用）。

设计（2026-10-07 空状态/启动界面重构）：
  - 顶部标题栏（``TopHeader``）已展示品牌与版本，欢迎卡**不再重复品牌行**
    （旧版 splash 打印 ``✦ v2.2.0``、欢迎屏再打印一次渐变品牌行，三者视觉
    冗余）；
  - 欢迎卡改为「运行环境信息卡 + 操作引导」：
      行1..N  ``◆ 标签  值``（模型 / 模式 / 主题 / 工作目录——值为空则跳过）
      空行
      引导行  ``› 直接输入消息开始对话`` / ``› /help … `` / ``› Tab …``

单一真源：欢迎卡内容仅在本模块构建，``chat_view._welcome_rows``（聊天区空态
实时渲染）与 ``apply._do_splash``（启动提交块）各自转换为自己的行表示
（``StyledRun`` / ``AnsiLine``），保证两处内容一致、不漂移。

依赖约束：仅依赖 core/style（Layer 0 样式）与函数内延迟 import 的运行期查询，
无循环依赖。
"""

from __future__ import annotations

import os
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

#: 操作引导行（首行为亮白，其余暗灰）。
GUIDE_ROWS: tuple = (
    "直接输入消息开始对话",
    "/help 查看命令 · /model 选择模型 · /theme 主题",
    "Tab 补全 · Ctrl+N 切换模型 · Ctrl+H 轨迹视图 · F1 帮助",
)


def display_width(text: str) -> int:
    """文本显示宽度（东亚宽/全角字符按 2 列计）。"""
    width = 0
    for ch in text:
        width += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return width


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
    return items


def welcome_card_rows(model, width: int = 0) -> list:
    """构建欢迎卡行（``[[(text, style|None), ...], ...]``）。

    Args:
        model: AppModel 实例（读取模型名）。
        width: 行宽预算（>0 时每行按显示宽度截断）。

    Returns:
        行列表——信息卡（``◆ 标签  值``）+ 空行 + 引导行；信息项为空时
        跳过该行（不显示空值），也跳过其后空行（避免连续空行）。
    """
    rows: list = []
    for label, value in environment_info(model):
        rows.append([
            ("  ", None),
            ("\u25c6 ", _S_DOT),
            (f"{label}  ", _S_LABEL),
            (value, _S_VALUE),
        ])
    if rows:
        rows.append([(" ", None)])
    for idx, guide in enumerate(GUIDE_ROWS):
        rows.append([
            ("  ", None),
            ("\u203a ", _S_DOT),
            (guide, _S_LEAD if idx == 0 else _S_HINT),
        ])
    if width and width > 0:
        rows = [truncate_segments(row, width) for row in rows]
        rows = [row if row else [(" ", None)] for row in rows]
    return rows
