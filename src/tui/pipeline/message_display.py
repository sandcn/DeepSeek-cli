"""
消息显示函数 — Facade 入口。

原本为单文件 ~873 行，现已按职责拆分为 4 个文件：
  - ``_io.py``：I/O 输出管理
  - ``_visual.py``：纯视觉组件
  - ``_context.py``：纯数据层
  - ``message_display.py``（本文件）：Facade + 核心显示逻辑

本文件保留为向后兼容入口，通过 ``from ._io`` / ``from ._visual`` /
``from ._context`` 导入拆分后的组件，并在 ``__all__`` 中统一 re-export。
"""

from __future__ import annotations

import logging
from typing import Any
from ...renderer import IncrementalRenderer
from ..core.output_target import IOutputTarget

from ...core.constants import (
    GRAY_256 as _D, RESET as _R,
    YELLOW_256 as _Y, BRIGHT_WHITE_256 as _BW,
    BOLD as _BD, BRIGHT_GREEN_256 as _BG,
)
from ..core.text_utils import (
    truncate, build_fade_in_ansi,
    build_warning_pulse_ansi, build_bounce_ansi,
)
from ..animation.animator import AnimatorContext
from ..core.effects import sine_color_range
from ..terminal.terminal import (
    get_terminal_width, NARROW_THRESHOLD,
    is_narrow, narrow_truncate, narrow_indent,
    narrow_sep_width,
)

# ── Facade re-export ──
from ._io import (
    OutputManager, _manager, reset_message_output,
    get_message_output, set_message_output,
    write, write_line, _OutputFileAdapter,
)
from ._visual import (
    _make_gradient_sep, _make_think_sep, _make_think_end,
    _USER_TAG, _ASST_TAG, _TOOL_TAG,
    _build_hint_text,
    _role_icon, _role_tag,
    RoleConfig, _ROLE_DEFAULTS,
    _make_default_user_tag, _make_default_assistant_tag, _make_default_tool_tag,
    _build_messages_header,
    _TOOL_CALL_PREVIEW_LEN, _TOOL_CONTENT_PREVIEW_LEN, _ASSISTANT_MD_THRESHOLD,
    _LINE_TRUNCATE_WIDTH, _SEP_LINE_WIDTH,
)
from ._context import (
    _scroll_window, _non_system_messages, MessageDisplayContext,
    _format_sandbox_text, _get_sandbox_text, _get_user_sandbox_text,
)


_logger = logging.getLogger(__name__)


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
            show_indicator=False, style="dim",
            _file=_output_file,
        )
        reason_renderer.write(reasoning)
        reason_renderer.close()
        _manager.write_line(f"\n{_make_think_end()}")
    if content and len(content) > _ASSISTANT_MD_THRESHOLD:
        # 渐显前缀写入 Markdown 渲染之前，后续渲染器会覆盖颜色
        _manager.write(_fade_prefix)
        renderer = IncrementalRenderer(
            show_indicator=False,
            _file=_output_file,
        )
        renderer.write(content)
        renderer.close()
    elif content:
        for line in content.split("\n"):
            _manager.write_line(f"  {_BG}\u2502{_R} {_fade_prefix}{line}")


# ── 消息列表全量显示 ────────────────────────────────────

def _render_message_item(
    i: int, m: dict, data: list[dict],
    agent: Any = None, idx_map: list[int] | None = None,
    speed: int = 0, role_map: dict[str, RoleConfig] | None = None,
) -> None:
    """渲染单条消息到输出。

    Args:
        i: 消息在 data 中的索引。
        m: 消息字典。
        data: 过滤 system 后的消息列表（用于沙盒查询）。
        agent: 可选 agent 引用。
        idx_map: data → messages 全量索引映射。
        speed: 打字速度。
        role_map: 可选的角色配置映射。
    """
    _breath_frame = AnimatorContext.get_default().breath_frame
    role = m.get("role", "?")
    icon = _role_icon(role, role_map)
    content = m.get("content") or ""

    if role == "user":
        sandbox_text = _get_user_sandbox_text(data, i, agent, idx_map)
    else:
        sandbox_text = _get_sandbox_text(agent, idx_map, i)

    # 优先使用 role_map 中的自定义 display_func（角色动态分发）
    if role_map is not None:
        cfg = role_map.get(role)
        if cfg is not None and cfg.display_func is not None:
            cfg.display_func(i, icon, m, sandbox_text, speed=speed,
                             breath_frame=_breath_frame, fade_frame=_breath_frame)
            return

    if m.get("tool_calls"):
        _display_tool_calls(i, icon, m, sandbox_text, breath_frame=_breath_frame, fade_frame=_breath_frame)
        return

    if role == "tool":
        text = content[:_TOOL_CONTENT_PREVIEW_LEN].replace("\n", " ")
        if len(content) > _TOOL_CONTENT_PREVIEW_LEN:
            text += "…"
        _manager.write_line(f"\n  {_D}\u2501{_R}" * min(narrow_sep_width(20), 10))
        _manager.write_line(f"  {_TOOL_TAG}  {_D}\u2514 {text}{_R}")
        return

    if role == "user":
        _display_user(i, icon, content, sandbox_text, breath_frame=_breath_frame, fade_frame=_breath_frame)
    else:
        _display_assistant(i, icon, m, sandbox_text, speed, breath_frame=_breath_frame, fade_frame=_breath_frame)


def _display_messages(
    data: list[dict],
    agent: Any = None,
    idx_map: list[int] | None = None,
    speed: int = 0,
    role_map: dict[str, RoleConfig] | None = None,
) -> None:
    """恢复会话后展示所有消息内容 — 使用主题色彩美化。

    Args:
        data: 过滤 system 后的消息列表。
        agent: 可选 agent 引用（用于沙盒查询）。
        idx_map: data → messages 全量索引映射。
        speed: 打字速度。
        role_map: 可选的角色配置映射，不传时使用硬编码默认值。
    """
    sep = _build_messages_header()
    for i, m in enumerate(data):
        _render_message_item(i, m, data, agent, idx_map, speed, role_map)
    _manager.write_line(f"  {_D}{sep}{_R}")


display_messages = _display_messages  # 公开别名


# ── 单行摘要生成 ────────────────────────────────────────

def _msg_line(
    m: dict, i: int, ctx: MessageDisplayContext,
    role_map: dict[str, RoleConfig] | None = None,
) -> tuple[str, str, str]:
    """生成消息的一行摘要显示文本。

    Args:
        m: 消息字典。
        i: 在 ctx.data 中的索引。
        ctx: 消息显示上下文（封装 agent / idx_map / data）。
        role_map: 可选的角色配置映射，不传时使用硬编码默认值。

    Returns:
        (icon, role, text) 三元组。
    """
    role = m.get("role", "?")
    icon = _role_icon(role, role_map)
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


def _msg_label(m: dict, i: int, ctx: MessageDisplayContext, breath_frame: int = 0, selected: bool = False, role_map: dict[str, RoleConfig] | None = None) -> str:
    """生成消息的彩色标签行（用于消息选择器显示）。

    Args:
        breath_frame: 呼吸帧号，0 表示使用静态色。
        selected: 是否为选中消息。为 True 且 breath_frame > 0 时应用呼吸色。
        role_map: 可选的角色配置映射，不传时使用硬编码默认值。
    """
    role = m.get("role", "?")
    icon = _role_icon(role, role_map)
    tag = _role_tag(role, breath_frame, role_map)
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
    role_map: dict[str, RoleConfig] | None = None,
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
        role_map: 可选的角色配置映射，不传时使用硬编码默认值。

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
        icon, role, text = _msg_line(ctx.data[i], i, ctx, role_map)
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
    # core display
    "_display_messages",
    "display_messages",
    "_display_tool_calls",
    "_display_user",
    "_display_assistant",
    "_render_message_item",
    # selectors
    "_make_message_lines",
    "_msg_line",
    "_msg_label",
    # re-export from _io
    "OutputManager",
    "_manager",
    "reset_message_output",
    "get_message_output",
    "set_message_output",
    "write",
    "write_line",
    "_OutputFileAdapter",
    # re-export from _visual
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
    "_LINE_TRUNCATE_WIDTH",
    "_SEP_LINE_WIDTH",
    # re-export from _context
    "_scroll_window",
    "_non_system_messages",
    "MessageDisplayContext",
    "_format_sandbox_text",
    "_get_sandbox_text",
    "_get_user_sandbox_text",
]
