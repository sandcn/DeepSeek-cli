"""工具元数据注册表 — 每个内置工具一条独立声明（一切皆插件）。

「一切皆插件」：工具元数据（``parallel_safe`` / ``requires_network`` /
``requires_terminal`` / ``timeout_estimate`` / ``category`` / ``priority`` /
``tool_category`` / ``description``）不再是各工具类上的 ``@tool_metadata``
装饰器常量，而是注册到本模块的声明表；每个内置工具由清单中的**独立插件
条目**（``tool_metadata``，经 ``src.plugins.tool_metadata_entries``）注册，
因而可被 Profile/Bundle 声明，也可被 Patch/Overlay 按 id 单独禁用、覆盖
（改元数据）或替换。

**清单接管**：``tool_metadata`` 聚合插件收到组合根注入的
``managed_tool_metadata``（清单已接管的工具名，含被禁用的）时经
``set_managed_builtin_metadata`` 声明这些工具由清单条目负责——对应内置
声明不再走默认装配；被禁用（未挂载）的条目因此真正缺席（``get_tool_metadata``
回退类装饰器/None）。无清单（单元测试、独立调用）时无接管，全部内置声明
默认生效。

运行时（MCP / ``defineTool`` 动态工具）仍可用 ``@tool_metadata`` 装饰器；
``get_tool_metadata`` 对未登记工具名的类回退类属性。
"""

from __future__ import annotations

import threading
from typing import Callable, Dict, List, Optional

from ..declarative import DeclarativeRegistry

#: 内置工具元数据声明（工具名 → 元数据字典）。值等同各工具类原
#: ``@tool_metadata(...)`` 装饰器参数（未列字段取 ToolMetadata 默认值）。
BUILTIN_TOOL_METADATA: Dict[str, dict] = {
    "bash": {
        "parallel_safe": False, "requires_network": False, "requires_terminal": False,
        "timeout_estimate": 0, "category": "general", "priority": 30,
        "tool_category": "bash", "description": "执行shell命令",
    },
    "bash_opt": {
        "parallel_safe": False, "requires_network": False, "requires_terminal": False,
        "timeout_estimate": 0, "category": "general", "priority": 30,
        "tool_category": "bash", "description": "操作后台bash任务",
    },
    "cp": {
        "parallel_safe": False, "requires_network": False, "requires_terminal": False,
        "timeout_estimate": 0, "category": "io", "priority": 10,
        "tool_category": "write", "description": "复制文件或目录",
    },
    "find": {
        "parallel_safe": True, "requires_network": False, "requires_terminal": False,
        "timeout_estimate": 0, "category": "code", "priority": 20,
        "tool_category": "read", "description": "在项目中查找文件和目录",
    },
    "ls": {
        "parallel_safe": True, "requires_network": False, "requires_terminal": False,
        "timeout_estimate": 0, "category": "code", "priority": 20,
        "tool_category": "read", "description": "列出目录内容",
    },
    "mkdir": {
        "parallel_safe": False, "requires_network": False, "requires_terminal": False,
        "timeout_estimate": 0, "category": "io", "priority": 10,
        "tool_category": "write", "description": "创建目录",
    },
    "mv": {
        "parallel_safe": False, "requires_network": False, "requires_terminal": False,
        "timeout_estimate": 0, "category": "io", "priority": 10,
        "tool_category": "write", "description": "移动文件或目录",
    },
    "read_file": {
        "parallel_safe": True, "requires_network": False, "requires_terminal": False,
        "timeout_estimate": 0, "category": "io", "priority": 10,
        "tool_category": "read", "description": "读取文件内容",
    },
    "read_image": {
        "parallel_safe": True, "requires_network": False, "requires_terminal": False,
        "timeout_estimate": 0, "category": "io", "priority": 10,
        "tool_category": "read", "description": "读取图像内容",
    },
    "rm": {
        "parallel_safe": False, "requires_network": False, "requires_terminal": False,
        "timeout_estimate": 0, "category": "io", "priority": 10,
        "tool_category": "write", "description": "删除文件或目录",
    },
    "search": {
        "parallel_safe": True, "requires_network": False, "requires_terminal": False,
        "timeout_estimate": 0, "category": "code", "priority": 20,
        "tool_category": "read", "description": "在项目源码中搜索正则模式",
    },
    "skill": {
        "parallel_safe": True, "requires_network": False, "requires_terminal": False,
        "timeout_estimate": 0, "category": "general", "priority": 100,
        "tool_category": "read", "description": "加载技能（skill）的完整指令",
    },
    "subagent": {
        "parallel_safe": False, "requires_network": False, "requires_terminal": False,
        "timeout_estimate": 0, "category": "general", "priority": 50,
        "tool_category": "general", "description": "并行子Agent调度",
    },
    "subagent_opt": {
        "parallel_safe": False, "requires_network": False, "requires_terminal": False,
        "timeout_estimate": 0, "category": "general", "priority": 50,
        "tool_category": "general", "description": "操作后台subagent任务",
    },
    "update_file": {
        "parallel_safe": False, "requires_network": False, "requires_terminal": False,
        "timeout_estimate": 0, "category": "io", "priority": 10,
        "tool_category": "write", "description": "更新文件内容",
    },
    "user_select": {
        "parallel_safe": True, "requires_network": False, "requires_terminal": True,
        "timeout_estimate": 120, "category": "interactive", "priority": 5,
        "tool_category": "interactive", "description": "用户交互选择",
    },
    "web_fetch": {
        "parallel_safe": True, "requires_network": True, "requires_terminal": False,
        "timeout_estimate": 15, "category": "general", "priority": 41,
        "tool_category": "read", "description": "网页全文获取",
    },
    "web_search": {
        "parallel_safe": True, "requires_network": True, "requires_terminal": False,
        "timeout_estimate": 30, "category": "general", "priority": 40,
        "tool_category": "read", "description": "网页搜索",
    },
    "write_file": {
        "parallel_safe": False, "requires_network": False, "requires_terminal": False,
        "timeout_estimate": 0, "category": "io", "priority": 10,
        "tool_category": "write", "description": "写入文件",
    },
    "cordis_inspect": {
        "parallel_safe": True, "category": "code", "priority": 60,
        "tool_category": "read", "description": "自省当前插件内核（服务/插件/生命周期）",
    },
    "cordis_define": {
        "parallel_safe": False, "category": "code", "priority": 60,
        "tool_category": "write", "description": "定义（写入）一个运行时插件文件",
    },
    "cordis_run": {
        "parallel_safe": False, "category": "code", "priority": 60,
        "tool_category": "write", "description": "挂载一个运行时插件文件到当前内核",
    },
    "cordis_stop": {
        "parallel_safe": False, "category": "code", "priority": 60,
        "tool_category": "write", "description": "停止（卸载）一个运行时插件",
    },
    "cordis_undefine": {
        "parallel_safe": False, "category": "code", "priority": 60,
        "tool_category": "write", "description": "删除一个运行时插件（停止 + 删除文件）",
    },
}

_REGISTRY = DeclarativeRegistry("工具元数据")
_REGISTRY.declare(BUILTIN_TOOL_METADATA)

_gen_lock = threading.RLock()
_generation = 0


def generation() -> int:
    """注册表代际（每次变更递增，供外部缓存失效判断）。"""
    with _gen_lock:
        return _generation


def _bump() -> None:
    global _generation
    with _gen_lock:
        _generation += 1


def _wrapped(undo: Callable[[], None]) -> Callable[[], None]:
    def _undo() -> None:
        undo()
        _bump()

    return _undo


# ── 自省 ─────────────────────────────────────────────


def builtin_tool_names() -> List[str]:
    """全部内置工具名（含被接管/禁用的，按声明顺序）。"""
    return list(_REGISTRY.builtin_ids())


def default_metadata(tool_name: str) -> dict:
    return dict(_REGISTRY.default(tool_name))


def active_metadata() -> Dict[str, dict]:
    """当前生效的工具元数据（``工具名 → 元数据``）。"""
    return {name: dict(value) for name, value in _REGISTRY.active().items()}


def metadata_for(tool_name: str) -> Optional[dict]:
    """按工具名取当前生效元数据（缺席返回 None）。"""
    value = _REGISTRY.active().get(str(tool_name))
    return dict(value) if value is not None else None


def registered_metadata_names() -> List[str]:
    return list(_REGISTRY.active())


# ── 注册 / 撤销 ───────────────────────────────────────


def register_builtin_metadata(tool_name: str, value: Optional[dict] = None) -> Callable[[], None]:
    """注册/覆盖内置工具元数据（``value=None`` 用默认声明）；返回幂等撤销。"""
    undo = _REGISTRY.register_builtin(tool_name, value)
    _bump()
    return _wrapped(undo)


def unregister_builtin_metadata(tool_name: str) -> bool:
    if _REGISTRY.unregister_builtin(tool_name):
        _bump()
        return True
    return False


def register_metadata(tool_name: str, value: dict) -> Callable[[], None]:
    """注册扩展元数据（不受内置约束）；返回幂等撤销。"""
    undo = _REGISTRY.register_extension(tool_name, value)
    _bump()
    return _wrapped(undo)


def unregister_metadata(tool_name: str) -> bool:
    if _REGISTRY.unregister_extension(tool_name):
        _bump()
        return True
    return False


# ── 清单接管 / 禁用 ───────────────────────────────────


def set_managed_builtin_metadata(names) -> Callable[[], None]:
    undo = _REGISTRY.set_managed(names)
    _bump()
    return _wrapped(undo)


def managed_metadata_names() -> List[str]:
    return list(_REGISTRY.managed_ids())


def disable_builtin_metadata(names) -> Callable[[], None]:
    undo = _REGISTRY.disable_builtin(names)
    _bump()
    return _wrapped(undo)


def describe() -> List[dict]:
    return _REGISTRY.describe()


def clear() -> None:
    _REGISTRY.clear()
    _bump()


def reset() -> None:
    _REGISTRY.reset()
    _bump()


__all__ = [
    "BUILTIN_TOOL_METADATA",
    "generation",
    "builtin_tool_names",
    "default_metadata",
    "active_metadata",
    "metadata_for",
    "registered_metadata_names",
    "register_builtin_metadata",
    "unregister_builtin_metadata",
    "register_metadata",
    "unregister_metadata",
    "set_managed_builtin_metadata",
    "managed_metadata_names",
    "disable_builtin_metadata",
    "describe",
    "clear",
    "reset",
]
