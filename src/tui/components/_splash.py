"""启动品牌屏组件 — SplashScreen。

在 ChatUIConsumer 启动时首次展示简洁欢迎信息：

  > deepseek-v4-flash Chat
  │   ────────────────────────────────────────
  │   /help   Esc中断   / 输前缀按 Tab 补全

设计模式: TuiComponent 子类，纯渲染组件（无状态管理）。
"""

from __future__ import annotations

from typing import ClassVar

from ..core.style import Style
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

    def __init__(self, model_name: str = "") -> None:
        self._model = model_name

    # ── 静态方法：重置展示状态（供测试用） ────────────

    @classmethod
    def reset_shown(cls) -> None:
        """重置展示标记，使下次 render() 可再次输出（测试用）。"""
        cls._shown = False

    # ── 核心渲染接口 ──────────────────────────────────

    def render(self) -> str:
        """渲染启动品牌屏。

        首次调用时生成欢迎信息，后续调用返回空字符串（仅展示一次）。

        Returns:
            欢迎信息字符串（ANSI 色号），已展示过则返回空字符串。
        """
        if self._shown:
            return ""
        self._shown = True

        model = self._model
        if not model:
            # 运行时惰性 import 避免循环
            from ...config import MODEL
            model = MODEL

        # 青色模型名 + Chat
        line1 = f"  {Style(fg=45).apply(f'> {model} Chat')}"
        # 分隔线（用青色边框字符 │ + 横线）
        sep = f"{Style(fg=45).apply('─' * 50)}"
        line2 = f"  {Style(fg=45).apply('│')}   {sep}"
        # 帮助信息
        help_text = "/help   Esc中断   / 输前缀按 Tab 补全"
        line3 = f"  {Style(fg=45).apply('│')}   {Style(fg=242).apply(help_text)}"

        return f"{line1}\n{line2}\n{line3}"

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
