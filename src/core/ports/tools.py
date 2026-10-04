"""工具端口 — 核心层与工具系统之间的抽象协议（纯抽象层）。

- ``ToolResult`` — 工具结构化结果数据类（文本摘要 + 多模态 content blocks），
  由核心层定义、工具系统使用，消除核心层对 ``tools.base`` 的反向依赖。
- ``ToolRegistryPort`` — 工具注册表协议（get_schemas/dispatch/build_system_prompt/
  get_tools/get_metadata），核心层经此访问工具注册表，不直接依赖
  ``tools.registry`` 具体实现。

实现（``tools.registry.ToolRegistry``）隐式满足 ``ToolRegistryPort``
（结构化子类型）；默认实例经 ``core.adapters.tools`` 延迟导入获取。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Optional, Protocol, Union, runtime_checkable

__all__ = ["ToolResult", "ToolRegistryPort"]


@dataclass
class ToolResult:
    """工具结构化结果 — 文本摘要 + 多模态 content blocks。

    工具 execute() 返回人类可读文本的同时，可通过 ``func.result_blocks``
    携带 OpenAI 兼容的多模态 content blocks（如 image_url data URI）。
    执行链路检测到 result_blocks 后将返回包装为本对象。

    Attributes:
        text: 给模型的文本摘要（展示/统计用）。
        blocks: OpenAI 兼容 content blocks 列表，可空（空时退化为纯文本）。
    """

    text: str
    blocks: Optional[List[dict]] = None

    def to_content(self) -> Union[str, List[dict]]:
        """转换为 tool 消息 content（str 或 list[dict]）。"""
        if self.blocks:
            return self.blocks
        return self.text

    @property
    def display_text(self) -> str:
        """展示/统计用文本（TUI 工具卡、token 估算等）。"""
        return self.text


@runtime_checkable
class ToolRegistryPort(Protocol):
    """工具注册表协议 — 核心层可见的工具系统接口。"""

    def get_schemas(self) -> list[dict]:
        """返回全部工具的 schema 列表。"""
        ...

    def dispatch(self, tool_name: str, arguments: dict, agent: Any = None) -> Any:
        """按名分发工具调用。"""
        ...

    def build_system_prompt(self) -> list[str]:
        """构建系统提示词中的工具章节。"""
        ...

    def get_tools(self) -> dict:
        """返回工具名 → 工具类映射。"""
        ...

    def get_metadata(self, tool_name: str) -> Optional[Any]:
        """返回指定工具的元数据。"""
        ...
