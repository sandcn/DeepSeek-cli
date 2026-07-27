"""状态行共享格式化纯函数模块 — 供 StatusBarWidget 使用。

提取自 status_bar.py 和 status_bar_widget.py 中的公共格式化逻辑。
所有函数为纯函数（不依赖 self 或实例状态），输入数据输出 ANSI 字符串。
颜色常量从 bottom_bar/theme.py 导入，无循环依赖。
"""

from __future__ import annotations

from ..core.formatter import format_elapsed, format_token_count, format_speed
from ..core.effects import build_glow_ansi
from ..animation.animator import AnimatorContext

from .bottom_bar.theme import (
    _COLOR_ACCENT,
    _COLOR_DIM,
    _COLOR_RESET,
    _COLOR_TIME,
    _COLOR_TOKEN,
    _COLOR_SPEED,
    _COLOR_TOOL_OK,
    _COLOR_TOOL_FAIL,
)


def build_model_label(model_name: str, status_active: bool, frame: int) -> str:
    """模型名呼吸色标签 — 带 · 图标和正弦波脉动色号。

    活跃态时 · 图标做正弦波呼吸（色号 36↔45），模型名使用主题强调色。
    非活跃态时 · 图标和模型名均使用静态主题强调色。

    Args:
        model_name: 模型名。
        status_active: 是否处于活跃状态（流式输出/工具调用中）。
        frame: 当前帧号（用于呼吸动画，通常来自 AnimatorContext.frame）。

    Returns:
        格式化的 ANSI 字符串（含颜色序列）。
        若 model_name 为空字符串，返回空字符串。
    """
    if not model_name:
        return ""

    ctx = AnimatorContext.get_default()
    if status_active:
        dot_color = ctx.sine_color(36, 45, 4, frame=frame)
        dot = f"\033[38;5;{dot_color}m\u00b7\033[0m"
    else:
        dot = f"{_COLOR_ACCENT}\u00b7{_COLOR_RESET}"

    return f"{dot} {_COLOR_ACCENT}{model_name}{_COLOR_RESET}"


def build_tool_count_text(
    tool_count: int,
    tool_total: int,
    tool_fail_count: int,
    frame: int,
) -> str:
    """工具计数文本（含 glow_gear 装饰）。

    格式规则：
      - 有运行中工具：``· <运行中>→<总数>``（总数绿/红分色）
      - 无运行中工具：``· <成功>/<总数>``（成功绿、总数红分色）
      - 无失败：``· <总数>``（纯绿）
      前缀带 glow_gear 呼吸装饰点。

    Args:
        tool_count: 当前运行中的工具数。
        tool_total: 累计工具总数。
        tool_fail_count: 失败工具数。
        frame: 当前帧号（用于 glow_gear 呼吸动画）。

    Returns:
        格式化的 ANSI 字符串（含颜色序列）。
        若 tool_total <= 0，返回空字符串。
    """
    if tool_total <= 0:
        return ""

    glow_gear = f"{build_glow_ansi(frame, 45, 12)}\u00b7\033[0m "

    if tool_count > 0:
        # 运行中格式：· <运行中>→<总数>
        if tool_fail_count > 0:
            total_colored = f"{_COLOR_TOOL_FAIL}{tool_total}{_COLOR_RESET}"
        else:
            total_colored = f"{_COLOR_TOOL_OK}{tool_total}{_COLOR_RESET}"
        return (
            f"{glow_gear}"
            f"{_COLOR_ACCENT}{tool_count}{_COLOR_RESET}"
            f"{_COLOR_DIM}→{_COLOR_RESET}"
            f"{total_colored}"
        )
    else:
        # 无运行工具：· <成功>/<总数> 或 · <总数>
        done = tool_total - tool_fail_count
        if tool_fail_count > 0:
            return (
                f"{glow_gear}"
                f"{_COLOR_TOOL_OK}{done}{_COLOR_RESET}"
                f"{_COLOR_DIM}/{_COLOR_RESET}"
                f"{_COLOR_TOOL_FAIL}{tool_total}{_COLOR_RESET}"
            )
        else:
            return f"{glow_gear}{_COLOR_TOOL_OK}{tool_total}{_COLOR_RESET}"


def build_elapsed_text(elapsed: float) -> str:
    """耗时格式化 — 蓝灰高亮色（含 ⏱ 图标）。

    委托 ``core.formatter.format_elapsed`` 实现时间格式化。

    Args:
        elapsed: 运行时间（秒）。

    Returns:
        格式化的 ANSI 字符串（含颜色序列）。
        若 elapsed <= 0，返回空字符串。
    """
    if elapsed <= 0:
        return ""
    return f"{_COLOR_TIME}\u23f1{_COLOR_RESET} {_COLOR_TIME}{format_elapsed(elapsed)}{_COLOR_RESET}"


def build_token_text(total: int) -> str:
    """令牌数格式化 — 靛蓝色（含 ⬡ 图标）。

    委托 ``core.formatter.format_token_count`` 实现令牌格式化（≥1000 显示 x.xk）。

    Args:
        total: Token 总数。

    Returns:
        格式化的 ANSI 字符串（含颜色序列）。
        若 total <= 0，返回空字符串。
    """
    if total <= 0:
        return ""
    tok_str = format_token_count(total)
    return f"{_COLOR_TOKEN}\u2b21{_COLOR_RESET} {_COLOR_TOKEN}{tok_str}t{_COLOR_RESET}"


def build_speed_text(speed: float) -> str:
    """速度格式化 — 琥珀色（含 ⚡ 图标）。

    委托 ``core.formatter.format_speed`` 实现速度格式化。

    Args:
        speed: Token 速率（tokens/sec）。

    Returns:
        格式化的 ANSI 字符串（含颜色序列）。
        若 speed <= 0，使用暗灰色图标（表示无有效速率）；返回空字符串。
    """
    if speed <= 0:
        return f"{_COLOR_DIM}\u26a1{_COLOR_RESET} {_COLOR_DIM}{format_speed(speed)}t/s{_COLOR_RESET}"
    return f"{_COLOR_SPEED}\u26a1{_COLOR_RESET} {_COLOR_SPEED}{format_speed(speed)}t/s{_COLOR_RESET}"


def build_glow_deco(frame: int) -> str:
    """末尾呼吸装饰点 — 辉光呼吸的 ``·`` 装饰。

    用于状态行末尾添加装饰性呼吸点，增强视觉层次。

    Args:
        frame: 当前帧号（用于呼吸动画）。

    Returns:
        格式化的 ANSI 字符串（含呼吸辉光色），如 ``\033[38;5;{color}m·\033[0m``。
    """
    return f"{build_glow_ansi(frame, 45, 12)}\u00b7\033[0m"


__all__ = [
    "build_model_label",
    "build_tool_count_text",
    "build_elapsed_text",
    "build_token_text",
    "build_speed_text",
    "build_glow_deco",
]
