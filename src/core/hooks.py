"""CoreHooks — 持有事件回调注册表，提供 on/off/_emit/copy/clear 操作。

职责：
- 作为独立模块持有 Hook 注册表（dict[str, list[Callable]]）
- 提供 on/off/_emit 注册/注销/触发操作
- 提供 copy/clear 支持测试和序列化
- __getitem__ 委托到内部 _hooks，保持 state.hooks["event_name"] 可用
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Callable

_logger = logging.getLogger(__name__)

__all__ = ["CoreHooks"]


class CoreHooks:
    """事件回调注册表。

    提供 on/off/_emit 操作管理事件回调。
    非 dataclass，自定义 __init__ 初始化内部 _hooks 为 defaultdict(list)。
    """

    def __init__(self) -> None:
        """初始化空的回调注册表。"""
        self._hooks: dict[str, list[Callable]] = defaultdict(list)

    def on(self, event: str, callback: Callable) -> None:
        """注册事件回调。"""
        self._hooks[event].append(callback)

    def off(self, event: str, callback: Callable) -> None:
        """移除事件回调。"""
        handlers = self._hooks.get(event, [])
        if callback in handlers:
            handlers.remove(callback)

    def _emit(self, event: str, **data) -> None:
        """触发事件，依次调用所有注册的回调。

        单个回调异常被吞并记录日志，不影响其他回调执行。
        """
        for cb in self._hooks.get(event, []):
            try:
                cb(**data)
            except Exception:
                _logger.exception("CoreHooks event '%s' 回调异常", event)

    def copy(self) -> CoreHooks:
        """返回新 CoreHooks 实例，_hooks 为浅拷贝。

        回调列表独立（添加/移除不影响原实例），但回调对象引用共享。
        """
        new_hooks = CoreHooks()
        new_hooks._hooks = defaultdict(list, {k: list(v) for k, v in self._hooks.items()})
        return new_hooks

    def clear(self) -> None:
        """清空所有事件回调。"""
        self._hooks.clear()

    def __getitem__(self, event: str) -> list[Callable]:
        """委托到 self._hooks，支持 state.hooks["event_name"] 的访问方式。"""
        return self._hooks[event]

    def __contains__(self, event: str) -> bool:
        """委托到 self._hooks，支持 "event" in state.hooks 判断。"""
        return event in self._hooks

    def __len__(self) -> int:
        """委托到 self._hooks，支持 len(state.hooks) 和 bool(state.hooks) 判断。"""
        return len(self._hooks)

    def __repr__(self) -> str:
        return f"CoreHooks({dict(self._hooks)})"
