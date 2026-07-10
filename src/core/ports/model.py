"""模型调用端口 — 异步端口抽象接口

适配器实现已移至 src.core.adapters.model。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence


# ─── 统一结果数据类 ────────────────────────────────────────────────────────────


@dataclass
class ModelResult:
    """模型调用统一结果。"""
    reasoning: str = ""
    content: str = ""
    usage: dict = field(default_factory=lambda: {"input": 0, "output": 0})
    tool_calls: list = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════
# 异步端口
# ═══════════════════════════════════════════════════════════════


class AsyncModelPort(ABC):
    """异步模型调用抽象端口。

    与 ModelPort 接口对等，但所有方法均为 async。
    核心层通过此接口使用 asyncio 调用 LLM。
    """

    @abstractmethod
    async def call(
        self,
        messages: list[dict],
        model: str,
        tools: list[dict] | None = None,
        display: Any = None,
        label: str | None = None,
        silent: bool = False,
    ) -> ModelResult:
        """异步流式调用模型，返回 ModelResult。"""
        ...

    @abstractmethod
    async def call_sync(
        self,
        messages: list[dict],
        model: str,
        tools: list[dict] | None = None,
        display: Any = None,
        label: str | None = None,
    ) -> ModelResult:
        """异步非流式调用模型，返回 ModelResult。"""
        ...
