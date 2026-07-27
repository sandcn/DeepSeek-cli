"""底部栏选择器抽象基类 — 消除 CommandPalette / SessionSwitcher 重复模式。

CommandPalette、SessionSwitcher 和 MessageEditor._interactive_message_select
共享相同的三步骤模式：
  1. TTLCache.get() → 获取候选项
  2. run_bottom_bar_selection() → 底部栏交互选择
  3. 处理结果 → 返回选中项

BaseBottomBarSelector 将此模式提取为模板方法，子类只需实现：
  - _fetch_items(): 获取候选项列表
  - _format_display(): 格式化显示文本（默认 str()）
  - _on_selected(): 选中项处理逻辑
  - _get_title(): 弹窗标题（默认 ""）

泛型参数：
  T — 候选项类型
  R — 选中后返回的结果类型
"""

from __future__ import annotations

from typing import Generic, TypeVar

from .bottom_bar.selection import run_bottom_bar_selection
from ..core.ttl_cache import TTLCache
from ..core.text_utils import truncate
from ..terminal.terminal import is_narrow, get_terminal_width

T = TypeVar("T")
R = TypeVar("R")


class BaseBottomBarSelector(Generic[T, R]):
    """底部栏选择器抽象基类。

    封装 TTLCache + run_bottom_bar_selection 的通用交互流程，
    子类通过覆写钩子方法定制具体行为。

    用法示例（命令面板）：
        class CommandPalette(BaseBottomBarSelector[str, str | None]):
            def _fetch_items(self) -> list[str]:
                return get_registered_command_names()
            def _on_selected(self, item: str) -> str | None:
                return item
            def _get_title(self) -> str:
                return "Command Palette"
    """

    def __init__(self, *, ttl: float = 60.0):
        """初始化选择器。

        Args:
            ttl: 缓存有效期（秒），默认 60s。传递给内部 TTLCache。
        """
        self._cache: TTLCache[list[T]] = TTLCache(
            fetcher=self._fetch_items, ttl=ttl,
        )

    # ── 子类必须实现的钩子方法 ─────────────────────────

    def _fetch_items(self) -> list[T]:
        """获取候选项列表（子类实现）。

        由 TTLCache 在缓存过期时调用，结果被缓存 ttl 秒。
        """
        raise NotImplementedError

    def _on_selected(self, item: T) -> R:
        """处理用户选中的项（子类实现）。

        Args:
            item: 用户确认选择的候选项。

        Returns:
            处理后的结果，由 show() 返回给调用方。
        """
        raise NotImplementedError

    # ── 子类可选覆写的钩子方法 ─────────────────────────

    def _format_display(self, items: list[T]) -> list[str]:
        """格式化候选项为显示文本列表（默认 str()）。

        Args:
            items: 候选项列表。

        Returns:
            与 items 等长的显示文本列表，传递给 run_bottom_bar_selection。
        """
        return [str(item) for item in items]

    def _get_title(self) -> str:
        """获取弹窗标题（默认空字符串）。"""
        return ""

    def _get_initial_idx(self, items: list[T]) -> int:
        """获取初始选中索引（默认 0，即第一项）。"""
        return 0

    def _narrow_truncate_display(self, display: list[str], min_width: int = 20) -> list[str]:
        """窄屏时截断显示标签，避免终端换行。

        Args:
            display: 要显示的标签列表。
            min_width: 最小宽度阈值，默认为 20。

        Returns:
            截断后的标签列表（非窄屏时原样返回）。
        """
        if not is_narrow():
            return display
        max_width = max(get_terminal_width() - 4, min_width)
        return [truncate(label, max_width) for label in display]

    # ── 增量过滤与匹配高亮钩子 ──────────────────────────

    def filter_items(self, items: list[T], query: str) -> list[T]:
        """根据查询字符串过滤候选项（默认不过滤，子类可选覆写）。

        Args:
            items: 原始候选项列表。
            query: 用户输入的查询字符串。

        Returns:
            过滤后的候选项列表。
        """
        return items

    def highlight_match(self, display_text: str, query: str) -> str:
        """在显示文本中高亮匹配部分（默认不高亮，子类可选覆写）。

        Args:
            display_text: 格式化后的显示文本（可能含 ANSI 颜色码）。
            query: 用户输入的查询字符串。

        Returns:
            高亮后的显示文本。
        """
        return display_text

    # ── 公开方法 ───────────────────────────────────────

    def refresh(self) -> None:
        """强制刷新缓存（线程安全）。"""
        self._cache.refresh()

    def show(self, bottom_bar=None, query: str = "") -> R | None:
        """在底部栏补全弹窗中打开选择器。

        完整流程：
          1. 从 TTLCache 获取候选项
          2. 根据 query 过滤候选项（filter_items）
          3. 格式化显示文本
          4. 对显示文本应用匹配高亮（highlight_match）
          5. 调用 run_bottom_bar_selection 交互
          6. 用户确认后调用 _on_selected 处理结果

        Args:
            bottom_bar: _BottomBar 实例（可选），传递给 run_bottom_bar_selection。
            query: 用于增量过滤和匹配高亮的查询字符串（可选）。

        Returns:
            _on_selected 的返回值；候选项为空或用户取消时返回 None。
        """
        items = self._cache.get()
        if not items:
            return None

        # 增量过滤
        filtered = self.filter_items(items, query)
        if not filtered:
            return None

        # 格式化显示文本
        display = self._format_display(filtered)

        # 匹配高亮
        if query:
            display = [self.highlight_match(d, query) for d in display]

        # items 参数传纯文本（不含 ANSI 码），用 str(item) 转换；
        # display 保留 ANSI 彩色文本用于显示，与 items 等长确保索引对应。
        result = run_bottom_bar_selection(
            [str(item) for item in filtered], display,
            title=self._get_title(),
            initial_idx=self._get_initial_idx(filtered),
            bottom_bar=bottom_bar,
            input_instance=getattr(bottom_bar, '_input', None) if bottom_bar is not None else None,
        )

        if result["action"] == "confirmed" and result["index"] is not None:
            idx = result["index"]
            if 0 <= idx < len(filtered):
                return self._on_selected(filtered[idx])

        return None


__all__ = ["BaseBottomBarSelector"]
