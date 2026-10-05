"""技能来源注册表 — 内置技能来源与插件扩展的单一来源。

「一切皆插件」：技能发现来源（项目 ``.skills`` / GitHub 安装
``.skills/installed``）不再是 ``SkillRegistry.roots()`` 里的硬编码，而是注册
到本注册表的 source 条目；每一项都由清单中的**独立插件条目**（``skill_source``）
显式声明，因而可被 Profile/Bundle 声明，也可被 Patch/Overlay 按 id 单独禁用、
覆盖或替换。

**清单接管**：``skill_sources`` 聚合插件收到组合根注入的
``managed_skill_sources``（清单已接管的 id，含被禁用的）时经
``set_managed_builtin_skill_sources`` 声明这些 id 由清单条目负责——对应内置
来源不再走默认装配。无清单（单元测试、独立调用）时无接管，全部内置来源默认
生效。

来源（source）是一个可调用对象 ``(registry, cwd) -> list[(path, source, rank)]``；
外部插件可经 ``ctx.skill_sources.register(name, factory)`` 注册自定义来源
（如远程仓库、运行时生成目录），与内置走同一套解析/替换/撤销机制。
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Dict, List, Tuple

_logger = logging.getLogger(__name__)

_lock = threading.RLock()
_ABSENT = object()


def _project_source(registry: Any, cwd: Any) -> list:
    """项目技能来源：``<项目根>/.skills``（rank 100）。"""
    from .models import RANK_PROJECT

    return [(registry.skills_dir(cwd), "project", RANK_PROJECT)]


def _installed_source(registry: Any, cwd: Any) -> list:
    """GitHub 安装来源：``<项目根>/.skills/installed/<owner>__<repo>``（rank 200）。"""
    from .models import RANK_INSTALLED

    root = registry.installed_root(cwd)
    if not root.is_dir():
        return []
    out: list = []
    try:
        for sub in sorted(root.iterdir()):
            if sub.name.startswith("."):
                continue
            if sub.is_dir():
                out.append((sub, "github", RANK_INSTALLED))
    except OSError:
        _logger.debug("读取 .skills/installed 目录异常", exc_info=True)
    return out


#: 内置来源声明（id → 工厂）——每项由清单中的独立插件条目注册。
_BUILTIN_SOURCE_SPECS: Tuple[Tuple[str, Callable[[Any, Any], list]], ...] = (
    ("project", _project_source),
    ("installed", _installed_source),
)

_builtin_specs: Dict[str, Callable[[Any, Any], list]] = dict(_BUILTIN_SOURCE_SPECS)
_registered_builtin: Dict[str, Callable[[Any, Any], list]] = {}
_managed_builtin: set = set()
_disabled_builtin: set = set()
_extension: Dict[str, Callable[[Any, Any], list]] = {}


def builtin_skill_source_ids() -> list[str]:
    return list(_builtin_specs)


def default_skill_source_factory(spec_id: str) -> Callable[[Any, Any], list]:
    try:
        return _builtin_specs[spec_id]
    except KeyError:
        raise KeyError(f"未知内置技能来源: {spec_id!r}（可用: {list(_builtin_specs)}）") from None


def _normalize_ids(ids) -> List[str]:
    if isinstance(ids, str):
        ids = [ids]
    selected: List[str] = []
    for item in ids or ():
        if item not in _builtin_specs:
            raise KeyError(f"未知内置技能来源: {item!r}（可用: {list(_builtin_specs)}）")
        selected.append(item)
    return selected


def active_skill_source_factories() -> Dict[str, Callable[[Any, Any], list]]:
    """当前生效的内置来源工厂（``id → 工厂``，含扩展项，内置在前）。"""
    with _lock:
        result: Dict[str, Callable[[Any, Any], list]] = {}
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
        for name, factory in _extension.items():
            result[name] = factory
        return result


def register_builtin_skill_source(spec_id: str, factory: Any = None) -> Callable[[], None]:
    """注册/覆盖一个内置来源（``factory=None`` 用默认工厂）；返回幂等撤销。"""
    if spec_id not in _builtin_specs:
        raise KeyError(f"未知内置技能来源: {spec_id!r}（可用: {list(_builtin_specs)}）")
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


def unregister_builtin_skill_source(spec_id: str) -> bool:
    with _lock:
        return _registered_builtin.pop(spec_id, None) is not None


def set_managed_builtin_skill_sources(ids) -> Callable[[], None]:
    selected = _normalize_ids(ids)
    with _lock:
        added = [item for item in selected if item not in _managed_builtin]
        _managed_builtin.update(added)

    def _undo() -> None:
        with _lock:
            for item in added:
                _managed_builtin.discard(item)

    return _undo


def managed_skill_source_ids() -> list[str]:
    with _lock:
        return sorted(_managed_builtin)


def disable_builtin_skill_sources(ids) -> Callable[[], None]:
    selected = _normalize_ids(ids)
    with _lock:
        added = [item for item in selected if item not in _disabled_builtin]
        _disabled_builtin.update(added)

    def _undo() -> None:
        with _lock:
            for item in added:
                _disabled_builtin.discard(item)

    return _undo


def register_skill_source(name: str, factory: Callable[[Any, Any], list]) -> Callable[[], None]:
    """注册扩展来源（``factory(registry, cwd) -> list[(path, source, rank)]``）。"""
    if not isinstance(name, str) or not name:
        raise ValueError(f"来源名必须是非空字符串: {name!r}")
    if not callable(factory):
        raise TypeError(f"来源工厂必须可调用: {factory!r}")
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


def skill_source_factories() -> Dict[str, Callable[[Any, Any], list]]:
    with _lock:
        return dict(_extension)


def resolve_skill_sources(registry: Any, cwd: Any = None) -> list:
    """按生效来源顺序解析技能根目录列表（单个来源失败不影响其余）。"""
    entries: list = []
    for spec_id, factory in active_skill_source_factories().items():
        try:
            entries.extend(factory(registry, cwd) or [])
        except Exception:  # noqa: BLE001 - 单个来源失败降级
            _logger.warning("技能来源解析失败: %s", spec_id, exc_info=True)
    return entries


def active_skill_sources(registry: Any, cwd: Any = None) -> list:
    """解析技能根目录列表（内核 ``ctx.skill_sources`` 优先，回退进程级注册表）。"""
    try:
        from ..kernel.runtime import active_service

        service = active_service("skill_sources")
        if service is not None:
            return service.resolve(registry, cwd)
    except Exception:  # pragma: no cover - 内核异常时回退
        _logger.debug("内核 skill_sources 服务解析失败，回退进程级注册表", exc_info=True)
    return resolve_skill_sources(registry, cwd)


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


__all__ = [
    "builtin_skill_source_ids",
    "default_skill_source_factory",
    "active_skill_source_factories",
    "register_builtin_skill_source",
    "unregister_builtin_skill_source",
    "set_managed_builtin_skill_sources",
    "managed_skill_source_ids",
    "disable_builtin_skill_sources",
    "register_skill_source",
    "skill_source_factories",
    "resolve_skill_sources",
    "active_skill_sources",
    "clear",
    "reset",
]
