"""pytest 配置

提供全局 fixture 确保测试间状态隔离。
"""

import sys
import pytest


@pytest.fixture(autouse=True)
def _cleanup_command_core_commands():
    """每个测试前后清理全局 _commands 字典，确保测试隔离。"""
    _do_cleanup_commands()
    yield
    _do_cleanup_commands()


def _do_cleanup_commands():
    """清理 _command_core._commands 字典。"""
    mod = sys.modules.get('src.core.internal._command_core')
    if mod is not None and hasattr(mod, '_commands'):
        mod._commands.clear()


@pytest.fixture(autouse=True)
def _cleanup_module_caches():
    """每个测试前后清理全局模块级缓存，防止跨测试内存泄漏。

    清理清单（6项）：
    1. _adapter_cache — src.api.model_async（模型适配器实例缓存）
    2. _clients — src.api.client_async（httpx.AsyncClient 连接池）
    3. _model_loop_registry — src.api._model_loops（持久化事件循环注册表）
    4. _default_cache — src.core.cache（全局 LRU 缓存单例）
    5. _active_consumer/_active_consumer_refcount — src.chat_ui.state（消费者全局引用）
    6. _active_monitor — src.api.escape_monitor（活跃 EscapeMonitor 实例）
    """
    _do_cleanup_module_caches()
    yield
    _do_cleanup_module_caches()


def _do_cleanup_module_caches():
    """执行全局模块级缓存清理。"""
    # 1. 清理 _adapter_cache — src.api.model_async
    try:
        mod = sys.modules.get('src.api.model_async')
        if mod is not None and hasattr(mod, '_adapter_cache'):
            mod._adapter_cache.clear()
    except Exception:
        pass

    # 2. 清理 _clients — src.api.client_async
    try:
        mod = sys.modules.get('src.api.client_async')
        if mod is not None and hasattr(mod, '_clients'):
            mod._clients.clear()
    except Exception:
        pass

    # 3. 清理 _model_loop_registry — src.api._model_loops
    try:
        mod = sys.modules.get('src.api._model_loops')
        if mod is not None and hasattr(mod, '_model_loop_registry'):
            lock = mod._model_loop_registry_lock
            with lock:
                for loop in mod._model_loop_registry.values():
                    if not loop.is_closed():
                        loop.close()
                mod._model_loop_registry.clear()
    except Exception:
        pass

    # 4. 清理 _default_cache — src.core.cache
    try:
        mod = sys.modules.get('src.core.cache')
        if mod is not None and hasattr(mod, 'reset_default_cache'):
            mod.reset_default_cache()
    except Exception:
        pass

    # 5. 清理 _active_consumer / _active_consumer_refcount — src.chat_ui.state
    try:
        mod = sys.modules.get('src.chat_ui.state')
        if mod is not None:
            lock = mod._state_global_lock
            with lock:
                try:
                    mod._active_consumer = None
                    mod._active_consumer_refcount = 0
                except TypeError:
                    pass
    except Exception:
        pass

    # 6. 清理 _active_monitor — src.api.escape_monitor
    try:
        mod = sys.modules.get('src.api.escape_monitor')
        if mod is not None and hasattr(mod, '_active_monitor'):
            lock = mod._active_monitor_lock
            with lock:
                try:
                    if mod._active_monitor is not None:
                        mod._active_monitor.stop()
                except Exception:
                    pass
    except Exception:
        pass
