# core 包 - 核心逻辑模块
"""core 包：核心逻辑模块。

★ 启动性能（懒导出）：本包 ``__init__`` 不再在导入期连锁加载
``agent_builder``（→ ``agent`` → ``tool_executor`` → ``src.tools`` 全量工具
模块，含 httpx/Pygments 等重型依赖）。任何 ``import src.core.<submodule>``
（如 ``src.core.tokens`` / ``src.core.interrupt_state``）都会先执行本文件——
若在导入期 eager 导入 ``AgentBuilder``，则「只为拿一个轻量子模块」的调用方
被迫加载整条 agent/tools 链（实测 ~500ms）。

改用 PEP 562 模块级 ``__getattr__`` 惰性解析：``from src.core import
AgentBuilder`` 首次访问时才导入 ``agent_builder``，其余子模块导入零连带成本。
"""

from __future__ import annotations

from importlib import import_module

#: 惰性导出表：公开名 → (子模块相对名, 子模块内属性名)
_LAZY_EXPORTS = {
    "AgentBuilder": (".agent_builder", "AgentBuilder"),
}

__all__ = ["AgentBuilder"]


def __getattr__(name: str):
    """PEP 562 模块级惰性属性解析（首次访问后写入模块命名空间缓存）。"""
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(target[0], __name__)
    value = getattr(module, target[1])
    globals()[name] = value
    return value


def __dir__() -> list:
    return sorted(set(globals()) | set(_LAZY_EXPORTS))
