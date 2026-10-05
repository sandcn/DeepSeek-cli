"""不变量插件 — 提供 ``ctx.invariants``（运行时自检）。

在独立 Fiber 里断言本插件树拥有的运行期关系（服务合法性、工具注册表同源、
Fiber 依赖一致、可替换组件齐全）。任何违反都会被记录/上报，便于「一切皆
插件」的动态组合在运行期保持自洽。
"""

from __future__ import annotations

import logging

from ..kernel import Service, plugin
from ..kernel.fiber import FiberState
from ..kernel.invariants import InvariantRegistry

_logger = logging.getLogger(__name__)


def _service_keys_valid(kernel) -> str | None:
    for key in kernel.service_keys():
        if not isinstance(key, str) or not key:
            return f"非法服务 key: {key!r}"
        if kernel.resolve_service(key) is None:
            return f"服务 {key!r} 的值为 None"
    return None


def _fibers_active_have_deps(kernel) -> str | None:
    for fiber in kernel.fibers():
        if fiber.state is FiberState.ACTIVE and not fiber.deps_ready():
            return f"Fiber {fiber.name!r} 处于 ACTIVE 但依赖未就绪: {fiber.missing_dependencies()}"
    return None


def _tools_registry_consistent(kernel) -> str | None:
    if not kernel.has_service("tools"):
        return None
    service = kernel.resolve_service("tools")
    registry = service.registry
    for name, tool_class in registry.get_tools().items():
        if getattr(tool_class, "name", None) != name:
            return f"工具 {name!r} 的 name 属性不匹配: {getattr(tool_class, 'name', None)!r}"
    if registry is not type(registry).default():
        return "工具注册表不是进程级默认注册表（与调度器/MCP 不同源）"
    return None


def _agent_loop_dependencies(kernel) -> str | None:
    if not kernel.has_service("agent_loop"):
        return None
    required = {"tools", "llm", "config", "prompt", "events", "presets"}
    missing = sorted(required - set(kernel.service_keys()))
    if missing:
        return f"agent_loop 已加载但缺少服务: {missing}"
    return None


def _presets_have_standard(kernel) -> str | None:
    if not kernel.has_service("presets"):
        return None
    if "standard" not in kernel.resolve_service("presets").list():
        return "presets 服务缺少内置 standard 预设"
    return None


def _agents_messages_recorded(kernel) -> str | None:
    """「模型可见即已记录」：活跃 Agent 的消息视图必须与其会话日志投影一致。"""
    if not kernel.has_service("agents"):
        return None
    registry = kernel.resolve_service("agents")
    for record in registry.active():
        messages = getattr(record.agent, "messages", None)
        if messages is None:
            continue
        verify = getattr(messages, "verify", None)
        if verify is None:
            continue
        try:
            consistent = verify()
        except Exception as exc:  # noqa: BLE001 - 校验失败即上报
            return f"Agent {record.id!r} 会话日志校验异常: {exc}"
        if not consistent:
            return (
                f"Agent {record.id!r} 的消息列表与会话日志投影不一致"
                "（「模型可见即已记录」被违反）"
            )
    return None


def _service_providers_present(kernel) -> str | None:
    """可替换 provider 的服务必须持有 provider（可插拔服务不得为空壳）。"""
    checks = (
        ("observability", "port"),
        ("notifications", "port"),
        ("persistence", "port"),
        ("checkpoint", "port"),
    )
    for key, getter in checks:
        if not kernel.has_service(key):
            continue
        service = kernel.resolve_service(key)
        provider = getattr(service, getter, None)
        if callable(provider):
            provider = provider()
        if provider is None:
            return f"服务 {key!r} 的 provider 为空"
    return None


def _llm_providers_available(kernel) -> str | None:
    """llm 服务必须至少有注册 provider，且保留兜底 provider。"""
    if not kernel.has_service("llm"):
        return None
    service = kernel.resolve_service("llm")
    names = getattr(service, "provider_names", None)
    if not callable(names):
        return None
    providers = list(names())
    if not providers:
        return "llm 服务没有任何已注册的模型 provider"
    if "openai_compat" not in providers:
        return "llm 服务缺少兜底 provider openai_compat"
    return None


def _renderer_extensions_readable(kernel) -> str | None:
    """renderer 扩展点必须可读（handler/filter 注册表自省不抛异常）。"""
    if not kernel.has_service("renderer"):
        return None
    service = kernel.resolve_service("renderer")
    for method in ("handlers", "filters"):
        read = getattr(service, method, None)
        if not callable(read):
            continue
        try:
            list(read())
        except Exception as exc:  # noqa: BLE001 - 读取失败即上报
            return f"renderer 扩展 {method} 读取失败: {exc}"
    return None


_BUILTIN_CHECKS = (
    ("services.keys_valid", _service_keys_valid),
    ("fibers.active_have_deps", _fibers_active_have_deps),
    ("tools.registry_consistent", _tools_registry_consistent),
    ("agent_loop.dependencies", _agent_loop_dependencies),
    ("presets.has_standard", _presets_have_standard),
    ("agents.messages_recorded", _agents_messages_recorded),
    ("services.providers_present", _service_providers_present),
    ("llm.providers_available", _llm_providers_available),
    ("renderer.extensions_readable", _renderer_extensions_readable),
)


class InvariantsService(Service):
    """不变量服务 — 占据 ``ctx.invariants``。"""

    provide = "invariants"
    name = "invariants"
    inject = ("tools",)

    def __init__(self, ctx, config=None):
        super().__init__(ctx, config)
        self._registry = InvariantRegistry()
        for name, check in _BUILTIN_CHECKS:
            self._registry.register(name, check)
        ctx.effect(lambda: self._registry.clear)
        # 启动自检：失败只记日志，不阻断启动（与 dsh 的 invariant fiber 一致）
        failures = self.check()
        for failure in failures:
            _logger.warning("运行时不变量失败: %s", failure)

    def names(self) -> list:
        return self._registry.names()

    def register(self, name: str, check) -> None:
        self._registry.register(name, check)

    def unregister(self, name: str) -> bool:
        return self._registry.unregister(name)

    def check(self) -> list:
        return self._registry.check_all(self.ctx.kernel)


@plugin("invariants", inject=["tools"], provide=["invariants"])
def apply(ctx):
    return InvariantsService(ctx)
