"""启动品牌屏组件 — SplashScreen。

在 ChatUIConsumer 启动时首次展示简洁欢迎信息：

  > deepseek-v4-flash Chat
  │   ────────────────────────────────────────
  │   /help   Esc中断   / 输前缀按 Tab 补全

动效增强（2026-07-16）：
  - 宽屏：彩虹模型名 + glow 辉光 `>` 提示符 + 彩虹分隔线
  - 窄屏：保持静态青色渲染
  - 自适应终端宽度

设计模式: TuiComponent 子类，纯渲染组件（无状态管理）。
"""

from __future__ import annotations

from typing import ClassVar

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

        # ── 自适应终端宽度 ──
        try:
            import shutil
            term_w = shutil.get_terminal_size().columns
        except Exception:
            term_w = 80

        # 窄屏：保持现有静态渲染（青色样式）
        if is_narrow():
            line1 = f"  {Style(fg=45).apply(f'> {model} Chat')}"
            sep_len = min(50, max(10, term_w - 8))
            sep = f"{Style(fg=45).apply('─' * sep_len)}"
            line2 = f"  {Style(fg=45).apply('│')}   {sep}"
            help_text = "/help   Esc中断   / 输前缀按 Tab 补全"
            line3 = f"  {Style(fg=45).apply('│')}   {Style(fg=242).apply(help_text)}"
            return f"{line1}\n{line2}\n{line3}"

        # ── 宽屏：彩虹辉光标题 + 彩虹分隔线 + 灰色帮助信息 ──
        from ..core.effects import build_rainbow_ansi, build_glow_ansi

        # 标题行：glow 辉光 `>` + 彩虹模型名
        glow_prefix = build_glow_ansi(frame=0, base_color=51, period=12)
        glow_reset = "\033[0m"
        line1 = f"  {glow_prefix}>{glow_reset} {build_rainbow_ansi(f'{model} Chat', frame=0)}"

        # 分隔线：自适应宽度（留边距 8 字符）+ 彩虹动效
        sep_len = min(60, max(20, term_w - 8))
        sep = build_rainbow_ansi('─' * sep_len, frame=0)
        line2 = f"  {glow_prefix}│{glow_reset}   {sep}"

        # 帮助信息（灰色）
        help_text = "/help   Esc中断   / 输前缀按 Tab 补全"
        line3 = f"  {glow_prefix}│{glow_reset}   {Style(fg=242).apply(help_text)}"

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

    def render_to_target(self, target) -> int:
        """通过 IOutputTarget 渲染品牌屏（inline 模式），返回估计行数。

        Args:
            target: IOutputTarget 实例。

        Returns:
            int: 渲染内容的估计行数（固定 4 行）。
        """
        content = self.render()
        if not content:
            return 0
        target.write_line(content)
        return 4


__all__ = [
    "SplashScreen",
]
