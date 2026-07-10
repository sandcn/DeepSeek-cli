"""渲染端口 — 核心层与 Markdown 渲染引擎的接口

适配器实现已移至 src.core.adapters.render。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional


class RenderPort(ABC):
    """抽象渲染端口

    核心层通过此接口将 Markdown 文本渲染为终端输出。
    基础设施层（api/renderer）实现此接口。
    """

    @abstractmethod
    def write(self, text: str) -> None:
        """增量渲染 Markdown 文本"""
        ...

    @abstractmethod
    def close(self) -> None:
        """关闭渲染器，刷出缓冲区"""
        ...

    @property
    @abstractmethod
    def pipeline(self):
        """Token 过滤器链（可选）"""
        ...
