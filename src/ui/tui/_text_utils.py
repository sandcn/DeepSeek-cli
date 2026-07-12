"""纯文本工具函数 — 从 message_editor / _message_display 提取的统一实现。

消除 _message_display._truncate() 和 message_editor._truncate_text()
两个语义相同但签名不同的重复定义，统一为单一 truncate() 函数。

渐变分隔线工具 — 供 _message_display 和 _bottom_bar_pkg 共享使用，
避免渐变分隔线 ANSI 构建逻辑在两处重复实现。

动效增强（2026-07-12）：
  - build_bounce_ansi(): 弹入动效（替代线性渐显）
  - build_enhanced_sep(): 增强版渐变分隔线（支持波动/流光）
  - build_sparkle_ansi(): 闪烁高亮
  - build_glow_ansi(): 辉光呼吸效果
  新增函数委托至 _effects.py 实现核心计算，本层只做 ANSI 构建。
"""

from __future__ import annotations


def truncate(
    text: str | None,
    max_len: int,
    *,
    suffix: str = "\u2026",  # "…"
    normalize: bool = True,
) -> str:
    """截断文本到指定长度。

    超长时在 max_len 位置截断并追加 suffix（默认 "…"）。
    normalize=True 时先规范化空白（替换换行为空格、去首尾空白），
    与 _message_display._truncate 行为一致。

    Args:
        text: 要截断的文本，None 视为空字符串。
        max_len: 最大字符数（不含 suffix）。
        suffix: 超长时追加的后缀。
        normalize: 是否先规范化空白。

    Returns:
        截断后的文本。长度 ≤ max_len 时原样返回。
    """
    if not text:
        return ""
    if max_len < 0:
        raise ValueError(f"max_len must be >= 0, got {max_len}")
    if normalize:
        text = text.replace("\n", " ").strip()
    if len(text) <= max_len:
        return text
    return text[:max_len] + suffix


# ── 渐变 ANSI 构建工具（共享模块，避免重复实现） ────────────

def build_gradient_ansi(colors: list[int], char: str = "\u2501", suffix_reset: bool = True) -> str:
    """从 256 色号列表构建渐变 ANSI 字符串。

    每个字符使用对应色号，适用于分隔线/进度条等场景。
    色号列表可通过 src.ui.colors.gradient_range() 生成。

    Args:
        colors: 256 色号列表（0-255）。
        char: 显示的字符，默认 ━ (U+2501)。
        suffix_reset: 是否在末尾追加 RESET 序列，默认 True。

    Returns:
        带 ANSI 256 色号的渐变字符串。
    """
    parts: list[str] = []
    for c in colors:
        parts.append(f"\033[38;5;{c}m{char}")
    result = "".join(parts)
    if suffix_reset:
        result += "\033[0m"
    return result


def build_gradient_ansi_frame(colors: list[int], index: int, char: str = "\u2501", suffix_reset: bool = True) -> str:
    """从颜色列表中取第 index 帧的颜色，构建单色 ANSI 字符串。

    适用于呼吸/脉动效果中需要逐帧输出单色字符的场景。
    index 超出范围时取模循环。

    Args:
        colors: 256 色号列表（0-255）。
        index: 帧索引，自动取模循环。
        char: 显示的字符，默认 ━ (U+2501)。
        suffix_reset: 是否在末尾追加 RESET 序列，默认 True。

    Returns:
        带 ANSI 256 色号的单色字符串。
    """
    if not colors:
        return ""
    color = colors[index % len(colors)]
    result = f"\033[38;5;{color}m{char}"
    if suffix_reset:
        result += "\033[0m"
    return result


def build_fade_in_ansi(fade_frame: int, total_frames: int = 3) -> str:
    """构建消息入场渐显 ANSI 序列。

    3 帧渐显：帧 0 → 暗灰(238)，帧 1 → 中灰(244)，帧 2+ → RESET（全亮）。
    总耗时约 300ms（3 帧 × ~100ms）。
    窄屏时跳过渐显（返回空字符串）。

    Args:
        fade_frame: 当前渐显帧号（0-based），≥ total_frames 时返回空字符串。
        total_frames: 渐显总帧数，默认 3。

    Returns:
        ANSI 颜色序列，≥ total_frames 时返回空字符串。
    """
    from ._terminal import is_narrow
    if is_narrow() or fade_frame >= total_frames:
        return ""
    # 238(DARK_GRAY_256) → 244(GRAY_256) → RESET 三帧渐亮
    FADE_COLORS = [238, 244]
    if fade_frame < len(FADE_COLORS):
        return f"\033[38;5;{FADE_COLORS[fade_frame]}m"
    return ""


def build_warning_pulse_ansi(
    frame: int,
    pulse_type: str = "error",
) -> str:
    """构建错误/告警脉冲 ANSI 序列。

    错误脉冲：红(196)↔亮红(9)，周期 6 帧
    告警脉冲：黄(214)↔亮黄(11)，周期 6 帧

    Args:
        frame: 当前呼吸帧号（AnimatorContext.breath_frame）。
        pulse_type: "error" 或 "warn"。

    Returns:
        ANSI 前景色序列，或空字符串（第 6 帧不使用额外脉冲色）。
    """
    from ._animator import BreathPalette
    palette_name = "error_pulse" if pulse_type == "error" else "warn_pulse"
    color = BreathPalette.get_color(palette_name, frame)
    return f"\033[38;5;{color}m"


def make_sep_gradient(
    width: int,
    start_color: int = 45,
    end_color: int = 237,
    char: str = "\u2501",
) -> str:
    """生成全宽渐变分隔线（统一工厂）。

    封装 gradient_range() + build_gradient_ansi() 内部组合。
    提供统一的渐变分隔线入口，消除 _message_display 和
    _bottom_bar_pkg/theme 中的两套独立实现。

    Args:
        width: 分隔线字符数。
        start_color: 起始 256 色号，默认 45（亮青）。
        end_color: 结束 256 色号，默认 237（深灰）。
        char: 字符，默认 ━ (U+2501)。

    Returns:
        带 ANSI 256 色渐变的完整分隔线字符串（含 RESET）。
    """
    from ..colors import gradient_range
    colors = gradient_range(start_color, end_color, width)
    return build_gradient_ansi(colors, char=char)


# ═══════════════════════════════════════════════════════════
# 增强动效函数（2026-07-12）
# ═══════════════════════════════════════════════════════════


def build_bounce_ansi(frame: int, total_frames: int = 6) -> str:
    """构建弹入动效 ANSI 序列（带弹跳超调）。

    比 build_fade_in_ansi 更生动：亮度变化带弹跳超调，
    模拟物体落地反弹的视觉效果。

    Args:
        frame: 当前渐显帧号（0-based）。
        total_frames: 弹入总帧数。

    Returns:
        ANSI 颜色序列，≥ total_frames 时返回空字符串。
    """
    from ._terminal import is_narrow
    if is_narrow() or frame >= total_frames:
        return ""
    from ._effects import bounce_frame_color
    color = bounce_frame_color(frame, total_frames)
    return f"\033[38;5;{color}m"


def build_sep_wave(
    colors: list[int], frame: int, char: str = "\u2501",
) -> str:
    """构建波动分隔线 ANSI 字符串。

    在渐变基础上叠加正弦波动，使分隔线看起来像"流动的水波"。
    帧号推进时波动沿分隔线方向传播。

    Args:
        colors: 基础渐变色号列表。
        frame: 当前帧号。
        char: 显示的字符。

    Returns:
        ANSI 格式的波动渐变分隔线（含 RESET）。
    """
    from ._effects import build_wave_sep_ansi
    return build_wave_sep_ansi(colors, frame, char)


def build_sep_shimmer(
    colors: list[int], frame: int, char: str = "\u2501",
) -> str:
    """构建流光扫光分隔线 ANSI 字符串。

    一条亮带沿分隔线方向周期性移动，产生"扫光"效果。
    视觉效果比静态渐变更引人注目。

    Args:
        colors: 基础渐变色号列表。
        frame: 当前帧号。
        char: 显示的字符。

    Returns:
        ANSI 格式的流光分隔线（含 RESET）。
    """
    from ._effects import build_shimmer_sep_ansi
    return build_shimmer_sep_ansi(colors, frame, char)


def build_sparkle_ansi(frame: int, base_color: int = 45, period: int = 6) -> str:
    """构建闪烁高亮 ANSI 序列。

    在 base_color 和 base_color+bright 间闪烁，
    适合用于需要吸引注意力的元素（新消息标记、完成提示等）。

    Args:
        frame: 当前帧号。
        base_color: 基准色号。
        period: 闪烁周期帧数。

    Returns:
        ANSI 前景色序列。
    """
    from ._effects import sparkle_color
    c = sparkle_color(frame, base_color, period=period)
    return f"\033[38;5;{c}m"


def build_glow_ansi(frame: int, base_color: int = 45, period: int = 12) -> str:
    """构建辉光呼吸 ANSI 序列。

    色号在 base_color 和 base_color+20 间正弦呼吸，
    产生"柔光呼吸"的视觉效果。适合标签、图标等元素。

    Args:
        frame: 当前帧号。
        base_color: 基准色号。
        period: 呼吸周期帧数。

    Returns:
        ANSI 前景色序列。
    """
    from ._effects import build_glow_ansi
    return build_glow_ansi(frame, base_color, period)


def build_left_border_ansi(frame: int, base_color: int = 23, period: int = 24) -> str:
    """构建左边缘呼吸边框 ANSI 序列。

    返回带呼吸辉光颜色的边框字符 │（U+2502），
    封装 build_glow_ansi + 边框字符 + RESET 的通用模式，
    供 WriteLineBlock / ErrorBlock / NotificationBlock 统一调用。

    Args:
        frame: 当前帧号（AnimatorContext.frame）。
        base_color: 呼吸基准色号，默认 23（暗青）。
        period: 呼吸周期帧数，默认 24。

    Returns:
        完整的 ANSI 边框序列（含 RESET），格式：
        ``\033[38;5;{color}m│\033[0m``
    """
    glow = build_glow_ansi(frame, base_color, period)
    return f"{glow}\u2502\033[0m"


def parse_theme_color(theme_key: str) -> int | None:
    """从 THEME 语义键中提取 256 色号。

    解析格式如 ``\033[38;5;45m`` 的 ANSI 前景色码，
    返回色号整数。解析失败返回 None。

    用途：组件需要从 THEME 键值中提取色号用于动效构建时，
    统一使用此函数避免各组件重复实现 regex 解析逻辑。

    Args:
        theme_key: THEME 字典中的语义键名（如 "border_breath" / "user" / "title"）。

    Returns:
        色号整数（0-255），解析失败或键不存在时返回 None。
    """
    import re
    from ..theme import THEME
    color_str = THEME.get(theme_key, "")
    if not color_str:
        return None
    match = re.search(r"38;5;(\d+)", color_str)
    if match:
        return int(match.group(1))
    return None


def make_sep_gradient_enhanced(
    width: int,
    start_color: int = 45,
    end_color: int = 237,
    char: str = "\u2501",
    *,
    effect: str = "none",
    frame: int = 0,
) -> str:
    """增强版渐变分隔线工厂（支持动效）。

    在 make_sep_gradient 基础上增加波动/流光等动效，
    统一管理所有分隔线效果的创建。

    Args:
        width: 分隔线字符数。
        start_color: 起始 256 色号。
        end_color: 结束 256 色号。
        char: 显示的字符。
        effect: 动效类型 "none"|"wave"|"shimmer"。
        frame: 当前帧号（effect 非 none 时使用）。

    Returns:
        带 ANSI 渐变的完整分隔线字符串（含 RESET）。
    """
    from ..colors import gradient_range
    colors = gradient_range(start_color, end_color, width)
    if effect == "wave" and frame > 0:
        return build_sep_wave(colors, frame, char)
    elif effect == "shimmer" and frame > 0:
        return build_sep_shimmer(colors, frame, char)
    return build_gradient_ansi(colors, char)


__all__ = [
    "truncate", "build_gradient_ansi", "build_gradient_ansi_frame",
    "build_fade_in_ansi", "build_warning_pulse_ansi", "make_sep_gradient",
    # 增强动效（2026-07-12）
    "build_bounce_ansi", "build_sep_wave", "build_sep_shimmer",
    "build_sparkle_ansi", "build_glow_ansi", "build_left_border_ansi",
    "parse_theme_color",
    "make_sep_gradient_enhanced",
]
