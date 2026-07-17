"""组件基类 — TuiComponent + Widget 统一体系。

TuiComponent 继承自 Widget（src.tui.widget_base 中的统一控件基类），
保持现有组件的完全向后兼容性。

从 _components.py 拆分，包含所有组件共用的基类和辅助函数。
"""

from __future__ import annotations

import logging
from abc import abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ...renderer.output import OutputAdapter

from ..render_buffer import RenderBuffer

from rich.text import Text

from ..widget_base import Widget

_logger = logging.getLogger(__name__)


class TuiComponent(Widget):
    """React Ink-like 渲染组件基类。

    继承自 ``Widget``（统一控件基类），
    保持现有 ``render()`` 和 ``render_to_adapter()`` 接口完全兼容。

    ## 生命周期

    组件生命周期调用顺序：
      1. ``did_mount()`` — 组件创建后调用（由 ``Framework.create_component()`` 触发）
      2. ``should_update(new_props)`` → 渲染前调用，返回 True 触发重渲染
      3. ``render()`` — 执行渲染输出
      4. ``will_unmount()`` — 组件销毁前调用

    所有生命周期方法默认空实现，子类可按需重写。

    ## 两种渲染路径

    路径 A（默认——适用于大部分组件）：
        子类仅实现 render() → str | Text。
        基类 render_to_adapter() 自动调用 render() 获取输出，
        再将结果通过 adapter.write() 写入 OutputAdapter。

        适用场景：UserMsgBlock、ErrorBlock、NotificationBlock 等
        输出格式固定、无需直接操作 adapter 的组件。

    路径 B（高级——需要直接操作 OutputAdapter）：
        子类重写 render_to_adapter()，完全绕过 render()，
        直接对 OutputAdapter 进行操作（如分段写入、ANSI 处理等）。

        适用场景：ToolOutputBlock（需要处理 \\r 回车/ANSI 转义）、
        ToolSummaryBlock（需要根据成功/失败组合多次写入）等
        输出逻辑复杂的组件。重写 render_to_adapter() 时仍应实现
        render() 作为降级/调试用途。
    """

    def __init__(self, props: dict | None = None, key: str | None = None) -> None:
        """初始化组件。

        Args:
            props: 外部传入的不可变属性字典（只读），默认 {}。
            key: 可选的身份标识键，用于 WidgetTree 中的查找。
        """
        super().__init__(props=props, key=key)

    def did_mount(self) -> None:
        """组件挂载后调用 — 执行初始化逻辑。

        由 ``Framework.create_component()`` 在组件创建后自动调用。
        子类可重写此方法执行初始化操作（如预计算渐变色号、注册事件等）。

        默认实现设置挂载标志。
        """
        self._mounted = True

    def will_unmount(self) -> None:
        """组件卸载前调用 — 清理资源。

        子类可重写此方法执行清理操作（如取消事件订阅、释放资源等）。

        默认实现清除挂载标志。
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
    def render(self, buffer: RenderBuffer | None = None) -> str | Text | None:
        """渲染组件内容。

        子类必须实现此方法。当传入 buffer 参数时，应将渲染内容写入 buffer
        并返回 None；未传入 buffer 时返回 str/Text（保持向后兼容）。

        Args:
            buffer: 可选的 RenderBuffer 实例。传入时，渲染内容直接写入 buffer。

        Returns:
            str | Text | None: 未传入 buffer 时返回渲染文本；传入时返回 None。
        """

    # ── Widget 兼容 render ─────────────────────────

    def _render_to_buffer(self, buffer: RenderBuffer) -> None:
        """将渲染结果写入 RenderBuffer（WidgetTree 兼容方法）。

        默认实现通过 self.render() 获取输出字符串并写入 buffer。
        子类可重写此方法实现更高效的 Widget 树渲染。

        Args:
            buffer: 目标 RenderBuffer 实例。
        """
        output = self.render()
        if isinstance(output, Text):
            output = output.plain
        if isinstance(output, str) and output:
            buffer.write(0, 0, output)

    def render_to_adapter(self, adapter: "OutputAdapter") -> int:
        """通过 OutputAdapter 渲染组件，返回估计行数。

        默认实现：创建临时 RenderBuffer，委托 render(buffer) 写入，
        然后将 buffer 内容通过 adapter.write() 输出。
        输出会通过 Text.from_ansi() 还原为 Rich Text 对象，
        保持与原有 `render() -> str | Text` 路径的向后兼容。

        子类可重写此方法以直接操作 adapter，实现高级渲染逻辑。

        Args:
            adapter: OutputAdapter 实例，用于将内容写入终端。

        Returns:
            int: 渲染内容的估计行数。
        """
        if not self.should_update():
            return 0
        try:
            # 获取终端宽度确定 buffer 尺寸
            try:
                import shutil
                term_w = shutil.get_terminal_size().columns
            except Exception:
                term_w = 80
            buf = RenderBuffer(max(term_w, 80), 1000)
            self.render(buf)
            output = buf.render()
        except Exception as exc:
            _logger.warning("组件 %s.render() 失败: %s", type(self).__name__, exc)
            adapter.write(f"\033[33m[渲染降级: {type(self).__name__}]\033[0m")
            return 1
        if output:
            # 将输出包装回 Text 对象以保持向后兼容（原 render()→Text 路径）
            try:
                adapter.write(Text.from_ansi(output))
            except Exception:
                adapter.write(output)
            return _estimate_content_lines(output)
        return 0

    # ── Widget 兼容 ──────────────────────────────────────

    def compose(self) -> "Widget | list[Widget]":
        """声明子控件组合。

        TuiComponent 默认是叶子控件，无子节点。
        返回空列表以支持 WidgetTree 递归渲染。
        """
        return []

    def update(self, new_props: dict | None = None) -> None:
        """更新组件状态并触发重渲染（Widget 兼容方法）。

        委托给 should_update() 判定是否需要重渲染。
        """
        if self.should_update(new_props):
            self._dirty = True

    # ── 辅助 ──────────────────────────────────────────────

    def _estimate_lines(self, text: str = "") -> int:
        """估算文本内容的终端行数。

        按文本中的换行符数量 + 1 计算行数。
        不处理终端换行（word wrapping），仅适用于粗略估计。

        Args:
            text: 要估算的文本。省略时使用空字符串。

        Returns:
            int: 估算的行数，至少为 1。
        """
        if not text:
            return 1
        return text.count('\n') + 1


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


def apply_fade_in(text: str, frame: int,
                  easing: str = "smooth",
                  total_frames: int = 6,
                  start_color: int = 240,
                  end_color: int = 253) -> str:
    """对文本应用 FadeIn 入场渐显动效。

    使用 FadeIn 过渡效果生成渐显前缀，包裹文本使其从暗灰渐变至目标色。
    无动效效果时（frame 为 0 或 FadeIn 返回空前缀）返回原文本。

    Args:
        text: 要应用动效的文本。
        frame: 当前帧号。
        easing: 缓动函数，默认 "smooth"。
        total_frames: 渐显总帧数，默认 6。
        start_color: 起始 256 色号，默认 240（暗灰）。
        end_color: 结束 256 色号，默认 253（亮白）。

    Returns:
        带 FadeIn 渐显包裹的文本。无动效时返回原文本。
    """
    if not text or frame <= 0:
        return text
    from .animation.transitions import FadeIn  # noqa: C0415
    fade = FadeIn(easing=easing, total_frames=total_frames,
                  start_color=start_color, end_color=end_color)
    fade_prefix = fade.render(frame)
    if fade_prefix:
        return f"{fade_prefix}{text}\033[0m"
    return text
