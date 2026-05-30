"""user_select 工具的待处理选择注册表（WebUI 模式）

提供线程安全的 Future 注册/解析/取消管理，供 user_select、routing handlers、
cleanup 等模块共享。
"""

from __future__ import annotations

import asyncio


class PendingSelectRegistry:
    """user_select 工具的待处理选择注册表（WebUI 模式）

    管理 user_select 创建的 asyncio.Future 对象，支持：
    - register: 注册新的选择请求
    - resolve: 完成指定选择
    - cancel_all: 取消所有待处理选择
    """

    def __init__(self) -> None:
        self._pending: dict[str, asyncio.Future] = {}

    def register(self, request_id: str) -> asyncio.Future:
        """注册新的选择请求，返回对应的 Future。

        Args:
            request_id: 唯一请求标识符

        Returns:
            等待选择结果的 Future
        """
        future = asyncio.get_event_loop().create_future()
        self._pending[request_id] = future
        return future

    def resolve(self, request_id: str, value: object) -> None:
        """完成指定选择请求。

        如果 request_id 不存在或 Future 已完成，则静默跳过。

        Args:
            request_id: 请求标识符
            value: 要设置的结果值
        """
        future = self._pending.pop(request_id, None)
        if future is not None and not future.done():
            future.set_result(value)

    def cancel_all(self) -> None:
        """取消所有待处理选择并清空注册表。"""
        for future in self._pending.values():
            if not future.done():
                future.cancel()
        self._pending.clear()

    def clear(self) -> None:
        """清空注册表（不取消 Future，用于测试重置）。"""
        self._pending.clear()

    def __contains__(self, request_id: str) -> bool:
        return request_id in self._pending


# 模块级单例 — 供 user_select / routing handlers / cleanup 共享
pending_selects = PendingSelectRegistry()
