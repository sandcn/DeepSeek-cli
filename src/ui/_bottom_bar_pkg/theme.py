"""_BottomBar 视觉主题常量 — ANSI 颜色、占位符文本、布局配置。

从 _bottom_bar.py 提取，供 _BottomBar 及其子模块共享。

颜色策略（单轨制 — 统一引用 core/constants.py）：
  - 所有 _COLOR_* ANSI 字符串由 core/constants.py 的 256 色常量构建
  - core/constants.py 是 256 色号的权威源，theme.py 仅构建 ANSI 序列
  - 动效呼吸逻辑委托 _effects.py 实现（sine_color/sine_color_range）
  - 消除与 core/constants.py 和 ui/colors.py 的颜色编号漂移

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
from ..tui._animator import AnimatorContext
from ..tui._effects import sine_color_range
from ..tui._effects import sine_color as _sine_color_fx
from ...core.constants import (
    CYAN_256, GRAY_256, DARK_GRAY_256, GREEN_256, RED_256,
    BRIGHT_YELLOW_256, YELLOW_256, WHITE_256, BRIGHT_CYAN_256,
)


# ── ANSI 序列辅助 ─────────────────────────────────────
def _fg(n: int) -> str:
    """构建 ANSI 256 色前景序列。"""
    return f"\033[38;5;{n}m"


def _bg(n: int) -> str:
    """构建 ANSI 256 色背景序列。"""
    return f"\033[48;5;{n}m"


# ── 底部栏布局配置 ──────────────────────────────────────────
_BOTTOM_MIN_HEIGHT = 10     # 终端太小时跳过底部栏
_BOTTOM_REFRESH_MS = 0.05   # 底部栏刷新节流（50ms）
_MIN_INPUT_ROWS = 3         # 输入区最小行数（空输入时至少显示 3 行）
_BOTTOM_MIN_LINES = 5       # setup() 中最小底部栏总行数（2 分隔线+状态行 + 3 最小输入行）

# ── ANSI 颜色常量（统一由 core/constants.py 的 256 色常量构建） ──
_COLOR_ACCENT = _fg(CYAN_256)          # 青色强调（提示符/模型名/状态）——CYAN_256=45
_COLOR_DEEP_CYAN = _fg(32)  # 深青（输入提示符最暗色）——32 深青
_COLOR_DIM = _fg(GRAY_256)             # 灰色次要（分隔线/占位/统计）——GRAY_256=242
_COLOR_RESET = "\033[0m"               # 重置（无需统一）
_COLOR_SELECT_BG = _bg(236)            # 选中项高亮背景（深灰背景 236）
_COLOR_SELECT_FG = _fg(WHITE_256)      # 选中项前景色（亮白 15）
_COLOR_BREATH_BG: list[int] = [235, 236, 237, 238, 239, 240, 239, 238, 237, 236]
"""呼吸背景色号序列（10帧对称周期）。"""
_COLOR_SEP = _fg(DARK_GRAY_256)        # 分隔线深灰——DARK_GRAY_256=237
_COLOR_SEP_START = _fg(CYAN_256)       # 分隔线起始青色——CYAN_256=45
_COLOR_COMPLETE_TITLE = f"\033[1;{_fg(CYAN_256)[1:]}"   # 补全弹窗标题色（亮青加粗）

# ── 补全弹窗视觉增强 ──────────────────────────────────────
_COLOR_COMPLETE_CMD_PREFIX = f"\033[1;{_fg(CYAN_256)[1:]}"  # 命令 / 前缀色（亮青加粗）
_COLOR_COMPLETE_DIR = _fg(110)                              # 路径补全目录色（蓝灰 110）
_COLOR_COMPLETE_MATCH = _fg(YELLOW_256)                     # 匹配前缀高亮色——YELLOW_256=221
_COLOR_TOOL_OK = _fg(GREEN_256)          # 工具成功计数——GREEN_256=41
_COLOR_TOOL_FAIL = _fg(RED_256)          # 工具失败计数——RED_256=196
_COLOR_TIME = _fg(110)                   # 蓝灰（耗时/时间戳）
_COLOR_TOKEN = _fg(68)                   # 靛蓝（Token 计数）
_COLOR_SPEED = _fg(BRIGHT_YELLOW_256)    # 琥珀色（速率）——BRIGHT_YELLOW_256=221

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
def make_sep_gradient(width: int, start_color: int = 45) -> str:
    """生成全宽渐变分隔线（青色→深灰）。

    委托到 _text_utils.make_sep_gradient() 实现，
    保持向后兼容。
    """
    from ..tui._text_utils import make_sep_gradient as _impl
    return _impl(width, start_color=start_color)


# ── 提示符呼吸动画（第四阶段美化） ──────────────────────────
_PROMPT_BREATH_COLORS: list[int] = gradient_range(32, 81, 6) + gradient_range(81, 32, 6)
"""提示符呼吸色号：暗青(32)↔亮青(81) 对称呼吸，12 帧。"""
_PROMPT_BREATH_LEN: int = 12


def get_prompt_breath_color(frame: int) -> str:
    """根据帧号返回当前提示符呼吸色的 ANSI 256 色序列。

    使用 AnimatorContext.sine_color 做正弦波呼吸，
    在深青(32)↔亮青(81)间平滑过渡。

    Args:
        frame: 呼吸帧号（自动取模 _PROMPT_BREATH_LEN）。

    Returns:
        ANSI 256 色前景色序列，格式 ``\\033[38;5;{color}m``。
    """
    if frame > 0:
        try:
            color = AnimatorContext.get_default().sine_color(32, 81, 12)
            return f"\033[38;5;{color}m"
        except Exception:
            pass
    if not _PROMPT_BREATH_COLORS:
        return _COLOR_DEEP_CYAN
    idx = frame % _PROMPT_BREATH_LEN
    color = _PROMPT_BREATH_COLORS[idx] if idx < len(_PROMPT_BREATH_COLORS) else 32
    return f"\033[38;5;{color}m"


# ── 分隔线呼吸色序（第四阶段美化） ──
_SEP_BREATH_COLORS: list[int] = [45, 44, 43, 42, 41, 40, 41, 42, 43, 44]
"""分隔线呼吸起始色：亮青(45)↔中青(40)↔亮青(45)，10 帧对称。"""
_SEP_BREATH_LEN: int = 10


def get_sep_breath_color(frame: int) -> str:
    """根据帧号返回当前分隔线呼吸起始色的 ANSI 256 色序列。

    使用 AnimatorContext.sine_color 做正弦波呼吸，
    在中青(40)↔亮青(45)间平滑过渡。

    Args:
        frame: 呼吸帧号（自动取模 _SEP_BREATH_LEN）。

    Returns:
        ANSI 256 色前景色序列，格式 ``\\033[38;5;{color}m``。
    """
    if frame > 0:
        try:
            color = AnimatorContext.get_default().sine_color(40, 45, 10)
            return f"\033[38;5;{color}m"
        except Exception:
            pass
    if not _SEP_BREATH_COLORS:
        return _COLOR_SEP_START
    idx = frame % _SEP_BREATH_LEN
    color = _SEP_BREATH_COLORS[idx] if idx < len(_SEP_BREATH_COLORS) else 45
    return f"\033[38;5;{color}m"


# ── 提示符辉光呼吸（Phase 3 增强） ──────────────────────────
def get_prompt_glow_color(frame: int) -> str:
    """返回当前提示符辉光色的 ANSI 256 色前景序列。

    使用 AnimatorContext.sine_color 做正弦波呼吸，
    在亮青(45)↔亮青高亮(81)间平滑过渡，产生辉光感。

    Args:
        frame: 呼吸帧号。

    Returns:
        ANSI 256 色前景色序列，格式 ``\\033[38;5;{color}m``。
    """
    try:
        color = AnimatorContext.get_default().sine_color(45, 81, 12)
    except Exception:
        color = 45
    return f"\033[38;5;{color}m"


# ── 呼吸背景辅助（Phase 3 增强） ──────────────────────────
def get_breath_bg_color(frame: int) -> str:
    """返回当前呼吸背景色的 ANSI 256 色背景序列。

    在 _COLOR_BREATH_BG 列表上使用 sine_color_range 做正弦波插值，
    产生平滑的呼吸背景效果。

    Args:
        frame: 呼吸帧号。

    Returns:
        ANSI 256 色背景色序列，格式 ``\\033[48;5;{color}m``。
    """
    try:
        color = sine_color_range(frame, _COLOR_BREATH_BG)
    except Exception:
        color = _COLOR_BREATH_BG[frame % len(_COLOR_BREATH_BG)] if _COLOR_BREATH_BG else 236
    return f"\033[48;5;{color}m"
