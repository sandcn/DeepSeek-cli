"""LazyLoader — 延迟导入辅助工具。

提供 LazyLoader 类，在首次属性访问时执行 import，
用于子包 __init__.py 的懒加载改造，降低模块加载启动时间。

用法：
    from src.tui._lazy import LazyLoader

    effects = LazyLoader("src.tui.core.effects")
    # 实际 import 在首次访问 effects.sine_breath_t 时触发
    val = effects.sine_breath_t(0, 12)
"""

from __future__ import annotations

import importlib


__all__: list[str] = ["LazyLoader"]


class LazyLoader:
    """延迟加载代理 — 在首次属性访问时才执行 import。

    Args:
        module_path: 模块的完整导入路径（如 ``"src.tui.core.effects"``）。

    用法：
        # 在 __init__.py 中替换直接导入
        effects = LazyLoader("src.tui.core.effects")
        # 后续访问 effects.XXX 时自动延迟导入
    """

    def __init__(self, module_path: str) -> None:
        self._module_path = module_path
        self._module = None

    def __getattr__(self, name: str):
        if self._module is None:
            self._module = importlib.import_module(self._module_path)
        return getattr(self._module, name)

    def __bool__(self) -> bool:
        return True

    def __repr__(self) -> str:
        return f"LazyLoader({self._module_path!r})"
