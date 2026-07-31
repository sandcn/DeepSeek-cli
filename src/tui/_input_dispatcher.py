"""InputDispatcher — TUI 输入事件分发胶水（提取自 _input.py，方向A 步骤1）。

将 Input 上帝类中 render 线程分发逻辑逐行迁移，保持零逻辑改动：
  - read_stdin_once 主循环 / process_events
  - _dispatch_key_event / _handle_tab / _handle_arrow_up / _handle_arrow_down
  - _dismiss_completion / _trigger_auto_completion
  - _do_interrupt（interrupt 回调注入）/ _handle_special_key
  - _parse_escape_sequence 委托 InputParser

InputDispatcher 组合持有 InputIO + InputBufferEditor + InputParser + 全部回调；
``read_stdin_once`` 状态检查（_fd_status / _active / _stop）委托 InputIO。

设计模式: 模板方法（Template Method）——``read_stdin_once()`` 骨架，
``_do_interrupt()`` / ``_handle_special_key()`` 具体步骤。

依赖方向:
  _input.py → _input_dispatcher.py 单向依赖；本模块不得 import _input（避免循环）。

模块级 ``import select`` / ``import os`` 供读取方法使用；可被
``patch("src.tui._input.select.select", ...)`` 经共享 select 模块全局拦截
（与 _input.py 原行为等价）。
"""

from __future__ import annotations

import logging
import os
import select
import threading
from typing import TYPE_CHECKING

from ._input_parser import InputParser, KeyEvent

if TYPE_CHECKING:
    from ._input_io import InputIO
    from ._input_buffer import InputBufferEditor

_logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# InputDispatcher — 事件分发胶水
# ═══════════════════════════════════════════════════════════

class InputDispatcher:
    """输入事件分发胶水。

    组合持有 InputIO（原始 I/O）+ InputBufferEditor（缓冲/历史/队列）
    + InputParser（ANSI 解析）与全部回调，承担 render 线程输入分发。

    由 Input 薄外观委托调用；``read_stdin_once()`` 为模板方法骨架。
    """

    def __init__(
        self,
        io: "InputIO",
        buffer_editor: "InputBufferEditor",
        parser: InputParser,
    ) -> None:
        self._io = io
        self._buffer_editor = buffer_editor
        self._parser = parser

        # ── 回调引用 ──
        self._special_key_callback = None
        self._completion_callback = None
        self._dismiss_completion_callback = None
        self._completion_navigate_callback = None
        self._auto_completion_callback = None
        # ★ interrupt 回调注入（方向A 步骤1）：由 _loop.py _setup_monitor 注入，
        #   None 缺省时 _do_interrupt 记 debug 日志并跳过（保证测试兼容）。
        self._interrupt_callback = None

        # ── Enter 抑制 ──
        self._suppress_enter: bool = False
        self._suppress_enter_lock = threading.Lock()

        # ── 残留 Enter 标记（editmsg 竞态修复） ──
        # editmsg 选择确认 Enter（CR）被抑制后，标记可能存在残留 LF（\n）待丢弃。
        # GIL 原子 bool，与 _suppress_enter 同等无锁访问（不改 API 签名）。
        self._enter_residual_pending: bool = False

        # ── 非可打印字符捕获 ──
        self._captured_input: bytearray = bytearray()
        self._captured_lock = threading.Lock()

    # ═══════════════════════════════════════════════════════
    # 中断与特殊按键处理（render 线程调用）
    # ═══════════════════════════════════════════════════════

    def _do_interrupt(self) -> None:
        """内联中断处理：设置中断标志 + 清空回显 + 请求异步中断（回调注入）。

        在 render 线程中调用（快速路径，由 ``read_stdin_once()`` 直接分发）。

        ★ interrupt 回调注入（方向A 步骤1）：原实现直接调用
        ``src.api.interrupt_async.request_interrupt_async()``（L42 import + L419 调用），
        现改为调用注入回调（``set_interrupt_callback``，由 _loop.py _setup_monitor
        注入 ``lambda: request_interrupt_async()``）；未注入时记 debug 日志并跳过，
        保证测试兼容（不抛异常）。
        """
        if self._io.stop.is_set():
            return
        if not self._buffer_editor.has_queued_input():
            self.reset_and_echo()
        else:
            self._io._flush_stdin_residual()
        self._io.set_interrupted()
        cb = self._interrupt_callback
        if cb is None:
            _logger.debug("_do_interrupt: 未注入 interrupt 回调，跳过异步中断请求")
        else:
            try:
                cb()
            except Exception:
                _logger.debug("_do_interrupt: interrupt 回调异常", exc_info=True)

    def _handle_special_key(self, action: str) -> None:
        """处理特殊按键（Ctrl+G/O/N/R）：直接调用回调并应用结果。

        在 render 线程中调用（由 ``read_stdin_once()`` 直接分发）。
        终端模式切换由回调函数内部直接操作 EscapeMonitor 完成。

        ★ 收敛确认（方向A 步骤1）：vim / editmsg / switch_model 业务已完全由
        ``_special_key_callback``（_special_keys.py 工厂，_loop.py 注入）承担；
        Input 仅保留 result 应用——editmsg 的 reset / set_buffer / handle_chars
        + ``_enter`` 属缓冲编辑职责（InputBufferEditor），保留。
        """
        cb = self._special_key_callback
        if cb is None:
            return
        text = self._buffer_editor.get_current_text()
        try:
            result = cb(action, text)
        except Exception:
            _logger.warning("特殊按键回调异常 (action=%s)", action, exc_info=True)
            return
        if result is not None and result != text:
            if action == 'editmsg':
                self.reset()
                self._buffer_editor.set_buffer(result)
            else:
                self.reset()
                self._buffer_editor.handle_chars(result)
        if action == 'editmsg':
            # editmsg 是用户主动发起的编辑/提交操作（Ctrl+O），
            # 清除 _suppress_enter 确保 _enter() 不被抑制
            self.set_suppress_enter(False)
            self._buffer_editor._enter()

    # ═══════════════════════════════════════════════════════
    # stdin 直接读取（render 线程调用）
    # ═══════════════════════════════════════════════════════

    def read_stdin_once(self) -> bool:
        """单次非阻塞 stdin 读取 + 直接分发（不经过事件队列）。

        Render 线程每帧调用一次。使用 select timeout=0 确保不阻塞渲染帧。
        单次迭代逻辑改为直接分发（不经过 queue.Queue 中间队列）。

        设计模式: 模板方法 — ``read_stdin_once()`` 为骨架，
        保留 ``_do_interrupt()`` / ``_handle_special_key()`` 具体步骤。

        Returns:
            True — 有数据被处理（读取并分发了至少一个输入单元）。
            False — 无数据可读、I/O 未激活、或已停止。
        """
        fd = self._io.fd

        # ── 状态检查（委托 InputIO） ──
        if not self._io.can_read():
            return False

        # ── select 非阻塞读取（timeout=0，不阻塞渲染帧） ──
        try:
            ready, _, _ = select.select([fd], [], [], 0)
        except (ValueError, OSError, TypeError, AttributeError):
            self._io.record_select_error()
            return False

        if not ready:
            return False

        self._io.reset_select_error()

        try:
            raw = os.read(fd, 1)
            if not raw:
                self._io.record_eof()
                return False
            self._io.reset_eof()
        except (ValueError, OSError, TypeError):
            self._io.mark_fd_error()
            return False

        first_byte = raw[0]

        # ── 残留 Enter 后置 LF/CR 丢弃（editmsg 竞态修复） ──
        # 若 _enter_residual_pending 置位（被抑制 Enter 后可能残留 LF），
        # 先清标记；首字节为 LF（0x0a）/ CR（0x0d）时丢弃并返回 True
        # （不触发 _enter()，prefill 保持可编辑）；非 LF/CR 首字节
        # （如用户立即输入字符）不误丢，继续正常分发。
        if self._enter_residual_pending:
            self._enter_residual_pending = False
            if first_byte in (0x0a, 0x0d):
                return True

        # ── ASCII 控制字符分发 ──
        if first_byte < 0x20 or first_byte == 0x7F:
            try:
                event = self._parser.feed_byte(first_byte)
                if event is None:
                    # ESC (0x1b) → 读取完整转义序列
                    event = self._parse_escape_sequence(fd)
                    kind = event.kind
                    if kind in ("escape", "interrupt"):
                        self._do_interrupt()
                    elif kind in (
                        "arrow_up", "arrow_down", "arrow_right", "arrow_left",
                        "home", "end", "delete", "backspace", "char",
                    ):
                        self._dispatch_key_event(event)
                    # unknown / csi_u → 静默忽略
                elif event.kind == "interrupt":
                    self._do_interrupt()
                elif event.kind == "ctrl_key":
                    ch = event.char
                    if ch == '\x07':          # Ctrl+G → vim
                        self._handle_special_key('vim')
                    elif ch == '\x0f':        # Ctrl+O → /editmsg
                        self._handle_special_key('editmsg')
                    elif ch in ('\x0e', '\x12'):  # Ctrl+N/R → 切换模型
                        self._handle_special_key('switch_model')
                    else:
                        self._dispatch_key_event(event)
                else:
                    # enter, tab, backspace, home, end, delete 等 → 直接分发
                    self._dispatch_key_event(event)
            except Exception:
                _logger.warning("控制字符分发异常", exc_info=True)
            return True

        # ── ASCII 可打印字符 ──
        if first_byte < 0x80:
            try:
                paste_text = self._io.try_read_paste(fd, chr(first_byte))
                if len(paste_text) > 1:
                    self._buffer_editor.handle_chars(paste_text)
                    self._trigger_auto_completion()
                else:
                    event = self._parser.feed_byte(first_byte)
                    if event is not None:
                        self._dispatch_key_event(event)
            except Exception:
                _logger.warning("ASCII 可打印字符分发异常", exc_info=True)
            return True

        # ── 多字节 UTF-8 序列 ──
        try:
            ch = self._io.read_utf8_char(fd, first_byte)
            if ch is not None:
                paste_text = self._io.try_read_paste(fd, ch)
                if len(paste_text) > 1:
                    self._buffer_editor.handle_chars(paste_text)
                    self._trigger_auto_completion()
                else:
                    self._dispatch_key_event(
                        KeyEvent(kind='char', char=ch,
                                 raw=ch.encode("utf-8", errors="replace"))
                    )
            else:
                self.capture_bytes(bytes([first_byte]))
        except Exception:
            _logger.warning("多字节 UTF-8 字符分发异常", exc_info=True)
        return True

    # ═══════════════════════════════════════════════════════
    # 事件处理（render 线程调用）
    # ═══════════════════════════════════════════════════════

    def process_events(self) -> None:
        """处理所有输入事件（render 线程调用）。

        循环调用 ``read_stdin_once()`` 直到无可读数据，
        确保一次渲染帧内处理完所有待处理的输入。
        """
        try:
            while self.read_stdin_once():
                pass
        except Exception:
            _logger.warning("process_events 异常", exc_info=True)

    def _dispatch_key_event(self, event: KeyEvent) -> None:
        """根据 KeyEvent.kind 分发到对应的输入处理器。

        Ctrl+G/O/N/R 等 ctrl_key 事件已在 read_stdin_once() 中拦截处理，
        此处分发不会收到 ctrl_key 分支。
        """
        kind = event.kind

        if kind == "enter":
            self._dismiss_completion()
            if not self._suppress_enter:
                self._buffer_editor._enter()
            else:
                # editmsg 选择确认 CR 被抑制后标记残留 LF（\n），
                # 由 read_stdin_once 丢弃，避免 LF 在 prefill 注入后被误提交。
                self._enter_residual_pending = True
        elif kind == "tab":
            self._handle_tab()
        elif kind == "backspace":
            self._dismiss_completion()
            if event.modifier == 1:
                self._buffer_editor._delete_word_left()
            else:
                self._buffer_editor._backspace()
            self._trigger_auto_completion()
        elif kind == "interrupt":
            _logger.debug("_dispatch_key_event: interrupt 事件到达队列（应内联处理）")
        elif kind == "home":
            self._dismiss_completion()
            self._buffer_editor._home()
        elif kind == "end":
            self._dismiss_completion()
            self._buffer_editor._end()
        elif kind == "delete":
            modifier = event.modifier
            if modifier == 0:
                self._dismiss_completion()
                self._buffer_editor._delete()
                self._trigger_auto_completion()
            elif modifier == 1:
                self._dismiss_completion()
                self._buffer_editor._delete_word_left()
                self._trigger_auto_completion()
            elif modifier == 2:
                self._dismiss_completion()
                self._buffer_editor._kill_to_bol()
                self._trigger_auto_completion()
            elif modifier == 3:
                self._dismiss_completion()
                self._buffer_editor._kill_to_eol()
                self._trigger_auto_completion()
        elif kind == "arrow_up":
            self._handle_arrow_up()
        elif kind == "arrow_down":
            self._handle_arrow_down()
        elif kind == "arrow_right":
            if event.modifier == 5:
                self._buffer_editor._word_right()
            else:
                self._buffer_editor._right()
        elif kind == "arrow_left":
            if event.modifier == 5:
                self._buffer_editor._word_left()
            else:
                self._buffer_editor._left()
        elif kind == "unknown":
            self._dismiss_completion()
            if event.raw:
                with self._captured_lock:
                    self._captured_input.append(event.raw[0])
        elif kind == "char":
            if event.char:
                self._buffer_editor.handle_char(event.char)
                self._trigger_auto_completion()

    # ═══════════════════════════════════════════════════════
    # 辅助分发方法
    # ═══════════════════════════════════════════════════════

    def _handle_tab(self) -> None:
        """处理 Tab 键：调用补全回调，失败则插入制表符。"""
        cb = self._completion_callback
        if cb is None:
            self._buffer_editor.handle_char('\t')
            return
        text = self._buffer_editor.get_current_text()
        try:
            result = cb(text)
        except Exception:
            _logger.debug("补全回调异常", exc_info=True)
            result = None
        if result is None:
            self._buffer_editor.handle_char('\t')
        else:
            self._buffer_editor.set_buffer(result)
            self._buffer_editor._echo(result)
            self._trigger_auto_completion()

    def _handle_arrow_up(self) -> None:
        """处理上箭头：补全弹窗可见时仅移动高亮，否则历史浏览。"""
        cb = self._completion_navigate_callback
        if cb is not None:
            try:
                text = self._buffer_editor.get_current_text()
                result = cb(-1, text)
            except Exception:
                _logger.debug("补全导航回调异常", exc_info=True)
                result = None
            if result is not None:
                if result != text:
                    self._buffer_editor.set_buffer(result)
                    self._buffer_editor._echo(result)
                    self._trigger_auto_completion()
                return
        self._buffer_editor._up()

    def _handle_arrow_down(self) -> None:
        """处理下箭头：补全弹窗可见时仅移动高亮，否则历史浏览。"""
        cb = self._completion_navigate_callback
        if cb is not None:
            try:
                text = self._buffer_editor.get_current_text()
                result = cb(1, text)
            except Exception:
                _logger.debug("补全导航回调异常", exc_info=True)
                result = None
            if result is not None:
                if result != text:
                    self._buffer_editor.set_buffer(result)
                    self._buffer_editor._echo(result)
                    self._trigger_auto_completion()
                return
        self._buffer_editor._down()

    def _dismiss_completion(self) -> None:
        """如果补全弹窗可见，关闭它。"""
        cb = self._dismiss_completion_callback
        if cb is not None:
            try:
                cb()
            except Exception:
                _logger.debug("关闭补全回调异常", exc_info=True)

    def _trigger_auto_completion(self) -> None:
        """获取当前文本并调用自动补全回调。"""
        cb = self._auto_completion_callback
        if cb is None:
            return
        text = self._buffer_editor.get_current_text()
        try:
            cb(text)
        except Exception:
            _logger.debug("自动补全回调异常", exc_info=True)

    # ═══════════════════════════════════════════════════════
    # 解析方法（委托 InputParser → _input_parser.py）
    # ═══════════════════════════════════════════════════════

    def _parse_escape_sequence(self, fd: int) -> KeyEvent:
        """读取并解析 ESC 转义序列（含 I/O，委托 InputParser）。"""
        return self._parser._parse_escape_sequence(fd)

    # ═══════════════════════════════════════════════════════
    # 缓冲重置辅助
    # ═══════════════════════════════════════════════════════

    def reset(self) -> None:
        """清空缓冲/队列状态 + 清除中断标志（与 _input.py 原 reset 语义等价）。"""
        self._io.clear_interrupted()
        self._buffer_editor.reset()

    def reset_and_echo(self) -> None:
        """重置缓冲区并回显空字符串（清空输入行视觉）。"""
        self.reset()
        self._buffer_editor._echo("")

    # ═══════════════════════════════════════════════════════
    # 回调接口
    # ═══════════════════════════════════════════════════════

    def set_special_key_callback(self, cb) -> None:
        """设置特殊按键回调（Ctrl+G/O/N/R）。

        cb 签名: (action: str, current_text: str) -> str | None
        """
        self._special_key_callback = cb

    def set_completion_callback(self, cb) -> None:
        """设置 Tab 补全回调。

        cb 签名: (text: str) -> str | None
        """
        self._completion_callback = cb

    def set_dismiss_completion_callback(self, cb) -> None:
        """设置补全弹窗关闭回调。

        cb 签名: () -> None
        """
        self._dismiss_completion_callback = cb

    def set_completion_navigate_callback(self, cb) -> None:
        """设置补全弹窗上下导航回调。

        cb 签名: (delta: int, text: str) -> str | None
        """
        self._completion_navigate_callback = cb

    def set_auto_completion_callback(self, cb) -> None:
        """设置自动补全回调。

        cb 签名: (text: str) -> None
        """
        self._auto_completion_callback = cb

    def set_interrupt_callback(self, cb) -> None:
        """设置中断回调（方向A 步骤1 注入点）。

        cb 签名: () -> None
        None 缺省时 ``_do_interrupt`` 记 debug 日志并跳过（测试兼容）。
        """
        self._interrupt_callback = cb

    def set_suppress_enter(self, suppress: bool) -> None:
        """设置 Enter 抑制标志（用于 editmsg 消息选择期间）。

        当 suppress=True 时，_dispatch_key_event 中的 Enter 分支
        将跳过 _enter() 调用，防止选择确认 Enter 被误提交为输入。

        线程安全：使用 _suppress_enter_lock 保护。
        """
        with self._suppress_enter_lock:
            self._suppress_enter = suppress
            # 防单 CR 终端：恢复 Enter 时清除残留标记，避免误丢弃用户后续回车。
            # suppress=True 时不清标记（保留至 LF 被处理或恢复 False）。
            if not suppress:
                self._enter_residual_pending = False

    def get_suppress_enter(self) -> bool:
        """获取当前 Enter 抑制状态。线程安全。"""
        with self._suppress_enter_lock:
            return self._suppress_enter

    # ═══════════════════════════════════════════════════════
    # 便捷方法
    # ═══════════════════════════════════════════════════════

    def capture_bytes(self, data: bytes) -> None:
        """追加原始字节到捕获缓冲区。线程安全。"""
        with self._captured_lock:
            self._captured_input.extend(data)

    def drain_captured(self) -> str:
        """排出并返回捕获的非可打印字符。"""
        with self._captured_lock:
            data = bytes(self._captured_input).decode("utf-8", errors="replace")
            self._captured_input.clear()
        return data


__all__ = ["InputDispatcher"]
