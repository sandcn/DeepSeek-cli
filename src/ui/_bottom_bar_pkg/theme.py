"""_BottomBar 视觉主题常量 — ANSI 颜色、占位符文本、布局配置。

从 _bottom_bar.py 提取，供 _BottomBar 及其子模块共享。

颜色策略（单轨制）：
  - 所有 _COLOR_* 常量使用 256 色 ANSI 序列（xterm-256color 标准）
  - 颜色编号与 `src/core/constants.py` 中 `_256` 后缀常量一致
  - 常量为纯字符串，可直接与 Blessed 的 move_xy/clear_eol 混合使用
  - _blessed_* 辅助函数供需要动态颜色的新代码使用

颜色编号对齐参考（与 core.constants _256 体系一致）：
  - ACCENT(45)      ← CYAN_256
  - DEEP_CYAN(32)   ← 深青，区别于 CYAN_256(45)
  - DIM(242)        ← GRAY_256
  - SELECT_BG(236)  ← 选中项背景色（较 DARK_GRAY_256 暗一级）
  - SEP(237)        ← DARK_GRAY_256
  - COMPLETE_MATCH(221) ← YELLOW_256
  - TOOL_OK(41)     ← GREEN_256
  - TOOL_FAIL(196)  ← RED_256
"""

from __future__ import annotations

from ..colors import gradient_range


# ── 底部栏布局配置 ──────────────────────────────────────────
_BOTTOM_MIN_HEIGHT = 10     # 终端太小时跳过底部栏
_BOTTOM_REFRESH_MS = 0.05   # 底部栏刷新节流（50ms）
_MIN_INPUT_ROWS = 3         # 输入区最小行数（空输入时至少显示 3 行）
_BOTTOM_MIN_LINES = 5       # setup() 中最小底部栏总行数（2 分隔线+状态行 + 3 最小输入行）

# ── ANSI 颜色常量（256 色体系，与 core.constants _256 后缀常量对齐） ──
_COLOR_ACCENT = "\033[38;5;45m"       # 青色强调（提示符/模型名/状态）——对齐 CYAN_256(45)
_COLOR_DEEP_CYAN = "\033[38;5;32m"    # 深青（输入提示符最暗色）——深青 32，区别于 CYAN_256(45)
_COLOR_DIM = "\033[38;5;242m"         # 灰色次要（分隔线/占位/统计）——对齐 GRAY_256(242)
_COLOR_RESET = "\033[0m"              # 重置
_COLOR_SELECT_BG = "\033[48;5;236m"   # 选中项高亮背景（深灰背景 236，较 DARK_GRAY_256 暗一级）
_COLOR_SELECT_FG = "\033[38;5;15m"    # 选中项前景色（亮白 15，确保反显高对比度）
_COLOR_BREATH_BG: list[int] = [235, 236, 237, 238, 239, 240, 239, 238, 237, 236]
"""呼吸背景色号序列（10帧对称周期：暗灰→较亮暗灰→暗灰）。
使用 gradient_range(235, 240, 6) 生成基色后手动构建对称呼吸周期。
供 _CompletionPopup 选中项呼吸效果使用，与 _COLOR_SELECT_BG 静态色共存。"""
_COLOR_SEP = "\033[38;5;237m"         # 分隔线深灰——对齐 DARK_GRAY_256(237)
_COLOR_SEP_START = "\033[38;5;45m"    # 分隔线起始青色——对齐 CYAN_256(45)
_COLOR_COMPLETE_TITLE = "\033[1;38;5;45m"   # 补全弹窗标题色（亮青加粗）——对齐 CYAN_256(45)

# ── 补全弹窗视觉增强 ──────────────────────────────────────
_COLOR_COMPLETE_CMD_PREFIX = "\033[1;38;5;45m"  # 命令补全 / 前缀色（亮青加粗，复用标题色）——对齐 CYAN_256(45)
_COLOR_COMPLETE_DIR = "\033[38;5;110m"           # 路径补全目录色（蓝灰 110，与 _COLOR_TIME 色系一致）
_COLOR_COMPLETE_MATCH = "\033[38;5;221m"         # 匹配前缀高亮色（浅黄 221）——对齐 YELLOW_256(221)

_COLOR_TOOL_OK = "\033[38;5;41m"      # 工具成功计数——对齐 GREEN_256(41)
_COLOR_TOOL_FAIL = "\033[38;5;196m"   # 工具失败计数——对齐 RED_256(196)
_COLOR_TIME = "\033[38;5;110m"        # 蓝灰（耗时/时间戳）
_COLOR_TOKEN = "\033[38;5;68m"        # 靛蓝（Token 计数）
_COLOR_SPEED = "\033[38;5;214m"       # 琥珀色（速率）——与统一体系一致

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
        from .._blessed import get_terminal as _get_term
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
        from .._blessed import get_terminal as _get_term
        return _get_term().on_color(color_num)
    except Exception:
        return f"\033[48;5;{color_num}m"


# ── 占位符文本 ─────────────────────────────────────
_PLACEHOLDER_TEXT = "输入消息 · /help 查看命令 · Ctrl+N 切换模型 · Tab 补全"
_PLACEHOLDER_COMPACT = "/help · Ctrl+N · Tab"  # 补全弹窗可见时使用
_PLACEHOLDER_STREAMING = "AI 生成中..."   # 流式输出期间使用


# ── 全宽渐变分隔线 ─────────────────────────────────
def make_sep_gradient(width: int) -> str:
    """生成全宽渐变分隔线（青色→深灰）。

    每个字符为 ━ (U+2501)，色号从青色(45)逐级渐变到深灰(237)。
    窄屏降级由调用方通过 is_narrow() 控制，本函数不处理降级。

    Args:
        width: 分隔线字符数（通常为终端宽度 - 2）。

    Returns:
        带 ANSI 256 色渐变的完整分隔线字符串（含 RESET）。
    """
    from ..tui._text_utils import build_gradient_ansi  # 延迟导入避免循环依赖
    colors = gradient_range(45, 237, width)
    return build_gradient_ansi(colors)
