"""_BottomBar 视觉主题常量 — ANSI 颜色、占位符文本、布局配置。

从 _bottom_bar.py 提取，供 _BottomBar 及其子模块共享。

颜色策略（双轨制）：
  - _COLOR_* 常量通过 StyledText.to_ansi_raw() 生成（单一 ANSI 事实来源）
  - _blessed_* 辅助函数供需要动态颜色的新代码使用
  - 颜色常量为纯字符串，可直接与 Blessed 的 move_xy/clear_eol 混合使用
"""

from __future__ import annotations

from ..chat_ui._styled import StyledText


# ── 底部栏布局配置 ──────────────────────────────────────────
_BOTTOM_MIN_HEIGHT = 10     # 终端太小时跳过底部栏
_BOTTOM_REFRESH_MS = 0.05   # 底部栏刷新节流（50ms）
_MIN_INPUT_ROWS = 3         # 输入区最小行数（空输入时至少显示 3 行）
_BOTTOM_MIN_LINES = 5       # setup() 中最小底部栏总行数（2 分隔线+状态行 + 3 最小输入行）

# ── ANSI 颜色常量（通过 StyledText.to_ansi_raw() 生成） ──
_COLOR_ACCENT = StyledText.to_ansi_raw(color_number=39)       # 青色强调（提示符/模型名/状态）
_COLOR_DEEP_CYAN = StyledText.to_ansi_raw(color_number=30)    # 深青（输入提示符最暗色）
_COLOR_DIM = StyledText.to_ansi_raw(color_number=245)         # 灰色次要（分隔线/占位/统计）
_COLOR_RESET = "\033[0m"              # 重置
_COLOR_SELECT_BG = StyledText.to_ansi_raw(bg_color_number=238)   # 选中项高亮背景（深灰背景，#238 比 #236 略亮，改善 light 主题可见性）
_COLOR_SELECT_FG = StyledText.to_ansi_raw(color_number=15)    # 选中项前景色（亮白，确保反显高对比度）
_COLOR_SEP = StyledText.to_ansi_raw(color_number=237)         # 分隔线深灰
_COLOR_COMPLETE_TITLE = StyledText.to_ansi_raw(color_number=45, bold=True)   # 补全弹窗标题色（亮青加粗）
_COLOR_TOOL_OK = StyledText.to_ansi_raw(color_number=40)      # 工具成功计数
_COLOR_TOOL_FAIL = StyledText.to_ansi_raw(color_number=9)     # 工具失败计数
_COLOR_TIME = StyledText.to_ansi_raw(color_number=110)        # 蓝灰（耗时/时间戳）
_COLOR_TOKEN = StyledText.to_ansi_raw(color_number=68)        # 靛蓝（Token 计数）
_COLOR_SPEED = StyledText.to_ansi_raw(color_number=214)       # 琥珀色（速率）

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
        from ._blessed import get_terminal as _get_term
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
        from ._blessed import get_terminal as _get_term
        return _get_term().on_color(color_num)
    except Exception:
        return f"\033[48;5;{color_num}m"


# ── 占位符文本 ─────────────────────────────────────
_PLACEHOLDER_TEXT = "输入消息 · /help 查看命令 · Ctrl+N 切换模型 · Tab 补全"
_PLACEHOLDER_COMPACT = "/help · Ctrl+N · Tab"  # 补全弹窗可见时使用
_PLACEHOLDER_STREAMING = "AI 生成中..."   # 流式输出期间使用
