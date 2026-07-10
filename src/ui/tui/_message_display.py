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

from src._compat import dataclass
from typing import Any
import os

from ...renderer import IncrementalRenderer
from ...core.sandbox_manager import get_sandbox_manager as _get_sandbox_manager
from ._terminal import (get_terminal_width, NARROW_THRESHOLD,
                        is_narrow, narrow_truncate, narrow_indent,
                        narrow_sep_width)
from ._text_utils import truncate
from ..output_target import IOutputTarget, TerminalTarget


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
        self._target: IOutputTarget = target or TerminalTarget()

    @property
    def target(self) -> IOutputTarget:
        return self._target

    @target.setter
    def target(self, new_target: IOutputTarget | None) -> None:
        self._target = new_target or TerminalTarget()

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


# ── 颜色快捷引用 ──────────────────────────────────────────

from ..colors import CYAN as _C, DIM as _D, RESET as _R, GREEN as _G, \
    YELLOW as _Y, BLUE as _B, BRIGHT_CYAN as _BC, BRIGHT_WHITE as _BW, \
    BOLD as _BD, DARK_GRAY as _DG, BRIGHT_GREEN as _BG

# ── 常量 ─────────────────────────────────────────────────

_TOOL_CALL_PREVIEW_LEN = 100
_TOOL_CONTENT_PREVIEW_LEN = 200
_ASSISTANT_MD_THRESHOLD = 100
_LINE_TRUNCATE_WIDTH = 55
_SEP_LINE_WIDTH = 25
_NARROW_SEP_REDUCTION = 10

# ── 美观分隔线（增强版） ────────────────────────────────────

_MSG_SEP = f"  {_DG}\u2500{_R}{_DG}\u2500{_R}{_DG}\u2500{_R}"  # ───
_THINK_SEP = (f"  {_BC}\u2501\u2501{_R}"          # ━━
              f"  {_BC}\u26a1{_R}"                #  ⚡
              f"{_DG}\u601d\u8003{_R}"            # 思考
              f"  {_D}\u2501\u2501{_R}")          #  ━━
_THINK_END = f"  {_D}\u2501\u2501{_R}"            # ━━

# ── 美观角色标签（带颜色背景感） ──────────────────────────
_USER_TAG = f"{_BC}\u25cf {_R}{_BC}USER{_R}"        # ● USER
_ASST_TAG = f"{_BG}\u25c6 {_R}{_BG}ASSISTANT{_R}"   # ◆ ASSISTANT
_TOOL_TAG = f"{_Y}\u2699 {_R}{_Y}TOOL{_R}"          # ⚙ TOOL

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


def _role_tag(role: str) -> str:
    """角色标签：将 role 映射为美观的彩色标签。"""
    return {"user": _USER_TAG, "assistant": _ASST_TAG, "tool": _TOOL_TAG}.get(role, _D + "·" + _R)


# _truncate 已迁移到 _text_utils.truncate（向后兼容：width → max_len）
def _truncate(text: str | None, width: int, *, suffix: str = "\u2026") -> str:
    """截断文本（向后兼容包装器，委托 _text_utils.truncate）。

    width 参数映射到 truncate() 的 max_len 参数。
    """
    return truncate(text, max_len=width, suffix=suffix, normalize=True)


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

def _display_tool_calls(i: int, icon: str, m: dict, sandbox_text: str) -> None:
    """显示 tool_calls 消息摘要 — 使用主题 muted 色。"""
    names = ", ".join(
        tc.get("function", {}).get("name", "?") for tc in m.get("tool_calls", [])
    )
    content = m.get("content") or ""
    text = content[:_TOOL_CALL_PREVIEW_LEN].replace("\n", " ") if content else ""
    if len(content) > _TOOL_CALL_PREVIEW_LEN:
        text += "…"
    sep = narrow_sep_width(20)
    _manager.write_line(f"\n  {_DG}\u2500{_R}" * min(sep, 10))
    _manager.write_line(f"  {_TOOL_TAG}  {_D}{icon}{_R} {_Y}{names}{_R}{_D}{sandbox_text}{_R}")
    if text:
        _manager.write_line(f"  {_D}  \u2514 {text}{_R}")


def _display_user(i: int, icon: str, content: str, sandbox_text: str) -> None:
    """显示用户消息 — 每行以 > 开头（白色加粗）。"""
    sep = narrow_sep_width(20)
    _manager.write_line(f"\n  {_DG}\u2500{_R}" * min(sep, 10))
    _manager.write_line(f"  {_BW}{_BD}>{_R} {_USER_TAG}  {_D}#{i}{_R}{_D}{sandbox_text}{_R}")
    for line in content.split("\n"):
        _manager.write_line(f"  {_BW}{_BD}>{_R} {line}")


def _display_assistant(
    i: int, icon: str, m: dict, sandbox_text: str, speed: int = 0,
) -> None:
    """显示助手消息（含 reasoning + content Markdown 渲染）— 使用主题色彩。"""
    sep = narrow_sep_width(20)
    _manager.write_line(f"\n  {_DG}\u2500{_R}" * min(sep, 10))
    _manager.write_line(f"  {_ASST_TAG}  {_D}#{i}{_R}{_D}{sandbox_text}{_R}")
    content = m.get("content") or ""
    reasoning = m.get("reasoning_content") or ""
    _output_file = _OutputFileAdapter(_manager.target)
    if reasoning:
        _manager.write_line(f"\n  {_THINK_SEP}")
        _manager.write_line("")
        reason_renderer = IncrementalRenderer(
            typing_speed=speed, show_indicator=False, style="dim",
            _file=_output_file,
        )
        reason_renderer.write(reasoning)
        reason_renderer.close()
        _manager.write_line(f"\n  {_THINK_END}")
    if content and len(content) > _ASSISTANT_MD_THRESHOLD:
        renderer = IncrementalRenderer(
            typing_speed=speed, show_indicator=False,
            _file=_output_file,
        )
        renderer.write(content)
        renderer.close()
    elif content:
        for line in content.split("\n"):
            _manager.write_line(f"  {_BG}\u2502{_R} {line}")


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
    # ★ 美化：亮青色花括号 + 信封图标
    header = f"  {_BC}\u2501{_R}  {_BC}\u2770\u2709\u6d88\u606f\u5217\u8868\u2771{_R}  {_BC}\u2501{_R}"  # ━  ❰✉消息列表❱  ━
    _manager.write_line(f"\n{header}")
    for i, m in enumerate(data):
        role = m.get("role", "?")
        icon = _role_icon(role)
        content = m.get("content") or ""

        if role == "user":
            sandbox_text = _get_user_sandbox_text(data, i, agent, idx_map)
        else:
            sandbox_text = _get_sandbox_text(agent, idx_map, i)

        if m.get("tool_calls"):
            _display_tool_calls(i, icon, m, sandbox_text)
            continue

        if role == "tool":
            text = content[:_TOOL_CONTENT_PREVIEW_LEN].replace("\n", " ")
            if len(content) > _TOOL_CONTENT_PREVIEW_LEN:
                text += "…"
            _manager.write_line(f"\n  {_D}\u2500{_R}" * min(narrow_sep_width(20), 10))
            _manager.write_line(f"  {_TOOL_TAG}  {_D}\u2514 {text}{_R}")
            continue

        if role == "user":
            _display_user(i, icon, content, sandbox_text)
        else:
            _display_assistant(i, icon, m, sandbox_text, speed)

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
        sandbox_text = _truncate(sandbox_text, 12)

    if m.get("tool_calls"):
        names = ", ".join(
            tc.get("function", {}).get("name", "?") for tc in m["tool_calls"]
        )
        text = _truncate(names, line_truncate) + sandbox_text
        return icon, role, text

    text = _truncate(content, line_truncate) + sandbox_text
    return icon, role, text


def _msg_label(m: dict, i: int, ctx: MessageDisplayContext) -> str:
    """生成消息的彩色标签行（用于消息选择器显示）。"""
    role = m.get("role", "?")
    icon = _role_icon(role)
    tag = _role_tag(role)
    content = m.get("content") or ""
    if m.get("tool_calls"):
        names = ", ".join(
            tc.get("function", {}).get("name", "?") for tc in m["tool_calls"]
        )
        return f"{tag}  {_D}#{i}{_R}  {_Y}{_truncate(names, 35)}{_R}"
    text = content.replace("\n", " ").strip()
    truncated = _truncate(text, 40)
    return f"{tag}  {_D}#{i}{_R}  {truncated}"


# ── 消息选择器行渲染 ────────────────────────────────────

def _make_message_lines(
    items: list[int], cursor: int, state: dict,
    ctx: MessageDisplayContext, title: str, tag: str,
    is_current: bool,
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
        if j == cursor:
            lines.append(("class:selected", f"{ind}\u25b6 {label}\n"))
        else:
            lines.append(("class:role.user", f"{ind}  {label}\n"))
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
