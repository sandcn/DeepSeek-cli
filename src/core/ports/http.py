"""HTTP 客户端端口 — 核心层与 HTTP 客户端的接口"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, AsyncIterator


class HttpClientPort(ABC):
    """抽象 HTTP 客户端端口

    核心层通过此接口发送 HTTP 请求，不直接依赖 httpx。
    """

    @abstractmethod
    async def chat_completions(
        self,
        *,
        model: str,
        messages: list,
        tools: list | None = None,
        stream: bool = False,
        stream_options: dict | None = None,
        **extra: Any,
    ) -> dict | AsyncIterator[dict]:
        """调用 /v1/chat/completions 接口

        非流式返回完整 JSON dict；
        流式返回 async generator，逐个 yield 解析后的 chunk dict。
        """
        ...

    @abstractmethod
    async def close(self) -> None:
        """关闭客户端，释放连接池资源"""
        ...


class DefaultHttpClientAdapter(HttpClientPort):
    """默认 HTTP 客户端适配器 — 包装 src/api/client_async.py

    通过延迟导入避免模块加载时触发配置读取。
    """

    async def chat_completions(
        self,
        *,
        model: str,
        messages: list,
        tools: list | None = None,
        stream: bool = False,
        stream_options: dict | None = None,
        **extra: Any,
    ) -> dict | AsyncIterator[dict]:
        from ...api.client_async import chat_completions_async
        return await chat_completions_async(
            model=model,
            messages=messages,
            tools=tools,
            stream=stream,
            stream_options=stream_options,
            **extra,
        )

    async def close(self) -> None:
        from ...api.client_async import close_all_clients
        await close_all_clients()
