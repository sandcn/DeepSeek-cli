"""
工具注册表
自动发现并注册所有工具类
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
import sys
import time
import logging
from typing import Dict, Type, Any, List, Optional

from .base import Func, ToolMetadata, get_tool_metadata
from .tool_policy import is_globally_disabled
from ..core.tool_display import TOOL_DISPLAY_NAME, get_tool_display_name  # noqa: F401  （re-export）

logger = logging.getLogger(__name__)

# ------------------------------------------------------------
#  全局单例引用（懒初始化）
# ------------------------------------------------------------
_default_registry: Optional['ToolRegistry'] = None


# ============================================================
#   ToolRegistry 类 — 实例化注册表
# ============================================================

class ToolRegistry:
    """工具注册表 — 实例化管理，不再共享模块级全局状态。"""

    def __init__(self, initial_tools: Optional[Dict[str, Type[Func]]] = None):
        """
        Args:
            initial_tools: 初始工具映射字典。若为 None，创建空注册表。
        """
        self._tools: Dict[str, Type[Func]] = initial_tools if initial_tools is not None else {}
        self._initialized = initial_tools is not None
        self._schema_cache: list[dict] | None = None

    # ── 实例方法 ────────────────────────────────────────────

    def register(self, tool_class: Type[Func]) -> None:
        """
        注册一个工具类到当前实例

        Args:
            tool_class: 工具类（必须是Func的子类）
        """
        if not inspect.isclass(tool_class) or not issubclass(tool_class, Func):
            raise ValueError(f"只能注册Func的子类，但收到了: {tool_class}")

        tool_name = tool_class.name
        if tool_name is None:
            raise ValueError(f"工具类 {tool_class.__name__} 未定义 name 属性")

        if tool_name in self._tools:
            logger.warning(f"工具 '{tool_name}' 已存在，将被覆盖")

        self._tools[tool_name] = tool_class
        self._schema_cache = None  # 使 schema 缓存失效
        logger.debug(f"注册工具: {tool_name}")

    def unregister(self, tool_name: str) -> bool:
        """从当前实例注销一个工具（MCP 动态工具关闭时清理）。

        Args:
            tool_name: 工具名称

        Returns:
            True 表示确实移除了该工具，False 表示原本不存在。
        """
        if tool_name not in self._tools:
            return False
        del self._tools[tool_name]
        self._schema_cache = None  # 使 schema 缓存失效
        logger.debug(f"注销工具: {tool_name}")
        return True

    def get_tools(self) -> Dict[str, Type[Func]]:
        """
        获取当前实例中所有已注册的工具

        Returns:
            工具名称到工具类的映射（副本）
        """
        self._ensure_initialized()
        return self._tools.copy()

    def get_metadata(self, tool_name: str) -> Optional[ToolMetadata]:
        """获取指定工具的元数据

        Args:
            tool_name: 工具名称

        Returns:
            工具的 ToolMetadata，工具未注册或未设置元数据时返回 None
        """
        self._ensure_initialized()
        tool_class = self._tools.get(tool_name)
        if tool_class is None:
            return None
        return get_tool_metadata(tool_class)

    def get_schemas(self) -> tuple:
        """
        获取当前实例中所有工具的函数调用模式

        Returns:
            工具模式元组（不可变；调用方如需列表请自行 list(...)），
            适用于 OpenAI 函数调用
        """
        self._ensure_initialized()

        if self._schema_cache is not None:
            return tuple(self._schema_cache)

        schemas = []
        for tool_name, tool_class in self._tools.items():
            try:
                schema = tool_class.to_tool_schema()
                schemas.append(schema)
            except Exception as e:
                logger.error(f"获取工具 {tool_name} 的模式失败: {e}")
                continue

        self._schema_cache = schemas
        return tuple(schemas)

    def dispatch(self, tool_name: str, arguments: dict, agent=None):
        """
        根据工具名称和参数调用工具

        Args:
            tool_name: 工具名称
            arguments: 参数字典
            agent: 可选的Agent实例

        Returns:
            工具实例（已执行 from_args 和 set_agent）
        """
        self._ensure_initialized()

        tool_class = self._tools.get(tool_name)
        if not tool_class:
            available = list(self._tools.keys())
            raise ValueError(f"工具未找到: {tool_name}，可用工具: {', '.join(available)}")

        tool_instance = tool_class.from_args(arguments)

        if agent is not None:
            tool_instance.set_agent(agent)

        return tool_instance

    def build_system_prompt(self) -> List[str]:
        """构建系统提示词，返回各部分字符串列表"""
        from ..prompt_builder.builder import build_system_prompt
        return build_system_prompt()

    def clear(self) -> None:
        """清空当前实例的工具注册表"""
        self._tools.clear()
        self._initialized = False
        self._schema_cache = None
        logger.info("工具注册表已清空")

    def _ensure_initialized(self) -> None:
        """确保本实例的工具已初始化（自动发现）"""
        if not self._initialized:
            self._discover_and_register()
            self._initialized = True

    def _discover_and_register(self) -> None:
        """自动发现并注册所有工具到当前实例"""
        logger.info("开始自动发现工具...")
        _start = time.perf_counter()
        for tool_class in discover_builtin_tools().values():
            self.register(tool_class)
        logger.info(f"工具发现完成，共注册 {len(self._tools)} 个工具")
        elapsed = time.perf_counter() - _start
        logger.debug("工具发现耗时: %.2fms", elapsed * 1000)

    def mark_initialized(self) -> None:
        """标记注册表已初始化（显式注册路径使用，避免懒发现重复注册）。"""
        self._initialized = True

    def is_initialized(self) -> bool:
        return self._initialized

    # ── 类方法 ──────────────────────────────────────────────

    @classmethod
    def default(cls) -> 'ToolRegistry':
        """返回默认 ToolRegistry 实例。

        内核优先：内核挂载 ``ctx.tools`` 服务后返回其注册表（与工具服务、
        工具调度器同源）；内核缺失或服务尚在构造中时回退进程级单例。
        """
        try:
            from ..kernel.runtime import active_service

            service = active_service("tools")
            registry = getattr(service, "registry", None) if service is not None else None
            if registry is not None:
                return registry
        except Exception:
            pass
        global _default_registry
        if _default_registry is None:
            _default_registry = cls()
        return _default_registry


# ============================================================
#   内置工具发现（无副作用 — 返回 name → 工具类映射）
# ============================================================

def discover_builtin_tools() -> Dict[str, Type[Func]]:
    """扫描 ``src.tools`` 包，返回内置工具类映射（name → class）。

    纯函数：只导入模块并收集 ``Func`` 子类，不修改任何注册表。
    供 ``ToolRegistry._discover_and_register`` 与内核 ``tools_builtin``
    插件（显式以插件方式注册内置工具）共用。

    ★ 全局禁用工具（``tool_policy.GLOBAL_DISABLED_TOOLS``，如 cordis 工具族）
    在此即被跳过——任何 agent（主 Agent 与全部 SubAgent 类型）的工具集中都不会
    出现它们，从加载层保证「任何 agent 都不能加载」。
    """
    package = sys.modules[__name__].__package__
    if not package:
        package = __name__.rsplit('.', 1)[0]

    found: Dict[str, Type[Func]] = {}
    try:
        tools_package = importlib.import_module(package)
        package_path = list(tools_package.__path__)
    except (ImportError, AttributeError) as e:
        logger.error(f"无法导入工具包: {e}")
        return found

    for _, module_name, is_pkg in pkgutil.iter_modules(package_path):
        if is_pkg:
            continue
        full_module_name = f"{package}.{module_name}"
        try:
            module = importlib.import_module(full_module_name)
        except Exception as e:
            logger.error(f"导入模块失败 {full_module_name}: {e}")
            continue
        for name, obj in vars(module).items():
            if (inspect.isclass(obj) and
                    issubclass(obj, Func) and
                    obj != Func and
                    obj.__module__ == module.__name__ and
                    obj.name is not None and
                    not is_globally_disabled(obj.name)):
                found.setdefault(obj.name, obj)
    return found


# ============================================================
#   全局便捷函数（内部使用 _default_registry）
# ============================================================

def register_tool(tool_class: Type[Func]) -> None:
    """
    注册一个工具类到默认注册表

    Args:
        tool_class: 工具类（必须是Func的子类）
    """
    ToolRegistry.default().register(tool_class)


def get_tools() -> Dict[str, Type[Func]]:
    """
    获取默认注册表中所有已注册的工具

    Returns:
        工具名称到工具类的映射（副本）
    """
    return ToolRegistry.default().get_tools()


def get_tool_schemas() -> List[Dict[str, Any]]:
    """
    获取默认注册表中所有工具的函数调用模式

    Returns:
        工具模式列表，适用于OpenAI函数调用
    """
    return ToolRegistry.default().get_schemas()


def clear_registry() -> None:
    """清空默认注册表（主要用于测试）"""
    global _default_registry
    _default_registry = None
    logger.info("工具注册表已清空")


# ── 工具显示名映射（实现已下沉 core.tool_display，此处 re-export） ──
