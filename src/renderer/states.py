"""states — RenderEngine 状态 dataclass 管理模块。

将原本内联在 engine.py 中的状态类独立出来，便于维护和扩展。

包含：
  - _CodeBlockState  — 代码块渲染状态
  - _DetailsState    — Details 折叠块状态
  - _TodoState       — 任务列表进度状态
"""

from __future__ import annotations

from dataclasses import field
from src._compat import dataclass


@dataclass(slots=True)
class _CodeBlockState:
    """代码块渲染状态。"""
    lang: str = ""
    line_num: int = 0
    indented: bool = False
    highlight_lines: list[int] = field(default_factory=list)
    linenos: bool = False
    #: 行号起始值与步长（``linenostart=3`` / ``linenostep=2``）
    lineno_start: int = 1
    lineno_step: int = 1

    def display_line_number(self) -> int:
        """当前行的**显示行号**（起始值 + (n-1)*步长）。"""
        return self.lineno_start + max(0, self.line_num - 1) * self.lineno_step


@dataclass(slots=True)
class _DetailsState:
    """Details 折叠块渲染状态。"""
    depth: int = 0


@dataclass(slots=True)
class _TodoState:
    """任务列表进度统计状态。"""
    total: int = 0
    done: int = 0
    active: bool = False

