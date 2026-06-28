"""StreamingMarkdown — 流式 Markdown 渲染组件。

TuiComponent 子类，接收逐块文本，累积缓存后通过 render_markdown
增量渲染为 ANSI 文本，配合可选光标闪烁。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import TuiComponent, _get_terminal_width

if TYPE_CHECKING:
    from ..vdom.vnode import VNode


class StreamingMarkdown(TuiComponent):
    """流式 Markdown 渲染组件。

    封装了 Markdown 文本的累积缓存和增量渲染逻辑。
    每帧仅对新增部分调用 render_markdown，避免 O(n²) 重复渲染。

    Attributes:
        _pending_text: 累积的原始 Markdown 文本。
        _last_rendered_len: 上次渲染时的文本长度（增量边界）。
        _cached_rendered: 已渲染的 ANSI 文本缓存。
        _done: 是否已标记完成。
        _show_cursor: 是否显示末尾光标。
        _cursor_char: 光标字符。
    """

    def __init__(self, show_cursor: bool = True, cursor_char: str = "▊"):
        """初始化流式 Markdown 组件。

        Args:
            show_cursor: 是否在未完成时显示光标闪烁。
            cursor_char: 光标字符，默认 "▊"。
        """
        super().__init__()
        self._pending_text: str = ""
        self._last_rendered_len: int = 0
        self._cached_rendered: str = ""
        self._done: bool = False
        self._show_cursor: bool = show_cursor
        self._cursor_char: str = cursor_char

    # ── 写入接口 ──

    def write(self, text: str) -> None:
        """追加文本块到累积缓冲区。

        Args:
            text: 原始 Markdown 文本块。
        """
        if self._done:
            return
        self._pending_text += text

    def flush_partial(self, frame: int) -> str:
        """增量渲染：仅对上次渲染后新增的文本调用 render_markdown。

        策略：
        1. 若文本未增长，直接返回缓存（含可选光标）。
        2. 若文本增长，提取增量部分 → render_markdown(delta)。
        3. 增量渲染结果追加到缓存。

        Args:
            frame: 当前动画帧号（用于光标闪烁，偶数帧显示光标）。

        Returns:
            已渲染的 ANSI 文本字符串（含可选闪烁光标）。
        """
        from ..infrastructure.markdown_renderer import render_markdown

        cur_len = len(self._pending_text)
        if cur_len == self._last_rendered_len:
            # 无新文本，返回缓存 + 光标
            return self._apply_cursor(frame)

        # 文本增长 → 增量渲染新增部分
        if cur_len > self._last_rendered_len:
            delta = self._pending_text[self._last_rendered_len:]
            try:
                term_w = _get_terminal_width()
            except Exception:
                term_w = 80
            delta_rendered = render_markdown(delta, width=term_w)
            self._cached_rendered += delta_rendered
            self._last_rendered_len = cur_len

        return self._apply_cursor(frame)

    def _apply_cursor(self, frame: int) -> str:
        """在渲染输出末尾添加闪烁光标（若启用且未完成）。

        Args:
            frame: 当前帧号。

        Returns:
            含光标的 ANSI 字符串。
        """
        result = self._cached_rendered
        if self._show_cursor and not self._done:
            if frame % 2 == 0:
                result += self._cursor_char
        return result

    def mark_done(self) -> None:
        """标记流式写入完成，停止光标闪烁。
        
        在标记完成前，强制刷新所有未渲染的缓冲内容，
        确保最后几个 token 不会丢失。
        """
        # 强制渲染所有剩余未渲染内容
        cur_len = len(self._pending_text)
        if cur_len > self._last_rendered_len:
            from ..infrastructure.markdown_renderer import render_markdown
            try:
                from .base import _get_terminal_width
                term_w = _get_terminal_width()
            except Exception:
                term_w = 80
            delta = self._pending_text[self._last_rendered_len:]
            delta_rendered = render_markdown(delta, width=term_w)
            self._cached_rendered += delta_rendered
            self._last_rendered_len = cur_len
        self._done = True

    def reset(self) -> None:
        """重置所有内部状态（用于组件复用）。"""
        self._pending_text = ""
        self._last_rendered_len = 0
        self._cached_rendered = ""
        self._done = False

    # ── 基类方法 ──

    @property
    def key(self) -> str:
        return "streaming_markdown"

    def update(self, props: dict) -> bool:
        changed = False
        if "done" in props and props["done"] != self._done:
            self._done = props["done"]
            changed = True
        return changed

    def render_vnode(self) -> "VNode":
        from ..vdom.vnode import VNode
        return VNode(
            type="streaming_markdown",
            key=self.key,
            props={
                "text": self._cached_rendered,
                "done": self._done,
                "pending_len": len(self._pending_text),
            },
        )

    def render(self) -> str:
        return self._cached_rendered
