"""StreamInputHandler 类。

封装流式输入系统：字符缓冲、历史导航、回显回调。
EscapeMonitor 通过组合持有本类实例，将流式输入逻辑委托出去。
"""

from __future__ import annotations

import threading
import logging

from ._history import (
    _read_history_file,
    _append_to_history_file,
    _compact_history_file,
    _HISTORY_MAX_ENTRIES,
    _HISTORY_COMPACT_RATIO,
    INPUT_HISTORY_FILE,
    _logger as _history_logger,
)

_logger = logging.getLogger(__name__)


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
        return text

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
        """
        with self._lock:
            submitted = self._submitted_text if self._input_ready.is_set() else None
            buffer_text = self._buffer
            self._submitted_text = ""
            self._buffer = ""
            self._cursor_pos = 0
            self._history_idx = -1
            self._saved_input_before_history = ""
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
        """设置缓冲区文本（用于预填），光标移到末尾。线程安全。

        同时清除残留的提交状态（_submitted_text / _input_ready），
        防止在 set_prefill 路径中残留的空提交覆盖预填内容。
        清理逻辑与 reset() 一致。
        """
        with self._lock:
            self._buffer = text
            self._cursor_pos = len(text)
            self._history_idx = -1
            self._submitted_text = ""
            self._input_ready.clear()

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
        """从 INPUT_HISTORY_FILE 加载历史行（多进程安全）。

        通过 _read_history_file() 加共享锁读取，保证跨进程一致性。
        使用两趟 O(n) 去重：第一趟记录最后出现索引，第二趟只保留最后出现。
        append 模式下文件末尾的条目最新，后出现覆盖先出现。

        兼容旧格式（\\n 未转义的历史文件）。
        限制最多 _HISTORY_MAX_ENTRIES 条防内存膨胀。
        加载完成后触发压缩（如需要）。

        注意：跨进程历史同步为启动时一次性加载，运行期间其他进程
        写入的条目在下次 EscapeMonitor.start() 前不可见（内存历史
        优先策略，避免冲掉当前会话的最新输入）。
        """
        raw, locked = _read_history_file()
        if not raw:
            # 文件不存在或为空时保留内存中已有的历史
            return

        lines = raw.splitlines()
        if not lines:
            return

        # 第一趟 O(n)：记录每个条目在文件中的最后出现索引（unescape 后）
        latest: dict[str, int] = {}
        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped:
                continue
            entry = self._unescape(stripped)
            if not entry:
                continue
            latest[entry] = i

        # 第二趟 O(n)：只保留最后出现的条目，保持原始顺序
        seen: set[str] = set()
        unique: list[str] = []
        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped:
                continue
            entry = self._unescape(stripped)
            if not entry:
                continue
            if i == latest.get(entry) and entry not in seen:
                unique.append(entry)
                seen.add(entry)

        # 合并到现有内存历史（防止文件过时时冲掉内存中的最新条目）
        file_entries = unique[:_HISTORY_MAX_ENTRIES]
        if self._history:
            if file_entries:
                existing = set(self._history)
                for entry in reversed(file_entries):
                    if entry not in existing:
                        self._history.append(entry)
                        existing.add(entry)
                self._history = self._history[:_HISTORY_MAX_ENTRIES]
            # 文件无内容时保留现有内存历史不变
        else:
            self._history = list(reversed(file_entries))

        # 成功获取锁时尝试压缩（避免多进程同时压缩）
        if locked:
            _compact_history_file()

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
        # 清空输入行视觉（回显空字符串清除输入行）
        self._echo("")

    def _append_history_locked(self, text: str) -> None:
        """保存输入到历史（需持 _lock）。

        内存去重后追加写入文件，多进程安全。
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
        if len(self._history) > _HISTORY_MAX_ENTRIES:
            self._history = self._history[:_HISTORY_MAX_ENTRIES]
        # 持久化（仅追加当前条目，多进程安全）
        escaped = text.replace("\n", "\\n")
        if not _append_to_history_file(escaped):
            _logger.warning("历史文件追加写入失败: %s", INPUT_HISTORY_FILE)

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
        光标位置保持在原始文本末尾，指示器作为视觉辅助不参与光标定位。"""
        with self._lock:
            pos = self._cursor_pos
            indicator = self._history_indicator
            if indicator:
                display_text = text + indicator
            else:
                display_text = text
        cb = self._echo_callback
        if cb is not None:
            try:
                cb(display_text, pos)
            except Exception:
                _logger.debug("_echo 回显回调失败", exc_info=True)
