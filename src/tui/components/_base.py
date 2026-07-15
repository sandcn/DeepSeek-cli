"""组件基类 — TuiComponent + _estimate_content_lines。

从 _components.py 拆分，包含所有组件共用的基类和辅助函数。

【inline 模式 · 2026-07-16 重构】
新增 render_to_target() 方法（IOutputTarget），支持 inline 模式直写 ANSI。
render_to_adapter() 保持向后兼容（全屏 Rich Console 模式）。
"""

# ⚠ 本文件保留独立实现，不可替换为 tui_framework.components.widget
# 原因: TuiComponent 依赖 OutputAdapter（应用渲染器层）和 rich.text.Text，
# framework 的 Widget 是交互式控件基类，接口完全不同。

from __future__ import annotations

import io
import logging
from abc import abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...renderer.output import OutputAdapter
    from ...tui_framework.terminal.output_target import IOutputTarget

from rich.text import Text
from rich.console import Console as RichConsole

_logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# Rich Text → ANSI 转换辅助（inline 模式用）
# ═══════════════════════════════════════════════════════════

def _rich_text_to_ansi(text: Text) -> str:
    """将 Rich Text 对象转换为 ANSI 字符串。

    使用临时 Rich Console 捕获输出，将 Rich 标记转换为终端 ANSI 转义序列。
    用于 inline 模式下将组件输出写入 IOutputTarget。

    Args:
        text: Rich Text 对象。

    Returns:
        含 ANSI 转义序列的纯文本字符串。
    """
    buf = io.StringIO()
    console = RichConsole(
        file=buf, force_terminal=True,
        color_system="truecolor", width=9999,
    )
    console.print(text, end="")
    return buf.getvalue()


class TuiComponent:
    """React Ink-like 渲染组件基类。

    所有子类必须实现 render() 方法，可选重写 render_to_adapter() / render_to_target()。

    ## 生命周期

    组件生命周期调用顺序：
      1. ``did_mount()`` — 组件创建后调用（由 ``Framework.create_component()`` 触发）
      2. ``should_update(new_props)`` → 渲染前调用，返回 True 触发重渲染
      3. ``render()`` — 执行渲染输出
      4. ``will_unmount()`` — 组件销毁前调用

    所有生命周期方法默认空实现，子类可按需重写。

    ## 三种渲染路径

    路径 A（默认——适用于大部分组件）：
        子类仅实现 render() → str | Text。
        基类 render_to_adapter() 自动调用 render() 获取输出，
        再将结果通过 adapter.write() 写入 OutputAdapter。

    路径 B（高级——需要直接操作 OutputAdapter）：
        子类重写 render_to_adapter()，完全绕过 render()，
        直接对 OutputAdapter 进行操作（如分段写入、ANSI 处理等）。

    路径 C（inline 模式——IOutputTarget）：
        子类可重写 render_to_target() 直接操作 IOutputTarget。
        默认实现：调用 render() → Rich Text → ANSI → target.write_line()。
        重写 render_to_target() 时仍应实现 render() 作为降级/调试用途。
    """

    def __init__(self) -> None:
        """初始化组件，标记为未挂载。"""
        self._mounted: bool = False

    def did_mount(self) -> None:
        """组件挂载后调用 — 执行初始化逻辑。

        由 ``Framework.create_component()`` 在组件创建后自动调用。
        子类可重写此方法执行初始化操作（如预计算渐变色号、注册事件等）。

        默认实现为空操作。
        """
        self._mounted = True

    def will_unmount(self) -> None:
        """组件卸载前调用 — 清理资源。

        子类可重写此方法执行清理操作（如取消事件订阅、释放资源等）。

        默认实现为空操作。
        """
        self._mounted = False

    def should_update(self, new_props: dict | None = None) -> bool:
        """渲染前调用 — 决定是否需要重渲染。

        子类可重写此方法实现局部更新优化，根据 new_props 判断是否需要
        重新渲染。默认始终返回 True（每次都重渲染），保持向后兼容。

        Args:
            new_props: 新的属性字典（可选），用于细粒度比较。

        Returns:
            True 触发重渲染，False 跳过渲染。
        """
        return True

    @abstractmethod
    def render(self) -> str | Text:
        """渲染组件内容。

        子类必须实现此方法，返回 str 或 rich.text.Text 对象。

        Returns:
            str | Text: 渲染后的文本内容，供 adapter.write() 输出。
        """

    def render_to_adapter(self, adapter: "OutputAdapter") -> int:
        """通过 OutputAdapter 渲染组件，返回估计行数。

        默认实现（路径 A）：
            调用 self.render() 获取输出，通过 adapter.write() 写入
            OutputAdapter，最后调用 _estimate_content_lines() 返回行数。

        重写场景（路径 B）：
            子类可重写此方法以绕过 render() 直接操作 adapter，
            实现分段写入、ANSI 转义处理等高级渲染逻辑。
            重写时仍建议实现 render() 作为降级/调试用途。

        Args:
            adapter: OutputAdapter 实例，用于将内容写入终端。

        Returns:
            int: 渲染内容的估计行数。
        """
        if not self.should_update():
            return 0
        output = self.render()
        if isinstance(output, (str, Text)):
            adapter.write(output)
            return _estimate_content_lines(str(output))
        return 0

    def render_to_target(self, target: "IOutputTarget") -> int:
        """通过 IOutputTarget 渲染组件（inline 模式），返回估计行数。

        默认实现（路径 C）：
            调用 self.render() 获取输出（str 或 Rich Text），
            转换为 ANSI 字符串后通过 target.write_line() 写入。

        重写场景：
            子类可重写此方法以绕过 render() 直接操作 IOutputTarget，
            实现分段写入等高级渲染逻辑。重写时仍建议实现
            render() 作为降级/调试用途。

        Args:
            target: IOutputTarget 实例，用于将 ANSI 内容写入终端。

        Returns:
            int: 渲染内容的估计行数。
        """
        if not self.should_update():
            return 0
        output = self.render()
        if not output:
            return 0
        if isinstance(output, str):
            target.write_line(output)
            return _estimate_content_lines(output)
        if isinstance(output, Text):
            ansi_str = _rich_text_to_ansi(output)
            target.write_line(ansi_str)
            return _estimate_content_lines(str(output))
        return 0


# ═══════════════════════════════════════════════════════════
# 行数估算辅助（内部使用）
# ═══════════════════════════════════════════════════════════

def _estimate_content_lines(text: str) -> int:
    """估算文本内容的终端行数。

    按文本中的换行符数量 + 1 计算行数。
    不处理终端换行（word wrapping），仅适用于粗略估计。

    Args:
        text: 要估算的文本。

    Returns:
        int: 估算的行数，至少为 1。
    """
    if not text:
        return 1
    return text.count('\n') + 1
