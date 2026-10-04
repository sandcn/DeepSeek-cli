"""内核桥接 — 适配器层从进程级内核解析运行时服务（缺失时回退默认实现）。

领域层/应用层需要「当前内核提供的实现」时，经本模块依赖倒置访问，
避免直接 import ``src.plugins`` 具体实现，也避免破坏既有默认路径。

「一切皆插件」语义：应用组合根（``src/app_init/main.py``）从 Profile 构建
内核插件树后，运行时组件（Agent / Session / Tools / Commands / Skills /
UI / Renderer / MCP ...）经本模块的内核优先访问器解析；内核缺失时（单元
测试、独立调用）回退既有默认实现，保证向后兼容。
"""

from __future__ import annotations

import inspect
from typing import Any, Callable, Optional


def _kernel():
    try:
        from ...kernel import get_current_kernel

        return get_current_kernel()
    except Exception:
        return None


# ── 工具执行管线（dsh 能力接缝：tools/pre-execute · execute · post-execute） ──


async def _call_base(base: Optional[Callable]) -> Any:
    if base is None:
        return None
    result = base()
    if inspect.isawaitable(result):
        result = await result
    return result


async def _run_waterfall(event: str, args: tuple, base: Optional[Callable] = None) -> Any:
    """在内核事件总线上运行 waterfall 链，``base`` 作为最内层 next()。

    无内核或该事件无监听器时直接执行 ``base()``（无 base 返回 None）。
    """
    kernel = _kernel()
    if kernel is None:
        return await _call_base(base)
    handlers = kernel.bus.listeners(event)
    if not handlers:
        return await _call_base(base)

    async def _dispatch(index: int) -> Any:
        if index >= len(handlers):
            return await _call_base(base)
        handler = handlers[index]

        async def _next() -> Any:
            return await _dispatch(index + 1)

        result = handler(*args, _next)
        if inspect.isawaitable(result):
            result = await result
        return result

    return await _dispatch(0)


async def tool_pre_execute(call: dict) -> Any:
    """运行 ``tools/pre-execute`` waterfall。

    监听器签名 ``(call, next)``；返回 ``{"allow": False, "reason": ...}``
    表示拒绝执行（调用方据此跳过工具），否则应 ``await next()`` 放行。
    无内核/无监听器时返回 None。
    """
    return await _run_waterfall("tools/pre-execute", (call,))


async def tool_execute(call: dict, runner: Callable) -> Any:
    """运行 ``tools/execute`` waterfall（环绕实际执行）。

    监听器签名 ``(call, next)``；``await next()`` 得到工具原始
    ``(output, success)``，可包装做超时/重试/审计。
    """
    return await _run_waterfall("tools/execute", (call,), base=runner)


async def tool_post_execute(call: dict, output: Any) -> Any:
    """运行 ``tools/post-execute`` waterfall（改写工具结果）。

    监听器签名 ``(call, output, next)``；``await next()`` 得到下游结果，
    返回新值即改写（返回 None 视为不改写）。
    """
    result = await _run_waterfall(
        "tools/post-execute", (call, output), base=lambda: output
    )
    return output if result is None else result


def has_kernel() -> bool:
    """当前进程是否已挂载内核插件树。"""
    return _kernel() is not None


def get_service(key: str, default: Any = None) -> Any:
    """按 key 解析内核服务（缺失时返回 default）。"""
    kernel = _kernel()
    if kernel is None:
        return default
    value = kernel.resolve_service(key)
    from ...kernel.context import _MISSING

    if value is _MISSING:
        return default
    return value


# ── 组合层工厂：内核服务提供的运行时组件构造器 ─────────────


def active_agent_factory() -> Optional[Callable[..., Any]]:
    """返回内核 ``ctx.agent_loop`` 的事件化 Agent 工厂（``factory(model=None)``）。

    无内核或服务缺失时返回 None，由调用方回退默认 Agent 构造。
    """
    service = get_service("agent_loop")
    if service is None:
        return None
    try:
        return lambda model=None: service.make_event_agent(model=model)
    except Exception:
        return None


def active_headless_agent_factory() -> Optional[Callable[..., Any]]:
    """返回内核 ``ctx.agent_loop`` 的无 UI Agent 工厂（NullPort，供 ChatSession 默认）。"""
    service = get_service("agent_loop")
    if service is None:
        return None
    factory = getattr(service, "create_headless_agent", None)
    if factory is None:
        return None
    return factory


def active_session_factory() -> Optional[Callable[..., Any]]:
    """返回内核 ``ctx.sessions`` 的会话工厂（返回已 initialize 的 ChatSession）。"""
    service = get_service("sessions")
    if service is None:
        return None
    return service.create


def active_chat_ui_factory() -> Optional[Callable[..., Any]]:
    """返回内核 ``ctx.ui`` 的 ChatUI 构造器（``factory() -> ChatUIConsumer``）。"""
    service = get_service("ui")
    if service is None:
        return None
    factory = getattr(service, "create_chat_ui", None)
    return factory


def active_command_registry() -> Any:
    """内核命令插件注册表；无内核时回退全局注册表。"""
    service = get_service("commands")
    if service is not None:
        return service.registry
    from ..commands.base import get_plugin_registry

    return get_plugin_registry()


def active_skill_registry() -> Any:
    """内核技能注册表；无内核时回退默认注册表。"""
    service = get_service("skills")
    if service is not None:
        return service.registry
    from ...skills.registry import default_registry

    return default_registry()


def active_policy():
    """内核策略服务；无内核时返回 None（调用方回退静态策略真源）。"""
    return get_service("policy")


def active_tool_registry():
    """内核 tools 服务的注册表；无内核时回退默认注册表。"""
    service = get_service("tools")
    if service is not None:
        return service.registry
    from .tools import get_default_tool_registry

    return get_default_tool_registry()


def active_async_model_port() -> Optional[Any]:
    """内核 llm 服务的异步模型端口；无内核时返回 None（由调用方用默认适配器）。"""
    service = get_service("llm")
    if service is not None:
        return service.model_port()
    return None


def active_config_port() -> Optional[Any]:
    service = get_service("config")
    return service.port if service is not None else None


def active_prompt_builder_port() -> Optional[Any]:
    service = get_service("prompt")
    return service.port if service is not None else None


__all__ = [
    "has_kernel",
    "get_service",
    "tool_pre_execute",
    "tool_execute",
    "tool_post_execute",
    "active_agent_factory",
    "active_headless_agent_factory",
    "active_session_factory",
    "active_chat_ui_factory",
    "active_command_registry",
    "active_skill_registry",
    "active_tool_registry",
    "active_async_model_port",
    "active_config_port",
    "active_prompt_builder_port",
    "active_policy",
]
