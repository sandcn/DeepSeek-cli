"""输出目标协议存根 — 替换已删除的 core/output_target.py。

2026-07-29 TUI 重构：原文件已随 core/ 目录清理被删除，
此处提供最小化 IOutputTarget 协议。
"""

from __future__ import annotations

from typing import List, Protocol, runtime_checkable


@runtime_checkable
class IOutputTarget(Protocol):
    """输出目标协议 — 定义终端渲染输出的抽象接口。

    由 BaseDisplay / ChatUIConsumer / WebUI 等实现。
    """

    def write_line(self, text: str) -> None: ...
    def flush(self) -> None: ...
    def display_messages(self, messages: list[dict], speed: int = 0) -> None: ...


__all__ = ["IOutputTarget"]
