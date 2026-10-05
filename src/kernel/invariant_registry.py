"""运行时不变量声明注册表 — 每条检查一个独立插件条目（一切皆插件）。

``ctx.invariants`` 服务此前把 42 条内置检查硬编码在插件内部（``_BUILTIN_CHECKS``
元组），无法被 Profile/Bundle 声明，也无法被 Patch/Overlay 按 id 单独禁用或
替换。本模块把内置检查的**声明**收敛为注册表，每一条都由清单中的
**独立插件条目**（``invariant``，经 ``src.plugins.invariant_entries``）显式
注册——因此可被 Profile/Bundle 声明，也可被 Patch/Overlay 按 id 单独禁用、
覆盖（替换检查实现）或替换。

检查实现以**点分引用**声明（``module:attr``），惰性解析——注册表是叶子模块
（仅依赖 ``src.declarative``），不引入任何业务依赖，也不产生 import 环。

**清单接管**：``invariants`` 聚合插件（``src.plugins.invariants``）收到组合根
注入的 ``managed_invariants``（清单已接管的 id，含被禁用的）时经
``set_managed_builtin_checks`` 声明这些 id 由清单条目负责——对应内置检查不再
走默认装配；被禁用（未挂载）的条目因此真正缺席。无清单（单元测试、独立调用）
时无接管，全部内置检查默认生效（向后兼容）。
"""

from __future__ import annotations

import importlib
import threading
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from ..declarative import DeclarativeRegistry

_lock = threading.RLock()


@dataclass(frozen=True)
class InvariantSpec:
    """一条内置不变量声明。"""

    id: str
    check: str
    description: str = ""

    def to_dict(self) -> dict:
        return {"id": self.id, "check": self.check, "description": self.description}


def _spec(spec_id: str, check: str, description: str = "") -> InvariantSpec:
    return InvariantSpec(id=spec_id, check=check, description=description)


#: 检查实现所在模块（点分引用前缀）
_CHECKS_MODULE = "src.plugins.invariant_checks"


def _ref(func_name: str) -> str:
    return f"{_CHECKS_MODULE}:{func_name}"


#: 内置不变量声明（id → 规格）——每项由清单中的独立插件条目注册。
_BUILTIN_SPECS: Dict[str, InvariantSpec] = {
    "services.keys_valid": _spec("services.keys_valid", _ref("service_keys_valid"),
                                 "所有服务 key 合法且服务值非 None"),
    "fibers.active_have_deps": _spec("fibers.active_have_deps", _ref("fibers_active_have_deps"),
                                     "ACTIVE Fiber 的依赖必须就绪"),
    "tools.registry_consistent": _spec("tools.registry_consistent", _ref("tools_registry_consistent"),
                                       "工具注册表名实一致且为默认同源注册表"),
    "tool_metadata.readable": _spec("tool_metadata.readable", _ref("tool_metadata_readable"),
                                    "工具元数据注册表覆盖全部内置工具"),
    "tool_consts.readable": _spec("tool_consts.readable", _ref("tool_consts_readable"),
                                  "工具常量注册表覆盖全部内置常量"),
    "event_types.readable": _spec("event_types.readable", _ref("event_types_readable"),
                                  "事件类型注册表覆盖全部内置事件类型"),
    "named_styles.readable": _spec("named_styles.readable", _ref("named_styles_readable"),
                                   "命名样式注册表覆盖全部内置命名样式"),
    "agent_loop.dependencies": _spec("agent_loop.dependencies", _ref("agent_loop_dependencies"),
                                     "agent_loop 已加载时其依赖服务必须齐备"),
    "presets.has_standard": _spec("presets.has_standard", _ref("presets_have_standard"),
                                  "presets 服务必须有内置 standard 预设"),
    "agents.messages_recorded": _spec("agents.messages_recorded", _ref("agents_messages_recorded"),
                                      "「模型可见即已记录」：Agent 消息与会话日志投影一致"),
    "services.providers_present": _spec("services.providers_present", _ref("service_providers_present"),
                                        "可替换 provider 的服务不得为空壳"),
    "llm.providers_available": _spec("llm.providers_available", _ref("llm_providers_available"),
                                      "llm 服务必须有 provider 且覆盖未禁用的内置 provider"),
    "renderer.extensions_readable": _spec("renderer.extensions_readable", _ref("renderer_extensions_readable"),
                                          "renderer handler/filter 注册表可读"),
    "middleware.registry_readable": _spec("middleware.registry_readable", _ref("agent_middleware_readable"),
                                          "Agent 中间件注册表可读"),
    "subagents.types_registered": _spec("subagents.types_registered", _ref("subagents_types_registered"),
                                        "ctx.subagents 注册全部内置 Agent 类型"),
    "stream.handlers_readable": _spec("stream.handlers_readable", _ref("stream_handlers_readable"),
                                      "流式处理器注册表可读且角色齐备"),
    "providers.readable": _spec("providers.readable", _ref("providers_readable"),
                                "通知后端 / 压缩策略 / MCP 传输注册表可读"),
    "tool_engines.readable": _spec("tool_engines.readable", _ref("tool_engines_readable"),
                                   "工具执行引擎注册表可读且默认引擎可解析"),
    "ui.consumers_views_readable": _spec("ui.consumers_views_readable", _ref("ui_consumers_views_readable"),
                                         "事件消费者 / UI 视图注册表可读且覆盖内置项"),
    "runtime_data.readable": _spec("runtime_data.readable", _ref("runtime_data_services_readable"),
                                   "运行时数据服务接入点齐全"),
    "web.providers_readable": _spec("web.providers_readable", _ref("web_providers_readable"),
                                    "Web 搜索/抓取提供者注册表覆盖内置项"),
    "themes.readable": _spec("themes.readable", _ref("themes_readable"),
                             "主题注册表覆盖全部内置主题"),
    "skill_sources.readable": _spec("skill_sources.readable", _ref("skill_sources_readable"),
                                    "技能来源注册表覆盖全部内置来源"),
    "session_projections.readable": _spec("session_projections.readable", _ref("session_projections_readable"),
                                          "会话投影注册表覆盖全部内置投影"),
    "renderer.targets_readable": _spec("renderer.targets_readable", _ref("renderer_targets_readable"),
                                       "渲染目标注册表覆盖全部内置目标"),
    "escape_monitor.readable": _spec("escape_monitor.readable", _ref("escape_monitor_readable"),
                                     "Escape 监看服务接入点齐全"),
    "tool_policy.globally_disabled_readable": _spec("tool_policy.globally_disabled_readable",
                                                    _ref("global_disabled_tools_readable"),
                                                    "全局禁用工具注册表覆盖全部内置项"),
    "presets.readable": _spec("presets.readable", _ref("presets_readable"),
                              "Preset 注册表覆盖全部内置 preset"),
    "prompt.registry_readable": _spec("prompt.registry_readable", _ref("prompt_registry_readable"),
                                      "提示词注册表覆盖全部内置运行模式/来源/片段"),
    "clawbot.commands_readable": _spec("clawbot.commands_readable", _ref("clawbot_commands_readable"),
                                       "ClawBot 命令注册表覆盖全部内置指令"),
    "subcommands.readable": _spec("subcommands.readable", _ref("subcommands_readable"),
                                  "CLI 子命令注册表覆盖全部内置子命令"),
    "keybindings.readable": _spec("keybindings.readable", _ref("keybindings_readable"),
                                  "键位绑定注册表覆盖全部内置绑定"),
    "special_keys.readable": _spec("special_keys.readable", _ref("special_keys_readable"),
                                   "特殊键处理器注册表覆盖全部内置处理器"),
    "tool_styles.readable": _spec("tool_styles.readable", _ref("tool_styles_readable"),
                                  "工具表现注册表覆盖全部内置表现条目"),
    "syntax.readable": _spec("syntax.readable", _ref("syntax_readable"),
                             "语法高亮语言注册表覆盖全部内置语言"),
    "presentation_data.readable": _spec("presentation_data.readable", _ref("presentation_data_readable"),
                                        "表现层数据注册表覆盖全部内置数据表"),
    "hosts.readable": _spec("hosts.readable", _ref("hosts_readable"),
                            "host 组件注册表覆盖全部内置 host"),
    "completion_providers.readable": _spec("completion_providers.readable", _ref("completion_providers_readable"),
                                           "补全提供者注册表覆盖全部内置提供者"),
    "status_segments.readable": _spec("status_segments.readable", _ref("status_segments_readable"),
                                      "状态栏段注册表覆盖全部内置段"),
    "kernel_admin.readable": _spec("kernel_admin.readable", _ref("kernel_admin_readable"),
                                   "内核管理服务接入点齐全"),
    "services.declared_provides": _spec("services.declared_provides", _ref("declared_provides_present"),
                                        "活跃 Fiber 声明的 provide 服务全部在册"),
    "singletons.kernel_source": _spec("singletons.kernel_source", _ref("singletons_kernel_source"),
                                      "进程级单例访问函数解析到内核服务独占实例"),
}

_REGISTRY = DeclarativeRegistry("不变量")
_REGISTRY.declare(_BUILTIN_SPECS)

#: 扩展不变量（id → 检查函数；不受内置约束）
_extension: Dict[str, Callable] = {}


def builtin_invariant_ids() -> List[str]:
    """全部内置不变量 id（含被接管/禁用的，按声明顺序）。"""
    return _REGISTRY.builtin_ids()


def default_invariant_spec(spec_id: str) -> InvariantSpec:
    return _REGISTRY.default(spec_id)


def resolve_check(spec: InvariantSpec) -> Callable:
    """解析规格中的点分引用为检查函数（``module:attr`` / ``module.attr``）。"""
    ref = spec.check
    if ":" in ref:
        module_name, _, attr = ref.partition(":")
    else:
        module_name, _, attr = ref.rpartition(".")
    if not module_name or not attr:
        raise ValueError(f"不变量检查引用非法: {ref!r}")
    module = importlib.import_module(module_name)
    obj: Any = module
    for part in attr.split("."):
        obj = getattr(obj, part)
    if not callable(obj):
        raise TypeError(f"不变量检查不可调用: {ref!r}")
    return obj


def active_invariant_specs() -> Dict[str, InvariantSpec]:
    """当前生效的内置不变量规格（``id → 规格``；按声明顺序）。

    仅含以规格声明（未用 callable 显式覆盖）的项。
    """
    return {
        spec_id: value
        for spec_id, value in _REGISTRY.active().items()
        if isinstance(value, InvariantSpec)
    }


def _resolve_active(value: Any) -> Callable:
    if isinstance(value, InvariantSpec):
        return resolve_check(value)
    if callable(value):
        return value
    raise TypeError(f"不变量注册值非法: {value!r}")


def active_invariant_checks() -> Dict[str, Callable]:
    """当前生效的内置不变量检查（``id → 检查函数``）。"""
    with _lock:
        return {spec_id: _resolve_active(value) for spec_id, value in _REGISTRY.active().items()}


def register_builtin_check(spec_id: str, check: Optional[Callable] = None) -> Callable[[], None]:
    """注册/覆盖一条内置检查（``check=None`` 用默认声明解析）；返回幂等撤销。"""
    if check is None:
        return _REGISTRY.register_builtin(spec_id, None)
    if not callable(check):
        raise TypeError(f"不变量检查必须可调用: {check!r}")
    return _REGISTRY.register_builtin(spec_id, check)


def unregister_builtin_check(spec_id: str) -> bool:
    return _REGISTRY.unregister_builtin(spec_id)


def set_managed_builtin_checks(ids) -> Callable[[], None]:
    """声明这些内置 id 由清单条目负责（默认装配被抑制）；返回撤销。"""
    return _REGISTRY.set_managed(ids)


def managed_check_ids() -> List[str]:
    return _REGISTRY.managed_ids()


def disable_builtin_checks(ids) -> Callable[[], None]:
    """禁用一个或多个内置检查（返回幂等撤销）。"""
    return _REGISTRY.disable_builtin(ids)


def disabled_check_ids() -> List[str]:
    return _REGISTRY.disabled_ids()


def register_check(spec_id: str, check: Callable) -> Callable[[], None]:
    """注册一条扩展检查（返回幂等撤销）；扩展项不受内置约束。"""
    if not isinstance(spec_id, str) or not spec_id:
        raise ValueError(f"不变量 id 必须是非空字符串: {spec_id!r}")
    if not callable(check):
        raise TypeError(f"不变量检查必须可调用: {check!r}")
    with _lock:
        previous = _extension.get(spec_id)
        _extension[spec_id] = check

    def _undo() -> None:
        with _lock:
            if previous is None:
                _extension.pop(spec_id, None)
            else:
                _extension[spec_id] = previous

    return _undo


def unregister_check(spec_id: str) -> bool:
    with _lock:
        return _extension.pop(spec_id, None) is not None


def extension_checks() -> Dict[str, Callable]:
    with _lock:
        return dict(_extension)


def active_checks() -> Dict[str, Callable]:
    """当前生效的检查（内置装配 + 扩展）。"""
    checks = active_invariant_checks()
    checks.update(extension_checks())
    return checks


def describe() -> List[dict]:
    return _REGISTRY.describe()


def clear() -> None:
    """清空扩展项与清单注册（测试用；不影响内置默认与禁用状态）。"""
    with _lock:
        _extension.clear()
    _REGISTRY.clear()


def reset() -> None:
    """重置全部状态到「无清单、无禁用、全部默认」（测试隔离用）。"""
    with _lock:
        _extension.clear()
    _REGISTRY.reset()


__all__ = [
    "InvariantSpec",
    "builtin_invariant_ids",
    "default_invariant_spec",
    "resolve_check",
    "active_invariant_specs",
    "active_invariant_checks",
    "register_builtin_check",
    "unregister_builtin_check",
    "set_managed_builtin_checks",
    "managed_check_ids",
    "disable_builtin_checks",
    "disabled_check_ids",
    "register_check",
    "unregister_check",
    "extension_checks",
    "active_checks",
    "describe",
    "clear",
    "reset",
]
