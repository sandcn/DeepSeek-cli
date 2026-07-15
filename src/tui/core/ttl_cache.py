"""通用 TTL 缓存工具 — 线程安全的泛型缓存

消除 command_palette / session_switcher 中重复的缓存实现。
支持泛型类型、自定义获取函数、线程安全、TTL 控制。

用法：
    cache = TTLCache(fetcher=get_registered_command_names, ttl=60.0)
    items = cache.get()       # 缓存未命中或过期时调用 fetcher
    cache.refresh()           # 强制刷新
    cache.clear()             # 清空缓存
"""
from tui_framework.core.ttl_cache import *

__all__ = ["TTLCache"]
