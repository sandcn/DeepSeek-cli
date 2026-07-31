"""_ComponentsNamespace — 向后兼容的组件命名空间（独立模块，方向B 步骤3）。

从 _consumer.py 迁出为独立模块，消除 ``_consumer ↔ _assembly`` 循环依赖：
  - _consumer.py 模块级 import _assembly（TuiAssembly/TuiAssemblyResult）
  - _assembly.py 函数内 import _consumer._ComponentsNamespace → 构成环

迁移后：
  - _consumer.py 从本模块 re-export（``from ._components import _ComponentsNamespace``），
    保持旧导入路径兼容（``from src.tui._consumer import _ComponentsNamespace`` 仍可用）
  - _assembly.py 也从本模块导入，不再触碰 _consumer → 无环

仅依赖 typing（Layer 0），不依赖任何 TUI 运行时模块。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._input import Input


# ═══════════════════════════════════════════════════════════
# 向后兼容的组件命名空间
# ═══════════════════════════════════════════════════════════

class _ComponentsNamespace:
    """向后兼容的组件命名空间。

    Attributes:
        input: 统一输入管理实例。
    """
    __slots__ = ('input',)

    def __init__(self, input_instance: "Input | None" = None):
        self.input = input_instance


__all__ = ["_ComponentsNamespace"]
