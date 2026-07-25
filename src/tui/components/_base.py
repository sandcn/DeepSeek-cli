"""组件基类 — TuiComponent + Widget 统一体系。

TuiComponent 继承自 Widget（src.tui.widget_base 中的统一控件基类），
保持现有组件的完全向后兼容性。

从 _components.py 拆分，包含所有组件共用的基类和辅助函数。
"""

from __future__ import annotations

import math
import logging
from abc import abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ...renderer.output import OutputAdapter

from ..render_buffer import RenderBuffer

from rich.text import Text

from ..widget_base import Widget

from ..core.text_utils import apply_fade_in, build_left_border_ansi
from ..core.effects import sine_color
from ..engine.utils import _truncate_msg
from ..framework import get_animator
from ..core.style import Style, StyleSheet
from ..terminal.terminal import is_narrow

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

    # ── 窄屏降级模板方法 ────────────────────────────

    def render_with_narrow_fallback(
        self,
        buffer: RenderBuffer | None = None,
        *,
        narrow_method=None,
    ) -> str | Text | None:
        """窄屏降级模板方法 — 窄屏时调用 narrow_method，宽屏时返回 None。

        子类可在 render() 中调用此方法，避免重复编写 ``if is_narrow()`` 判断。
        
        使用示例::

            def render(self, buffer=None):
                result = self.render_with_narrow_fallback(buffer, narrow_method=self._render_narrow)
                if result is not None:
                    return result
                # ... 宽屏渲染逻辑 ...

        Args:
            buffer: 可选的 RenderBuffer 实例。
                传入时，若窄屏则自动将 narrow_method() 结果写入 buffer。
            narrow_method: 窄屏时调用的可调用对象，签名 ``() -> str | Text | None``。
                未传入时自动尝试调用 self._render_narrow() 作为默认兜底。

        Returns:
            窄屏时返回 narrow_method() 的结果（已写入 buffer 若传入）；
            宽屏时返回 None（继续宽屏渲染）。
        """
        if is_narrow():
            method = narrow_method or self._render_narrow
            result = method()
            if buffer is not None and result:
                text = result.plain if isinstance(result, Text) else str(result)
                if text:
                    buffer.write(0, 0, text)
            return result
        return None

    def _render_narrow(self) -> str | Text | None:
        """窄屏降级渲染 — 默认返回空字符串，子类可重写。

        推荐使用 ``render_with_narrow_fallback()`` + ``_render_narrow()`` 模式
        替代手动 ``if is_narrow():`` 分支判断。子类实现 ``_render_narrow()``
        返回窄屏降级文本，由模板方法自动处理 buffer 写入和返回语义。
        """
        return ""

    def _finalize_render(
        self,
        result: str | Text | None,
        buffer: RenderBuffer | None,
    ) -> str | Text | None:
        """统一终版渲染输出 — 消除所有子类中的 ``if buffer is not None: ...`` 样板代码。

        所有 TuiComponent 子类的 render() 方法统一使用此方法处理返回：
        - 有 buffer：将 result 写入 buffer 并返回 None
        - 无 buffer：直接返回 result（字符串/Text）

        使用示例::

            def render(self, buffer=None):
                result = self._build_content()
                return self._finalize_render(result, buffer)

        Args:
            result: 渲染结果（字符串或无格式文本/None）。
            buffer: 可选的 RenderBuffer 实例。传入时，渲染结果自动写入 buffer。

        Returns:
            buffer 不为 None 时返回 None；否则返回 result。
        """
        if buffer is not None and result:
            text = result.plain if isinstance(result, Text) else str(result)
            if text:
                buffer.write(0, 0, text)
            return None
        return result

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


# ── FadeIn 缓动因子（共享于 ErrorBlock / NotificationBlock） ──
_FADE_TOTAL_FRAMES = 6
_FADE_START_COLOR = 238


def _fade_factor(frame: int, total: int = _FADE_TOTAL_FRAMES) -> float:
    """计算 smooth easing 渐显因子 [0.0, 1.0]。

    FadeIn 不再通过 ANSI 前缀包裹（会被后续显式色号覆盖），
    而是将缓动因子融入边框/辉光色号的计算中——frame 0 时色号从 dim 开始，
    随帧递增渐亮至目标色。

    Args:
        frame: 当前帧号。
        total: 渐显总帧数。

    Returns:
        缓动因子，0.0（初始最暗）→ 1.0（全亮）。
    """
    if frame <= 0:
        return 0.0
    t = min(frame / total, 1.0)
    return (math.sin((t - 0.5) * math.pi) + 1) / 2


def _fade_color(target: int, fade: float,
                base: int = _FADE_START_COLOR) -> int:
    """将缓动因子融入色号：暗色 → 目标色。

    Args:
        target: 目标色号（256 色）。
        fade: 缓动因子 [0.0, 1.0]。
        base: 起始暗色。

    Returns:
        插值后的色号，clamp 到 [0, 255]。
    """
    return max(0, min(255, int(base + (target - base) * fade)))


# ── 捕获优先渲染辅助（共享于 AnswerBlock / ThinkingBlock） ──

def _render_captured_or_raw(
    buffer: RenderBuffer,
    obj: object,
    captured_attr_name: str,
    content_list: list[str],
) -> None:
    """将捕获的 ANSI 渲染输出或原始累积文本写入 buffer。

    优先使用 IncrementalRenderer 捕获的渲染后输出（保留 Markdown 格式、
    语法高亮等），捕获不可用时回退到原始累积纯文本。

    Args:
        buffer: 目标 RenderBuffer 实例。
        obj: 包含捕获属性的对象（如 ChatRenderState 实例）。
        captured_attr_name: 捕获属性名（如 ``"captured_content_output"``）。
        content_list: 原始累积内容列表（如 ``self._cumulative_content``）。
    """
    captured = getattr(obj, captured_attr_name, None)
    if captured:
        rendered = "".join(captured)
        if rendered:
            buffer.write(0, 0, rendered)
            return
    full_content = "".join(content_list)
    if full_content:
        buffer.write(0, 0, full_content)


# ── 渲染缓冲区辅助（共享于 ToolSummaryBlock / TuiComponent.render_to_adapter） ──

def _render_via_buffer(
    component: TuiComponent,
    adapter: "OutputAdapter",
    buffer_height: int = 1000,
) -> int:
    """通过 RenderBuffer 渲染组件并写入 OutputAdapter，返回估计行数。

    提取 TuiComponent.render_to_adapter() 中的通用渲染流程为独立辅助函数，
    供其他组件（如 ToolSummaryBlock）复用，消除重复的 render_to_adapter() 实现。

    Args:
        component: 要渲染的 TuiComponent 实例。
        adapter: OutputAdapter 实例，用于将内容写入终端。
        buffer_height: RenderBuffer 高度，默认 1000（保持向后兼容）。

    Returns:
        int: 渲染内容的估计行数，失败时返回 1（降级消息）。
    """
    try:
        import shutil
        term_w = shutil.get_terminal_size().columns
    except Exception:
        term_w = 80
    try:
        buf = RenderBuffer(max(term_w, 80), buffer_height)
        component.render(buf)
        output = buf.render()
    except Exception as exc:
        _logger.warning("组件 %s.render() 失败: %s", type(component).__name__, exc)
        adapter.write(f"\033[33m[渲染降级: {type(component).__name__}]\033[0m")
        return 1
    if output:
        try:
            adapter.write(Text.from_ansi(output))
        except Exception:
            adapter.write(output)
        return _estimate_content_lines(output)
    return 0


# ═══════════════════════════════════════════════════════════
# StyledMessageBlock — 参数化 ErrorBlock / NotificationBlock 共享逻辑
# ═══════════════════════════════════════════════════════════

class StyledMessageBlock(TuiComponent):
    """带样式的消息块 — 参数化 ErrorBlock / NotificationBlock 的共享逻辑。

    通过 prefix_char、color、narrow_style_key 等参数化差异，
    消除 ErrorBlock 和 NotificationBlock 的代码重复。

    窄屏：Text.assemble(prefix_char + message)
    宽屏：sine_color 辉光呼吸 + build_left_border_ansi 边框 + 入场 FadeIn
    """

    def __init__(
        self,
        prefix_char: str,
        color: int,
        narrow_style_key: str,
        message: str = "",
        *,
        props: dict | None = None,
        truncate: bool = False,
        max_len: int = 200,
    ) -> None:
        super().__init__(props=props)
        self.prefix_char = prefix_char
        self.color = color
        self.narrow_style_key = narrow_style_key
        self._truncate = truncate
        self._max_len = max_len
        self._message = _truncate_msg(message, max_len) if truncate else message

    def _render_narrow(self) -> str | Text | None:
        """窄屏渲染 — 静态样式消息，不加动效。"""
        style_obj = StyleSheet.get(self.narrow_style_key)
        rich_style = style_obj.to_rich() if style_obj else None
        return Text.assemble(
            (f"\n  {self.prefix_char} ", rich_style),
            (self._message, rich_style),
        )

    def render(self, buffer: RenderBuffer | None = None) -> str | Text | None:
        result = self.render_with_narrow_fallback(buffer, narrow_method=self._render_narrow)
        if result is not None:
            # 窄屏分支已由 render_with_narrow_fallback 处理并写入 buffer
            return result
        # 宽屏：sine_color 辉光呼吸 + build_left_border_ansi 边框 + 入场 FadeIn
        animator = get_animator()
        frame = animator.frame
        fade = _fade_factor(frame)
        glow_lo = _fade_color(self.color, fade)
        glow_hi = _fade_color(min(255, self.color + 15), fade)
        glow_color = sine_color(frame, glow_lo, glow_hi, 12)
        glow_style = Style(fg=glow_color)
        border_breath = StyleSheet.resolve("border_breath", Style(fg=23))
        border_target = border_breath.fg if border_breath.fg is not None else 23
        border_edge = build_left_border_ansi(frame, border_target, 24)
        ansi_str = (
            f"\n  {border_edge}"
            f" {glow_style.to_ansi()}{self.prefix_char} \033[0m"
            f"{glow_style.to_ansi()}{self._message}\033[0m"
        )
        result = Text.from_ansi(ansi_str)
        return self._finalize_render(result, buffer)


# ═══════════════════════════════════════════════════════════
# StreamingBlock — 参数化 ThinkingBlock / AnswerBlock 共享逻辑
# ═══════════════════════════════════════════════════════════

class StreamingBlock(TuiComponent):
    """流式内容块 — 参数化 ThinkingBlock / AnswerBlock 的共享逻辑。

    消除 ThinkingBlock 和 AnswerBlock 的代码重复。
    子类只需实现 _build_header(), _get_renderer(), _on_first_write(),
    _close_renderer() 等钩子。
    """

    def __init__(self, rs, captured_attr_name: str, *, props: dict | None = None) -> None:
        super().__init__(props=props)
        self._rs = rs
        self._captured_attr_name = captured_attr_name
        self._cumulative_content: list[str] = []
        self._first_write = True

    def _build_header(self) -> str | Text | None:
        """子类实现：构建标题（窄屏/宽屏）"""
        return None

    def _get_renderer(self):
        """子类实现：获取渲染器"""
        raise NotImplementedError

    def _on_first_write(self) -> None:
        """子类实现：首次写入时的额外操作"""
        pass

    def _close_renderer(self) -> None:
        """子类实现：关闭渲染器"""
        pass

    def _is_first_write(self) -> bool:
        """判断是否为首次写入。默认检查 _first_write 标志。

        子类可覆盖此方法实现自定义首次写入判断逻辑（如 ThinkingBlock
        基于 reasoning_state 判断）。
        """
        return self._first_write

    def _apply_first_write_effect(self, text: str) -> str:
        """首次写入时对文本应用入场动效（如 FadeIn）。

        窄屏时跳过动效返回原文本。子类可覆盖此方法实现自定义首次写入动效。

        Args:
            text: 待写入的文本。

        Returns:
            应用动效后的文本（窄屏时返回原文本）。
        """
        if is_narrow():
            return text
        frame = get_animator().frame
        return apply_fade_in(text, frame)

    def write(self, text: str) -> int:
        """写入内容到累积列表和渲染器，返回估计行数。

        集成渲染器写入和 FadeIn 入场动效。
        子类可通过 _is_first_write() 和 _apply_first_write_effect() 钩子自定义行为。

        Args:
            text: 待写入的文本内容。

        Returns:
            int: 内容的估计行数（通过 _estimate_content_lines 计算）。
        """
        # 先判断首次写入，再获取渲染器 —— 顺序不可调换：
        # ThinkingBlock 的 _get_renderer() 有修改 reasoning_state 的副作用，
        # _is_first_write() 依赖 reasoning_state 判断首次写入。
        is_first = self._is_first_write()
        rr = self._get_renderer()
        if rr is None:
            # 渲染器不可用时仅累积
            self._cumulative_content.append(text)
            return 0

        if is_first:
            self._first_write = False
            self._on_first_write()
            header = self._build_header()
            if header:
                header_str = str(header.plain if isinstance(header, Text) else header)
                self._cumulative_content.append(header_str)
                rr.write(header_str)
            text = self._apply_first_write_effect(text)
            self._cumulative_content.append(text)  # ← 追加首次内容
        else:
            self._cumulative_content.append(text)

        rr.write(text)
        return _estimate_content_lines(text)

    def render(self, buffer: RenderBuffer | None = None) -> str | Text | None:
        if buffer is not None:
            _render_captured_or_raw(
                buffer, self._rs, self._captured_attr_name, self._cumulative_content
            )
            return None
        return self._finalize_render("".join(self._cumulative_content), buffer)

    def close(self) -> None:
        """关闭流式块"""
        self._close_renderer()