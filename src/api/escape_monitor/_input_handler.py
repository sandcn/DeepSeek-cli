"""StreamInputHandler 类 — InputBuffer 的薄委托层。

退化为 InputBuffer 的适配层，保留所有公开方法签名不变，
内部委托到 InputBuffer。非可打印字符捕获逻辑保留在本层。

向后兼容 EscapeMonitor 现有调用方。
"""

from __future__ import annotations

import threading
import logging

from ...tui.input._buffer import InputBuffer

_logger = logging.getLogger(__name__)


class StreamInputHandler:
    """InputBuffer 薄委托层 — 保持向后兼容的公开接口。

    非可打印字符捕获（_captured_input / _captured_lock）保留在本层，
    输入缓冲/光标/历史等核心逻辑委托到 InputBuffer。
    所有公开方法签名与旧实现完全一致。
    """

    def __init__(
        self,
        input_buffer: InputBuffer,
        captured_input: bytearray,
        captured_lock: threading.Lock,
    ):
        self._buf = input_buffer
        # ── 非可打印字符捕获（保留在 EscapeMonitor 层） ──
        self._captured_input = captured_input
        self._captured_lock = captured_lock

    # ── 公开接口（委托 InputBuffer） ──────────────────────

    def handle_char(self, ch: str) -> None:
        """处理流式输入字符：非可打印→捕获，可打印→委托 InputBuffer。

        非可打印字符捕获逻辑保留在本层（EscapeMonitor 负责），
        可打印字符直接委托 InputBuffer.handle_char。
        """
        if not (ch.isprintable() or ch in (' ', '\t', '\n')):
            with self._captured_lock:
                self._captured_input.extend(ch.encode("utf-8", errors="replace"))
            return
        self._buf.handle_char(ch)

    def handle_chars(self, text: str) -> None:
        """批量处理多个字符（粘贴/预填场景）。委托 InputBuffer。"""
        self._buf.handle_chars(text)

    def get_queued_input(self) -> str | None:
        """获取排队输入。委托 InputBuffer。"""
        return self._buf.get_queued_input()

    def has_queued_input(self) -> bool:
        """是否有排队输入等待处理。委托 InputBuffer。"""
        return self._buf.has_queued_input()

    def get_current_text(self) -> str:
        """获取当前正在输入的文本。委托 InputBuffer。"""
        return self._buf.get_current_text()

    def reset(self) -> None:
        """清空所有流式输入状态。委托 InputBuffer。"""
        self._buf.reset()

    def drain_all(self) -> tuple[str | None, str]:
        """排出所有流式输入状态。委托 InputBuffer。"""
        return self._buf.drain_all()

    def set_echo_callback(self, callback) -> None:
        """设置流式输入回显回调。委托 InputBuffer。

        callback 签名: (display_text: str, cursor_pos: int) -> None
        """
        self._buf.set_echo_callback(callback)

    def set_buffer(self, text: str) -> None:
        """设置缓冲区文本（用于预填）。委托 InputBuffer。"""
        self._buf.set_buffer(text)

    def load_history(self) -> None:
        """加载历史文件。委托 InputBuffer。"""
        self._buf.load_history()

    # ── 内部方法（由 EscapeMonitor._monitor_* 调用） ──────

    def _echo(self, text: str) -> None:
        """调用回显回调。委托 InputBuffer._echo。

        InputBuffer._echo 内部自动处理历史指示器追加和光标位置获取。
        """
        self._buf._echo(text)

    def _backspace(self) -> None:
        self._buf._backspace()

    def _left(self) -> None:
        self._buf._left()

    def _right(self) -> None:
        self._buf._right()

    def _enter(self) -> None:
        self._buf._enter()

    def _home(self) -> None:
        self._buf._home()

    def _end(self) -> None:
        self._buf._end()

    def _word_left(self) -> None:
        self._buf._word_left()

    def _word_right(self) -> None:
        self._buf._word_right()

    def _up(self) -> None:
        self._buf._up()

    def _down(self) -> None:
        self._buf._down()

    def _delete(self) -> None:
        self._buf._delete()

    def _delete_word_left(self) -> None:
        self._buf._delete_word_left()

    def _kill_to_bol(self) -> None:
        self._buf._kill_to_bol()

    def _kill_to_eol(self) -> None:
        self._buf._kill_to_eol()
