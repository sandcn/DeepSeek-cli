"""工具常量条目插件 — 清单中每个内置常量一条独立条目。

「一切皆插件」：工具运行期常量（排除目录/文件模式、路径安全集合、编码检测
常量、文件大小阈值、图片扩展名、网页移除标签等）不再硬编码在
``src/tools/_constants`` 等模块的字面量里，而是由清单中的独立条目声明::

    - id: tool_const_executed_dirs
      plugin: src.plugins.tool_const_entries:apply_tool_const
      config:
        name: EXCLUDED_DIRS                  # 内置常量名（可被 patch/overlay 定位）
        # value: ["node_modules", ...]        # 可选：整值覆盖

插件挂载时把该名称的内置常量注册进注册表（``value=None`` 用默认声明）；卸载时
撤销（可逆副作用）。条目被 Patch/Overlay 禁用即不挂载，对应内置常量随之缺席
（``tool_consts`` 聚合插件经 ``managed_tool_consts`` 抑制默认装配）。
"""

from __future__ import annotations

import importlib

from ..kernel import plugin

#: 由工具模块（非 ``_constants``）声明到注册表的内置常量 → 声明模块。
#: 条目独立挂载顺序不确定，注册前按需导入声明模块，保证常量已登记。
_CONST_DECLARERS = {
    "IMAGE_EXT_FORMAT": "src.tools.read_image",
    "IMAGE_FORMAT_MEDIA": "src.tools.read_image",
    "IMAGE_EXTENSIONS": "src.tools.read_image",
    "REMOVE_TAGS": "src.tools.page_fetcher",
    "REMOVE_CLASS_KEYWORDS": "src.tools.page_fetcher",
    "PRIVATE_PREFIXES": "src.tools.page_fetcher",
    "DATE_META_PATTERNS": "src.tools.page_fetcher",
}


def _ensure_declared(name: str) -> None:
    module_name = _CONST_DECLARERS.get(name)
    if module_name:
        try:
            importlib.import_module(module_name)
        except Exception:  # noqa: BLE001 - 声明模块导入失败由下方未知常量报错
            pass


@plugin("tool_const_entry")
def apply_tool_const(ctx):
    from ..tools import _constants  # noqa: F401 - 确保基础常量已声明
    from ..tools.const_registry import (
        builtin_constant_names,
        register_builtin_constant,
    )

    name = ctx.config.get("name")
    if not name:
        raise ValueError("tool_const 条目缺少 config.name")
    if name not in builtin_constant_names():
        _ensure_declared(name)
    if name not in builtin_constant_names():
        raise ValueError(f"未知内置工具常量: {name!r}")
    value = ctx.config.get("value")
    undo = register_builtin_constant(name, value)
    ctx.effect(lambda: undo)


__all__ = ["apply_tool_const"]
