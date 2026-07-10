"""HTTP 客户端端口适配器 — DefaultHttpClientAdapter"""
from __future__ import annotations

from typing import Any, AsyncIterator

from ..ports.http import HttpClientPort


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
