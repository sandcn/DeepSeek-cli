"""
单击 Esc 中断所有任务。

在模型执行期间启动后台线程监听键盘，按一次 Esc（或 Ctrl+C）
会设置本地 interrupted 标志并触发全局中断信号，让所有任务停止。

状态机设计（简化版，消除原 4 个同步原语导致的死锁风险）：

  停止 (STOP)
    │
    ├── _stop.set() → 线程退出 loop → finally 恢复终端 → 结束
    │
   活跃 (ACTIVE)
    │
    ├── pause()    → clear _active → 恢复终端（释放 stdin）
    │                  线程检测到 _active 被 clear →
    │                  恢复终端 → 等待 _active 重新 set
    │
    ├── resume()   → set _active → 线程重新挂接终端 → 继续监听
    │
    └── Esc/Ctrl+C → _do_interrupt() → 设置全局中断信号

同步原语简化：
  - 原 4 个（_lock, _stop, _paused, _pause_event）→ 2 个（_lock, _stop, _active）
  - _interrupted 保留为 threading.Event（线程安全布尔）
  - _active 替代 _paused + _pause_event，统一表示"是否活跃监听"
"""

from __future__ import annotations

import os
import sys
import time
import threading
import logging
from pathlib import Path
from .interrupt_async import request_interrupt_async
from ..config.defaults import INPUT_HISTORY_FILE, INPUT_DRAFT_FILE

_logger = logging.getLogger(__name__)

# 监控配置
MONITOR_JOIN_TIMEOUT = 1.0    # 线程 join 超时时间（秒）
UNIX_SELECT_TIMEOUT = 0.1     # Unix select 超时时间（秒）
WINDOWS_POLL_INTERVAL = 0.05  # Windows 轮询间隔（秒）
_POLL_INTERVAL = 0.1          # ESC序列检测等待超时（秒）— 100ms 确保 ANSI 序列（如 ↑↓箭头）有充足时间到达

# 全局活跃实例（供其他模块暂停/恢复）
_active_monitor = None
_active_monitor_lock = threading.RLock()


class StreamInputHandler:
    """封装流式输入系统：字符缓冲、历史导航、回显回调。

    EscapeMonitor 通过组合持有本类实例，将流式输入逻辑委托出去。
    线程安全：所有公开方法内部使用 _lock 保护共享状态。
    """

    def __init__(self, captured_input: bytearray, captured_lock: threading.Lock):
        self._buffer: str = ""
        self._cursor_pos: int = 0          # 当前输入光标位置（插入点索引）
        self._submitted_text: str = ""       # Enter 提交的文本，供 get_queued_input 读取
        self._input_ready = threading.Event()
        self._lock = threading.Lock()
        self._echo_callback = None  # Callable[[str], None] | None
        # ── 历史导航（上下箭头浏览输入历史） ──
        self._history: list[str] = []        # 历史行（index=0 为最近一条）
        self._history_idx: int = -1          # -1=非导航模式，>=0=历史索引
        self._saved_input_before_history: str = ""  # 进入历史导航前的原始输入
        # ── 输入草稿持久化（实时保存缓冲区到文件，崩溃/重启后恢复） ──
        self._draft_path = INPUT_DRAFT_FILE
        self._last_draft_save = 0.0       # 上次保存时间戳（节流用）
        self._draft_throttle = 0.3        # 至少间隔 300ms 写一次磁盘

        # ── 非可打印字符捕获（回退到 EscapeMonitor 的 _captured_input） ──
        self._captured_input = captured_input
        self._captured_lock = captured_lock

    # ── 公开接口 ──────────────────────────────────────────

    def handle_char(self, ch: str) -> None:
        """处理流式输入字符：插入到缓冲区光标位置并回显。

        如果当前处于历史导航模式，先退出导航（保留当前行作为新基线），
        再插入字符。
        """
        # 过滤控制字符（保留可打印字符和常见空白）
        # \n 通过：Shift+Enter/Alt+Enter 插入换行走此路径
        if not (ch.isprintable() or ch in (' ', '\t', '\n')):
            # 不可打印的控制字符 → 捕获到 _captured_input
            with self._captured_lock:
                self._captured_input.extend(ch.encode("utf-8", errors="replace"))
            return
        with self._lock:
            if self._history_idx >= 0:
                # 用户在历史行上开始编辑 → 退出历史导航
                self._history_idx = -1
            # 在光标位置插入字符
            self._buffer = (
                self._buffer[:self._cursor_pos]
                + ch
                + self._buffer[self._cursor_pos:]
            )
            self._cursor_pos += len(ch)
            text = self._buffer
        self._echo(text)

    def handle_chars(self, text: str) -> None:
        """批量处理多个字符（粘贴/预填场景），只在全部插入后触发一次回显。

        比逐字符调用 handle_char 更高效：锁竞争从 N 次降为 1 次，
        回显回调从 N 次降为 1 次（对底部栏 refresh 性能至关重要——
        大段粘贴时避免频繁 ANSI I/O 导致的卡顿）。

        粘贴文本中的控制字符（\\n/\\r/\\t 等）作为普通可打印字符
        插入缓冲区，不会触发 Enter 提交或退格等操作，保持粘贴完整性。

        线程安全。
        """
        with self._lock:
            if self._history_idx >= 0:
                self._history_idx = -1
            self._buffer = (
                self._buffer[:self._cursor_pos]
                + text
                + self._buffer[self._cursor_pos:]
            )
            self._cursor_pos += len(text)
            result = self._buffer
        self._echo(result)

    def get_queued_input(self) -> str | None:
        """获取排队输入（用户在 LLM 生成期间按 Enter 提交的文本）。

        返回 None 表示无排队输入。线程安全。
        读取后清空 _submitted_text 和 _input_ready 标志。
        """
        if not self._input_ready.is_set():
            return None
        with self._lock:
            text = self._submitted_text
            self._submitted_text = ""
            self._input_ready.clear()
        return text if text else None

    def has_queued_input(self) -> bool:
        """是否有排队输入等待处理。"""
        return self._input_ready.is_set()

    def get_current_text(self) -> str:
        """获取当前正在输入的文本（不消费）。线程安全。"""
        with self._lock:
            return self._buffer

    def reset(self) -> None:
        """清空所有流式输入状态（缓冲区、提交文本、历史导航）。线程安全。"""
        with self._lock:
            self._buffer = ""
            self._cursor_pos = 0
            self._submitted_text = ""
            self._input_ready.clear()
            self._history_idx = -1
            self._saved_input_before_history = ""

    def drain_all(self) -> tuple[str | None, str]:
        """排出所有流式输入状态：返回 (submitted_text, buffer_text)。

        submitted_text: 如果Enter被按下则返回提交文本（消费），否则None。
        buffer_text: 当前缓冲区文本（消费）。
        调用后重置所有状态，线程安全。

        同时清除草稿文件（文本已被调用方消费）。
        """
        with self._lock:
            submitted = self._submitted_text if self._input_ready.is_set() else None
            buffer_text = self._buffer
            self._submitted_text = ""
            self._buffer = ""
            self._cursor_pos = 0
            self._history_idx = -1
            self._saved_input_before_history = ""
        self._clear_draft()
        return submitted, buffer_text

    def set_echo_callback(self, callback) -> None:
        """设置流式输入回显回调。

        callback 签名: (display_text: str, cursor_pos: int) -> None
          display_text: 带历史指示器的回显文本（如 "hello [历史 2/5]"）
          cursor_pos: 当前输入光标位置（用于光标定位）
        在 monitor 线程中调用，应保证线程安全。
        """
        self._echo_callback = callback

    def set_buffer(self, text: str) -> None:
        """设置缓冲区文本（用于预填），光标移到末尾。线程安全。"""
        with self._lock:
            self._buffer = text
            self._cursor_pos = len(text)
            self._history_idx = -1

    # ── 输入草稿持久化 ──────────────────────────────────

    def _save_draft(self, text: str) -> None:
        """实时保存当前输入文本到草稿文件（节流）。

        以 _draft_throttle 秒为间隔节流磁盘写入，避免每字符 I/O。
        仅在 text 非空时保存（空文本表示已提交/清空，不应覆盖草稿）。
        """
        now = time.time()
        if now - self._last_draft_save < self._draft_throttle:
            return
        self._last_draft_save = now
        try:
            self._draft_path.parent.mkdir(parents=True, exist_ok=True)
            self._draft_path.write_text(text, encoding="utf-8")
        except OSError:
            _logger.debug("草稿文件保存失败: %s", self._draft_path, exc_info=True)

    def _clear_draft(self) -> None:
        """提交后删除草稿文件（文本已进入输入历史，无需继续保存）。"""
        try:
            self._draft_path.unlink(missing_ok=True)
        except OSError:
            _logger.debug("草稿文件清除失败: %s", self._draft_path, exc_info=True)

    def _load_draft(self) -> str:
        """加载上次未提交的输入草稿（崩溃/重启后恢复）。"""
        try:
            return self._draft_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    @staticmethod
    def _unescape(line: str) -> str:
        """将文件中转义的 \\n 还原为真实换行符。

        只还原字面 \\n（反斜杠+n），不处理其他转义序列。

        注意：用户输入的字面反斜杠+n 序列（如输入 "hello\\\\nworld"）
        会在写入时被保留为 "hello\\\\nworld"，读取时会被还原为含真实换行符的
        "hello\\nworld"。这是所有基于行的文件格式在转义换行时的固有限制，
        对历史浏览场景影响可接受——用户更关注内容而非精确的转义格式。
        """
        return line.replace("\\n", "\n")

    def load_history(self) -> None:
        """从 INPUT_HISTORY_FILE 加载历史行。

        文件每行一条记录，记录中的 \\n 为转义后的字面 \\n（兼容逐行存储）。
        读取时还原 \\n 到真实换行符。

        兼容旧格式（\\n 未转义的历史文件）。
        限制最多 1000 条防内存膨胀。
        加载完成后恢复上次未提交的输入草稿（崩溃/重启后恢复）。
        """
        try:
            raw = INPUT_HISTORY_FILE.read_text(encoding="utf-8", errors="replace")
        except (OSError, FileNotFoundError):
            # 文件不存在时保留内存中已有的历史，不清空
            return

        seen: set[str] = set()
        unique: list[str] = []
        for line in raw.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            # 还原转义的 \\n（兼容新旧格式：旧格式无反斜杠+n，还原无影响）
            entry = self._unescape(stripped)
            if entry and entry not in seen:
                seen.add(entry)
                unique.append(entry)
        # 合并文件条目到现有内存历史（防止文件过时时冲掉内存中的最新条目）
        file_entries = unique[:1000]
        if self._history:
            if file_entries:
                existing = set(self._history)
                for entry in reversed(file_entries):
                    if entry not in existing:
                        self._history.append(entry)
                        existing.add(entry)
                self._history = self._history[:1000]
            # 文件为空时保留现有内存历史不变
        else:
            self._history = file_entries

        # 加载上次未提交的输入草稿（崩溃/重启后恢复）
        draft = self._load_draft()
        if draft:
            self._buffer = draft
            self._cursor_pos = len(draft)

    # ── 内部方法（由 EscapeMonitor._monitor_* 调用） ──────

    def _backspace(self) -> None:
        """处理流式输入退格：删除光标前一个字符。

        如果当前处于历史导航模式，先退出导航再退格。
        """
        with self._lock:
            if self._history_idx >= 0:
                self._history_idx = -1
            if self._cursor_pos > 0:
                self._buffer = (
                    self._buffer[:self._cursor_pos - 1]
                    + self._buffer[self._cursor_pos:]
                )
                self._cursor_pos -= 1
            text = self._buffer
        self._echo(text)

    def _left(self) -> None:
        """左箭头：光标左移一格，重绘并定位。"""
        with self._lock:
            if self._cursor_pos > 0:
                self._cursor_pos -= 1
            text = self._buffer
        self._echo(text)

    def _right(self) -> None:
        """右箭头：光标右移一格，重绘并定位。"""
        with self._lock:
            if self._cursor_pos < len(self._buffer):
                self._cursor_pos += 1
            text = self._buffer
        self._echo(text)

    def _enter(self) -> None:
        """处理流式输入 Enter：保存提交文本、标记就绪、清空缓冲区。"""
        with self._lock:
            text = self._buffer
            self._submitted_text = text
            self._buffer = ""
            self._cursor_pos = 0
            self._input_ready.set()
            if self._history_idx >= 0:
                self._history_idx = -1
            # ★ 保存到历史（内存 + 文件）
            self._append_history_locked(text)
        # 清除草稿（文本已提交到历史文件，无需继续保存）
        self._clear_draft()
        # 清空输入行视觉（回显空字符串清除输入行）
        self._echo("")

    def _append_history_locked(self, text: str) -> None:
        """保存输入到历史（需持 _lock）。

        持久化文件每行一条记录，记录中的 \\n 转义为字面 \\n（反斜杠+n），
        兼容 prompt_toolkit FileHistory 的逐行读取格式。
        """
        if not text.strip():
            return
        # 去重：移除旧的出现
        if text in self._history:
            self._history.remove(text)
        self._history.insert(0, text)
        # 限制最多 1000 条
        if len(self._history) > 1000:
            self._history = self._history[:1000]
        # 持久化
        try:
            INPUT_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
            INPUT_HISTORY_FILE.write_text(
                "\n".join(h.replace("\n", "\\n") for h in self._history),
                encoding="utf-8",
            )
        except OSError:
            _logger.warning("历史文件写入失败: %s", INPUT_HISTORY_FILE, exc_info=True)

    def _up(self) -> None:
        """上箭头：多行时间光标上移一行；首行或单行回退到历史浏览。

        多行输入且光标不在首行时：保持同一列偏移，光标上移一行。
        单行输入或多行输入光标已在首行时：浏览上一条历史输入。
        """
        # ── 阶段1：多行光标上移（锁内计算，锁外 _echo） ──
        text = None
        with self._lock:
            if '\n' in self._buffer:
                before_cursor = self._buffer[:self._cursor_pos]
                cur_line = before_cursor.count('\n')
                if cur_line > 0:
                    # 非首行：光标上移一行，保持同一列偏移
                    lines = self._buffer.split('\n')
                    # 当前行起始字符索引
                    pos = sum(len(lines[i]) + 1 for i in range(cur_line))
                    col = self._cursor_pos - pos
                    # 上一行起始 + 列限幅（不超出上行末尾）
                    prev_start = sum(len(lines[i]) + 1 for i in range(cur_line - 1))
                    prev_len = len(lines[cur_line - 1])
                    self._cursor_pos = prev_start + min(col, prev_len)
                    text = self._buffer
        if text is not None:
            self._echo(text)
            return

        # ── 阶段2：单行或首行 → 历史浏览 ──
        with self._lock:
            if not self._history:
                return
            if self._history_idx < 0:
                # 首次进入历史导航 → 保存当前输入
                self._saved_input_before_history = self._buffer
                self._history_idx = 0
            elif self._history_idx < len(self._history) - 1:
                self._history_idx += 1
            # else: 已到最早一条，不再移动
            self._buffer = self._history[self._history_idx]
            self._cursor_pos = len(self._buffer)  # 光标移到末尾
            text = self._buffer
        self._echo(text)

    def _home(self) -> None:
        """Home：光标移到当前逻辑行首。

        对于含 \\n 的多行文本，跳到光标所在逻辑行的行首；
        单行文本跳到缓冲区开头。"""
        with self._lock:
            if '\n' in self._buffer:
                before_cursor = self._buffer[:self._cursor_pos]
                last_nl = before_cursor.rfind('\n')
                self._cursor_pos = last_nl + 1  # \\n 后第一个字符
            else:
                self._cursor_pos = 0
            text = self._buffer
        self._echo(text)

    def _end(self) -> None:
        """End：光标移到当前逻辑行尾。

        对于含 \\n 的多行文本，跳到光标所在逻辑行的行尾（\\n 前）；
        单行文本跳到缓冲区末尾。"""
        with self._lock:
            if '\n' in self._buffer:
                after_cursor = self._buffer[self._cursor_pos:]
                next_nl = after_cursor.find('\n')
                if next_nl >= 0:
                    self._cursor_pos = self._cursor_pos + next_nl
                else:
                    self._cursor_pos = len(self._buffer)
            else:
                self._cursor_pos = len(self._buffer)
            text = self._buffer
        self._echo(text)

    def _word_left(self) -> None:
        """Ctrl+左：向左跳一个词（按字母数字+下划线组成的词边界）。

        先跳过光标左侧的空白/标点，再跳过单词字符，停在词首。"""
        with self._lock:
            if self._cursor_pos <= 0:
                text = self._buffer
            else:
                pos = self._cursor_pos - 1
                # 跳过光标左侧紧邻的空白/标点
                while pos >= 0 and not (
                    self._buffer[pos].isalnum() or self._buffer[pos] == '_'
                ):
                    pos -= 1
                # 跳过单词字符
                while pos >= 0 and (
                    self._buffer[pos].isalnum() or self._buffer[pos] == '_'
                ):
                    pos -= 1
                self._cursor_pos = pos + 1
                text = self._buffer
        self._echo(text)

    def _word_right(self) -> None:
        """Ctrl+右：向右跳一个词（按字母数字+下划线组成的词边界）。

        先跳过当前光标处的空白/标点，再跳过单词字符，
        最后跳过紧邻的非单词字符，停在下一个词首（标准 readline 行为）。

        例如 "hello world" 中从位置 0 按下 Ctrl+→，
        光标跳到位置 6（"world" 的 'w'）。"""
        with self._lock:
            n = len(self._buffer)
            if self._cursor_pos >= n:
                text = self._buffer
            else:
                pos = self._cursor_pos
                # 1. 跳过当前光标处的空白/标点
                while pos < n and not (
                    self._buffer[pos].isalnum() or self._buffer[pos] == '_'
                ):
                    pos += 1
                # 2. 跳过单词字符
                while pos < n and (
                    self._buffer[pos].isalnum() or self._buffer[pos] == '_'
                ):
                    pos += 1
                # 3. 跳过紧邻的非单词字符，到达下一个词首（readline 标准行为）
                while pos < n and not (
                    self._buffer[pos].isalnum() or self._buffer[pos] == '_'
                ):
                    pos += 1
                self._cursor_pos = pos
                text = self._buffer
        self._echo(text)

    def _down(self) -> None:
        """下箭头：多行时间光标下移一行；尾行或单行回退到历史浏览。

        多行输入且光标不在尾行时：保持同一列偏移，光标下移一行。
        单行输入或多行输入光标已在尾行时：浏览下一条历史输入。
        """
        # ── 阶段1：多行光标下移（锁内计算，锁外 _echo） ──
        text = None
        with self._lock:
            if '\n' in self._buffer:
                before_cursor = self._buffer[:self._cursor_pos]
                cur_line = before_cursor.count('\n')
                lines = self._buffer.split('\n')
                if cur_line < len(lines) - 1:
                    # 非尾行：光标下移一行，保持同一列偏移
                    pos = sum(len(lines[i]) + 1 for i in range(cur_line))
                    col = self._cursor_pos - pos
                    # 下一行起始 + 列限幅（不超出下行末尾）
                    next_start = sum(len(lines[i]) + 1 for i in range(cur_line + 1))
                    next_len = len(lines[cur_line + 1])
                    self._cursor_pos = next_start + min(col, next_len)
                    text = self._buffer
        if text is not None:
            self._echo(text)
            return

        # ── 阶段2：尾行或单行 → 历史浏览 ──
        with self._lock:
            if not self._history:
                return
            if self._history_idx < 0:
                # 非导航模式 → 无操作
                return
            elif self._history_idx > 0:
                self._history_idx -= 1
                self._buffer = self._history[self._history_idx]
            else:
                # _history_idx == 0：回到最新 → 退出导航，恢复原始输入
                self._history_idx = -1
                self._buffer = self._saved_input_before_history
            self._cursor_pos = len(self._buffer)  # 光标移到末尾
            text = self._buffer
        self._echo(text)

    def _delete(self) -> None:
        """Del：删除光标后的字符。

        光标在末尾时不操作（无删除内容）。
        在历史浏览模式下先退出导航（与 _backspace 一致）。"""
        with self._lock:
            if self._history_idx >= 0:
                self._history_idx = -1
            n = len(self._buffer)
            if self._cursor_pos < n:
                self._buffer = (
                    self._buffer[:self._cursor_pos]
                    + self._buffer[self._cursor_pos + 1:]
                )
            text = self._buffer
        self._echo(text)

    def _delete_word_left(self) -> None:
        """Ctrl+W / Alt+Backspace：删除光标前的一个词（按字母数字+下划线
        组成的词边界）。

        删除从词首到光标位置的字符，光标移到词首位置。
        在历史浏览模式下先退出导航（与 _backspace 一致）。"""
        with self._lock:
            if self._history_idx >= 0:
                self._history_idx = -1
            if self._cursor_pos <= 0:
                text = self._buffer
            else:
                pos = self._cursor_pos - 1
                # 跳过光标左侧紧邻的空白/标点
                while pos >= 0 and not (
                    self._buffer[pos].isalnum() or self._buffer[pos] == '_'
                ):
                    pos -= 1
                # 跳过单词字符
                while pos >= 0 and (
                    self._buffer[pos].isalnum() or self._buffer[pos] == '_'
                ):
                    pos -= 1
                word_start = pos + 1
                self._buffer = (
                    self._buffer[:word_start]
                    + self._buffer[self._cursor_pos:]
                )
                self._cursor_pos = word_start
                text = self._buffer
        self._echo(text)

    def _kill_to_bol(self) -> None:
        """Ctrl+U：删除光标到当前逻辑行首。

        多行文本时只删除当前逻辑行的光标前部分，保留其他行完整。
        单行文本时删除光标到缓冲区开头。
        在历史浏览模式下先退出导航（与 _backspace 一致）。"""
        with self._lock:
            if self._history_idx >= 0:
                self._history_idx = -1
            if self._cursor_pos <= 0:
                text = self._buffer
            else:
                before_cursor = self._buffer[:self._cursor_pos]
                last_nl = before_cursor.rfind('\n')
                line_start = last_nl + 1  # \n 后的第一个字符
                self._buffer = (
                    self._buffer[:line_start]
                    + self._buffer[self._cursor_pos:]
                )
                self._cursor_pos = line_start
                text = self._buffer
        self._echo(text)

    def _kill_to_eol(self) -> None:
        """Ctrl+K：删除光标到当前逻辑行尾。

        多行文本时只删除当前行光标后的部分，保留其他行完整。
        单行文本时删除光标到缓冲区末尾。
        在历史浏览模式下先退出导航（与 _backspace 一致）。"""
        with self._lock:
            if self._history_idx >= 0:
                self._history_idx = -1
            n = len(self._buffer)
            if self._cursor_pos >= n:
                text = self._buffer
            else:
                after_cursor = self._buffer[self._cursor_pos:]
                next_nl = after_cursor.find('\n')
                if next_nl >= 0:
                    line_end = self._cursor_pos + next_nl
                else:
                    line_end = n
                self._buffer = (
                    self._buffer[:self._cursor_pos]
                    + self._buffer[line_end:]
                )
                # cursor_pos 不变
                text = self._buffer
        self._echo(text)

    @property
    def _history_indicator(self) -> str:
        """历史浏览状态指示器，非导航模式返回空字符串。

        在历史浏览模式（上下箭头）中返回 " [历史 N/M]",
        N 为当前索引（1-based）, M 为总条数。"""
        if self._history_idx < 0:
            return ""
        total = len(self._history)
        current = self._history_idx + 1
        return f" [历史 {current}/{total}]"

    def _echo(self, text: str) -> None:
        """调用回显回调，传入文本和光标位置（在 save/restore 内定位光标）。

        在历史浏览模式下自动追加历史指示器到回显文本（如 " [历史 2/5]"），
        光标位置保持在原始文本末尾，指示器作为视觉辅助不参与光标定位。

        同时将当前输入内容实时保存到草稿文件（节流），确保崩溃/重启后
        可恢复未提交的输入文本。"""
        with self._lock:
            pos = self._cursor_pos
            indicator = self._history_indicator
            if indicator:
                display_text = text + indicator
            else:
                display_text = text
        # ★ 实时保存输入草稿（text 是原始缓冲区内容，不含指示器）
        #   空文本（提交/中断清空后）不保存，以免覆盖有效草稿
        if text:
            self._save_draft(text)
        cb = self._echo_callback
        if cb is not None:
            try:
                cb(display_text, pos)
            except Exception:
                _logger.debug("_echo 回显回调失败", exc_info=True)


class EscapeMonitor:
    """后台监听 Esc 键，用于中断所有任务。

    状态机简化：原 _paused + _pause_event 两个 Event
    合并为单一 _active Event。——消除状态不一致风险
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._interrupted = threading.Event()
        self._stop = threading.Event()
        # _active: set = 活跃监听中, clear = 已暂停（让出 stdin）
        self._active = threading.Event()
        self._active.set()  # 默认活跃
        # ★ P1 修复：_paused_ack 用于 resume() 与 monitor 线程的精确同步，
        #   替代不可靠的 time.sleep(0.05)。monitor 线程在 _wait_while_paused
        #   进入等待前设置此 Event，resume() 等待确认后再恢复活跃。
        self._paused_ack = threading.Event()
        self._paused_ack.set()  # 初始为已确认（未暂停时无需等待）
        # _monitor_ready：monitor 线程完成 cbreak 设置后 set，
        # 供 user_select 等调用方精确同步（替代不可靠的 asyncio.sleep）。
        # 初始 set：未启动时视为 ready（无需等待）。
        self._monitor_ready = threading.Event()
        self._monitor_ready.set()
        self._initial_flush_done = False
        self._thread = None
        self._old_settings = None
        self._saved_original_settings = None  # 永久保存的终端设置副本
        self._captured_input = bytearray()
        self._captured_lock = threading.Lock()
        # ── 流式输入处理器（组合模式） ──
        self._input_handler = StreamInputHandler(self._captured_input, self._captured_lock)
        # ── 特殊按键回调（Ctrl+G/O/N/R） ──
        self._special_key_callback = None
        # ── Tab 补全回调 ──
        self._completion_callback = None       # (text: str) -> str | None
        self._dismiss_completion_callback = None  # () -> None
        self._completion_navigate_callback = None   # (delta: int, text: str) -> str | None
        # ── 自动补全回调（用户输入可打印字符时自动触发） ──
        self._auto_completion_callback = None  # (text: str) -> None

    # ── 公开接口 ──────────────────────────────────────────

    def start(self):
        """开始监听（非阻塞），在执行前调用。"""
        global _active_monitor, _active_monitor_lock
        # 防止重复启动线程
        if self._thread is not None and self._thread.is_alive():
            return
        # 确保全局中断信号已清除
        from .interrupt_async import reset_interrupt_async
        reset_interrupt_async()
        self._interrupted.clear()
        self._stop.clear()
        self._active.set()
        # 清除流式输入状态
        self._input_handler.reset()
        # 加载历史并重置导航状态
        self._input_handler.load_history()
        # ★ 将恢复的输入草稿回显到 UI（用户立即可见上次未提交的文本）
        self._input_handler._echo(self._input_handler.get_current_text())
        self._monitor_ready.clear()  # 重置：等待线程完成 cbreak 设置
        self._thread = threading.Thread(target=self._monitor, daemon=True)
        self._thread.start()
        with _active_monitor_lock:
            _active_monitor = self

    def stop(self):
        """停止监听，恢复终端设置。"""
        global _active_monitor, _active_monitor_lock
        # 先发停止信号，让 monitor 线程尽快退出
        self._stop.set()
        self._active.set()  # 唤醒可能阻塞在 wait() 的线程

        # 清除本地中断标志，避免影响后续会话
        self._interrupted.clear()

        # 同步清除全局 asyncio 中断信号，防止残留
        from .interrupt_async import reset_interrupt_async
        reset_interrupt_async()

        if self._thread:
            self._thread.join(timeout=MONITOR_JOIN_TIMEOUT)
            if self._thread.is_alive():
                _logger.warning("EscapeMonitor 线程 join 超时，已放弃等待")
            self._thread = None
        # 确保终端设置恢复（线程的 finally 可能未执行完）
        self._restore_terminal_settings()
        with _active_monitor_lock:
            if _active_monitor is self:
                _active_monitor = None

    def pause(self):
        """暂停监听（停止读 stdin 并恢复终端设置），用于让出终端控制权。

        调用方（如 user_select）需要独占 stdin 时先 pause，操作完成后 resume。
        pause/resume 配对使用，不可嵌套。

        注意：pause 期间按键未被监听，在此期间按下 Esc 不会触发中断。
        """
        # 设置暂停标志，monitor 线程会在下次循环检测到并进入等待
        self._active.clear()

        # 恢复终端设置，让其他组件（如 prompt_toolkit）接管 stdin
        # ★ 不持 _lock 调用：_restore_terminal_settings 内部已持锁，
        #   避免与 monitor 线程的锁顺序依赖。
        self._restore_terminal_settings()

    def resume(self):
        """恢复监听。

        pause 期间按键未被监听，在此期间按下 Esc 不会触发中断。

        与 pause 对称：清除中断、等待 monitor 线程确认已进入暂停状态、
        恢复活跃标志让线程重新挂接终端。使用 Event 精确同步替代 time.sleep。
        """
        # 清除本地中断标志，因为中断已经处理/不存在
        self._interrupted.clear()
        # 同步清除全局信号，防止 pause 期间收到的中断残留到下一轮
        from .interrupt_async import reset_interrupt_async
        reset_interrupt_async()
        # ★ P1 修复：等待 monitor 线程确认已进入暂停状态，再恢复活跃。
        #   使用 Event 精确同步替代不可靠的 time.sleep(0.05)。
        #   超时 1.0s 作为安全兜底，防止 monitor 线程卡死时永久阻塞。
        self._paused_ack.wait(timeout=1.0)
        self._paused_ack.clear()
        # ★ 恢复 cbreak 模式（_handle_special_key 路径中 pause() 已恢复 cooked 模式，
        #   resume() 必须显式重新进入 cbreak，否则 monitor 循环检测到 _active 已 set
        #   跳过重设路径，终端停留在 cooked 模式导致输入不可用）
        self._apply_monitor_settings()
        # 恢复活跃标志，monitor 线程检测到后重新挂接终端
        self._active.set()

    def drain_captured_input(self) -> str:
        """排出并返回 LLM 生成期间用户键入的非中断字符。

        在线程停止后调用，返回所有非 ESC/Ctrl+C 字符的 UTF-8 解码文本，
        并清空缓冲区。线程安全。
        """
        with self._captured_lock:
            data = bytes(self._captured_input).decode("utf-8", errors="replace")
            self._captured_input.clear()
        return data

    def drain_stream_input(self) -> tuple[str | None, str]:
        """排出流式输入：返回 (queued_input, buffer_text)。

        在 monitor 停止后调用（线程已退出，无并发风险）。
        queued_input: 用户按Enter提交的文本，没有则None。
        buffer_text: 缓冲区中未提交的文本（用户正在输入但未按Enter）。
        调用后清空 StreamInputHandler 内部状态。
        """
        return self._input_handler.drain_all()

    def set_echo_callback(self, callback) -> None:
        """设置流式输入回显回调。薄委托到 StreamInputHandler。

        callback 签名: (text: str) -> None
        在 monitor 线程中调用，应保证线程安全。
        """
        self._input_handler.set_echo_callback(callback)

    def set_special_key_callback(self, callback) -> None:
        """设置特殊按键回调（Ctrl+G/O/N/R）。

        callback 签名: (action: str, current_text: str) -> str | None
          action: 'vim' | 'editmsg' | 'switch_model'
          current_text: 当前缓冲区文本
          返回替换后的文本，None 表示不修改。

        回调在 monitor 暂停后调用（终端已恢复 cooked mode），
        可安全执行 vim 等交互式操作。返回后 monitor 自动恢复。
        """
        self._special_key_callback = callback

    def set_completion_callback(self, callback) -> None:
        """设置 Tab 补全回调。

        callback 签名: (text: str) -> str | None
          text: 当前输入缓冲区文本。
          返回替换后的文本（补全选中项），None 表示无补全结果。

        返回 None 时 Tab 回退为插入制表符。
        返回非 None 时替换整个缓冲区文本。
        """
        self._completion_callback = callback

    def set_dismiss_completion_callback(self, callback) -> None:
        """设置补全弹窗关闭回调。

        callback 签名: () -> None
        在用户按下任何非 Tab 键时调用，用于清除补全弹窗。
        """
        self._dismiss_completion_callback = callback

    def set_completion_navigate_callback(self, callback) -> None:
        """设置补全弹窗上下导航回调。

        callback 签名: (delta: int, text: str) -> str | None
          delta: -1=上, +1=下。
          text: 当前输入缓冲区文本（由 _handle_arrow_up/_handle_arrow_down 传入）。
          返回替换后的文本，None 表示弹窗不可见（回退为正常上下键行为）。
        """
        self._completion_navigate_callback = callback

    def set_auto_completion_callback(self, callback) -> None:
        """设置自动补全回调。

        callback 签名: (text: str) -> None
          text: 当前输入缓冲区文本。
        在用户输入可打印字符时调用，用于自动弹出补全弹窗。
        回调在 monitor 线程中调用，应保证线程安全。
        """
        self._auto_completion_callback = callback

    def set_prefill(self, text: str) -> None:
        """设置预填文本到流式输入缓冲区。线程安全。"""
        self._input_handler.set_buffer(text)
        self._input_handler._echo(text)

    def _pause_for_callback(self) -> None:
        """在 monitor 线程内部暂停监听（不等待 _paused_ack）。

        与 pause() 不同，pause() 依赖 monitor 线程的外部循环进入
        _wait_while_paused() 来设置 _paused_ack，而本方法直接在
        monitor 线程内部调用，不经过 ack 同步路径。直接恢复终端
        cooked mode 后即可安全执行回调。

        调用方保证在 monitor 线程中调用。
        """
        from .interrupt_async import reset_interrupt_async
        self._interrupted.clear()
        reset_interrupt_async()
        self._active.clear()
        # 直接恢复终端 cooked mode，不经过 _wait_while_paused()
        self._restore_terminal_settings()

    def _resume_from_callback(self) -> None:
        """在 monitor 线程内部恢复监听（不等待 _paused_ack）。

        与 _pause_for_callback 配对使用。直接重新挂接终端并恢复
        活跃标志，不经过 ack 等待路径。

        调用方保证在 monitor 线程中调用。
        """
        from .interrupt_async import reset_interrupt_async
        self._interrupted.clear()
        reset_interrupt_async()
        # 直接恢复 cbreak 模式
        self._apply_monitor_settings()
        self._active.set()

    def _handle_special_key(self, action: str) -> None:
        """处理特殊按键（Ctrl+G/O/N/R）：暂停 monitor → 回调 → 恢复 → 更新缓冲区。

        在 monitor 线程中调用。使用 _pause_for_callback()/_resume_from_callback()
        绕过标准的 pause/resume ack 同步路径（该路径依赖 monitor 线程外部循环
        设置 _paused_ack，而本方法已在 monitor 线程内部执行，ack 永远等待超时）。

        恢复终端 cooked mode 后回调可安全运行 vim 等交互式程序。
        回调返回后直接重新挂接终端，无 1 秒 ack 等待延迟。
        """
        cb = self._special_key_callback
        if cb is None:
            return
        text = self._input_handler.get_current_text()
        self._pause_for_callback()
        try:
            result = cb(action, text)
        finally:
            self._resume_from_callback()
        if result is not None and result != text:
            self._input_handler.reset()
            # 使用 handle_chars 批量处理，避免逐字符 O(n²) 插入
            # handle_chars 内部已调用 _echo，无需再显式调用
            self._input_handler.handle_chars(result)

        # ★ Ctrl+O (editmsg)：设置缓冲区后自动提交，直接打开消息编辑界面
        if action == 'editmsg':
            self._input_handler._enter()

    def get_queued_input(self) -> str | None:
        """获取排队输入。薄委托到 StreamInputHandler。"""
        return self._input_handler.get_queued_input()

    def has_queued_input(self) -> bool:
        """是否有排队输入等待处理。薄委托到 StreamInputHandler。"""
        return self._input_handler.has_queued_input()

    def get_current_stream_input(self) -> str:
        """获取当前正在输入的文本。薄委托到 StreamInputHandler。"""
        return self._input_handler.get_current_text()

    def reset_stream_input(self) -> None:
        """清空流式输入缓冲区。薄委托到 StreamInputHandler。"""
        self._input_handler.reset()

    @property
    def interrupted(self):
        return self._interrupted.is_set()

    # ── 内部实现 ──────────────────────────────────────────

    def _do_interrupt(self):
        """设置本地和全局中断信号。"""
        if self._stop.is_set():
            return
        self._interrupted.set()
        # 清除流式输入缓冲区（中断时丢弃未提交的输入）
        self._input_handler.reset()
        self._input_handler._echo("")  # ★ 刷新底部栏显示空输入
        request_interrupt_async()

    def _monitor(self):
        """主监控循环，确保异常时恢复终端设置"""
        try:
            self._monitor_unix()
        except Exception as e:
            _logger.debug("Unix监控方式失败，回退到Windows方式: %s", e)
            try:
                self._monitor_win()
            except Exception as e2:
                _logger.warning("Unix和Windows两种监控方式均失败: %s / %s", e, e2)
                self._restore_terminal_settings()

    def _restore_terminal_settings_impl(self):
        """实际终端设置恢复逻辑（无锁，由调用方保证线程安全）。

        Windows 上 termios 不可用，直接跳过恢复（无需恢复）。
        """
        try:
            import termios
        except ImportError:
            return  # Windows: termios 不可用，无需恢复终端设置
        settings = self._old_settings
        if settings is None:
            settings = self._saved_original_settings
        if settings is not None:
            try:
                termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, settings)
                self._old_settings = None
            except Exception as e:
                _logger.warning("终端设置恢复失败: %s", e)

    def _restore_terminal_settings(self, *, _lock_held: bool = False):
        """确保终端设置恢复（在异常或线程结束时调用），线程安全。

        Args:
            _lock_held: 调用方是否已持有 self._lock。
                        为 False 时自动获取锁（默认）。
        """
        if _lock_held:
            self._restore_terminal_settings_impl()
        else:
            with self._lock:
                self._restore_terminal_settings_impl()

    def _wait_while_paused(self, timeout: float) -> bool:
        """等待直到活跃或停止，返回 True 表示应停止。

        在暂停状态（_active 被 clear）时调用。
        等待期间定期检查 _stop 信号，避免永久阻塞。

        通知 resume() 调用方本线程已进入暂停等待状态。
        """
        # ★ P1 修复：通知 resume() 本线程已进入暂停等待状态，
        #   替代不可靠的 time.sleep(0.05) 同步方式。
        self._paused_ack.set()
        while not self._stop.is_set():
            if self._active.wait(timeout=timeout):
                break
        return self._stop.is_set()

    def _apply_monitor_settings(self) -> None:
        """获取当前终端设置并设置为 cbreak 模式（线程安全）。"""
        import termios
        import tty
        with self._lock:
            try:
                fd = sys.stdin.fileno()
                self._old_settings = termios.tcgetattr(fd)
                tty.setcbreak(fd)
            except Exception as e:
                _logger.warning("设置终端 cbreak 模式失败: %s", e)

    def _flush_stdin_residual(self, max_flush: int = 50) -> None:
        """非阻塞清理 stdin 残留字节（暂停前后积累的逃逸序列等）。"""
        import select
        flushed = 0
        while flushed < max_flush:
            if self._stop.is_set():
                return
            try:
                fd = sys.stdin.fileno()
                ready, _, _ = select.select([fd], [], [], 0.05)
                if not ready:
                    break
                os.read(fd, 1)
                flushed += 1
            except (ValueError, OSError, TypeError, AttributeError):
                break

    def _try_read_paste(self, fd: int, first_chars: str) -> str:
        """检测并读取粘贴内容（退避 select 检测突发字符流）。

        在已读取单个字符后调用，用递增超时 select（1ms→2ms→3ms）
        检测 stdin 上是否有连续快速到达的字符序列。有则一次性批量
        读取所有可用数据并解码。

        人工键入的字符间隔通常 >20ms，三次退避检测都不会读到额外
        数据，直接返回 first_chars（单字符，非粘贴）。
        粘贴的字符间隔 <1ms，三次都能读到数据，进入批量读取路径。

        Args:
            fd: stdin 文件描述符。
            first_chars: 已读取的首个 ASCII 字符/完整 UTF-8 多字节序列。

        Returns:
            first_chars（非粘贴/单字符）或完整的粘贴文本（多字符）。
        """
        import select
        # 退避检测：1ms → 2ms → 3ms，任一未读到数据即判定非粘贴
        for delay in (0.001, 0.002, 0.003):
            try:
                has_more, _, _ = select.select([fd], [], [], delay)
            except (ValueError, OSError, TypeError, AttributeError):
                return first_chars
            if not has_more:
                return first_chars
        # 三次退避都读到数据 → 粘贴模式：批量读取所有可用字节
        extra = b''
        try:
            while True:
                has_more, _, _ = select.select([fd], [], [], 0.01)
                if not has_more:
                    break
                more = os.read(fd, 65536)
                if not more:
                    break
                extra += more
                if len(extra) >= 262144:  # 256KB 安全上限
                    break
        except (ValueError, OSError, TypeError, AttributeError):
            pass
        if not extra:
            return first_chars
        return first_chars + extra.decode("utf-8", errors="replace")

    def _monitor_unix(self):
        """Unix/Cygwin: 用 termios + select 读取原始按键。"""
        import termios
        import select

        fd = sys.stdin.fileno()
        try:
            # 保存初始终端设置
            self._old_settings = termios.tcgetattr(fd)
            self._saved_original_settings = self._old_settings  # 永久保存副本
        except Exception as e:
            _logger.warning("无法获取终端设置，EscapeMonitor 不可用: %s", e)
            self._monitor_ready.set()  # 失败也标记 ready，避免调用方永久阻塞
            return

        # 设置为 cbreak 模式并清空 stdin
        # ★ 使用 try/finally 确保 _monitor_ready 总是被设置，
        #   即使 _apply_monitor_settings 失败。
        try:
            self._apply_monitor_settings()
        finally:
            self._monitor_ready.set()
        # 首次应用设置后（仅在初始启动时）清空 stdin 缓冲区，
        # 避免 tcflush 丢失暂停恢复路径中用户键入的合法字符
        termios.tcflush(fd, termios.TCIFLUSH)
        self._initial_flush_done = True

        try:
            while not self._stop.is_set():
                # ── 暂停状态处理 ──────────────────────────
                if not self._active.is_set():
                    self._restore_terminal_settings()
                    if self._wait_while_paused(UNIX_SELECT_TIMEOUT):
                        return  # _stop 被设置，退出
                    # 恢复监听：重新挂接终端
                    self._apply_monitor_settings()
                    self._flush_stdin_residual()
                    continue  # 回到循环顶部，重新进入正常监听

                # ── 正常监听 ──────────────────────────────
                try:
                    ready, _, _ = select.select([fd], [], [], UNIX_SELECT_TIMEOUT)
                except (ValueError, OSError, TypeError, AttributeError):
                    # stdin 可能已关闭或不可读，跳过本轮
                    time.sleep(UNIX_SELECT_TIMEOUT)
                    continue
                if not ready:
                    continue

                try:
                    raw = os.read(fd, 1)
                    if not raw:
                        continue
                except (ValueError, OSError, TypeError):
                    # stdin 读取失败，跳过本轮
                    continue

                first_byte = raw[0]

                # ── ASCII 控制字符分发 ────────────────────
                if first_byte < 0x20 or first_byte == 0x7F:
                    self._dispatch_control_char(first_byte, raw)
                    continue

                # ── ASCII 可打印字符（单字节，直接处理 + 粘贴检测） ──
                if first_byte < 0x80:
                    paste_text = self._try_read_paste(fd, chr(first_byte))
                    if len(paste_text) > 1:
                        self._input_handler.handle_chars(paste_text)
                    else:
                        self._input_handler.handle_char(paste_text)
                    self._trigger_auto_completion()
                    continue

                # ── 多字节 UTF-8 序列（如中文、日文、韩文等 CJK 字符 + 粘贴检测） ──
                ch = self._read_utf8_char(fd, first_byte)
                if ch is not None:
                    paste_text = self._try_read_paste(fd, ch)
                    if len(paste_text) > 1:
                        self._input_handler.handle_chars(paste_text)
                    else:
                        self._input_handler.handle_char(paste_text)
                    self._trigger_auto_completion()
        finally:
            self._restore_terminal_settings(_lock_held=False)

    def _dispatch_control_char(self, first_byte: int, raw: bytes) -> None:
        """分发 ASCII 控制字符到对应处理器。

        从 _monitor_unix 主循环中提取，封装控制字符（0x00-0x1F / 0x7F）
        的解码和分发逻辑。
        """
        try:
            ch = raw.decode("utf-8", errors="replace")
        except (ValueError, UnicodeDecodeError):
            return
        if ch == '\x1b':
            self._handle_escape()
        elif ch == '\x03':
            self._do_interrupt()
            self._flush_stdin_residual()
        elif ch == '\x07':          # Ctrl+G → vim 编辑
            self._handle_special_key('vim')
        elif ch == '\x0f':          # Ctrl+O → /editmsg
            self._handle_special_key('editmsg')
        elif ch == '\x0e':          # Ctrl+N → 切换模型
            self._handle_special_key('switch_model')
        elif ch == '\x12':          # Ctrl+R → 切换模型（备用，Cygwin 终端会拦截 Ctrl+N）
            self._handle_special_key('switch_model')
            self._handle_special_key('switch_model')
        elif ch == '\x09':          # Tab → 补全
            self._handle_tab()
        elif ch in ('\r', '\n'):  # Enter → 提交
            self._dismiss_completion()
            self._input_handler._enter()
        elif ch in ('\x7f', '\b'):
            self._dismiss_completion()
            self._input_handler._backspace()
            self._trigger_auto_completion()
        elif ch == '\x01':          # Ctrl+A → 行首
            self._dismiss_completion()
            self._input_handler._home()
        elif ch == '\x05':          # Ctrl+E → 行尾
            self._dismiss_completion()
            self._input_handler._end()
        elif ch == '\x17':          # Ctrl+W → 删除前一个词
            self._dismiss_completion()
            self._input_handler._delete_word_left()
            self._trigger_auto_completion()
        elif ch == '\x15':          # Ctrl+U → 删除到行首
            self._dismiss_completion()
            self._input_handler._kill_to_bol()
            self._trigger_auto_completion()
        elif ch == '\x0b':          # Ctrl+K → 删除到行尾
            self._dismiss_completion()
            self._input_handler._kill_to_eol()
            self._trigger_auto_completion()
        else:
            self._dismiss_completion()
            # 其他控制字符 → 旧行为：捕获到 _captured_input
            with self._captured_lock:
                self._captured_input.append(first_byte)

    def _trigger_auto_completion(self) -> None:
        """获取当前文本并调用自动补全回调。

        在用户输入可打印字符后调用，自动弹出补全弹窗。
        线程安全：回调内部使用 try/except 包围。
        """
        cb = self._auto_completion_callback
        if cb is None:
            return
        text = self._input_handler.get_current_text()
        try:
            cb(text)
        except Exception:
            _logger.debug("自动补全回调异常", exc_info=True)

    def _handle_tab(self) -> None:
        """处理 Tab 键：调用补全回调，失败则插入制表符。"""
        cb = self._completion_callback
        if cb is None:
            self._input_handler.handle_char('\t')
            return
        text = self._input_handler.get_current_text()
        try:
            result = cb(text)
        except Exception:
            _logger.debug("补全回调异常", exc_info=True)
            result = None
        if result is None:
            # 无补全结果 → 插入制表符
            self._input_handler.handle_char('\t')
        else:
            # 用补全结果替换整个缓冲区
            self._input_handler.set_buffer(result)
            self._input_handler._echo(result)
            self._trigger_auto_completion()

    def _dismiss_completion(self) -> None:
        """如果补全弹窗可见，关闭它。"""
        cb = self._dismiss_completion_callback
        if cb is not None:
            try:
                cb()
            except Exception:
                _logger.debug("关闭补全回调异常", exc_info=True)

    def _handle_arrow_up(self) -> None:
        """处理上箭头：补全弹窗可见时导航，否则历史浏览。"""
        cb = self._completion_navigate_callback
        if cb is not None:
            try:
                text = self._input_handler.get_current_text()
                result = cb(-1, text)
            except Exception:
                _logger.debug("补全导航回调异常", exc_info=True)
                result = None
            if result is not None:
                self._input_handler.set_buffer(result)
                self._input_handler._echo(result)
                self._trigger_auto_completion()
                return
        self._input_handler._up()

    def _handle_arrow_down(self) -> None:
        """处理下箭头：补全弹窗可见时导航，否则历史浏览。"""
        cb = self._completion_navigate_callback
        if cb is not None:
            try:
                text = self._input_handler.get_current_text()
                result = cb(1, text)
            except Exception:
                _logger.debug("补全导航回调异常", exc_info=True)
                result = None
            if result is not None:
                self._input_handler.set_buffer(result)
                self._input_handler._echo(result)
                self._trigger_auto_completion()
                return
        self._input_handler._down()

    def _read_utf8_char(self, fd: int, first_byte: int) -> str | None:
        """读取完整的多字节 UTF-8 字符序列。

        从 _monitor_unix 主循环中提取，封装 UTF-8 多字节序列的
        字节数判断、续字节读取和解码逻辑。

        Args:
            fd: stdin 文件描述符。
            first_byte: 已读取的首字节（高位为 1，即 >= 0x80）。

        Returns:
            解码后的 Unicode 字符，或 None（无效/不完整序列，字节已捕获）。
        """
        import select
        # 根据首字节确定该字符的总字节数
        if (first_byte & 0xE0) == 0xC0:
            total_bytes = 2
        elif (first_byte & 0xF0) == 0xE0:
            total_bytes = 3
        elif (first_byte & 0xF8) == 0xF0:
            total_bytes = 4
        else:
            # 无效的 UTF-8 首字节（续字节单独出现）→ 原始捕获
            with self._captured_lock:
                self._captured_input.append(first_byte)
            return None

        # 读取剩余续字节（with short timeout）
        buf = bytes([first_byte])
        for _ in range(total_bytes - 1):
            try:
                has_data, _, _ = select.select(
                    [fd], [], [], UNIX_SELECT_TIMEOUT / 2)
            except (ValueError, OSError, TypeError, AttributeError):
                break
            if not has_data:
                break
            try:
                more = os.read(fd, 1)
                if not more:
                    break
                buf += more
            except (ValueError, OSError, TypeError):
                break

        # 解码完整序列（可能因超时不完整，用 errors="replace" 容错）
        try:
            return buf.decode("utf-8")
        except UnicodeDecodeError:
            # 不完整/无效序列 → 原始字节捕获
            with self._captured_lock:
                self._captured_input.extend(buf)
            return None

    def _handle_escape(self):
        """处理 Esc 按键，区分单 Esc 和 ANSI 转义序列。

        必须使用 os.read(fd, 1) 而非 sys.stdin.read(1) 来逐字节读取，
        否则 sys.stdin 的 BufferedReader 会一次性读取多字节到 Python
        缓冲区，导致后续 select 检查 OS 级 fd 时误判为"无更多数据"，
        从而将上下箭头（\\x1b[A）等 ANSI 序列误当作单 ESC 中断。
        """
        import select
        fd = sys.stdin.fileno()
        try:
            has_more, _, _ = select.select([fd], [], [], _POLL_INTERVAL)
        except (ValueError, OSError, TypeError, AttributeError):
            # stdin 可能已关闭或不可读，视为单 Esc
            self._do_interrupt()
            return
        if not has_more:
            self._do_interrupt()
            self._flush_stdin_residual()
            return
        try:
            raw = os.read(fd, 1)
            if not raw:
                self._do_interrupt()
                return
            next_ch = raw.decode("utf-8", errors="replace")
        except (ValueError, OSError, TypeError):
            self._do_interrupt()
            return
        if next_ch == '[':
            # CSI 序列：完整解析参数 + 终结符，支持：
            #   - 简单 CSI: \x1b[A (上箭头), \x1b[H (Home), \x1b[F (End)
            #   - 功能键:  \x1b[1~ (Home), \x1b[4~ (End)
            #   - 修饰符:  \x1b[1;5D (Ctrl+左), \x1b[1;5C (Ctrl+右)
            #   - CSI u:   \x1b[13;2u (Shift+Enter), \x1b[13;3u (Alt+Enter)
            params: list[int] = []
            current = ""
            terminator: str | None = None
            try:
                while select.select([fd], [], [], 0.01)[0]:
                    raw_c = os.read(fd, 1)
                    if not raw_c:
                        break
                    c = raw_c.decode("utf-8", errors="replace")
                    if c == ';':
                        try:
                            params.append(int(current) if current else 0)
                        except ValueError:
                            params.append(0)
                        current = ""
                    elif c.isdigit():
                        current += c
                    elif c.isalpha() or c == '~':
                        if current:
                            try:
                                params.append(int(current))
                            except ValueError:
                                params.append(0)
                        terminator = c
                        break
            except (ValueError, OSError, TypeError):
                pass

            if terminator is None:
                pass  # 序列不完整，静默忽略
            elif terminator == 'u':
                # CSI u 模式: \x1b[<keycode>;<modifier>u
                keycode = params[0] if len(params) >= 1 else 0
                modifier = params[1] if len(params) >= 2 else 1
                if keycode == 13 and modifier in (2, 3, 5):
                    # Shift+Enter(2) / Alt+Enter(3) / Ctrl+Enter(5) → 插入换行
                    self._input_handler.handle_char('\n')
            elif terminator == '~':
                # 功能键序列: \x1b[N~ (N=1/7=Home, 4/8=End)
                p = params[0] if params else 0
                if p in (1, 7):
                    self._input_handler._home()
                elif p in (3,):
                    # Delete 键：删除光标后字符
                    self._dismiss_completion()
                    self._input_handler._delete()
                    self._trigger_auto_completion()
                elif p in (4, 8):
                    self._input_handler._end()
            elif terminator == 'H':
                # Home (\x1b[H)
                self._input_handler._home()
            elif terminator == 'F':
                # End (\x1b[F)
                self._input_handler._end()
            elif terminator == 'C':
                # 右箭头 或 Ctrl+右
                if len(params) >= 2 and params[1] == 5:
                    self._input_handler._word_right()
                else:
                    self._input_handler._right()
            elif terminator == 'D':
                # 左箭头 或 Ctrl+左
                if len(params) >= 2 and params[1] == 5:
                    self._input_handler._word_left()
                else:
                    self._input_handler._left()
            elif terminator == 'A':
                self._handle_arrow_up()
            elif terminator == 'B':
                self._handle_arrow_down()
        elif next_ch == 'O':
            # ESC O 序列（F1-F4）：跳过功能键标识
            try:
                if select.select([fd], [], [], _POLL_INTERVAL)[0]:
                    os.read(fd, 1)
            except (ValueError, OSError, TypeError):
                pass
        elif next_ch == '\x7f':
            # Alt+Backspace → 删除前一个词（同 Ctrl+W）
            self._dismiss_completion()
            # 消耗可能跟随的额外字节
            try:
                if select.select([fd], [], [], 0.01)[0]:
                    os.read(fd, 1)
            except (ValueError, OSError, TypeError):
                pass
            self._input_handler._delete_word_left()
            self._trigger_auto_completion()
        elif next_ch == '\x1b':
            # 双 Esc（Alt+Esc）→ 视为中断
            self._do_interrupt()
            self._flush_stdin_residual()
        else:
            # 其他 ESC 序列 → 视为中断
            self._do_interrupt()
            self._flush_stdin_residual()

    def _monitor_win(self):
        """Windows (非 Cygwin): 用 msvcrt 读取按键。"""
        import msvcrt

        # Windows 不需要 cbreak 设置，直接标记 ready
        self._monitor_ready.set()

        try:
            while not self._stop.is_set():
                # ── 暂停状态处理 ──────────────────────────
                if not self._active.is_set():
                    if self._wait_while_paused(WINDOWS_POLL_INTERVAL):
                        return
                    # 恢复后清空残留按键
                    while msvcrt.kbhit():
                        msvcrt.getch()
                    continue

                # ── 正常监听 ──────────────────────────────
                if not msvcrt.kbhit():
                    time.sleep(WINDOWS_POLL_INTERVAL)
                    continue

                ch = msvcrt.getch()
                if ch == b'\x1b':
                    self._handle_escape_win()
                elif ch == b'\x03':
                    self._do_interrupt()
                    while msvcrt.kbhit():
                        msvcrt.getch()
                elif ch == b'\x07':          # Ctrl+G → vim 编辑
                    self._handle_special_key('vim')
                elif ch == b'\x0f':          # Ctrl+O → /editmsg
                    self._handle_special_key('editmsg')
                elif ch == b'\x0e':          # Ctrl+N → 切换模型
                    self._handle_special_key('switch_model')
                elif ch == b'\x12':          # Ctrl+R → 切换模型（备用）
                    self._handle_special_key('switch_model')
                elif ch == b'\x09':          # Tab → 补全
                    self._handle_tab()
                elif ch in (b'\r', b'\n'):  # Enter
                    self._dismiss_completion()
                    self._input_handler._enter()
                elif ch in (b'\x08', b'\x7f'):  # Backspace
                    self._dismiss_completion()
                    self._input_handler._backspace()
                    self._trigger_auto_completion()
                else:
                    # 流式输入字符
                    try:
                        char = ch.decode("utf-8", errors="replace")
                        self._input_handler.handle_char(char)
                    except Exception:
                        with self._captured_lock:
                            self._captured_input.extend(ch)
                    self._trigger_auto_completion()
        finally:
            self._restore_terminal_settings()

    def _handle_escape_win(self):
        """Windows 平台 Esc 序列处理。"""
        import msvcrt
        if not msvcrt.kbhit():
            self._do_interrupt()
            while msvcrt.kbhit():
                msvcrt.getch()
            return

        next_ch = msvcrt.getch()
        if next_ch == b'[':
            final_ch = None
            while msvcrt.kbhit():
                c = msvcrt.getch()
                if c.isalpha() or c == b'~':
                    final_ch = c
                    break
            if final_ch == b'A':
                self._handle_arrow_up()
            elif final_ch == b'B':
                self._handle_arrow_down()
            elif final_ch == b'C':
                self._input_handler._right()
            elif final_ch == b'D':
                self._input_handler._left()
        elif next_ch == b'O':
            if msvcrt.kbhit():
                msvcrt.getch()
        elif next_ch == b'\x1b':
            self._do_interrupt()
            while msvcrt.kbhit():
                msvcrt.getch()
        else:
            self._do_interrupt()


def get_active_monitor():
    """获取当前活跃的 EscapeMonitor 实例（如果有）。"""
    global _active_monitor_lock
    with _active_monitor_lock:
        return _active_monitor


def stop_active_monitor():
    """停止当前活跃的 EscapeMonitor（如果存在）。"""
    monitor = get_active_monitor()
    if monitor is not None:
        try:
            monitor.stop()
        except Exception:
            _logger.warning("EscapeMonitor.stop() 异常", exc_info=True)
