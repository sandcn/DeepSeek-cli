"""chat_ui 补全模块 — Tab 补全交互逻辑。

Layer 2 — 依赖 _BottomBar + CompletionEngine（来自 ui 层）。

_CmplHandler 在 EscapeMonitor 线程中：
  1. 计算候选项（CompletionEngine）
  2. 设置补全状态（self._bb.show_completions / hide_completions / cycle_completion）
  3. 请求 render 线程重绘（self._request_redraw）
  4. 查询只读状态（is_completion_visible / get_selected_completion）
"""

from __future__ import annotations

from typing import Callable, TYPE_CHECKING
import os

if TYPE_CHECKING:
    from ..ui._completion import CompletionEngine
    from ._protocols import BottomBarProtocol


def _get_match_prefix(items: list, last_word: str) -> str:
    """计算补全弹窗的匹配前缀。

    路径补全场景（file/dir 类型）：使用 basename 作为匹配前缀，
    因为弹窗中只显示文件名（不显示路径前缀）。
    非路径场景：直接使用最后一个词。
    """
    if items and items[0].item_type in ("file", "dir"):
        return os.path.basename(last_word) if '/' in last_word else last_word
    return last_word


class _CmplHandler:
    """Tab 补全交互处理器。

    由 EscapeMonitor 线程回调驱动，管理补全弹窗的
    首次激活、循环选择、关闭和上下键导航。

    与 CompletionEngine（纯计算型）分工：
      - CompletionEngine：计算补全候选项（命令/路径/参数）
      - _CmplHandler：管理补全 UI 交互流程（弹窗/循环/应用）
    """

    def __init__(
        self, bottom_bar: "BottomBarProtocol", engine: "CompletionEngine",
        request_redraw: Callable[[], None],
    ):
        self._bb = bottom_bar
        self._engine = engine
        self._request_redraw = request_redraw
        self._last_auto_text: str | None = None

    def on_tab(self, text: str) -> str | None:
        """Tab 补全入口。

        补全弹窗已可见 → 确认当前选中项并应用。
        弹窗不可见 → 计算候选项，显示弹窗，返回首个匹配。
        """
        if self._bb.is_completion_visible:
            return self._cycle_tab(text)
        return self._first_tab(text)

    def on_dismiss(self) -> None:
        """关闭补全弹窗（ESC/非 Tab 按键触发）。"""
        self._bb.hide_completions()
        self._request_redraw()
        self._last_auto_text = None  # 重置防抖，允许下次相同文本重新触发

    def on_navigate(self, delta: int, text: str) -> str | None:
        """上下键导航补全弹窗（delta: -1=上, +1=下）。

        text 参数由 EscapeMonitor 传入当前输入缓冲区文本，
        确保与 on_tab 使用同一来源的 text，消除 _last_text 过期风险。

        弹窗不可见时返回 None，EscapeMonitor 回退为正常上下键行为。
        弹窗可见时更新选中状态 + 请求 render 线程重绘。
        """
        if not self._bb.is_completion_visible:
            return None
        self._bb.cycle_completion(delta)
        self._request_redraw()
        return text  # 仅导航高亮，不应用补全文字

    def on_auto(self, text: str) -> None:
        """自动补全入口 — 用户输入可打印字符时自动触发。

        规则：
          - 文本为空 → 隐藏弹窗
          - 不以 / 开头且长度 < 2 → 隐藏弹窗（避免过早弹出）
          - 有候选项 → 显示/更新弹窗，选中索引重置为 0
          - 无候选项 → 隐藏弹窗
        """
        # 防抖：文本未变化时跳过重复计算（None 为哨兵值，首次调用不跳过）
        if self._last_auto_text is not None and text == self._last_auto_text:
            return

        if not text:
            self._bb.hide_completions()
            self._request_redraw()
            self._last_auto_text = text
            return

        # 最小触发长度：命令（/开头）1字符即可，普通文本至少2字符
        if not text.startswith('/') and len(text) < 2:
            self._bb.hide_completions()
            self._request_redraw()
            self._last_auto_text = text
            return

        items = self._engine.complete(text)
        if not items:
            self._bb.hide_completions()
            self._request_redraw()
            self._last_auto_text = text
            return

        words = text.split()
        last_word = words[-1] if words else ""

        match_prefix = _get_match_prefix(items, last_word)

        self._bb.show_completions(
            [item.display for item in items], 0,
            texts=[item.text for item in items],
            start_pos=items[0].start_pos,
            orig_prefix=last_word,
            types=[item.item_type for item in items],
            match_prefix=match_prefix,
        )
        self._request_redraw()
        self._last_auto_text = text

    # ── 内部方法 ──────────────────────────────────────

    def _cycle_tab(self, text: str) -> str | None:
        """已可见弹窗 → **确认当前选中项**，应用补全到输入缓冲区。

        Tab 键走此路径：直接获取当前高亮的补全项并应用（确认语义）。
        与 on_navigate（箭头键）不同——后者只移动高亮不应用补全（导航语义）。
        弹窗重新出现后 Tab 可继续确认新的选中项（配合 auto-completion 自动刷新）。
        """
        repl_text, start_pos, orig_prefix = self._bb.get_selected_completion()
        if not repl_text:
            return None
        return _apply_completion(text, repl_text, start_pos, orig_prefix)

    def _first_tab(self, text: str) -> str | None:
        """首次 Tab → 计算候选项，设置状态 + 请求重绘。"""
        items = self._engine.complete(text)
        if not items:
            self._bb.hide_completions()
            self._request_redraw()
            return None

        words = text.split()
        last_word = words[-1] if words else ""

        match_prefix = _get_match_prefix(items, last_word)

        self._bb.show_completions(
            [item.display for item in items], 0,
            texts=[item.text for item in items],
            start_pos=items[0].start_pos,
            orig_prefix=last_word,
            types=[item.item_type for item in items],
            match_prefix=match_prefix,
        )
        self._request_redraw()
        return _apply_completion(
            text, items[0].text, items[0].start_pos, last_word,
        )


def _apply_completion(
    text: str, repl_text: str, start_pos: int, orig_prefix: str,
) -> str:
    """将补全结果应用到输入文本（模块级纯函数）。

    三阶段定位 orig_prefix 的替换位置：
      1. rfind 全文搜索 — "最后一个匹配"语义天然对齐光标附近输入
      2. start_pos 裁剪回退 — 基于偏移量裁剪尾部后拼接
      3. 返回 repl_text — 兜底全替换
    """
    if orig_prefix:
        idx = text.rfind(orig_prefix)
        if idx >= 0:
            return text[:idx] + repl_text

    if start_pos < 0:
        trim_len = -start_pos
        if trim_len >= len(text):
            return repl_text
        return text[:len(text) - trim_len] + repl_text

    # start_pos > 0：保留供非 CompletionEngine 来源的调用（当前路径未触发）
    if start_pos > 0 and start_pos < len(text):
        return text[:start_pos] + repl_text
    return repl_text
