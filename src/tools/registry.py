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
from ._constants import TOOL_DISPLAY_NAME

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

    def get_schemas(self) -> List[Dict[str, Any]]:
        """
        获取当前实例中所有工具的函数调用模式

        Returns:
            工具模式列表，适用于OpenAI函数调用
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

        package = sys.modules[__name__].__package__
        if not package:
            logger.warning("__package__ 为空，从 __name__ 推断包名: %s", __name__.rsplit('.', 1)[0])
            package = __name__.rsplit('.', 1)[0]

        try:
            tools_package = importlib.import_module(package)
            package_path = list(tools_package.__path__)
        except (ImportError, AttributeError) as e:
            logger.error(f"无法导入工具包: {e}")
            return

        for _, module_name, is_pkg in pkgutil.iter_modules(package_path):
            if is_pkg:
                continue

            full_module_name = f"{package}.{module_name}"

            try:
                module = importlib.import_module(full_module_name)
                logger.debug(f"导入模块: {full_module_name}")

                for name, obj in vars(module).items():
                    if (inspect.isclass(obj) and
                        issubclass(obj, Func) and
                        obj != Func and
                        obj.__module__ == module.__name__ and
                        obj.name is not None):

                        self.register(obj)
                        logger.info(f"发现并注册工具: {name} ({full_module_name})")

            except Exception as e:
                logger.error(f"导入模块失败 {full_module_name}: {e}")

        logger.info(f"工具发现完成，共注册 {len(self._tools)} 个工具")
        elapsed = time.perf_counter() - _start
        logger.debug("工具发现耗时: %.2fms", elapsed * 1000)

    # ── 类方法 ──────────────────────────────────────────────

    @classmethod
    def default(cls) -> 'ToolRegistry':
        """返回模块级默认 ToolRegistry 实例（单例模式）"""
        global _default_registry
        if _default_registry is None:
            _default_registry = cls()
        return _default_registry


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


# ── 工具显示名映射（UI显示用，映射表见 _constants.TOOL_DISPLAY_NAME） ──

def get_tool_display_name(tool_name: str) -> str:
    """获取工具在UI上显示的完整名称（对齐 Claude Code），无映射则返回原名称。"""
    return TOOL_DISPLAY_NAME.get(tool_name, tool_name)
