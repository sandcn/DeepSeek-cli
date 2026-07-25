"""纯文本工具函数 — 从 message_editor / _message_display 提取的统一实现。

.. deprecated::
    ``build_gradient_ansi`` / ``build_gradient_ansi_frame``
    将于未来版本移除。新代码请分别使用 ``build_gradient()`` 或
    从 ``src.tui.core.effects`` 直接导入对应函数。


消除 _message_display._truncate() 和 message_editor._truncate_text()
两个语义相同但签名不同的重复定义，统一为单一 truncate() 函数。

渐变分隔线工具 — 供 _message_display 和 bottom_bar 子包共享使用，
避免渐变分隔线 ANSI 构建逻辑在两处重复实现。

动效增强（2026-07-12）：
  - build_bounce_ansi(): 弹入动效（替代线性渐显）
  - build_enhanced_sep(): 增强版渐变分隔线（支持波动/流光）
  - build_sparkle_ansi()、build_glow_ansi() 已迁移至 effects 模块
  新增函数委托至 _effects.py 实现核心计算，本层只做 ANSI 构建。
"""

from __future__ import annotations

import warnings

from .style import SEP_COLOR_START, SEP_COLOR_END  # 命名色号常量
from ..animation.transitions import FadeIn


def truncate(
    text: str | None,
    max_len: int,
    *,
    suffix: str = "\u2026",  # "…"
    normalize: bool = True,
) -> str:
    """截断文本到指定长度（ANSI 安全）。

    超长时在 max_len 位置截断并追加 suffix（默认 "…"）。
    normalize=True 时先规范化空白（替换换行为空格、去首尾空白），
    与 _message_display._truncate 行为一致。

    若文本包含 ANSI 转义序列，自动降级到视觉宽度截断
    （委托 ansi.truncate_ansi_visual），确保 ANSI 颜色样式保留。

    Args:
        text: 要截断的文本（可含 ANSI 转义码），None 视为空字符串。
        max_len: 最大字符数（不含 suffix）；对 ANSI 文本视为视觉宽度
                 （终端列数，CJK=2，ASCII=1）。
        suffix: 超长时追加的后缀（ANSI 文本始终使用 "…" + RESET）。
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
    # ANSI 安全：含转义序列时使用 visual width 截断，保留颜色样式
    if "\033" in text:
        from .ansi_utils import truncate_ansi_visual
        return truncate_ansi_visual(text, max_len)
    if len(text) <= max_len:
        return text
    return text[:max_len] + suffix


# ── 渐变 ANSI 构建工具（共享模块，避免重复实现） ────────────

def build_gradient_ansi(colors: list[int], char: str = "\u2501", suffix_reset: bool = True) -> str:
    """[Deprecated] 从 256 色号列表构建渐变 ANSI 字符串。

    Deprecated: 请使用 build_gradient() 代替。将在未来版本中移除。

    每个字符使用对应色号，适用于分隔线/进度条等场景。
    色号列表可通过 src.ui.colors.gradient_range() 生成。

    Args:
        colors: 256 色号列表（0-255）。
        char: 显示的字符，默认 ━ (U+2501)。
        suffix_reset: 是否在末尾追加 RESET 序列，默认 True。

    Returns:
        带 ANSI 256 色号的渐变字符串。
    """
    warnings.warn(
        "build_gradient_ansi is deprecated, use build_gradient() instead",
        DeprecationWarning,
        stacklevel=2,
    )
    parts: list[str] = []
    for c in colors:
        parts.append(f"\033[38;5;{c}m{char}")
    result = "".join(parts)
    if suffix_reset:
        result += "\033[0m"
    return result


def build_gradient_ansi_frame(colors: list[int], index: int, char: str = "\u2501", suffix_reset: bool = True) -> str:
    """[Deprecated] 从颜色列表中取第 index 帧的颜色，构建单色 ANSI 字符串。

    Deprecated: 请使用 build_gradient() 代替。将在未来版本中移除。

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
    warnings.warn(
        "build_gradient_ansi_frame is deprecated, use build_gradient() instead",
        DeprecationWarning,
        stacklevel=2,
    )
    if not colors:
        return ""
    color = colors[index % len(colors)]
    result = f"\033[38;5;{color}m{char}"
    if suffix_reset:
        result += "\033[0m"
    return result


# ═══════════════════════════════════════════════════════════
# 渐变构建统一入口（2026-07-26 重构合并）
# ═══════════════════════════════════════════════════════════


def build_gradient(
    width: int,
    start_color: int = SEP_COLOR_START,
    end_color: int = SEP_COLOR_END,
    char: str = "\u2501",
    *,
    effect: str = "none",
    frame: int = 0,
) -> str:
    """构建渐变字符串 — 统一入口。

    合并 4 个渐变构建函数（build_gradient_ansi / build_gradient_ansi_frame /
    make_sep_gradient / make_sep_gradient_enhanced）为单一入口。
    通过 effect 参数选择不同效果策略。

    Args:
        width: 渐变宽度（字符数）。
        start_color: 起始色号，默认 SEP_COLOR_START (45/亮青)。
        end_color: 结束色号，默认 SEP_COLOR_END (237/深灰)。
        char: 填充字符，默认 ━ (U+2501)。
        effect: 效果模式（"none" / "frame" / "sep" / "wave" / "shimmer"）。
        frame: 动画帧号（effect 为 "wave"/"shimmer" 时有效）。

    Returns:
        含 ANSI 256 色号的渐变字符串（含 RESET 后缀）。
    """
    from .gradient import gradient_range

    # 计算基础渐变色号列表
    colors = gradient_range(start_color, end_color, width) if width > 0 else []
    if not colors:
        return ""

    if effect == "wave":
        from .effects import build_wave_sep_ansi
        return build_wave_sep_ansi(colors, frame, char)
    elif effect == "shimmer":
        from .effects import build_shimmer_sep_ansi
        return build_shimmer_sep_ansi(colors, frame, char)
    else:
        # "none", "frame", "sep" — 标准多色渐变（均使用 gradient_range + ANSI 构建）
        ansi_parts: list[str] = []
        for c in colors:
            ansi_parts.append(f"\033[38;5;{c}m{char}")
        return "".join(ansi_parts) + "\033[0m"


def apply_fade_in(text: str, frame: int,
                  easing: str = "smooth",
                  total_frames: int = 6,
                  start_color: int = 240,
                  end_color: int = 253) -> str:
    """对文本应用 FadeIn 入场渐显动效。

    使用 FadeIn 过渡效果生成渐显前缀，包裹文本使其从暗灰渐变至目标色。
    无动效效果时（frame 为 0 或 FadeIn 返回空前缀）返回原文本。

    Args:
        text: 要应用动效的文本。
        frame: 当前帧号。
        easing: 缓动函数，默认 "smooth"。
        total_frames: 渐显总帧数，默认 6。
        start_color: 起始 256 色号，默认 240（暗灰）。
        end_color: 结束 256 色号，默认 253（亮白）。

    Returns:
        带 FadeIn 渐显包裹的文本。无动效时返回原文本。
    """
    if not text or frame <= 0:
        return text
    fade = FadeIn(easing=easing, total_frames=total_frames,
                  start_color=start_color, end_color=end_color)
    fade_prefix = fade.render(frame)
    if fade_prefix:
        return f"{fade_prefix}{text}\033[0m"
    return text


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
    from ..animation.animator import BreathPalette
    palette_name = "error_pulse" if pulse_type == "error" else "warn_pulse"
    color = BreathPalette.get_color(palette_name, frame)
    return f"\033[38;5;{color}m"


def make_sep_gradient(
    width: int,
    start_color: int = SEP_COLOR_START,
    end_color: int = SEP_COLOR_END,
    char: str = "\u2501",
) -> str:
    """生成全宽渐变分隔线（委托至 build_gradient）。

    .. deprecated::
        Use :func:`build_gradient` instead.

    保留原有函数签名（向后兼容），内部委托至 build_gradient() 统一实现。

    Args:
        width: 分隔线字符数。
        start_color: 起始 256 色号，默认 45（亮青）。
        end_color: 结束 256 色号，默认 237（深灰）。
        char: 字符，默认 ━ (U+2501)。

    Returns:
        带 ANSI 256 色渐变的完整分隔线字符串（含 RESET）。
    """
    warnings.warn(
        "make_sep_gradient is deprecated, use build_gradient() instead",
        DeprecationWarning,
        stacklevel=2,
    )
    return build_gradient(width, start_color=start_color, end_color=end_color, char=char, effect="none")


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
    from ..terminal.narrow import is_narrow
    if is_narrow() or frame >= total_frames:
        return ""
    from .effects import bounce_frame_color
    color = bounce_frame_color(frame, total_frames)
    return f"\033[38;5;{color}m"



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
    from ._wave import build_glow_ansi
    glow = build_glow_ansi(frame, base_color, period)
    return f"{glow}\u2502\033[0m"


def parse_theme_color(theme_key: str) -> int | None:
    """从 THEME 语义键提取 256 色号（委托至 Style.parse_theme_color）。

    保持公开接口兼容。

    Args:
        theme_key: THEME 字典中的语义键名（如 "border_breath" / "user" / "title"）。

    Returns:
        色号整数（0-255），键不存在或格式不匹配时返回 None。
    """
    from .style import Style
    return Style.parse_theme_color(theme_key)


# ═══════════════════════════════════════════════════════════
# 新增渲染效果 ANSI 构建器（2026-07-15 框架整合）
# ═══════════════════════════════════════════════════════════


def make_sep_gradient_enhanced(
    width: int,
    start_color: int = SEP_COLOR_START,
    end_color: int = SEP_COLOR_END,
    char: str = "\u2501",
    *,
    effect: str = "none",
    frame: int = 0,
) -> str:
    """增强版渐变分隔线工厂（委托至 build_gradient）。

    .. deprecated::
        Use :func:`build_gradient` instead.

    保留原有函数签名（向后兼容），内部委托至 build_gradient() 统一实现。

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
    warnings.warn(
        "make_sep_gradient_enhanced is deprecated, use build_gradient() instead",
        DeprecationWarning,
        stacklevel=2,
    )
    return build_gradient(width, start_color=start_color, end_color=end_color, char=char, effect=effect, frame=frame)


def build_title_border(
    tl: str, tr: str, h: str,
    box_width: int,
    title: str,
    align: str = "left",
    *,
    pre: str = "",
    suf: str = "",
    title_pre: str = "",
    title_suf: str = "",
    width_func=None,
) -> str:
    """构建带标题的顶部边框行。

    封装标题左/中/右对齐 + 水平填充的通用算法，
    供 layout.py Border._build_top_border() 和 _panel.py Panel._build_top_border()
    统一调用，消除两处重复实现。

    格式: ``{pre}{tl}{h*left}{title_pre}[ {title} ]{title_suf}{pre}{h*right}{tr}{suf}``

    Args:
        tl: 左上角字符。
        tr: 右上角字符。
        h: 水平边框字符。
        box_width: 边框总宽度。
        title: 标题文本（空字符串时不显示标题）。
        align: 标题对齐方式，"left" / "center" / "right"，默认 "left"。
        pre: 边框 ANSI 颜色前缀（对边框字符生效）。
        suf: 边框 ANSI 颜色后缀。
        title_pre: 标题 ANSI 颜色前缀（对标题文本生效）。
        title_suf: 标题 ANSI 颜色后缀。
        width_func: 宽度计算函数，接收字符串返回视觉列宽。默认 ``len``，
                    处理 CJK 字符时传 ``ansi_utils.visual_width``。

    Returns:
        带 ANSI 颜色的顶部边框字符串。
    """
    if width_func is None:
        width_func = len
    fill_w = box_width - 2  # 减去 tl + tr
    if not title:
        return f"{pre}{tl}{h * fill_w}{tr}{suf}"

    # 标题装饰: "[ title ]"
    title_deco = f"[ {title} ]"
    title_vw = width_func(title) + 4  # 括号+空格

    if title_vw > fill_w:
        max_t = fill_w - 4
        if max_t < 1:
            return f"{pre}{tl}{h * fill_w}{tr}{suf}"
        title_disp = title[:max_t]
        title_deco = f"[ {title_disp} ]"
        title_vw = len(title_disp) + 4

    remaining = fill_w - title_vw
    if align == "left":
        left_h, right_h = 0, remaining
    elif align == "right":
        left_h, right_h = remaining, 0
    else:  # center
        left_h = remaining // 2
        right_h = remaining - left_h

    return (
        f"{pre}{tl}{h * left_h}"
        f"{title_pre}{title_deco}{title_suf}"
        f"{pre}{h * right_h}{tr}{suf}"
    )


__all__ = [
    "truncate", "build_gradient_ansi", "build_gradient_ansi_frame",
    "build_warning_pulse_ansi", "make_sep_gradient",
    # 渐变统一入口（2026-07-26 重构）
    "build_gradient",
    # 增强动效（2026-07-12）
    "build_bounce_ansi", "build_left_border_ansi",
    "parse_theme_color",
    "make_sep_gradient_enhanced",
    # FadeIn 动效（从 _base.py 迁移至此）
    "apply_fade_in",
    "build_title_border",
]
