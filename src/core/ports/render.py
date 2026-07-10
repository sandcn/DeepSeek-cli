"""渲染端口 — 核心层与 Markdown 渲染引擎的接口

将 src.renderer 的渲染能力抽象为端口，
核心层不直接依赖具体渲染实现。
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


class DefaultRenderAdapter(RenderPort):
    """默认渲染适配器 — 包装 IncrementalRenderer

    通过延迟导入避免模块加载时触发 rich.Console 初始化，
    仅在首次 write() 调用时才初始化渲染器实例。
    """

    def __init__(self, **kwargs):
        self._renderer = None
        self._kwargs = kwargs

    def _ensure(self):
        if self._renderer is None:
            from ...renderer import IncrementalRenderer
            self._renderer = IncrementalRenderer(**self._kwargs)

    def write(self, text: str) -> None:
        self._ensure()
        self._renderer.write(text)

    def close(self) -> None:
        if self._renderer is not None:
            self._renderer.close()

    @property
    def pipeline(self):
        self._ensure()
        return self._renderer.pipeline


class NullRenderAdapter(RenderPort):
    """空渲染适配器 — 丢弃所有渲染输出

    用于测试和非交互模式，不产生任何终端输出。
    """

    def write(self, text: str) -> None:
        pass

    def close(self) -> None:
        pass

    @property
    def pipeline(self):
        return None
