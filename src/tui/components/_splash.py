"""启动品牌屏组件 — SplashScreen。

在 ChatUIConsumer 启动时首次展示简洁欢迎信息：

  > deepseek-v4-flash Chat
  │   ────────────────────────────────────────
  │   /help   Esc中断   / 输前缀按 Tab 补全

设计模式: TuiComponent 子类，纯渲染组件（无状态管理）。
"""

from __future__ import annotations

from typing import ClassVar

from ..render_buffer import RenderBuffer
from ..core.style import Style
from ..terminal.terminal import is_narrow
from ._base import TuiComponent


# ═══════════════════════════════════════════════════════════
# SplashScreen — 启动品牌屏组件
# ═══════════════════════════════════════════════════════════

_WELCOME_LINES: list[str] = []


class SplashScreen(TuiComponent):
    """启动品牌屏组件 — 首次启动时展示简洁欢迎信息。

    渲染内容（仅首次展示一次）：
      > <模型名> Chat
      │   ────────────────────────────────────────
      │   /help   Esc中断   / 输前缀按 Tab 补全

    模型名从 CHAT_MODEL 环境变量或配置文件中读取。
    """

    # 类级状态：每个线程/进程仅展示一次
    _shown: ClassVar[bool] = False

    def __init__(self, model_name: str = "", *, props: dict | None = None) -> None:
        super().__init__(props=props)
        self._model = model_name

    # ── 静态方法：重置展示状态（供测试用） ────────────

    @classmethod
    def reset_shown(cls) -> None:
        """重置展示标记，使下次 render() 可再次输出（测试用）。"""
        cls._shown = False

    # ── 核心渲染接口 ──────────────────────────────────

    def render(self, buffer: RenderBuffer | None = None) -> str | None:
        """渲染启动品牌屏。

        首次调用时生成欢迎信息，后续调用返回空字符串（仅展示一次）。

        Args:
            buffer: 可选的 RenderBuffer 实例。传入时，渲染内容直接写入 buffer。

        Returns:
            未传入 buffer 时返回欢迎信息字符串（ANSI 色号），
            已展示过则返回空字符串；
            传入 buffer 时返回 None。
        """
        if self._shown:
            return ""
        self._shown = True

        model = self._model
        if not model:
            # 运行时惰性 import 避免循环
            from ...config import MODEL
            model = MODEL

        # 窄屏：保持现有静态渲染（青色样式）
        if is_narrow():
            line1 = f"  {Style(fg=45).apply(f'> {model} Chat')}"
            sep = f"{Style(fg=45).apply('─' * 50)}"
            line2 = f"  {Style(fg=45).apply('│')}   {sep}"
            help_text = "/help   Esc中断   / 输前缀按 Tab 补全"
            line3 = f"  {Style(fg=45).apply('│')}   {Style(fg=242).apply(help_text)}"
            result = f"{line1}\n{line2}\n{line3}"
        else:
            # 宽屏：彩虹模型名 + 彩虹分隔线 + 灰色帮助信息
            # ✅ 窄屏降级已确认：上方 is_narrow() 分支处理窄屏静态渲染，
            #    build_rainbow_ansi 仅在宽屏路径调用，无窄屏兼容问题。
            from ..core.effects import build_rainbow_ansi
            line1 = f"  {build_rainbow_ansi(f'> {model} Chat', frame=0)}"
            sep = build_rainbow_ansi('─' * 50, frame=0)
            line2 = f"  {Style(fg=45).apply('│')}   {sep}"
            help_text = "/help   Esc中断   / 输前缀按 Tab 补全"
            line3 = f"  {Style(fg=45).apply('│')}   {Style(fg=242).apply(help_text)}"
            result = f"{line1}\n{line2}\n{line3}"

        if buffer is not None:
            if result:
                buffer.write(0, 0, result)
            return None
        return result

    def render_to_adapter(self, adapter) -> int:
        """通过 OutputAdapter 渲染品牌屏，返回估计行数。

        Args:
            adapter: OutputAdapter 实例。

        Returns:
            int: 渲染内容的估计行数（固定 4 行）。
        """
        content = self.render()
        if not content:
            return 0
        adapter.write(content)
        return 4


__all__ = [
    "SplashScreen",
]
