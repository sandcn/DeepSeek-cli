"""事件消费者注册表 — 内置消费者与插件扩展的单一来源。

「一切皆插件」：终端事件消费者（``output`` 输出事件直写 / ``chat_ui`` 聊天界面
渲染 / ``error_handler`` 日志错误上屏处理器）不再硬编码在 ``src.plugins.consumers``
里，而是注册到本注册表；每一项都由清单中的**独立插件条目**（``consumer``）显式
声明，可被 Profile/Bundle 声明，也可被 Patch/Overlay 按 id 单独禁用、覆盖或替换。

**清单接管**：``consumers`` 聚合插件收到组合根注入的 ``managed_consumers``
（清单已接管的 id，含被禁用的）时经 ``set_managed_builtin_consumers`` 声明这些
id 由清单条目负责；无清单（单元测试、独立调用）时无接管，全部内置消费者默认
生效。

消费者规格：``factory(**kwargs) -> consumer``；返回对象可提供 ``start()`` /
``stop()``（错误处理器消费者用 start/stop 注册/注销日志处理器）。
"""

from __future__ import annotations

import importlib
import logging
import threading
from typing import Any, Callable, Dict, List, Tuple

_logger = logging.getLogger(__name__)

_lock = threading.RLock()
_ABSENT = object()

_BUILTIN_SPECS: Tuple[Tuple[str, str, str], ...] = (
    ("output", "src.tui.events.consumers", "OutputConsumer"),
    ("chat_ui", "src.tui.consumer", "ChatUIConsumer"),
    ("error_handler", "src.tui.events.consumer_registry", "_ErrorHandlerConsumer"),
)


def _make_factory(module_name: str, class_name: str):
    def _factory(**kwargs):
        module = importlib.import_module(module_name)
        cls = getattr(module, class_name)
        return cls(**kwargs)

    _factory.__name__ = f"_builtin_{class_name}_factory"
    return _factory


_builtin_specs: Dict[str, Callable[..., Any]] = {
    spec_id: _make_factory(module_name, class_name)
    for spec_id, module_name, class_name in _BUILTIN_SPECS
}

_registered_builtin: Dict[str, Callable[..., Any]] = {}
_managed_builtin: set = set()
_disabled_builtin: set = set()
_extension: Dict[str, Callable[..., Any]] = {}


def _normalize_ids(ids) -> List[str]:
    if isinstance(ids, str):
        ids = [ids]
    selected: List[str] = []
    for item in ids or ():
        if item not in _builtin_specs:
            raise KeyError(f"未知内置事件消费者: {item!r}（可用: {list(_builtin_specs)}）")
        selected.append(item)
    return selected


def builtin_consumer_ids() -> list[str]:
    return list(_builtin_specs)


def default_consumer_factory(spec_id: str) -> Callable[..., Any]:
    try:
        return _builtin_specs[spec_id]
    except KeyError:
        raise KeyError(f"未知内置事件消费者: {spec_id!r}（可用: {list(_builtin_specs)}）") from None


def builtin_consumer_factories() -> Dict[str, Callable[..., Any]]:
    with _lock:
        result: Dict[str, Callable[..., Any]] = {}
        for spec_id, default in _builtin_specs.items():
            if spec_id in _disabled_builtin:
                continue
            override = _registered_builtin.get(spec_id)
            if override is not None:
                result[spec_id] = override
                continue
            if spec_id in _managed_builtin:
                continue
            result[spec_id] = default
        return result


def register_builtin_consumer(spec_id: str, factory: Any = None) -> Callable[[], None]:
    if spec_id not in _builtin_specs:
        raise KeyError(f"未知内置事件消费者: {spec_id!r}（可用: {list(_builtin_specs)}）")
    with _lock:
        previous = _registered_builtin.get(spec_id, _ABSENT)
        _registered_builtin[spec_id] = factory if factory is not None else _builtin_specs[spec_id]

    def _undo() -> None:
        with _lock:
            if previous is _ABSENT:
                _registered_builtin.pop(spec_id, None)
            else:
                _registered_builtin[spec_id] = previous

    return _undo


def unregister_builtin_consumer(spec_id: str) -> bool:
    with _lock:
        return _registered_builtin.pop(spec_id, None) is not None


def set_managed_builtin_consumers(ids) -> Callable[[], None]:
    selected = _normalize_ids(ids)
    with _lock:
        added = [item for item in selected if item not in _managed_builtin]
        _managed_builtin.update(added)

    def _undo() -> None:
        with _lock:
            for item in added:
                _managed_builtin.discard(item)

    return _undo


def managed_consumer_ids() -> list[str]:
    with _lock:
        return sorted(_managed_builtin)


def disable_builtin_consumers(ids) -> Callable[[], None]:
    selected = _normalize_ids(ids)
    with _lock:
        added = [item for item in selected if item not in _disabled_builtin]
        _disabled_builtin.update(added)

    def _undo() -> None:
        with _lock:
            for item in added:
                _disabled_builtin.discard(item)

    return _undo


def register_consumer(name: str, factory: Callable[..., Any]) -> Callable[[], None]:
    """注册一个扩展消费者（新消费者参与解析）；返回幂等撤销。"""
    if not isinstance(name, str) or not name:
        raise ValueError(f"消费者名必须是非空字符串: {name!r}")
    if not callable(factory):
        raise TypeError(f"消费者工厂必须可调用: {factory!r}")
    with _lock:
        previous = _extension.get(name, _ABSENT)
        _extension[name] = factory

    def _undo() -> None:
        with _lock:
            if previous is _ABSENT:
                _extension.pop(name, None)
            else:
                _extension[name] = previous

    return _undo


def consumer_factories() -> Dict[str, Callable[..., Any]]:
    with _lock:
        return dict(_extension)


def consumer_names() -> List[str]:
    with _lock:
        return sorted(set(builtin_consumer_factories()) | set(_extension))


def resolve_consumer_factory(spec_id: str):
    """解析某 id 的消费者工厂（扩展 → 生效内置 → None）。"""
    with _lock:
        factory = _extension.get(spec_id)
        if factory is None:
            factory = builtin_consumer_factories().get(spec_id)
    return factory


def build_consumer(spec_id: str, **kwargs):
    """构造某 id 的消费者（缺席返回 None）。"""
    factory = resolve_consumer_factory(spec_id)
    if factory is None:
        return None
    return factory(**kwargs)


def clear() -> None:
    with _lock:
        _extension.clear()
        _registered_builtin.clear()


def reset() -> None:
    with _lock:
        _extension.clear()
        _registered_builtin.clear()
        _managed_builtin.clear()
        _disabled_builtin.clear()


class _ErrorHandlerConsumer:
    """日志错误上屏处理器消费者（start/stop 注册/注销）。"""

    def __init__(self, **kwargs):
        self._installed = False

    def start(self) -> None:
        from ..consumer import setup_chat_ui_error_handler

        if self._installed:
            return
        setup_chat_ui_error_handler()
        self._installed = True

    def stop(self) -> None:
        try:
            from ..consumer import teardown_chat_ui_error_handler

            teardown_chat_ui_error_handler()
        except Exception:
            _logger.debug("注销 ChatUI 错误处理器异常", exc_info=True)
        self._installed = False


__all__ = [
    "builtin_consumer_ids",
    "builtin_consumer_factories",
    "default_consumer_factory",
    "register_builtin_consumer",
    "unregister_builtin_consumer",
    "set_managed_builtin_consumers",
    "managed_consumer_ids",
    "disable_builtin_consumers",
    "register_consumer",
    "consumer_factories",
    "consumer_names",
    "resolve_consumer_factory",
    "build_consumer",
    "clear",
    "reset",
]
