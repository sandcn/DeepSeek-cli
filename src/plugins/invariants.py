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
    for method in ("handlers", "filters", "builtin_handlers", "builtin_filters",
                   "builtin_handler_ids", "builtin_filter_ids"):
        read = getattr(service, method, None)
        if not callable(read):
            continue
        try:
            list(read())
        except Exception as exc:  # noqa: BLE001 - 读取失败即上报
            return f"renderer 扩展 {method} 读取失败: {exc}"
    return None


def _agent_middleware_readable(kernel) -> str | None:
    """Agent 中间件注册表必须可读（清单接管/禁用自省不抛异常）。"""
    try:
        from ..core.middleware.registry import builtin_middleware_factories, builtin_middleware_ids

        list(builtin_middleware_ids())
        list(builtin_middleware_factories())
    except Exception as exc:  # noqa: BLE001 - 读取失败即上报
        return f"Agent 中间件注册表读取失败: {exc}"
    return None


def _subagents_types_registered(kernel) -> str | None:
    """``ctx.subagents`` 必须注册全部内置 Agent 类型（类型是清单独立条目）。"""
    if not kernel.has_service("subagents"):
        return None
    service = kernel.resolve_service("subagents")
    try:
        types = set(service.types())
    except Exception as exc:  # noqa: BLE001 - 读取失败即上报
        return f"subagents 类型注册表读取失败: {exc}"
    if not types:
        return "subagents 服务没有任何已注册的 Agent 类型"
    missing = sorted({"map", "review", "plan", "execute"} - types)
    if missing:
        return f"subagents 服务缺少内置 Agent 类型: {missing}"
    return None


def _stream_handlers_readable(kernel) -> str | None:
    """流式处理器注册表必须可读，且 ``ctx.stream`` 覆盖全部内置角色。"""
    try:
        from ..api.stream.registry import (
            builtin_stream_handler_factories,
            builtin_stream_handler_ids,
        )

        ids = list(builtin_stream_handler_ids())
        builtin_stream_handler_factories()
    except Exception as exc:  # noqa: BLE001 - 读取失败即上报
        return f"流式处理器注册表读取失败: {exc}"
    if not kernel.has_service("stream"):
        return None
    service = kernel.resolve_service("stream")
    try:
        active = set(service.handlers())
    except Exception as exc:  # noqa: BLE001 - 读取失败即上报
        return f"ctx.stream 处理器读取失败: {exc}"
    missing = sorted(set(ids) - active)
    if missing:
        return f"ctx.stream 缺少内置流式处理器: {missing}"
    return None


def _ui_consumers_views_readable(kernel) -> str | None:
    """事件消费者 / UI 视图注册表必须可读，且生效项覆盖全部内置项。"""
    try:
        from ..tui.events.consumer_registry import builtin_consumer_ids, consumer_names
        from ..tui.app.view_registry import builtin_view_ids, active_view_ids

        builtin_consumer_ids()
        builtin_view_ids()
        consumers = set(consumer_names())
        views = set(active_view_ids())
    except Exception as exc:  # noqa: BLE001 - 读取失败即上报
        return f"事件消费者/UI 视图注册表读取失败: {exc}"
    if kernel.has_service("consumers"):
        try:
            active = set(kernel.resolve_service("consumers").consumers())
        except Exception as exc:  # noqa: BLE001 - 读取失败即上报
            return f"ctx.consumers 消费者读取失败: {exc}"
        missing = sorted(consumers - active)
        if missing:
            return f"ctx.consumers 缺少内置消费者: {missing}"
    if kernel.has_service("ui"):
        try:
            active_views = set(kernel.resolve_service("ui").views())
        except Exception as exc:  # noqa: BLE001 - 读取失败即上报
            return f"ctx.ui 视图读取失败: {exc}"
        missing = sorted(views - active_views)
        if missing:
            return f"ctx.ui 缺少内置视图: {missing}"
    return None


def _declared_provides_present(kernel) -> str | None:
    """活跃 Fiber 声明的 ``provide`` 服务必须全部在册（严格模式的运行期保障）。"""
    missing = []
    for fiber in kernel.fibers():
        if fiber.state is not FiberState.ACTIVE:
            continue
        for key in getattr(fiber.definition, "provide", ()) or ():
            if not kernel.has_service(str(key)):
                missing.append(str(key))
    if missing:
        return f"已声明提供的服务缺失: {sorted(set(missing))}"
    return None


def _runtime_data_services_readable(kernel) -> str | None:
    """运行时数据服务（消息队列/多模态/上下文选择与摘要/统计/token）可读。"""
    checks = (
        ("message_queue", ("create",)),
        ("multimodal", ("is_multimodal", "optimize_messages_for_upload")),
        ("context_selector", ("select_for_compression",)),
        ("context_summarizer", ("summarize",)),
        ("stats", ("token_stats",)),
        ("tokens", ("estimate",)),
    )
    for key, methods in checks:
        if not kernel.has_service(key):
            continue
        service = kernel.resolve_service(key)
        for method in methods:
            if not callable(getattr(service, method, None)):
                return f"服务 {key!r} 缺少方法 {method!r}"
    return None


def _tool_engines_readable(kernel) -> str | None:
    """工具执行引擎注册表必须可读，且默认引擎可解析（调度不中断）。"""
    try:
        from ..core.tool_engines import (
            builtin_tool_engine_factories,
            builtin_tool_engine_ids,
            default_tool_engine,
        )

        list(builtin_tool_engine_ids())
        builtin_tool_engine_factories()
        if not callable(default_tool_engine()):
            return "工具执行引擎默认引擎不可调用"
    except Exception as exc:  # noqa: BLE001 - 读取失败即上报
        return f"工具执行引擎注册表读取失败: {exc}"
    return None


def _providers_readable(kernel) -> str | None:
    """可替换 provider 注册表必须可读，且生效项非空。

    覆盖：通知后端 / 上下文压缩策略 / MCP 传输。
    """
    if kernel.has_service("notifications"):
        try:
            service = kernel.resolve_service("notifications")
            backends = list(service.backends())
            from ..notifications.registry import builtin_notification_backend_ids

            missing = sorted(set(builtin_notification_backend_ids()) - set(backends))
            if missing:
                return f"ctx.notifications 缺少内置通知后端: {missing}"
        except Exception as exc:  # noqa: BLE001 - 读取失败即上报
            return f"通知后端注册表读取失败: {exc}"
    if kernel.has_service("context"):
        try:
            service = kernel.resolve_service("context")
            names = list(service.strategy_names())
            if not names:
                return "ctx.context 没有任何已注册的压缩策略"
        except Exception as exc:  # noqa: BLE001 - 读取失败即上报
            return f"压缩策略注册表读取失败: {exc}"
    if kernel.has_service("mcp"):
        try:
            from ..mcp.transport_registry import builtin_mcp_transport_ids

            service = kernel.resolve_service("mcp")
            transports = set(service.transports())
            missing = sorted(set(builtin_mcp_transport_ids()) - transports)
            if missing:
                return f"ctx.mcp 缺少内置传输: {missing}"
        except Exception as exc:  # noqa: BLE001 - 读取失败即上报
            return f"MCP 传输注册表读取失败: {exc}"
    return None


def _singletons_kernel_source(kernel) -> str | None:
    """进程级单例访问函数必须解析到内核服务独占实例（内核服务为唯一真源）。"""
    if kernel.has_service("events"):
        from ..core.adapters.events import DisplayEventBusAdapter
        from ..core.events.display_bus import DisplayEventBus
        from ..core.events.event_bus import get_default_bus

        events = kernel.resolve_service("events")
        if get_default_bus() is not events.bus:
            return "核心事件总线单例未指向 ctx.events 服务"
        if DisplayEventBus.get_default() is not events.display_bus:
            return "显示事件总线单例未指向 ctx.events 服务"
        if DisplayEventBusAdapter.get_default() is not events.event_adapter():
            return "显示事件适配器单例未指向 ctx.events 服务"
    if kernel.has_service("output"):
        from ..core.adapters.output import DefaultOutputAdapter

        if DefaultOutputAdapter.get_default() is not kernel.resolve_service("output").port:
            return "默认输出端口单例未指向 ctx.output 服务"
    if kernel.has_service("cache"):
        from ..core.cache import get_default_cache

        if get_default_cache() is not kernel.resolve_service("cache").cache:
            return "默认缓存单例未指向 ctx.cache 服务"
    if kernel.has_service("observability"):
        from ..core.telemetry.metrics import get_default_collector
        from ..core.telemetry.tracer import get_default_tracer
        from ..observability.facade import get_default_facade

        observability = kernel.resolve_service("observability")
        if get_default_facade() is not observability.provider:
            return "默认可观测门面单例未指向 ctx.observability 服务"
        if get_default_collector() is not observability.collector:
            return "默认指标收集器单例未指向 ctx.observability 服务"
        if get_default_tracer() is not observability.tracer:
            return "默认追踪器单例未指向 ctx.observability 服务"
    if kernel.has_service("mcp"):
        from ..mcp.manager import McpManager

        if McpManager.default() is not kernel.resolve_service("mcp").manager:
            return "McpManager 单例未指向 ctx.mcp 服务"
    if kernel.has_service("ui"):
        from ..tui._screen import TerminalWidthCache
        from ..tui._subagent_panel import SubAgentPanelController

        ui = kernel.resolve_service("ui")
        if TerminalWidthCache.get_default() is not ui.width_cache:
            return "终端宽度缓存单例未指向 ctx.ui 服务"
        if SubAgentPanelController.get_default() is not ui.subagent_panel:
            return "SubAgent 面板控制器单例未指向 ctx.ui 服务"
    return None


def _web_providers_readable(kernel) -> str | None:
    """Web 搜索/抓取提供者注册表必须可读，且生效项覆盖内置项。"""
    if kernel.has_service("web_search"):
        try:
            from ..tools.search_provider_registry import builtin_search_provider_ids

            service = kernel.resolve_service("web_search")
            names = set(service.provider_names())
            missing = sorted(set(builtin_search_provider_ids()) - names)
            if missing:
                return f"ctx.web_search 缺少内置搜索提供者: {missing}"
        except Exception as exc:  # noqa: BLE001 - 读取失败即上报
            return f"Web 搜索提供者注册表读取失败: {exc}"
    if kernel.has_service("web_fetch"):
        try:
            from ..tools.fetch_provider_registry import builtin_fetch_provider_ids

            service = kernel.resolve_service("web_fetch")
            names = set(service.provider_names())
            missing = sorted(set(builtin_fetch_provider_ids()) - names)
            if missing:
                return f"ctx.web_fetch 缺少内置抓取提供者: {missing}"
        except Exception as exc:  # noqa: BLE001 - 读取失败即上报
            return f"Web 抓取提供者注册表读取失败: {exc}"
    return None


def _themes_readable(kernel) -> str | None:
    """主题注册表必须可读，且生效项覆盖全部内置主题。"""
    if not kernel.has_service("themes"):
        return None
    try:
        from ..tui.core._theme import builtin_theme_names

        service = kernel.resolve_service("themes")
        names = set(service.names())
        missing = sorted(set(builtin_theme_names()) - names)
        if missing:
            return f"ctx.themes 缺少内置主题: {missing}"
    except Exception as exc:  # noqa: BLE001 - 读取失败即上报
        return f"主题注册表读取失败: {exc}"
    return None


def _skill_sources_readable(kernel) -> str | None:
    """技能来源注册表必须可读，且生效项覆盖全部内置来源。"""
    if not kernel.has_service("skill_sources"):
        return None
    try:
        from ..skills.source_registry import builtin_skill_source_ids

        service = kernel.resolve_service("skill_sources")
        names = set(service.source_names())
        missing = sorted(set(builtin_skill_source_ids()) - names)
        if missing:
            return f"ctx.skill_sources 缺少内置来源: {missing}"
    except Exception as exc:  # noqa: BLE001 - 读取失败即上报
        return f"技能来源注册表读取失败: {exc}"
    return None


def _session_projections_readable(kernel) -> str | None:
    """会话投影注册表必须可读，且生效项覆盖全部内置投影。"""
    if not kernel.has_service("session_projections"):
        return None
    try:
        from ..core.session_log.builtin_projections import builtin_projection_names

        service = kernel.resolve_service("session_projections")
        names = set(service.names())
        missing = sorted(set(builtin_projection_names()) - names)
        if missing:
            return f"ctx.session_projections 缺少内置投影: {missing}"
    except Exception as exc:  # noqa: BLE001 - 读取失败即上报
        return f"会话投影注册表读取失败: {exc}"
    return None


def _renderer_targets_readable(kernel) -> str | None:
    """渲染目标注册表必须可读，且生效项覆盖全部内置目标。"""
    if not kernel.has_service("renderer"):
        return None
    try:
        from ..renderer.targets.registry import builtin_render_target_ids

        service = kernel.resolve_service("renderer")
        names = set(service.target_ids())
        missing = sorted(set(builtin_render_target_ids()) - names)
        if missing:
            return f"ctx.renderer 缺少内置渲染目标: {missing}"
    except Exception as exc:  # noqa: BLE001 - 读取失败即上报
        return f"渲染目标注册表读取失败: {exc}"
    return None


def _escape_monitor_readable(kernel) -> str | None:
    """Escape 监看服务必须可读（活跃实例查询/停止/创建接入点齐全）。"""
    if not kernel.has_service("escape_monitor"):
        return None
    service = kernel.resolve_service("escape_monitor")
    for method in ("active", "stop", "create"):
        if not callable(getattr(service, method, None)):
            return f"escape_monitor 服务缺少方法 {method!r}"
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
    ("middleware.registry_readable", _agent_middleware_readable),
    ("subagents.types_registered", _subagents_types_registered),
    ("stream.handlers_readable", _stream_handlers_readable),
    ("providers.readable", _providers_readable),
    ("tool_engines.readable", _tool_engines_readable),
    ("ui.consumers_views_readable", _ui_consumers_views_readable),
    ("runtime_data.readable", _runtime_data_services_readable),
    ("web.providers_readable", _web_providers_readable),
    ("themes.readable", _themes_readable),
    ("skill_sources.readable", _skill_sources_readable),
    ("session_projections.readable", _session_projections_readable),
    ("renderer.targets_readable", _renderer_targets_readable),
    ("escape_monitor.readable", _escape_monitor_readable),
    ("services.declared_provides", _declared_provides_present),
    ("singletons.kernel_source", _singletons_kernel_source),
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
        # 启动自检：等内核稳定（无 PENDING/LOADING）后再跑，失败只记日志、不阻断
        # 启动（与 dsh 的 invariant fiber 一致）。若在此同步自检，部分依赖其它
        # 插件注册的服务（如清单条目注册的压缩策略/流式处理器）尚未就绪，会
        # 产生假阳性告警。
        self._schedule_startup_check()

    def _schedule_startup_check(self) -> None:
        import asyncio

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._deferred_startup_check())

    async def _deferred_startup_check(self) -> None:
        import asyncio

        for _ in range(500):
            await asyncio.sleep(0)
            try:
                fibers = list(self.ctx.kernel.fibers())
            except Exception:
                break
            if all(f.state.value not in ("PENDING", "LOADING") for f in fibers):
                break
        try:
            failures = self.check()
        except Exception:
            _logger.debug("启动不变量自检异常", exc_info=True)
            return
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
