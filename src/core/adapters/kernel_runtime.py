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


# ── 事件切面：emit / waterfall / serial / bail ──────────────


async def emit_event(event: str, *args: Any) -> None:
    """经内核事件总线 emit（无内核时 no-op）。"""
    kernel = _kernel()
    if kernel is None:
        return
    await kernel.bus.emit(event, *args)


def notify_event(event: str, *args: Any) -> None:
    """经内核事件总线同步 emit（无内核时 no-op）。"""
    kernel = _kernel()
    if kernel is None:
        return
    kernel.bus.emit_sync(event, *args)


async def run_waterfall(event: str, *args: Any, base: Optional[Callable] = None) -> Any:
    """运行 waterfall 事件链（无内核/无监听器时执行 base）。"""
    return await _run_waterfall(event, args, base=base)


async def run_serial(event: str, *args: Any) -> Any:
    """运行 serial 事件链（无内核时返回 None）。"""
    kernel = _kernel()
    if kernel is None:
        return None
    return await kernel.bus.serial(event, *args)


async def run_bail(event: str, *args: Any) -> Any:
    """运行 bail 事件链（无内核时返回 None）。"""
    kernel = _kernel()
    if kernel is None:
        return None
    return await kernel.bus.bail(event, *args)


def event_listeners(event: str) -> list:
    """返回某事件的监听器列表（无内核时为空）。"""
    kernel = _kernel()
    if kernel is None:
        return []
    return kernel.bus.listeners(event)


# ── 能力接缝辅助（Consumer 经此使用能力，Provider 可替换） ──


async def spawn_process(command, *, shell: bool = True, **kwargs):
    """经 shell 能力接缝创建原始进程（无内核时回退 asyncio 本地创建）。

    Consumer（bash 等）借此启动进程；把 shell Provider 指向远程沙箱，进程
    便在沙箱里启动。
    """
    import asyncio

    service = get_service("shell")
    if service is not None and hasattr(service, "create_process"):
        try:
            return await service.create_process(command, shell=shell, **kwargs)
        except Exception:
            import logging

            logging.getLogger(__name__).debug("经 shell 接缝创建进程失败，回退本地", exc_info=True)
    if shell:
        return await asyncio.create_subprocess_shell(command, **kwargs)
    return await asyncio.create_subprocess_exec(*command, **kwargs)


def fs_read_text(path, *, encoding: str = "utf-8", errors: str = "replace"):
    """经 fs 接缝读取文本（无内核时回退本地实现）。"""
    service = get_service("fs")
    if service is not None:
        return service.read_text(path, encoding=encoding, errors=errors)
    from ...tools.file_ops import _sync_read_local

    content = _sync_read_local(path, encoding, errors)
    if content is None:
        raise FileNotFoundError(path)
    return content


def fs_write_text(path, content, *, encoding: str = "utf-8"):
    """经 fs 接缝写入文本（无内核时回退本地实现）。"""
    service = get_service("fs")
    if service is not None:
        return service.write_text(path, content, encoding=encoding)
    from ...tools.file_ops import _atomic_write_local

    _atomic_write_local(path, content, encoding)


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


def active_agents_service():
    """内核 Agent 注册表服务；无内核时返回 None。"""
    return get_service("agents")


def register_agent(agent, **kwargs):
    """把 Agent 登记到内核活跃注册表；无内核/无注册表时返回 None。"""
    service = get_service("agents")
    if service is None:
        return None
    try:
        return service.register(agent, **kwargs)
    except Exception:
        import logging

        logging.getLogger(__name__).debug("登记 agent 失败", exc_info=True)
        return None


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
    "emit_event",
    "notify_event",
    "run_waterfall",
    "run_serial",
    "run_bail",
    "event_listeners",
    "spawn_process",
    "fs_read_text",
    "fs_write_text",
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
    "active_agents_service",
    "register_agent",
]
