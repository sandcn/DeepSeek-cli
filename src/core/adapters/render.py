"""渲染端口适配器 — DefaultRenderAdapter、NullRenderAdapter"""
from __future__ import annotations

from typing import Any

from ..ports.render import RenderPort


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
