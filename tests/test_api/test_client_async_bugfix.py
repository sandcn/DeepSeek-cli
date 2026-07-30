"""测试 B6 修复：client_async.py _clients 使用 WeakKeyDictionary。"""

from __future__ import annotations

import asyncio
import gc
import weakref
from unittest.mock import patch

import pytest


class TestB6WeakKeyDictionary:
    """B6 修复：_clients 字典条目永不回收"""

    async def test_same_loop_returns_same_client(self):
        """同一事件循环返回同一 client"""
        from src.api.client_async import get_async_client
        c1 = await get_async_client()
        c2 = await get_async_client()
        assert c1 is c2, "同一事件循环应返回同一 httpx.AsyncClient 实例"

    async def test_reset_creates_new_client(self):
        """reset 后获取新的 client"""
        from src.api.client_async import get_async_client, reset_async_client
        c1 = await get_async_client()
        await reset_async_client()
        c2 = await get_async_client()
        assert c1 is not c2, "reset 后应返回新的 httpx.AsyncClient 实例"

    async def test_weak_key_dict_structure(self):
        """确认 _clients 是 WeakKeyDictionary 且使用 loop 对象而非 id(loop) 作键"""
        from src.api.client_async import _clients
        assert isinstance(_clients, weakref.WeakKeyDictionary), (
            f"_clients 应为 WeakKeyDictionary，实际为 {type(_clients)}"
        )
        loop = asyncio.get_running_loop()
        # 调用 get_async_client 确保条目存在
        from src.api.client_async import get_async_client
        await get_async_client()
        assert loop in _clients, (
            "当前事件循环实例应为 _clients 的键"
        )
