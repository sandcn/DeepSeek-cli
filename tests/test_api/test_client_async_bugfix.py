"""测试 B6 修复：client_async.py _clients 使用 WeakKeyDictionary。"""

from __future__ import annotations

import asyncio
import gc
import logging
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


class TestHeadersNoApiKey:
    """回归测试：未设置 API_KEY 时 _headers/_headers_anthropic 不抛 UnboundLocalError。

    修复前：函数内 `_api_key_warned = True` 缺少 `global` 声明，Python 将
    `_api_key_warned` 视为局部变量，`if not _api_key_warned` 读取时抛
    UnboundLocalError——正是该分支想处理的「缺失密钥」场景反而崩溃。
    """

    @pytest.fixture(autouse=True)
    def _reset_warned_flag(self):
        from src.api import client_async as ca
        with patch.object(ca, "_api_key_warned", False), \
             patch.object(ca, "API_KEY", ""):
            yield

    def test_headers_without_api_key_no_crash(self, caplog):
        """_headers 在 API_KEY 为空时不抛异常，返回空 Bearer 头。"""
        from src.api import client_async as ca
        with caplog.at_level(logging.WARNING, logger="src.api.client_async"):
            headers = ca._headers()
        assert headers["Authorization"] == "Bearer "
        assert headers["Content-Type"] == "application/json"
        assert ca._api_key_warned is True, "首次缺失密钥应置位警告标志"

    def test_headers_warns_once(self, caplog):
        """缺失 API_KEY 的警告只打印一次（_api_key_warned 真正生效）。"""
        from src.api import client_async as ca
        with caplog.at_level(logging.WARNING, logger="src.api.client_async"):
            ca._headers()
            ca._headers()
            ca._headers_anthropic()
        warn_msgs = [
            r.message for r in caplog.records
            if "未设置 API 密钥" in str(r.message)
        ]
        assert len(warn_msgs) == 1, f"警告应仅一次，实际 {len(warn_msgs)} 次"

    def test_headers_anthropic_without_api_key_no_crash(self, caplog):
        """_headers_anthropic 在 API_KEY 为空时不抛异常。"""
        from src.api import client_async as ca
        headers = ca._headers_anthropic()
        assert headers["x-api-key"] == ""
        assert headers["anthropic-version"] == "2023-06-01"

