"""_BottomBar 视觉主题常量 — ANSI 颜色、占位符文本、布局配置。

从 _bottom_bar.py 提取，供 _BottomBar 及其子模块共享。

颜色策略（双轨制）：
  - _COLOR_* 常量为硬编码 ANSI 序列（原始色号标注于注释，便于追溯）
  - _blessed_* 辅助函数供需要动态颜色的新代码使用
  - 颜色常量为纯字符串，可直接与 Blessed 的 move_xy/clear_eol 混合使用
"""

from __future__ import annotations

__all__ = [
    # 布局配置
    "_BOTTOM_MIN_HEIGHT",
    "_BOTTOM_REFRESH_MS",
    "_MIN_INPUT_ROWS",
    "_BOTTOM_MIN_LINES",
    # ANSI 颜色常量
    "_COLOR_ACCENT",
    "_COLOR_DEEP_CYAN",
    "_COLOR_DIM",
    "_COLOR_RESET",
    "_COLOR_SELECT_BG",
    "_COLOR_SELECT_FG",
    "_COLOR_SEP",
    "_COLOR_COMPLETE_TITLE",
    "_COLOR_TOOL_OK",
    "_COLOR_TOOL_FAIL",
    "_COLOR_TIME",
    "_COLOR_TOKEN",
    "_COLOR_SPEED",
    # Blessed 颜色辅助函数
    "_blessed_fg",
    "_blessed_bg",
    # 占位符文本
    "_PLACEHOLDER_TEXT",
    "_PLACEHOLDER_COMPACT",
    "_PLACEHOLDER_STREAMING",
]

# ── 底部栏布局配置 ──────────────────────────────────────────
_BOTTOM_MIN_HEIGHT = 10     # 终端太小时跳过底部栏
_BOTTOM_REFRESH_MS = 0.05   # 底部栏刷新节流（50ms）
_MIN_INPUT_ROWS = 3         # 输入区最小行数（空输入时至少显示 3 行）
_BOTTOM_MIN_LINES = 5       # setup() 中最小底部栏总行数（2 分隔线+状态行 + 3 最小输入行）

# ── ANSI 颜色常量（硬编码，原始色号标注于注释） ──
_COLOR_ACCENT = "\033[38;5;39m"       # 色号 39 — 青色强调（提示符/模型名/状态）
_COLOR_DEEP_CYAN = "\033[38;5;30m"    # 色号 30 — 深青（输入提示符最暗色）
_COLOR_DIM = "\033[38;5;245m"         # 色号 245 — 灰色次要（分隔线/占位/统计）
_COLOR_RESET = "\033[0m"              # 重置
_COLOR_SELECT_BG = "\033[48;5;238m"   # 色号 238(bg) — 选中项高亮背景（深灰，#238 比 #236 略亮，改善 light 主题可见性）
_COLOR_SELECT_FG = "\033[38;5;15m"    # 色号 15 — 选中项前景色（亮白，确保反显高对比度）
_COLOR_SEP = "\033[38;5;237m"         # 色号 237 — 分隔线深灰
_COLOR_COMPLETE_TITLE = "\033[1;38;5;45m"   # 色号 45, bold — 补全弹窗标题色（亮青加粗）
_COLOR_TOOL_OK = "\033[38;5;40m"      # 色号 40 — 工具成功计数
_COLOR_TOOL_FAIL = "\033[38;5;9m"     # 色号 9 — 工具失败计数
_COLOR_TIME = "\033[38;5;110m"        # 色号 110 — 蓝灰（耗时/时间戳）
_COLOR_TOKEN = "\033[38;5;68m"        # 色号 68 — 靛蓝（Token 计数）
_COLOR_SPEED = "\033[38;5;214m"       # 色号 214 — 琥珀色（速率）

# ── በBlessed 颜色辅助函数 ─────────────────────────────────────
# 供需要动态颜色的新代码使用，与现有 _COLOR_* 常量共存


def _blessed_fg(color_num: int) -> str:
    """通过 Blessed 生成 256 色前景 ANSI 序列。

    回退：Blessed 不可用时返回原始 ANSI 序列。

    Args:
        color_num: 256 色号（0-255）。

    Returns:
        ANSI 颜色序列字符串。
    """
    try:
        from src.ui._blessed import get_terminal as _get_term
        return _get_term().color(color_num)
    except Exception:
        return f"\033[38;5;{color_num}m"


def _blessed_bg(color_num: int) -> str:
    """通过 Blessed 生成 256 色背景 ANSI 序列。

    回退：Blessed 不可用时返回原始 ANSI 序列。

    Args:
        color_num: 256 色号（0-255）。

    Returns:
        ANSI 背景色序列字符串。
    """
    try:
        from src.ui._blessed import get_terminal as _get_term
        return _get_term().on_color(color_num)
    except Exception:
        return f"\033[48;5;{color_num}m"


# ── 占位符文本 ─────────────────────────────────────
_PLACEHOLDER_TEXT = "输入消息 · /help 查看命令 · Ctrl+N 切换模型 · Tab 补全"
_PLACEHOLDER_COMPACT = "/help · Ctrl+N · Tab"  # 补全弹窗可见时使用
_PLACEHOLDER_STREAMING = "AI 生成中..."   # 流式输出期间使用
