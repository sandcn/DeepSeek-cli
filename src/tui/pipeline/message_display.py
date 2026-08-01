"""消息显示函数 — 精简版消息格式化与显示。

提供 MessageDisplayContext（消息上下文封装）、display_messages（消息列表显示）、
RoleConfig（角色图标配置）。

★ 输出路径统一（2026-07-31 方向C 步骤4）：本函数为「非 ChatUI 上下文兜底直写实现」。
ChatUI 活跃时消息显示统一走路径 A（``ChatUIConsumer.display_messages →
DisplayMsgsCmd → TuiRenderer._do_display_messages → on_display_messages``，
经 render_lock 保护）；调用方（``CommandUiAdapter.display_messages`` /
``_session_setup``）已委托路径 A。本函数保留供无 ChatUI 场景（如单次模式）
与向后兼容 re-export（``src.tui.pipeline``）使用。
"""

from __future__ import annotations

import sys
from src._compat import dataclass
from typing import Any

from ...core.constants import DIM, RESET, CYAN, YELLOW, GREEN


@dataclass
class RoleConfig:
    """角色显示配置。"""
    icon: str = "?"


_DEFAULT_ROLE_MAP: dict[str, RoleConfig] = {
    "user": RoleConfig(icon="\u25cf"),       # ●
    "assistant": RoleConfig(icon="\u25c6"),  # ◆
    "tool": RoleConfig(icon="\u2699"),       # ⚙
}


def _content_str(content: Any) -> str:
    """将 content（可能是 str 或 list[dict]）转换为纯文本字符串。

    ★ 消毒残留原始 ANSI：消息内容来自会话历史，可能透传工具输出里的转义序列。
    保留进文本会让宽度测量把转义码当可见字符（宽度膨胀 → 误触发 wrap），
    ``wrap_line`` 逐字符截断把转义序列拦腰截断（残留 ``;49;00m``）渲染错乱。
    消息按统一角色色显示，消毒（剥完整合法序列 + 移除孤立 ESC）符合显示语义。
    """
    from src.tui.ink.helpers import strip_ansi as _strip_ansi
    if isinstance(content, str):
        return _strip_ansi(content).replace("\x1b", "")
    if isinstance(content, list):
        parts = []
        for c in content:
            if isinstance(c, dict):
                parts.append(str(c.get("text", c)))
            else:
                parts.append(str(c))
        return _strip_ansi(" ".join(parts)).replace("\x1b", "")
    return _strip_ansi(str(content)).replace("\x1b", "")


def _truncate(text: str, max_len: int) -> str:
    """截断文本到指定长度（单一真源，方向3 步骤16）。

    统一 message_editor 语义：先去除换行/回车（\n → 空格、\r → 删除），
    超长时截断并追加 "..."。本函数与 ``_content_str`` 为 message_display 与
    message_editor 共用的公共函数（message_editor 已删除本地副本改从此导入）。
    """
    text = text.replace('\n', ' ').replace('\r', '')
    if len(text) <= max_len:
        return text
    return text[:max_len - 3] + "..."


@dataclass
class MessageDisplayContext:
    """消息显示上下文 — 封装消息列表及其元数据。"""

    data: list[dict]
    """非 system 消息列表（副本，不含 system 消息）。"""
    agent: Any = None
    """ChatAgent 实例（可选）。"""
    idx_map: list[int] = None  # type: ignore[assignment]
    """索引映射：data[i] 在原 messages 中的位置。"""

    def __post_init__(self):
        if self.idx_map is None:
            self.idx_map = list(range(len(self.data)))

    @classmethod
    def from_messages(cls, messages: list[dict]) -> MessageDisplayContext:
        """从消息列表创建上下文。"""
        data = [m for m in messages if m.get("role") != "system"]
        return cls(data=data, idx_map=None)

    @classmethod
    def from_agent(cls, agent: Any) -> MessageDisplayContext:
        """从 agent 创建上下文。"""
        messages = getattr(agent, 'messages', [])
        data = [m for m in messages if m.get("role") != "system"]
        idx_map = []
        for i, m in enumerate(messages):
            if m.get("role") != "system":
                idx_map.append(i)
        return cls(data=data, agent=agent, idx_map=idx_map)


def display_messages(
    data: list[dict],
    agent: Any = None,
    idx_map: list[int] | None = None,
    speed: int = 0,
) -> None:
    """将消息列表渲染到终端（非 ChatUI 上下文兜底直写实现）。

    ★ 输出路径统一（2026-07-31 方向C 步骤4）：ChatUI 活跃时消息显示由路径 A
    （``ChatUIConsumer.display_messages`` → ``DisplayMsgsCmd`` 管线）承担，本函数
    仅在无 ChatUI 场景（单次模式等）作为兜底直接写入 ``sys.__stdout__``；
    调用方（``CommandUiAdapter`` / ``_session_setup``）已委托路径 A。
    函数体零改动，保留向后兼容 re-export。

    Args:
        data: 消息列表（不含 system 消息）。
        agent: ChatAgent 实例（可选）。
        idx_map: 索引映射（可选）。
        speed: 保留参数（兼容旧接口，当前忽略）。
    """
    for msg in data:
        role = msg.get("role", "")
        cfg = _DEFAULT_ROLE_MAP.get(role)
        icon = cfg.icon if cfg else "\u00b7"
        content = _content_str(msg.get("content", ""))
        if not content.strip():
            continue
        # 截断过长的消息用于显示（_truncate 内部已去除 \n/\r）
        preview = _truncate(content, 120)
        sys.__stdout__.write(f"  {icon} [{role}] {preview}\n")
    sys.__stdout__.flush()


__all__ = [
    "MessageDisplayContext",
    "RoleConfig",
    "display_messages",
]
