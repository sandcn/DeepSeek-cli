"""chat_ui 控件模块 — Control 基类 + TextControl / MarkdownControl。

TextControl: Layer 1 — 依赖 _const（Style 常量引用由调用方传入）。
MarkdownControl: Layer 2 — 依赖 api.renderer（IncrementalRenderer / OutputAdapter）。

控件体系：
  Control (ABC)          — 控件基类（write / close / refresh_width / is_closed）
    ├── TextControl      — 纯文本控件（prefix + style + raw/ansi 路径）
    └── MarkdownControl  — 流式 Markdown 控件（封装 IncrementalRenderer）

设计目标：将 ContentRenderer 中分散的 _render_styled_line() / _write_text_or_ansi()
逻辑抽取到控件中，使渲染方法通过控件抽象操作，提升复用性和可扩展性。

Style 常量（_STYLE_BOLD / _STYLE_DIM / _STYLE_ERROR 等）由调用方从
._const 导入并作为参数传入 TextControl，本模块不直接引用。
"""

from __future__ import annotations

import logging
import sys
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

_logger = logging.getLogger(__name__)

from rich.style import Style
from rich.text import Text

if TYPE_CHECKING:
    from ..api.renderer import IncrementalRenderer
    from ..api.renderer.output import OutputAdapter


# ═══════════════════════════════════════════════════════════
# Control — 控件抽象基类
# ═══════════════════════════════════════════════════════════

class Control(ABC):
    """控件抽象基类 — 定义 write / close / refresh_width 公共接口。

    所有终端渲染控件（Text、Markdown、未来 Table/CodeBlock 等）
    统一实现此接口，使 ContentRenderer 可以通过多态委托渲染。

    生命周期：
      write(text) → ... → close()
      创建后即处于"打开"状态，close() 后 is_closed=True。
      close() 后 write() 静默跳过（幂等保护）。
    """

    @abstractmethod
    def write(self, text: str) -> None:
        """写入文本内容（子类实现具体渲染逻辑）。"""
        ...

    # ── 公共属性 ────────────────────────────────────────

    @property
    def start_line(self) -> int:
        """起始行号。"""
        return self._start_line

    @start_line.setter
    def start_line(self, value: int) -> None:
        self._start_line = value

    @property
    def level(self) -> int:
        """层级。"""
        return self._level

    @level.setter
    def level(self, value: int) -> None:
        self._level = value

    # ── 生命周期 ────────────────────────────────────────

    def close(self) -> None:
        """关闭控件，释放资源。

        默认 no-op，子类可覆盖以添加清理逻辑（flush、close 渲染器等）。
        幂等——多次调用无副作用。
        """

    @property
    @abstractmethod
    def is_closed(self) -> bool:
        """控件是否已关闭。"""
        ...

    def refresh_width(self) -> None:
        """刷新终端宽度缓存（对应 OutputAdapter.force_refresh_width()，绕过 5s TTL）。

        默认 no-op，子类可覆盖以委托内部渲染器刷新。
        供 resize 检测路径调用。
        """


# ═══════════════════════════════════════════════════════════
# TextControl — 纯文本控件（prefix + style）
# ═══════════════════════════════════════════════════════════

class TextControl(Control):
    """纯文本渲染控件 — 前缀 + Rich Style 样式化输出。

    封装原 ContentRenderer._render_styled_line() 和 _write_text_or_ansi()
    的逻辑，通过 OutputAdapter 输出到终端。

    支持三种写入路径：
      write()      — 前缀 + 样式渲染（如 "> 用户消息"）
      write_raw()  — 纯文本直写（跳过 Rich 样式管线）
      write_ansi() — ANSI 转义序列文本解析 + 回退
    """

    def __init__(
        self,
        output_adapter: "OutputAdapter",
        prefix: str = "",
        style: Style | None = None,
        start_line: int = 0,
        level: int = 0,
    ) -> None:
        """创建 TextControl 实例。

        Args:
            output_adapter: Rich Console 输出适配器（必填，不能为 None）
            prefix: 前缀字符串（如 "\\n  > "、"\\n  ! "、"\\n  · "），直接拼接到文本前
            style: Rich Style 对象，同时作用于前缀和文本
            start_line: 起始行号（默认 0）
            level: 层级（默认 0）

        Raises:
            ValueError: 若 output_adapter 为 None
        """
        if output_adapter is None:
            raise ValueError("TextControl: output_adapter 不能为 None")
        self._adapter = output_adapter
        self._prefix = prefix
        self._style = style
        self._start_line = start_line
        self._level = level
        self._closed = False

    # ── 公共接口 ────────────────────────────────────────

    def write(self, text: str) -> None:
        """写入含前缀和样式的文本行。

        已关闭时静默跳过（幂等保护）。
        空字符串时仍输出前缀以实现换行效果（前提是 prefix 非空）。
        """
        if self._closed:
            return
        self._adapter.write(
            Text.assemble((self._prefix, self._style), (text, self._style))
        )

    def write_raw(self, text: str) -> None:
        """纯文本直写（跳过 Rich 样式管线，无前缀）。

        适用于 \\r 覆盖输出、解析进度等信息。
        已关闭时静默跳过。
        """
        if self._closed:
            return
        self._adapter.write_raw(text)

    def write_ansi(self, text: str) -> None:
        """写入含 ANSI 转义序列的文本。

        尝试用 Text.from_ansi() 解析渲染，解析失败时回退到 write_raw()。
        回退路径也失败则记录日志并静默跳过。
        已关闭时静默跳过。
        """
        if self._closed:
            return
        try:
            self._adapter.write(Text.from_ansi(text))
        except Exception:
            _logger.warning(
                "TextControl.write_ansi: Text.from_ansi 解析失败，回退到 write_raw",
                exc_info=True,
            )
            try:
                self._adapter.write_raw(text)
            except Exception:
                _logger.warning(
                    "TextControl.write_ansi: write_raw 回退也失败",
                    exc_info=True,
                )

    def close(self) -> None:
        """关闭控件（标记关闭 + flush 适配器）。幂等。"""
        if self._closed:
            return
        self._closed = True
        self._adapter.flush()

    @property
    def is_closed(self) -> bool:
        return self._closed


# ═══════════════════════════════════════════════════════════
# MarkdownControl — 流式 Markdown 控件
# ═══════════════════════════════════════════════════════════

class MarkdownControl(Control):
    """流式 Markdown 渲染控件 — 封装 IncrementalRenderer。

    统一推理/内容两个流式 Markdown 路径，对外暴露统一 Control 接口。
    内部直接委托 IncrementalRenderer，不做二次封装——避免重复创建
    Console 实例和 output_lock 竞争。

    is_closed 由本类自维护 _closed 标志保证幂等性，
    不依赖 IncrementalRenderer 内部实现细节。
    """

    def __init__(
        self,
        style: str = "",
        show_indicator: bool = False,
        typing_speed: int = 1000,
        start_line: int = 0,
        level: int = 0,
    ) -> None:
        """创建 MarkdownControl 实例。

        内部创建 IncrementalRenderer 实例（自行管理 Console 和
        OutputAdapter）。与 TextControl 的 _tool_adapter 各用各的
        Console——两条渲染管线（流式 Markdown / 工具输出）的宽度缓存
        独立刷新（5s TTL），写入串行化由全局 output_lock 保证。

        Args:
            style: Rich Console style 字符串（如 "dim"）
            show_indicator: 是否显示流式光标指示器
            typing_speed: 打字机效果速度（字符/秒，1000=即时）
            start_line: 起始行号（默认 0）
            level: 层级（默认 0）
        """
        from ..api.renderer import IncrementalRenderer
        self._renderer: "IncrementalRenderer" = IncrementalRenderer(
            style=style,
            _file=sys.__stdout__,
            typing_speed=typing_speed,
            show_indicator=show_indicator,
        )
        self._start_line = start_line
        self._level = level
        self._closed = False

    # ── 公共接口 ────────────────────────────────────────

    def write(self, text: str) -> None:
        """写入 Markdown 文本（委托 IncrementalRenderer）。

        已关闭时静默跳过（幂等保护）。
        """
        if self._closed:
            return
        self._renderer.write(text)

    def close(self) -> None:
        """关闭 Markdown 渲染器。幂等——多次调用无副作用。"""
        if self._closed:
            return
        self._closed = True
        self._renderer.close()

    def refresh_width(self) -> None:
        """刷新终端宽度缓存（委托 IncrementalRenderer.force_refresh_width()，
        绕过内部 OutputAdapter 的 5s TTL）。
        """
        self._renderer.force_refresh_width()

    @property
    def is_closed(self) -> bool:
        return self._closed
