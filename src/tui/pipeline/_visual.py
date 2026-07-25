"""纯视觉组件 — message_display 的字符串生成与视觉渲染职责。

包含分隔线、角色标签、提示文本、角色配置等纯视觉/字符串函数。
少数函数（如 _build_messages_header）写 I/O，其余只生成字符串。
"""

from __future__ import annotations

import logging
import re
from typing import Any, Callable

from src._compat import dataclass
from ..terminal.terminal import (get_terminal_width, NARROW_THRESHOLD,
                                 is_narrow, narrow_truncate, narrow_indent,
                                 narrow_sep_width)
from ..core.text_utils import (truncate, build_gradient_ansi, build_fade_in_ansi,
                               build_warning_pulse_ansi, make_sep_gradient,
                               build_bounce_ansi,
                               build_sparkle_ansi, build_glow_ansi)
from ..animation.animator import AnimatorContext, BreathPalette
from ..core.effects import sine_color_range

from ._io import _manager


# ── 颜色快捷引用（256 色） ─────────────────────────────────

from ...core.constants import (
    CYAN_256, GRAY_256 as _D, RESET as _R,
    YELLOW_256 as _Y, BRIGHT_CYAN_256 as _BC, BRIGHT_WHITE_256 as _BW,
    BOLD as _BD, BRIGHT_GREEN_256 as _BG,
)
from ..core.gradient import gradient_range
from ..core.theme import THEME
from ..core.style import Style, StyleSheet

# 角色标签 sparkle 静态基准色（与动态呼吸色解耦，避免双重调制噪音）
_SPARKLE_BASE_USER = 45    # 青色
_SPARKLE_BASE_ASST = 41    # 绿色
_SPARKLE_BASE_TOOL = 221   # 琥珀黄

_logger = logging.getLogger(__name__)

# ── 常量 ─────────────────────────────────────────────────

_TOOL_CALL_PREVIEW_LEN = 100
_TOOL_CONTENT_PREVIEW_LEN = 200
_ASSISTANT_MD_THRESHOLD = 100
_LINE_TRUNCATE_WIDTH = 55
_SEP_LINE_WIDTH = 25
_NARROW_SEP_REDUCTION = 10

# ── 美观分隔线（256 色 + 全宽渐变增强版） ───────────────────

def _make_gradient_sep(start_color: int = 45, end_color: int = 237, steps: int = 0, breath_frame: int = 0) -> str:
    """生成全宽渐变分隔线，委托到 _text_utils.make_sep_gradient()。

    保留原有函数签名（向后兼容），实际调用统一工厂实现。
    窄屏时自动缩短宽度（通过 narrow_sep_width），保持窄屏简洁性。

    Args:
        start_color: 起始 256 色号（默认 45/青色）
        end_color: 结束 256 色号（默认 237/深灰）
        steps: 渐变步数（= 字符数）。0 表示根据终端宽度自适应。
        breath_frame: 呼吸帧号，0 表示使用静态色。

    Returns:
        含 ANSI 256 色号的渐变分隔线字符串，前缀两个空格缩进。
        极窄终端（宽度 ≤ 4）时降级为单色分隔线。
    """
    if steps <= 0:
        tw = get_terminal_width()
        if tw <= 0:
            # 极窄兜底：单色分隔线，避免 gradient_range 空列表
            _logger.debug("_make_gradient_sep: tw=%d <= 0, fallback to mono sep", tw)
            return f"  {chr(0x2501) * 8}{_R}"
        if tw >= NARROW_THRESHOLD:
            steps = min(tw - 4, 80)  # 宽屏全宽
        else:
            steps = narrow_sep_width(50)  # 窄屏自适应缩短
        if steps < 2:
            steps = 2  # 自动检测时最少 2 步确保起止色号都可见
        _logger.debug(
            "_make_gradient_sep: tw=%d, steps=%d", tw, steps,
        )
    if breath_frame > 0 and not is_narrow():
        start_color = BreathPalette.get_color("sep_msg", breath_frame)
        return "  " + make_sep_gradient(steps, start_color=start_color, end_color=end_color, effect="wave", frame=breath_frame)
    return "  " + make_sep_gradient(steps, start_color=start_color, end_color=end_color)


def _make_think_sep(breath_frame: int = 0) -> str:
    """生成全宽渐变思考分隔线（含 ⚡ 脉动闪烁效果）。

    左右两侧为青(45)→深灰(237)渐变，中间为 ⚡思考 标签。
    呼吸色范围：深蓝(24)↔亮青(87)，12 帧周期。
    ⚡ 符号使用偏移 3 帧的相位色，与主呼吸色交替形成闪烁效果。
    窄屏时自动缩短宽度。

    Args:
        breath_frame: 呼吸帧号，0 表示使用静态青色。
    """
    tw = get_terminal_width()
    if tw >= NARROW_THRESHOLD:
        full_width = min(tw - 4, 80)
    else:
        full_width = narrow_sep_width(50)
    # "  ⚡思考  " 视觉宽度约 8 字符
    if breath_frame > 0 and not is_narrow():
        # ⚡ 符号使用 build_sparkle_ansi 闪烁更生动
        think_color = BreathPalette.get_color("think", breath_frame)
        center_text = (
            f"  {build_sparkle_ansi(breath_frame, 45, 6)}\u26a1"  # ⚡ 闪烁更生动
            f"\033[38;5;{think_color}m\u601d\u8003"                # 思考 主呼吸色
            f"{_R}  "
        )
    else:
        center_text = f"  {CYAN_256}\u26a1\u601d\u8003{_R}  "
    half_width = max(4, (full_width - 8) // 2)
    left_colors = gradient_range(45, 237, half_width)
    right_colors = gradient_range(45, 237, half_width)
    left = "".join(f"\033[38;5;{c}m\u2501" for c in left_colors)
    right = "".join(f"\033[38;5;{c}m\u2501" for c in right_colors)
    return f"  {left}{_R}{center_text}{right}{_R}"


def _make_think_end() -> str:
    """生成全宽渐变思考结束标记。

    使用青(45)→深灰(237)渐变，宽度为思考分隔线的一半。
    """
    tw = get_terminal_width()
    if tw >= NARROW_THRESHOLD:
        full_width = min(tw - 4, 80)
    else:
        full_width = narrow_sep_width(50)
    width = max(4, full_width // 2)
    colors = gradient_range(45, 237, width)
    line = "".join(f"\033[38;5;{c}m\u2501" for c in colors)
    return f"  {line}{_R}"

# ── 美观角色标签（256 色背景增强版） ──────────────────────
# 窄屏时通过 _role_tag() 函数降级为无背景色（仅保留文字色）
_USER_TAG = (
    f"\033[48;5;235m{_BC}\u25cf {_R}"               # 暗灰背景(235) + ●(亮青81)
    f"\033[48;5;235m{_BC}USER{_R}"                  # 暗灰背景(235) + USER(亮青81)
    f"\033[0m"                                       # 全重置，背景色不溢出
)
_ASST_TAG = (
    f"\033[48;5;22m{_BG}\u25c6 {_R}"                # 暗绿背景(22) + ◆(亮绿47)
    f"\033[48;5;22m{_BG}ASSISTANT{_R}"               # 暗绿背景(22) + ASSISTANT(亮绿47)
    f"\033[0m"                                       # 全重置，背景色不溢出
)
_TOOL_TAG = (
    f"\033[48;5;94m\033[38;5;227m\u2699 {_R}"       # 暗黄背景(94) + ⚙(亮黄227)
    f"\033[48;5;94m\033[38;5;227mTOOL{_R}"           # 暗黄背景(94) + TOOL(亮黄227)
    f"\033[0m"                                       # 全重置，背景色不溢出
)

# ── 消息选择器提示文本 — 按窄屏/宽屏分两组 ────────────────
_HINT_NARROW = "  \u2191\u2193\u9009  Enter\u91cd\u5199  r\u6062\u590d  d\u622a\u65ad"     # ↑↓选 Enter重写 r恢复 d截断
_HINT_NARROW_ALL = "  R\u5168\u6062\u590d"                                                 # R全恢复
_HINT_WIDE = "  \u2191\u2193 \u9009\u62e9  Enter \u4ece\u6b64\u91cd\u5199  r \u4ece\u6b64\u6062\u590d  d \u4ece\u6b64\u622a\u65ad"  # ↑↓ 选择  Enter 从此重写  r 从此恢复  d 从此截断
_HINT_WIDE_ALL = "  R \u6062\u590d\u5168\u90e8"                                            # R 恢复全部
_HINT_ESC = "  Esc\u8fd4\u56de\n"                                                           # Esc返回\n


def _build_hint_text(is_current: bool, narrow: bool) -> str:
    """根据屏幕宽度和是否当前会话构建操作提示文本。"""
    if narrow:
        hint = _HINT_NARROW
        if not is_current:
            hint += _HINT_NARROW_ALL
    else:
        hint = _HINT_WIDE
        if not is_current:
            hint += _HINT_WIDE_ALL
    return hint + _HINT_ESC


# ── 工具函数 ──────────────────────────────────────────────

def _role_icon(role: str, role_map: dict[str, RoleConfig] | None = None) -> str:
    """角色图标映射：优先从 role_map 获取，回退到 user/assistant/tool 硬编码。

    使用语义化的简约符号 + 视觉层级区分：
      - user（用户）      → ●  （实心圆，表示用户输入）
      - assistant（助手） → ◆  （实心菱形，表示 AI 回复）
      - tool（工具）      → ⚙  （齿轮，表示工具调用）
      - 其他             → ·   （中性占位符）

    所有符号均为标准 Unicode，无需 Nerd Font 即可正常显示。

    Args:
        role: 角色名。
        role_map: 可选的角色配置映射，不传时使用硬编码默认值。
    """
    if role_map is not None:
        cfg = role_map.get(role)
        if cfg is not None:
            return cfg.icon
    return {"user": "\u25cf", "assistant": "\u25c6", "tool": "\u2699"}.get(role, "\u00b7")


def _role_tag(role: str, breath_frame: int = 0, role_map: dict[str, RoleConfig] | None = None) -> str:
    """角色标签：将 role 映射为美观的彩色标签。

    优先从 role_map 获取标签生成函数；未提供 role_map 或角色不在映射中时，
    回退到 user/assistant/tool 硬编码逻辑（向后兼容）。

    宽屏模式（≥80列）：返回带背景色的增强标签。
    窄屏模式（<80列）：降级为无背景色，仅保留文字色，确保可读性。

    Args:
        role: 角色名（user/assistant/tool）。
        breath_frame: 呼吸帧号，0 表示使用静态色。
        role_map: 可选的角色配置映射，不传时使用硬编码默认值。
    """
    if role_map is not None:
        cfg = role_map.get(role)
        if cfg is not None:
            return cfg.tag_func(breath_frame)

    if is_narrow():
        # 窄屏降级：无背景色，仅保留图标和文字色
        return {
            "user": f"{_BC}\u25cf {_BC}USER{_R}",
            "assistant": f"{_BG}\u25c6 {_BG}ASSISTANT{_R}",
            "tool": f"\033[38;5;227m\u2699 \033[38;5;227mTOOL{_R}",
        }.get(role, _D + "\u00b7" + _R)

    if breath_frame > 0:
        # 从 THEME['tag_breath'] 提取呼吸基准色号，构建左侧呼吸边框
        _tag_breath_match = re.search(r"38;5;(\d+)", THEME['tag_breath'])
        if _tag_breath_match:
            _border_base = int(_tag_breath_match.group(1))
            _border_ansi = build_glow_ansi(breath_frame, _border_base, 12)
            _border_prefix = f"{_border_ansi}\u2503\033[0m "  # ┃ 呼吸边框
        else:
            _border_prefix = ""

        if role == "user":
            bc = BreathPalette.get_color("role_user", breath_frame)
            # 图标使用 sparkle 闪烁（静态基准色，与呼吸色解耦），文字保持呼吸色
            return (
                f"{_border_prefix}"
                f"\033[48;5;235m{build_sparkle_ansi(breath_frame, _SPARKLE_BASE_USER, 6)}\u25cf {_R}"
                f"\033[48;5;235m\033[38;5;{bc}mUSER{_R}"
                f"\033[0m"
            )
        elif role == "assistant":
            bc = BreathPalette.get_color("role_asst", breath_frame)
            # 图标使用 sparkle 闪烁（静态基准色，与呼吸色解耦），文字保持呼吸色
            return (
                f"{_border_prefix}"
                f"\033[48;5;22m{build_sparkle_ansi(breath_frame, _SPARKLE_BASE_ASST, 6)}\u25c6 {_R}"
                f"\033[48;5;22m\033[38;5;{bc}mASSISTANT{_R}"
                f"\033[0m"
            )
        elif role == "tool":
            bc = BreathPalette.get_color("role_tool", breath_frame)
            # 图标使用 sparkle 闪烁（静态基准色，与呼吸色解耦），文字保持呼吸色
            return (
                f"{_border_prefix}"
                f"\033[48;5;94m{build_sparkle_ansi(breath_frame, _SPARKLE_BASE_TOOL, 6)}\u2699 {_R}"
                f"\033[48;5;94m\033[38;5;{bc}mTOOL{_R}"
                f"\033[0m"
            )

    return {
        "user": _USER_TAG,
        "assistant": _ASST_TAG,
        "tool": _TOOL_TAG,
    }.get(role, _D + "\u00b7" + _R)


# ── 角色配置（泛化：从硬编码到可配置） ─────────────────

@dataclass(slots=True)
class RoleConfig:
    """消息角色显示配置 — 封装图标、标签生成和自定义显示行为。

    用于泛化 _role_icon / _role_tag 中硬编码的 user/assistant/tool 角色映射，
    支持框架层通过 role_map 参数注入自定义角色。

    属性:
        icon: Unicode 角色图标字符（如 ● ◆ ⚙）。
        tag_func: 标签生成函数，签名 (breath_frame: int) -> str，
                  返回含完整 ANSI 转义码的标签字符串。
        display_func: 可选的自定义消息显示函数，
                      签名需与 _display_user / _display_assistant 兼容。
    """
    icon: str
    tag_func: Callable[[int], str]
    display_func: Callable[..., None] | None = None

    @staticmethod
    def defaults() -> dict[str, "RoleConfig"]:
        """返回默认角色映射（user/assistant/tool），保持向后兼容。

        通过模块级 _ROLE_DEFAULTS 获取，避免每次调用重新构建。
        """
        return dict(_ROLE_DEFAULTS)


# ── 默认标签生成函数（提取自 _role_tag 的硬编码逻辑） ──

def _make_default_user_tag(breath_frame: int = 0) -> str:
    """生成 user 角色标签（提取自 _role_tag 的硬编码逻辑）。

    窄屏降级为无背景色版本，宽屏支持呼吸动画。
    """
    if is_narrow():
        return f"{_BC}\u25cf {_BC}USER{_R}"
    if breath_frame > 0:
        _tag_breath_match = re.search(r"38;5;(\d+)", THEME['tag_breath'])
        _border_prefix = ""
        if _tag_breath_match:
            _border_base = int(_tag_breath_match.group(1))
            _border_ansi = build_glow_ansi(breath_frame, _border_base, 12)
            _border_prefix = f"{_border_ansi}\u2503\033[0m "
        bc = BreathPalette.get_color("role_user", breath_frame)
        return (
            f"{_border_prefix}"
            f"\033[48;5;235m{build_sparkle_ansi(breath_frame, _SPARKLE_BASE_USER, 6)}\u25cf {_R}"
            f"\033[48;5;235m\033[38;5;{bc}mUSER{_R}"
            f"\033[0m"
        )
    return _USER_TAG


def _make_default_assistant_tag(breath_frame: int = 0) -> str:
    """生成 assistant 角色标签（提取自 _role_tag 的硬编码逻辑）。"""
    if is_narrow():
        return f"{_BG}\u25c6 {_BG}ASSISTANT{_R}"
    if breath_frame > 0:
        _tag_breath_match = re.search(r"38;5;(\d+)", THEME['tag_breath'])
        _border_prefix = ""
        if _tag_breath_match:
            _border_base = int(_tag_breath_match.group(1))
            _border_ansi = build_glow_ansi(breath_frame, _border_base, 12)
            _border_prefix = f"{_border_ansi}\u2503\033[0m "
        bc = BreathPalette.get_color("role_asst", breath_frame)
        return (
            f"{_border_prefix}"
            f"\033[48;5;22m{build_sparkle_ansi(breath_frame, _SPARKLE_BASE_ASST, 6)}\u25c6 {_R}"
            f"\033[48;5;22m\033[38;5;{bc}mASSISTANT{_R}"
            f"\033[0m"
        )
    return _ASST_TAG


def _make_default_tool_tag(breath_frame: int = 0) -> str:
    """生成 tool 角色标签（提取自 _role_tag 的硬编码逻辑）。"""
    if is_narrow():
        return f"\033[38;5;227m\u2699 \033[38;5;227mTOOL{_R}"
    if breath_frame > 0:
        _tag_breath_match = re.search(r"38;5;(\d+)", THEME['tag_breath'])
        _border_prefix = ""
        if _tag_breath_match:
            _border_base = int(_tag_breath_match.group(1))
            _border_ansi = build_glow_ansi(breath_frame, _border_base, 12)
            _border_prefix = f"{_border_ansi}\u2503\033[0m "
        bc = BreathPalette.get_color("role_tool", breath_frame)
        return (
            f"{_border_prefix}"
            f"\033[48;5;94m{build_sparkle_ansi(breath_frame, _SPARKLE_BASE_TOOL, 6)}\u2699 {_R}"
            f"\033[48;5;94m\033[38;5;{bc}mTOOL{_R}"
            f"\033[0m"
        )
    return _TOOL_TAG


# 模块级默认角色映射（供 _role_icon / _role_tag 回退使用）
_ROLE_DEFAULTS: dict[str, RoleConfig] = {
    "user": RoleConfig(
        icon="\u25cf",
        tag_func=_make_default_user_tag,
        display_func=None,  # 使用默认 _display_user
    ),
    "assistant": RoleConfig(
        icon="\u25c6",
        tag_func=_make_default_assistant_tag,
        display_func=None,  # 使用默认 _display_assistant
    ),
    "tool": RoleConfig(
        icon="\u2699",
        tag_func=_make_default_tool_tag,
        display_func=None,  # 使用默认 _display_tool_calls
    ),
}


# ── 消息列表全量显示（标题行） ──────────────────────────

def _build_messages_header() -> str:
    """构建消息列表美化标题行并写入输出，返回尾部装饰线字符串。

    Returns:
        尾部装饰线字符串（用于消息列表末尾的闭合）。
    """
    sep_width = narrow_sep_width(50)
    sep = "\u2501" * sep_width
    # ★ 美化：渐变青色花括号 + 信封图标
    # 左右装饰 ━ 从中心向外渐变：中心亮青(45)→边缘深灰(237)
    _header_side_colors = gradient_range(237, 45, 8)  # 深灰→亮青（从左到中心，更丰富渐变）
    _header_side_left = "".join(f"\033[38;5;{c}m\u2501" for c in _header_side_colors)
    _header_side_right = "".join(f"\033[38;5;{c}m\u2501" for c in reversed(_header_side_colors))  # 亮青→深灰（从中心到右）
    _header_bf = AnimatorContext.get_default().breath_frame
    # 标题区域使用呼吸色（窄屏或呼吸帧为0时退化为静态亮青）
    if _header_bf > 0 and not is_narrow():
        _title_color = BreathPalette.get_color("sep_msg", _header_bf)
        header = f"  {_header_side_left}{_R}  \033[38;5;{_title_color}m\u2770\u2709\u6d88\u606f\u5217\u8868\u2771{_R}  {_header_side_right}{_R}"
    else:
        header = f"  {_header_side_left}{_R}  {_BC}\u2770\u2709\u6d88\u606f\u5217\u8868\u2771{_R}  {_header_side_right}{_R}"
    _manager.write_line(f"\n{header}")
    return sep


__all__ = [
    "_make_gradient_sep",
    "_make_think_sep",
    "_make_think_end",
    "_USER_TAG",
    "_ASST_TAG",
    "_TOOL_TAG",
    "_build_hint_text",
    "_role_icon",
    "_role_tag",
    "RoleConfig",
    "_ROLE_DEFAULTS",
    "_make_default_user_tag",
    "_make_default_assistant_tag",
    "_make_default_tool_tag",
    "_build_messages_header",
    "_TOOL_CALL_PREVIEW_LEN",
    "_TOOL_CONTENT_PREVIEW_LEN",
    "_ASSISTANT_MD_THRESHOLD",
]
