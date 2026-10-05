"""LLM Provider 注册表 — 模型适配器的插件化注册与路由。

「一切皆插件」：模型适配器不再是硬编码的 ``if/elif`` 路由，而是注册到
进程级 :class:`LlmProviderRegistry` 的 provider 条目。内置 provider
（deepseek / anthropic / ollama / openai_compat）作为独立注册项存在，
外部插件可经 ``ctx.llm.register_provider(...)`` 注册自己的 provider，
与内置走同一套匹配、覆盖、撤销机制（对应 dsh 的 ``dsh-llm-*`` 拆包）。

匹配规则（与既有行为一致）：

- 前缀匹配：模型名小写后以某前缀开头（长前缀优先由注册顺序保证）；
- 子串匹配：模型名包含某子串（如 ``claude``）；
- fallback：无其它条目匹配时使用（openai 兼容默认）。

后注册的条目优先匹配；同名注册替换旧条目（撤销时恢复旧条目），因此
外部插件可以覆盖内置 provider，卸载后自动回退。
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Iterable, Optional

_logger = logging.getLogger(__name__)


def _normalize(value: Any) -> tuple:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,) if value else ()
    if isinstance(value, Iterable):
        return tuple(str(item) for item in value if item)
    return ()


class ProviderEntry:
    """一条 provider 注册项。"""

    __slots__ = ("name", "factory", "prefixes", "substrings", "fallback", "source")

    def __init__(
        self,
        name: str,
        factory: Callable[[], Any],
        *,
        prefixes: Any = (),
        substrings: Any = (),
        fallback: bool = False,
        source: str = "builtin",
    ) -> None:
        if not isinstance(name, str) or not name:
            raise ValueError(f"provider 名称必须是非空字符串: {name!r}")
        if not callable(factory):
            raise TypeError(f"provider 工厂必须可调用: {factory!r}")
        self.name = name
        self.factory = factory
        self.prefixes = _normalize(prefixes)
        self.substrings = _normalize(substrings)
        self.fallback = bool(fallback)
        self.source = source

    def matches(self, model_lower: str) -> bool:
        if self.fallback:
            return False
        if any(model_lower.startswith(prefix) for prefix in self.prefixes):
            return True
        if any(sub in model_lower for sub in self.substrings):
            return True
        return False

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "prefixes": list(self.prefixes),
            "substrings": list(self.substrings),
            "fallback": self.fallback,
            "source": self.source,
        }

    def __repr__(self) -> str:  # pragma: no cover - 调试辅助
        return (
            f"<ProviderEntry {self.name!r} prefixes={self.prefixes} "
            f"substrings={self.substrings} fallback={self.fallback}>"
        )


class LlmProviderRegistry:
    """模型 provider 注册表（进程级单例由 :func:`default_registry` 提供）。"""

    def __init__(self) -> None:
        self._entries: list[ProviderEntry] = []
        self._cache: dict[str, Any] = {}
        self._lock = threading.RLock()
        self._version = 0

    @property
    def version(self) -> int:
        """注册表版本号（注册/撤销时递增，供外部缓存失效判断）。"""
        return self._version

    # ── 注册 / 撤销 ───────────────────────────────────────

    def register(
        self,
        name: str,
        factory: Callable[[], Any],
        *,
        prefixes: Any = (),
        substrings: Any = (),
        fallback: bool = False,
        source: str = "plugin",
    ) -> Callable[[], None]:
        """注册 provider，返回撤销函数（撤销时恢复被覆盖的同名条目）。"""
        entry = ProviderEntry(
            name,
            factory,
            prefixes=prefixes,
            substrings=substrings,
            fallback=fallback,
            source=source,
        )
        with self._lock:
            previous: Optional[ProviderEntry] = None
            for index in range(len(self._entries) - 1, -1, -1):
                if self._entries[index].name == name:
                    previous = self._entries.pop(index)
                    break
            self._entries.append(entry)
            self._bump_version_locked()

        released = False

        def _undo() -> None:
            nonlocal released
            if released:
                return
            released = True
            with self._lock:
                try:
                    self._entries.remove(entry)
                except ValueError:
                    pass
                if previous is not None:
                    self._entries.append(previous)
                self._bump_version_locked()

        return _undo

    def unregister(self, name: str) -> bool:
        with self._lock:
            for index in range(len(self._entries) - 1, -1, -1):
                if self._entries[index].name == name:
                    self._entries.pop(index)
                    self._bump_version_locked()
                    return True
        return False

    # ── 解析 ─────────────────────────────────────────────

    def resolve(self, model: str) -> Any:
        """按模型名解析适配器实例（带缓存）。"""
        key = (model or "").lower()
        with self._lock:
            cached = self._cache.get(model)
            if cached is not None:
                return cached
            entry = self._match(key)
            if entry is None:
                raise ValueError(f"没有匹配的 LLM provider: {model!r}")
            adapter = entry.factory()
            self._cache[model] = adapter
            return adapter

    def _match(self, model_lower: str) -> Optional[ProviderEntry]:
        for entry in reversed(self._entries):
            if entry.fallback:
                continue
            if entry.matches(model_lower):
                return entry
        for entry in self._entries:
            if entry.fallback:
                return entry
        return None

    def match_name(self, model: str) -> Optional[str]:
        """返回匹配该模型的 provider 名（无匹配返回 None）。"""
        entry = self._match((model or "").lower())
        return entry.name if entry is not None else None

    # ── 自省 ─────────────────────────────────────────────

    def names(self) -> list:
        return [entry.name for entry in self._entries]

    def entries(self) -> list:
        return list(self._entries)

    def describe(self) -> list:
        return [entry.to_dict() for entry in self._entries]

    # ── 缓存 ─────────────────────────────────────────────

    def _bump_version_locked(self) -> None:
        """清空解析缓存并递增版本号（外部适配器缓存据此失效）。"""
        self._cache.clear()
        self._version += 1


# ── 内置 provider ────────────────────────────────────────

def _deepseek_factory() -> Any:
    from .adapters import DeepSeekAdapter

    return DeepSeekAdapter()


def _anthropic_factory() -> Any:
    from .adapters.anthropic import AnthropicAdapter

    return AnthropicAdapter()


def _ollama_factory() -> Any:
    from .adapters.ollama import OllamaAdapter

    return OllamaAdapter()


def _openai_compat_factory() -> Any:
    from .adapters import OpenAICompatAdapter

    return OpenAICompatAdapter()


#: 内置 provider 声明（供默认注册表与 ``llm_providers`` 插件共用）。
BUILTIN_PROVIDERS: tuple = (
    {
        "name": "openai_compat",
        "factory": _openai_compat_factory,
        "fallback": True,
    },
    {
        "name": "deepseek",
        "factory": _deepseek_factory,
        "prefixes": ("deepseek",),
    },
    {
        "name": "anthropic",
        "factory": _anthropic_factory,
        "prefixes": ("anthropic",),
        "substrings": ("claude",),
    },
    {
        "name": "ollama",
        "factory": _ollama_factory,
        "prefixes": ("ollama",),
    },
)


def register_builtin_providers(registry: Optional[LlmProviderRegistry] = None) -> LlmProviderRegistry:
    """把内置 provider 注册到注册表（默认进程级注册表）。"""
    registry = registry if registry is not None else default_registry()
    for spec in BUILTIN_PROVIDERS:
        registry.register(
            spec["name"],
            spec["factory"],
            prefixes=spec.get("prefixes", ()),
            substrings=spec.get("substrings", ()),
            fallback=bool(spec.get("fallback", False)),
            source="builtin",
        )
    return registry


# ── 进程级默认注册表 ─────────────────────────────────────

_default_registry: Optional[LlmProviderRegistry] = None
_default_lock = threading.Lock()


def build_default_registry() -> LlmProviderRegistry:
    """构建一个含全部内置 provider 的新注册表（不接入进程级单例）。

    供 ``ctx.llm`` 服务在构造期持有自己的注册表实例（「一切皆插件」：模型
    provider 路由是内核服务的一部分）；也供无内核回退路径构建默认单例。
    """
    registry = LlmProviderRegistry()
    for spec in BUILTIN_PROVIDERS:
        registry.register(
            spec["name"],
            spec["factory"],
            prefixes=spec.get("prefixes", ()),
            substrings=spec.get("substrings", ()),
            fallback=bool(spec.get("fallback", False)),
            source="builtin",
        )
    return registry


def default_registry() -> LlmProviderRegistry:
    """返回默认 provider 注册表。

    内核优先：内核挂载 ``ctx.llm`` 服务后返回其注册表（与模型服务同源）；
    内核缺失或服务尚在构造中时回退进程级单例（懒初始化时注册内置 provider）。
    """
    try:
        from ..kernel.runtime import active_service

        service = active_service("llm")
        registry = service.provider_registry() if service is not None else None
        if registry is not None:
            return registry
    except Exception:
        pass

    global _default_registry
    if _default_registry is None:
        with _default_lock:
            if _default_registry is None:
                _default_registry = build_default_registry()
    return _default_registry


def reset_default_registry() -> None:
    """重置进程级默认注册表（测试用）。"""
    global _default_registry
    with _default_lock:
        _default_registry = None


def resolve_adapter(model: str) -> Any:
    """按模型名解析适配器（默认注册表入口）。"""
    return default_registry().resolve(model)


__all__ = [
    "LlmProviderRegistry",
    "ProviderEntry",
    "BUILTIN_PROVIDERS",
    "default_registry",
    "reset_default_registry",
    "register_builtin_providers",
    "resolve_adapter",
]
