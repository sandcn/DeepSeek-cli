"""
消息显示函数 — 从 message_editor 拆分出的纯显示/格式化职责。

包含：
- 角色图标、截断、沙盒信息格式化
- 单条消息显示（tool_calls / user / assistant）
- 消息列表全量显示（_display_messages）
- 单行摘要生成（_msg_line）
- 消息选择器行渲染（_make_message_lines）
"""

from __future__ import annotations

import logging
import re
from src._compat import dataclass
from typing import Any
import os

from ...renderer import IncrementalRenderer
from ...core.sandbox_manager import get_sandbox_manager as _get_sandbox_manager
from ..terminal.terminal import (get_terminal_width, NARROW_THRESHOLD,
                                 is_narrow, narrow_truncate, narrow_indent,
                                 narrow_sep_width)
from ..core.text_utils import (truncate, build_gradient_ansi, build_fade_in_ansi,
                               build_warning_pulse_ansi, make_sep_gradient,
                               build_bounce_ansi, make_sep_gradient_enhanced,
                               build_sparkle_ansi, build_glow_ansi)
from ..core.animator import AnimatorContext, BreathPalette
from ..core.effects import sine_color_range
from ...ui.output_target import IOutputTarget, TerminalTarget


def _scroll_window(cursor: int, state: dict, total: int) -> tuple[int, int]:
    """计算可见窗口 [start, end)。"""
    max_visible = state.get("max", 15)
    if total <= max_visible:
        return 0, total
    offset = state.get("scroll", 0)
    if cursor < offset:
        offset = cursor
    elif cursor >= offset + max_visible:
        offset = cursor - max_visible + 1
    state["scroll"] = offset
    return offset, min(offset + max_visible, total)


# ── 输出目标管理器（封装模块级可变状态） ──

class OutputManager:
    """输出目标管理器 — 封装 IOutputTarget 的全局访问。

    消除模块级可变变量 + getter/setter 函数模式，
    提供统一写入接口。模块级实例供内部函数使用，
    get_message_output/set_message_output 保持向后兼容。
    """

    def __init__(self, target: IOutputTarget | None = None) -> None:
        self._target: IOutputTarget = target if target is not None else TerminalTarget()

    @property
    def target(self) -> IOutputTarget:
        return self._target

    @target.setter
    def target(self, new_target: IOutputTarget | None) -> None:
        self._target = new_target if new_target is not None else TerminalTarget()

    def write(self, text: str) -> None:
        self._target.write(text)

    def write_line(self, text: str = "") -> None:
        self._target.write_line(text)


# 模块级实例（保持向后兼容）
# 设计妥协：模块级可变状态违反「零模块级可变状态」架构原则，
# 但完全迁移为实例级依赖注入需改动所有调用方（message_editor 等），
# 当前提供 reset_message_output() 供测试隔离使用。
_manager: OutputManager = OutputManager()


def reset_message_output() -> None:
    """重置消息输出目标为默认 TerminalTarget（测试用）。"""
    _manager.target = TerminalTarget()


def get_message_output() -> IOutputTarget:
    """获取当前消息显示输出目标（动态解析，非 import 时固定值）。

    所有 display 函数通过此函数获取输出目标，确保 set_message_output()
    注入后所有调用方即时生效（消除 Python import 值绑定导致的引用僵死）。
    """
    return _manager.target


def set_message_output(target: IOutputTarget | None) -> None:
    """设置消息显示模块的输出目标（用于测试注入）。

    Args:
        target: 输出目标实例。None 时恢复默认 TerminalTarget。
    """
    _manager.target = target


# ── 模块级便捷写入函数（统一外部调用方访问路径） ─────

def write(text: str) -> None:
    """写入文本到消息显示输出。"""
    _manager.write(text)


def write_line(text: str = "") -> None:
    """写入一行文本到消息显示输出。"""
    _manager.write_line(text)


# ── I/O 注入适配器 ──────────────────────────────────────

@dataclass(slots=True)
class _OutputFileAdapter:
    """将 IOutputTarget 适配为 file-like 对象，供 IncrementalRenderer._file 注入。

    Rich Console 的 file 参数需要 write + flush 方法。
    此适配器将 IOutputTarget.write() 桥接到 file-like 协议，
    实现显示输出的 I/O 注入闭环。
    """
    _target: IOutputTarget

    def write(self, s: str) -> None:
        self._target.write(s)

    def flush(self) -> None:
        pass  # IOutputTarget 无 flush 概念

    def isatty(self) -> bool:
        return hasattr(self._target, 'isatty') and self._target.isatty()


# ── 颜色快捷引用（256 色） ─────────────────────────────────

from ...core.constants import (
    CYAN_256, GRAY_256 as _D, RESET as _R,
    YELLOW_256 as _Y, BRIGHT_CYAN_256 as _BC, BRIGHT_WHITE_256 as _BW,
    BOLD as _BD, BRIGHT_GREEN_256 as _BG,
)
from ...ui.colors import gradient_range
from ...ui.theme import THEME

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
        return "  " + make_sep_gradient_enhanced(steps, start_color=start_color, end_color=end_color, effect="wave", frame=breath_frame)
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

def _role_icon(role: str) -> str:
    """角色图标映射：用户·助手·工具 · 系统空置。

    使用语义化的简约符号 + 视觉层级区分：
      - user（用户）      → ●  （实心圆，表示用户输入）
      - assistant（助手） → ◆  （实心菱形，表示 AI 回复）
      - tool（工具）      → ⚙  （齿轮，表示工具调用）
      - 其他             → ·   （中性占位符）

    所有符号均为标准 Unicode，无需 Nerd Font 即可正常显示。
    """
    return {"user": "\u25cf", "assistant": "\u25c6", "tool": "\u2699"}.get(role, "\u00b7")


def _role_tag(role: str, breath_frame: int = 0) -> str:
    """角色标签：将 role 映射为美观的彩色标签。

    宽屏模式（≥80列）：返回带背景色的增强标签。
    窄屏模式（<80列）：降级为无背景色，仅保留文字色，确保可读性。

    Args:
        role: 角色名（user/assistant/tool）。
        breath_frame: 呼吸帧号，0 表示使用静态色。
    """
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



def _format_sandbox_text(sandbox_info: dict | None) -> str:
    """格式化沙盒信息为显示文本。"""
    if not sandbox_info or sandbox_info.get("count", 0) == 0:
        return ""
    changes = sandbox_info.get("file_changes", [])
    count = sandbox_info.get("count", 0)
    parts = []
    for fc in changes:
        name = os.path.basename(fc["file_path"])
        ctype = fc["change_type"]
        parts.append(f"{name}({ctype})")
    return f" [沙盒: 改变了{count}个文件: " + ", ".join(parts) + "]"


def _get_sandbox_text(agent: Any, idx_map: list[int] | None, data_idx: int) -> str:
    """获取沙盒信息文本。"""
    if not agent or not idx_map or data_idx >= len(idx_map):
        return ""
    sandbox_manager = _get_sandbox_manager()
    if not sandbox_manager:
        return ""
    real_idx = idx_map[data_idx]
    info = sandbox_manager.get_sandbox_info(real_idx) or {}
    return _format_sandbox_text(info)


def _get_user_sandbox_text(
    data: list[dict], data_idx: int, agent: Any, idx_map: list[int] | None,
) -> str:
    """对于 user 消息，查找其后最近的 assistant(tool_calls) 的沙盒信息。"""
    if not agent or not idx_map:
        return ""
    for j in range(data_idx + 1, len(data)):
        m = data[j]
        if m.get("role") == "user":
            break
        if m.get("tool_calls"):
            return _get_sandbox_text(agent, idx_map, j)
    return ""


# ═══════════════════════════════════════════════════════════
# MessageDisplayContext — 消除 agent/idx_map/data 三重参数传递
# ═══════════════════════════════════════════════════════════

def _non_system_messages(messages: list[dict]) -> tuple[list[dict], list[int]]:
    """过滤 system 消息，返回 (data, idx_map)。

    data = messages 中 role != "system" 的消息列表，
    idx_map 是 data 索引到 messages 全量索引的映射。
    """
    data: list[dict] = []
    idx_map: list[int] = []
    for i, m in enumerate(messages):
        if m.get("role") != "system":
            data.append(m)
            idx_map.append(i)
    return data, idx_map


@dataclass(slots=True)
class MessageDisplayContext:
    """消息显示上下文 — 封装 agent 相关的三个强关联参数。

    消除 _msg_line / _make_message_lines 中反复传递
    agent / idx_map / data 三个参数的模式。

    data = messages 过滤 system 后的结果，
    idx_map 是 messages 全量到 data 的索引映射。

    用法：
        ctx = MessageDisplayContext.from_messages(agent.messages)
        # 或直接使用 agent：
        ctx = MessageDisplayContext.from_agent(agent)
        _msg_line(msg, i, ctx)
        _make_message_lines(items, cursor, state, ctx, title, tag, is_current)
    """
    data: list[dict]
    agent: Any = None
    idx_map: list[int] | None = None

    @classmethod
    def from_messages(cls, messages: list[dict], agent: Any = None) -> "MessageDisplayContext":
        """从消息列表构建上下文（提取 data + idx_map）。

        Args:
            messages: 完整消息列表（含 system 消息）。
            agent: 可选的 agent 引用（用于沙盒查询等）。

        Returns:
            构建好的 MessageDisplayContext。
        """
        data, idx_map = _non_system_messages(messages)
        return cls(data=data, agent=agent, idx_map=idx_map)

    @classmethod
    def from_agent(cls, agent: Any) -> "MessageDisplayContext":
        """从 agent 自动构建上下文（提取 data + idx_map）。

        等价于 MessageDisplayContext.from_messages(agent.messages, agent=agent)。
        """
        if agent is None:
            return cls(data=[])
        return cls.from_messages(agent.messages, agent=agent)


# ── 单条消息显示 ────────────────────────────────────────

def _display_tool_calls(i: int, icon: str, m: dict, sandbox_text: str, breath_frame: int = 0, fade_frame: int = 0) -> None:
    """显示 tool_calls 消息摘要 — 使用主题 muted 色。

    若工具调用含错误（检查 error 字段），在输出前加错误脉冲色。

    Args:
        breath_frame: 呼吸帧号，0 表示使用静态色。
        fade_frame: 渐显帧号，0 表示无渐显。
    """
    names = ", ".join(
        tc.get("function", {}).get("name", "?") for tc in m.get("tool_calls", [])
    )
    content = m.get("content") or ""
    text = content[:_TOOL_CALL_PREVIEW_LEN].replace("\n", " ") if content else ""
    if len(content) > _TOOL_CALL_PREVIEW_LEN:
        text += "…"
    if breath_frame > 0 and fade_frame > 0:
        _fade_prefix = build_bounce_ansi(fade_frame, 6)
    else:
        _fade_prefix = build_fade_in_ansi(fade_frame)
    # 检查工具调用是否含错误
    _has_error = bool(m.get("error")) or any(
        tc.get("error") for tc in m.get("tool_calls", [])
    )
    _pulse_prefix = ""
    if _has_error and breath_frame > 0:
        _pulse_prefix = build_warning_pulse_ansi(breath_frame, "error")
    # 使用全宽渐变分隔线（青→深灰，每列一色号），支持呼吸色
    _manager.write_line(f"\n{_make_gradient_sep(breath_frame=breath_frame)}")
    # 窄屏时 _role_tag 自动降级无背景色版本；宽屏时支持呼吸色
    tag = _role_tag("tool", breath_frame)
    _manager.write_line(f"  {tag}  {_D}{icon}{_R} {_pulse_prefix}{_Y}{names}{_R}{_D}{sandbox_text}{_R}")
    if text:
        _manager.write_line(f"  {_D}  \u2514 {_fade_prefix}{text}{_R}")


def _display_user(i: int, icon: str, content: str, sandbox_text: str, breath_frame: int = 0, fade_frame: int = 0) -> None:
    """显示用户消息 — 每行以 > 开头（白色加粗）。

    Args:
        breath_frame: 呼吸帧号，0 表示使用静态色。
        fade_frame: 渐显帧号，0 表示无渐显。
    """
    if breath_frame > 0 and fade_frame > 0:
        _fade_prefix = build_bounce_ansi(fade_frame, 6)
    else:
        _fade_prefix = build_fade_in_ansi(fade_frame)
    # 使用全宽渐变分隔线（青→深灰，每列一色号），支持呼吸色
    _manager.write_line(f"\n{_make_gradient_sep(breath_frame=breath_frame)}")
    # 窄屏时 _role_tag 自动降级无背景色版本；宽屏时支持呼吸色
    tag = _role_tag("user", breath_frame)
    _manager.write_line(f"  {_BW}{_BD}>{_R} {tag}  {_D}#{i}{_R}{_D}{sandbox_text}{_R}")
    for line in content.split("\n"):
        _manager.write_line(f"  {_BW}{_BD}>{_R} {_fade_prefix}{line}")


def _display_assistant(
    i: int, icon: str, m: dict, sandbox_text: str, speed: int = 0,
    breath_frame: int = 0, fade_frame: int = 0,
) -> None:
    """显示助手消息（含 reasoning + content Markdown 渲染）— 使用主题色彩。

    Args:
        breath_frame: 呼吸帧号，0 表示使用静态色。
        fade_frame: 渐显帧号，0 表示无渐显。
    """
    if breath_frame > 0 and fade_frame > 0:
        _fade_prefix = build_bounce_ansi(fade_frame, 6)
    else:
        _fade_prefix = build_fade_in_ansi(fade_frame)
    # 使用全宽渐变分隔线（青→深灰，每列一色号），支持呼吸色
    _manager.write_line(f"\n{_make_gradient_sep(breath_frame=breath_frame)}")
    # 窄屏时 _role_tag 自动降级无背景色版本；宽屏时支持呼吸色
    tag = _role_tag("assistant", breath_frame)
    _manager.write_line(f"  {tag}  {_D}#{i}{_R}{_D}{sandbox_text}{_R}")
    content = m.get("content") or ""
    reasoning = m.get("reasoning_content") or ""
    _output_file = _OutputFileAdapter(_manager.target)
    if reasoning:
        _manager.write_line(f"\n{_make_think_sep(breath_frame=breath_frame)}")
        _manager.write_line("")
        reason_renderer = IncrementalRenderer(
            typing_speed=speed, show_indicator=False, style="dim",
            _file=_output_file,
        )
        reason_renderer.write(reasoning)
        reason_renderer.close()
        _manager.write_line(f"\n{_make_think_end()}")
    if content and len(content) > _ASSISTANT_MD_THRESHOLD:
        # 渐显前缀写入 Markdown 渲染之前，后续渲染器会覆盖颜色
        _manager.write(_fade_prefix)
        renderer = IncrementalRenderer(
            typing_speed=speed, show_indicator=False,
            _file=_output_file,
        )
        renderer.write(content)
        renderer.close()
    elif content:
        for line in content.split("\n"):
            _manager.write_line(f"  {_BG}\u2502{_R} {_fade_prefix}{line}")


# ── 消息列表全量显示 ────────────────────────────────────

def _display_messages(
    data: list[dict],
    agent: Any = None,
    idx_map: list[int] | None = None,
    speed: int = 0,
) -> None:
    """恢复会话后展示所有消息内容 — 使用主题色彩美化。"""
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
    for i, m in enumerate(data):
        _breath_frame = AnimatorContext.get_default().breath_frame
        role = m.get("role", "?")
        icon = _role_icon(role)
        content = m.get("content") or ""

        if role == "user":
            sandbox_text = _get_user_sandbox_text(data, i, agent, idx_map)
        else:
            sandbox_text = _get_sandbox_text(agent, idx_map, i)

        if m.get("tool_calls"):
            _display_tool_calls(i, icon, m, sandbox_text, breath_frame=_breath_frame, fade_frame=_breath_frame)
            continue

        if role == "tool":
            text = content[:_TOOL_CONTENT_PREVIEW_LEN].replace("\n", " ")
            if len(content) > _TOOL_CONTENT_PREVIEW_LEN:
                text += "…"
            _manager.write_line(f"\n  {_D}\u2501{_R}" * min(narrow_sep_width(20), 10))
            _manager.write_line(f"  {_TOOL_TAG}  {_D}\u2514 {text}{_R}")
            continue

        if role == "user":
            _display_user(i, icon, content, sandbox_text, breath_frame=_breath_frame, fade_frame=_breath_frame)
        else:
            _display_assistant(i, icon, m, sandbox_text, speed, breath_frame=_breath_frame, fade_frame=_breath_frame)

    _manager.write_line(f"  {_D}{sep}{_R}")


display_messages = _display_messages  # 公开别名


# ── 单行摘要生成 ────────────────────────────────────────

def _msg_line(
    m: dict, i: int, ctx: MessageDisplayContext,
) -> tuple[str, str, str]:
    """生成消息的一行摘要显示文本。

    Args:
        m: 消息字典。
        i: 在 ctx.data 中的索引。
        ctx: 消息显示上下文（封装 agent / idx_map / data）。

    Returns:
        (icon, role, text) 三元组。
    """
    role = m.get("role", "?")
    icon = _role_icon(role)
    content = m.get("content") or ""

    if role == "user" and ctx.data:
        sandbox_text = _get_user_sandbox_text(ctx.data, i, ctx.agent, ctx.idx_map)
    else:
        sandbox_text = _get_sandbox_text(ctx.agent, ctx.idx_map, i)

    line_truncate = narrow_truncate(_LINE_TRUNCATE_WIDTH, 30, 18)
    if is_narrow() and sandbox_text:
        sandbox_text = truncate(sandbox_text, max_len=12, normalize=True)

    if m.get("tool_calls"):
        names = ", ".join(
            tc.get("function", {}).get("name", "?") for tc in m["tool_calls"]
        )
        text = truncate(names, max_len=line_truncate, normalize=True) + sandbox_text
        return icon, role, text

    text = truncate(content, max_len=line_truncate, normalize=True) + sandbox_text
    return icon, role, text


def _msg_label(m: dict, i: int, ctx: MessageDisplayContext, breath_frame: int = 0, selected: bool = False) -> str:
    """生成消息的彩色标签行（用于消息选择器显示）。

    Args:
        breath_frame: 呼吸帧号，0 表示使用静态色。
        selected: 是否为选中消息。为 True 且 breath_frame > 0 时应用呼吸色。
    """
    role = m.get("role", "?")
    icon = _role_icon(role)
    tag = _role_tag(role, breath_frame)
    content = m.get("content") or ""
    # 当 breath_frame > 0 且 selected=True 时，为标签添加呼吸色
    if breath_frame > 0 and selected:
        breath_color = sine_color_range(breath_frame, [44, 45, 46, 47, 46, 45, 44])
        # 呼吸色应用于 #i 索引和截断文本部分（tag 有独立颜色不受影响）
        breath_mark = f"\033[38;5;{breath_color}m"
        breath_reset = _R
    else:
        breath_mark = ""
        breath_reset = ""
    if m.get("tool_calls"):
        names = ", ".join(
            tc.get("function", {}).get("name", "?") for tc in m["tool_calls"]
        )
        return f"{tag}  {breath_mark}{_D}#{i}{_R}  {_Y}{truncate(names, max_len=35, normalize=True)}{_R}{breath_reset}"
    text = content.replace("\n", " ").strip()
    truncated = truncate(text, max_len=40, normalize=True)
    return f"{tag}  {breath_mark}{_D}#{i}{_R}  {truncated}{breath_reset}"


# ── 消息选择器行渲染 ────────────────────────────────────

def _make_message_lines(
    items: list[int], cursor: int, state: dict,
    ctx: MessageDisplayContext, title: str, tag: str,
    is_current: bool,
    selected_index: int = -1,
    breath_frame: int = 0,
) -> list[tuple[str, str]]:
    """生成消息选择器的显示行列表。

    Args:
        items: 可选项索引列表（在 ctx.data 中的索引）。
        cursor: 当前光标位置。
        state: 选择器状态字典。
        ctx: 消息显示上下文。
        title: 标题文本。
        tag: 标题后缀标签。
        is_current: 是否为当前会话。
        selected_index: 选中消息在 ctx.data 中的索引，-1 表示无选中。
        breath_frame: 呼吸帧号，0 表示使用静态色。

    Returns:
        (样式类, 文本) 行列表。
    """
    tw = get_terminal_width()
    narrow = tw < NARROW_THRESHOLD
    sep_width = narrow_sep_width(_SEP_LINE_WIDTH)
    indent = narrow_indent(normal=2)
    ind = " " * indent
    if narrow:
        title_text = f"{ind}{title}{tag}"
    else:
        title_text = f"{ind}{title}{tag}  {len(items)} 条消息"
    # ★ 装饰分隔线
    sep_line = ind + "\u2501" * sep_width
    lines = [
        ("class:title", title_text + "\n"),
        ("class:sep", sep_line + "\n"),
    ]
    s, e = _scroll_window(cursor, state, len(items))
    if s > 0:
        lines.append(("class:dim", f"  {ind}\u2191 更多...\n"))
    for j in range(s, e):
        i = items[j]
        icon, role, text = _msg_line(ctx.data[i], i, ctx)
        if narrow:
            label = f" {j:2d}{icon} {text}"
        else:
            label = f" {j:3d} {icon} {text}"
        # 脉动指示符：选中消息显示 ▸（呼吸色），未选中显示两个空格占位
        if selected_index >= 0 and i == selected_index:
            pulse_color = sine_color_range(breath_frame, [45, 81])
            prefix = f"\033[38;5;{pulse_color}m\u25b8{_R} "
        else:
            prefix = "  "
        if j == cursor:
            lines.append(("class:selected", f"{ind}{prefix}{label}\n"))
        else:
            lines.append(("class:role.user", f"{ind}{prefix}{label}\n"))
    if e < len(items):
        lines.append(("class:dim", f"  {ind}\u2193 更多...\n"))
    lines.append(("class:sep", sep_line + "\n"))
    lines.append(("class:hint", _build_hint_text(is_current, narrow)))
    return lines


__all__ = [
    "_display_messages",
    "display_messages",
    "_make_message_lines",
    "_msg_line",
    "OutputManager",
    "get_message_output",
    "set_message_output",
    "write",
    "write_line",
]
