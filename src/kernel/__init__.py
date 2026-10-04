"""内核包 — 无特权内核（一切皆插件）。

对外导出：Kernel / Context / Service / Plugin / Fiber 状态机、
ConfigTree（Profile/Bundle/Patch）、插件加载与 dump 工具。
"""

from __future__ import annotations

from .config_tree import (
    Bundle,
    ConfigTree,
    Patch,
    PluginSpec,
    Profile,
    ResolvedPlugin,
    materialize,
)
from .context import Context
from .dump import format_config_dump, format_runtime_snapshot, summarize
from .errors import (
    DependencyError,
    FiberStateError,
    KernelError,
    PluginError,
    ServiceExists,
    ServiceNotFound,
)
from .events import DispatchMode, EventBus
from .fiber import Fiber, FiberState
from .kernel import (
    Kernel,
    current_context,
    get_current_kernel,
    set_current_kernel,
)
from .plugin import Plugin, as_plugin, plugin
from .scope import Scope, ScopeRegistry
from .service import Service

__all__ = [
    "Kernel",
    "Context",
    "Service",
    "Plugin",
    "plugin",
    "as_plugin",
    "Scope",
    "ScopeRegistry",
    "Fiber",
    "FiberState",
    "EventBus",
    "DispatchMode",
    "ConfigTree",
    "Bundle",
    "Profile",
    "Patch",
    "PluginSpec",
    "ResolvedPlugin",
    "materialize",
    "format_config_dump",
    "format_runtime_snapshot",
    "summarize",
    "KernelError",
    "ServiceNotFound",
    "ServiceExists",
    "DependencyError",
    "PluginError",
    "FiberStateError",
    "set_current_kernel",
    "get_current_kernel",
    "current_context",
]
