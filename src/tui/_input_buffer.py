"""InputBufferEditor — TUI 输入缓冲编辑 + 历史 + 队列（提取自 _input.py，方向A 步骤1）。

将 Input 上帝类中的缓冲状态、编辑方法、历史管理、队列与回显逻辑逐行迁移，
保持零逻辑改动：
  - 缓冲状态: _buffer / _cursor_pos / _submitted_text / _input_ready / _lock
  - 编辑方法: handle_char / handle_chars / set_buffer / _backspace / _left / _right /
              _up / _down / _home / _end / _word_left / _word_right / _delete /
              _delete_word_left / _kill_to_bol / _kill_to_eol / _enter
  - 历史管理: load_history / _append_history_locked / _history* / _unescape
  - 队列: get_queued_input / has_queued_input / drain_all / reset / get_current_text
  - 回显: _echo / _history_indicator

InputBufferEditor 通过构造注入 ``echo_callback``（缺省 None 时 _echo 静默）与
``history_io`` 历史文件 I/O 适配器（由 _input.py 注入，保持测试 patch 路径）。

历史写盘决策（方向A 步骤1 评估，2026-07-31）：
  ``_append_history_locked`` 保持每 Enter 调用一次 ``_append_to_history_file``
  （**保持现状**）。批量化会引入崩溃时历史丢失风险与退出冲刷复杂度，收益低，
  故不批量化。

★ review 方向（2026-08-05）：线程模型收敛——每 Enter 创建 daemon 线程改为
  共享串行后台 writer（``_HistoryDiskWriter``：单 daemon 线程 + 有界队列）。
  「不批量化」决策不变（写入仍逐条异步落盘），仅复用线程、防高频 Enter 的
  线程创建开销与磁盘竞争（详见 _HistoryDiskWriter 注释）。

★ 模块边界（2026-08-05 架构优化）：历史写盘已拆分至 ``_history_disk.py``
  （``_safe_disk_append``/``_HistoryDiskWriter``/``_HISTORY_DISK_WRITER``）；
  本模块经 re-export 保持旧导入路径兼容（``from src.tui._input_buffer import
  _HISTORY_DISK_WRITER`` 等）。编辑/历史/队列主体（InputBufferEditor）保留。

设计模式: 策略（Strategy）——编辑算法族与 I/O 解耦。
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Callable

from src.api.escape_monitor._history import _HISTORY_MAX_ENTRIES

# ★ 历史写盘（模块边界优化，2026-08-05）：_safe_disk_append /
#   _HistoryDiskWriter / _HISTORY_DISK_WRITER 迁至 _history_disk.py（后台
#   写盘独立职责）；本模块 re-export 保持旧导入路径兼容
#   （``from src.tui._input_buffer import _HISTORY_DISK_WRITER`` 等）。
from src.tui._history_disk import (
    _safe_disk_append,
    _HistoryDiskWriter,
    _HISTORY_DISK_WRITER,
)

_logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# InputBufferEditor — 缓冲编辑 + 历史 + 队列
# ═══════════════════════════════════════════════════════════

class InputBufferEditor:
    """输入缓冲编辑 + 历史 + 队列。

    负责缓冲状态、编辑操作、历史管理与队列语义（_input_ready 事件），
    不含原始 I/O（InputIO 承担）与事件分发（InputDispatcher 承担）。

    ``echo_callback`` 缺省 None 时 ``_echo`` 静默（不输出）；
    ``history_io`` 提供历史文件读写适配（测试可注入 mock 隔离磁盘）。
    """

    def __init__(
        self,
        history_file: Path,
        echo_callback: Callable[[str, int], None] | None = None,
        history_io=None,
    ) -> None:
        # ── 缓冲状态（原 InputBuffer） ──
        self._buffer: str = ""
        self._cursor_pos: int = 0
        self._submitted_text: str = ""
        self._input_ready = threading.Event()
        self._lock = threading.Lock()
        self._echo_callback = echo_callback

        # ── 历史（原 InputBuffer） ──
        self._history: list[str] = []
        self._history_idx: int = -1
        self._saved_input_before_history: str = ""
        # P2-1：魔法数字 1000 → 收敛至 _HISTORY_MAX_ENTRIES（escape_monitor._history 常量）
        # P3-2 说明：history_file 参数不参与 I/O——历史读写经构造注入的
        # ``_history_io`` 适配器（_input.py 注入 _HistoryIO）；history_file 仅
        # 保留作日志/兼容参数（旧 API 签名约束，load_history / _append_history_locked
        # 均不直接使用此路径）。
        self._history_file = history_file
        self._history_max_entries = _HISTORY_MAX_ENTRIES

        # ── 历史文件 I/O 适配器（由 _input.py 注入，保持测试 patch 路径） ──
        self._history_io = history_io

        # ── 反向历史搜索（方向D 步骤14，Ctrl+R 配置门控） ──
        self._search_query: str = ""
        self._search_matches: list[str] = []
        self._search_idx: int = -1
        self._search_active: bool = False
        self._search_saved_buffer: str = ""

    # ═══════════════════════════════════════════════════════
    # 回调
    # ═══════════════════════════════════════════════════════

    def set_echo_callback(self, cb) -> None:
        """设置流式输入回显回调。

        cb 签名: (display_text: str, cursor_pos: int) -> None
        """
        self._echo_callback = cb

    # ═══════════════════════════════════════════════════════
    # 缓冲操作（原 InputBuffer → 内联为实例方法）
    # ═══════════════════════════════════════════════════════

    def handle_char(self, ch: str) -> None:
        """处理流式输入字符：插入到缓冲区光标位置并回显。"""
        if not (ch.isprintable() or ch in (' ', '\t', '\n')):
            return
        with self._lock:
            if self._history_idx >= 0:
                self._history_idx = -1
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

        PERF-6：大粘贴用 list 拼接 ``''.join(parts)``（避免三次切片+拼接的
        多重临时分配，超大粘贴单次 O(n) 完成）。

        方向2（handle_chars \\r 过滤）：粘贴文本含 \\r（CR）不进入缓冲
        （``text.replace("\\r", "")``——\\n 保留用于多行输入）。仅影响粘贴/
        预填路径（``set_buffer`` 不受影响）。
        """
        text = text.replace("\r", "")
        with self._lock:
            if self._history_idx >= 0:
                self._history_idx = -1
            parts = [
                self._buffer[:self._cursor_pos],
                text,
                self._buffer[self._cursor_pos:],
            ]
            self._buffer = ''.join(parts)
            self._cursor_pos += len(text)
            result = self._buffer
        self._echo(result)

    def get_queued_input(self) -> str | None:
        """获取排队输入（Enter 提交的文本），返回 None 表示无排队输入。"""
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
        """获取当前正在输入的文本（不消费）。"""
        with self._lock:
            return self._buffer

    def reset(self) -> None:
        """清空所有流式输入状态（缓冲区、提交文本、历史导航、搜索状态）。

        （中断标志 _interrupted 由 InputDispatcher.reset() 一并清除，
        本方法仅负责缓冲/队列状态。）
        """
        with self._lock:
            self._buffer = ""
            self._cursor_pos = 0
            self._submitted_text = ""
            self._input_ready.clear()
            self._history_idx = -1
            self._saved_input_before_history = ""
            self._search_query = ""
            self._search_matches = []
            self._search_idx = -1
            self._search_active = False
            self._search_saved_buffer = ""

    def drain_all(self) -> tuple[str | None, str]:
        """排出所有流式输入状态：返回 (submitted_text, buffer_text)。

        BUG-T7：drain_all 清理 ``_input_ready`` 事件——消费后事件不残留 set
        状态（消除编排器对「事件已 set 但 submitted 已清空」的防御路径依赖）。
        """
        with self._lock:
            submitted = self._submitted_text if self._input_ready.is_set() else None
            buffer_text = self._buffer
            self._submitted_text = ""
            self._buffer = ""
            self._cursor_pos = 0
            self._history_idx = -1
            self._saved_input_before_history = ""
            self._search_query = ""
            self._search_matches = []
            self._search_idx = -1
            self._search_active = False
            self._search_saved_buffer = ""
            self._input_ready.clear()
        return submitted, buffer_text

    def set_buffer(self, text: str) -> None:
        """设置缓冲区文本（用于预填），光标移到末尾。"""
        with self._lock:
            self._buffer = text
            self._cursor_pos = len(text)
            self._history_idx = -1
            self._submitted_text = ""
            self._input_ready.clear()

    def get_history_indicator(self) -> str:
        """历史浏览状态指示器，非导航模式返回空字符串。"""
        return self._history_indicator

    # ═══════════════════════════════════════════════════════
    # 历史管理
    # ═══════════════════════════════════════════════════════

    @staticmethod
    def _unescape(line: str) -> str:
        """将文件中转义的 \\n 还原为真实换行符。"""
        return line.replace("\\n", "\n")

    def load_history(self) -> None:
        """从 INPUT_HISTORY_FILE 加载历史行（多进程安全）。

        P3-6 说明：本方法**不加锁**直接改写 ``_history``——设计上仅在装配/
        启动阶段（render 线程启动前）调用，与渲染线程的 ``search_enter`` /
        ``_append_history_locked`` 无并发窗口；若未来在运行期调用，须先获取
        ``_lock``（既有限制文档化，非缺陷）。
        """
        raw, locked = self._history_io.read()
        if not raw:
            return

        lines = raw.splitlines()
        if not lines:
            return

        # 第一趟 O(n)：记录每个条目在文件中的最后出现索引
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

        # 合并到现有内存历史
        file_entries = unique[:_HISTORY_MAX_ENTRIES]
        if self._history:
            if file_entries:
                existing = set(self._history)
                for entry in reversed(file_entries):
                    if entry not in existing:
                        self._history.append(entry)
                        existing.add(entry)
                self._history = self._history[:_HISTORY_MAX_ENTRIES]
        else:
            self._history = list(reversed(file_entries))

        if locked:
            self._history_io.compact()

    # ═══════════════════════════════════════════════════════
    # 缓冲编辑操作（原 InputBuffer 内部方法 → 私有方法）
    # ═══════════════════════════════════════════════════════

    def _backspace(self) -> None:
        """退格：删除光标前一个字符。"""
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
        """左箭头：光标左移一格。"""
        with self._lock:
            if self._cursor_pos > 0:
                self._cursor_pos -= 1
            text = self._buffer
        self._echo(text)

    def _right(self) -> None:
        """右箭头：光标右移一格。"""
        with self._lock:
            if self._cursor_pos < len(self._buffer):
                self._cursor_pos += 1
            text = self._buffer
        self._echo(text)

    def _enter(self, append_history: Callable[[str], None] | None = None) -> None:
        """Enter：保存提交文本、标记就绪、清空缓冲区。

        Args:
            append_history: 历史追加回调。None 时使用自身 ``_append_history_locked``；
                外观层注入其 ``_append_history_locked`` 以保持测试对外观实例的
                ``patch.object(inp, "_append_history_locked", ...)`` 拦截路径。

        方向D 步骤14：反向历史搜索激活时（Ctrl+R），Enter 应用当前匹配到缓冲
        并退出搜索（不提交——bash 语义：接受匹配项到命令行继续编辑）。
        搜索分支置于 ``_input_ready`` 早退判断**之前**（P2-6 修复）：搜索模式
        不提交、不受抑制语义约束——若进入搜索前存在未消费排队输入，早退会静默
        跳过搜索 Enter/Tab 应用匹配，搜索状态残留。
        """
        with self._lock:
            if self._search_active:
                # 搜索模式：应用当前匹配到缓冲并退出搜索（不提交）
                if self._search_matches and 0 <= self._search_idx < len(self._search_matches):
                    self._buffer = self._search_matches[self._search_idx]
                self._cursor_pos = len(self._buffer)
                self._search_query = ""
                self._search_matches = []
                self._search_idx = -1
                self._search_active = False
                self._search_saved_buffer = ""
                text = self._buffer
                applied_search = True
            else:
                if self._input_ready.is_set():
                    return
                text = self._buffer
                applied_search = False
                self._submitted_text = text
                self._buffer = ""
                self._cursor_pos = 0
                self._input_ready.set()
                if self._history_idx >= 0:
                    self._history_idx = -1
                if append_history is not None:
                    append_history(text)
                else:
                    self._append_history_locked(text)
        if applied_search:
            self._echo(text)
        else:
            self._echo("")

    # ═══════════════════════════════════════════════════════
    # 反向历史搜索（方向D 步骤14，Ctrl+R 配置门控）
    # ═══════════════════════════════════════════════════════

    def search_enter(self, query: str) -> bool:
        """进入反向历史搜索：对 ``_history`` 过滤 ``query in entry`` 建立匹配列表。

        Args:
            query: 搜索查询（进入搜索时的缓冲文本）。查询为空时不进入。

        Returns:
            True — 已进入搜索模式（无论是否有匹配）；False — 查询为空未进入。
        """
        if not query:
            return False
        with self._lock:
            self._search_query = query
            self._search_matches = [
                entry for entry in self._history if query in entry
            ]
            # 最近匹配优先（_history[0] 为最新）
            self._search_idx = 0 if self._search_matches else -1
            self._search_active = True
            self._search_saved_buffer = self._buffer
        return True

    def search_next(self) -> str:
        """循环移动到下一匹配（更旧），返回当前匹配文本。"""
        with self._lock:
            if self._search_matches:
                self._search_idx = (self._search_idx + 1) % len(self._search_matches)
                return self._search_matches[self._search_idx]
            return ""

    def search_prev(self) -> str:
        """循环移动到上一匹配（更新），返回当前匹配文本。"""
        with self._lock:
            if self._search_matches:
                self._search_idx = (self._search_idx - 1) % len(self._search_matches)
                return self._search_matches[self._search_idx]
            return ""

    def search_exit(self, apply: bool = False) -> str:
        """退出搜索：apply=True 用当前匹配替换缓冲；否则恢复进入搜索前的缓冲。

        P3-5：apply=True 且**无匹配**时也恢复 ``_search_saved_buffer``（修复前
        无匹配时 buffer 保持搜索期间用户编辑后的文本，语义不明确）——无匹配可
        应用 → 行为与 apply=False 一致（恢复进入搜索前的缓冲）。

        P3-18：搜索查询固定为进入搜索时的缓冲（``search_enter(query)`` 记录）；
        搜索期间新输入不更新查询，Enter 应用匹配会覆盖搜索期间新输入（计划内
        简化设计）。

        Returns:
            退出后的缓冲文本。
        """
        with self._lock:
            if apply and self._search_matches and 0 <= self._search_idx < len(self._search_matches):
                self._buffer = self._search_matches[self._search_idx]
            else:
                # apply=True 且无匹配 → 恢复进入搜索前的缓冲（P3-5）
                self._buffer = self._search_saved_buffer
            self._cursor_pos = len(self._buffer)
            self._search_query = ""
            self._search_matches = []
            self._search_idx = -1
            self._search_active = False
            self._search_saved_buffer = ""
            text = self._buffer
        self._echo(text)
        return text

    def is_search_active(self) -> bool:
        """是否处于反向历史搜索模式。"""
        return self._search_active

    def _append_history_locked(self, text: str) -> None:
        """保存输入到历史（需持 _lock；写盘部分异步执行）。

        历史写盘决策（方向A 步骤1 评估，2026-07-31）：保持每 Enter 调用一次
        ``_append_to_history_file``（**保持现状**）——批量化会引入崩溃时历史丢失
        风险与退出冲刷复杂度，收益低，故不批量化。

        方向3（Enter fsync 阻塞渲染修复）：``_append_history_locked`` 在渲染线程
        持锁被 ``_enter`` 调用，原实现内同步执行 ``os.fsync``（Termux ext4
        10-100ms）阻塞渲染帧与所有锁竞争路径——写盘迁移到后台 daemon 线程
        （``_safe_disk_append``），锁内仅更新内存历史（零阻塞）。
        """
        if not text.strip():
            return
        if text in self._history:
            self._history.remove(text)
        self._history.insert(0, text)
        if len(self._history) > self._history_max_entries:
            self._history = self._history[:self._history_max_entries]
        escaped = text.replace("\n", "\\n")
        # ★ review 方向：写盘迁移到共享串行后台 writer（单 daemon 线程 +
        # 有界队列），替代每 Enter 创建 daemon 线程（线程创建开销/磁盘竞争）。
        # 权衡见 _HistoryDiskWriter 注释：不改变「不批量化」决策。
        _HISTORY_DISK_WRITER.submit(self._history_io, escaped)

    def _up(self) -> None:
        """上箭头：多行上移一行；首行或单行回退到历史浏览。"""
        # ── 阶段1：多行光标上移 ──
        text = None
        with self._lock:
            if '\n' in self._buffer:
                before_cursor = self._buffer[:self._cursor_pos]
                cur_line = before_cursor.count('\n')
                if cur_line > 0:
                    lines = self._buffer.split('\n')
                    pos = sum(len(lines[i]) + 1 for i in range(cur_line))
                    col = self._cursor_pos - pos
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
                self._saved_input_before_history = self._buffer
                self._history_idx = 0
            elif self._history_idx < len(self._history) - 1:
                self._history_idx += 1
            self._buffer = self._history[self._history_idx]
            self._cursor_pos = len(self._buffer)
            text = self._buffer
        self._echo(text)

    def _home(self) -> None:
        """Home：光标移到当前逻辑行首。

        方向2（_history_idx 重置修复）：历史浏览中按 Home 退出历史导航——
        与其他编辑方法（_backspace/_delete/_delete_word_left/_kill_to_bol/
        _kill_to_eol）一致，防止后续编辑污染历史导航状态。
        """
        with self._lock:
            if self._history_idx >= 0:
                self._history_idx = -1
            if '\n' in self._buffer:
                before_cursor = self._buffer[:self._cursor_pos]
                last_nl = before_cursor.rfind('\n')
                self._cursor_pos = last_nl + 1
            else:
                self._cursor_pos = 0
            text = self._buffer
        self._echo(text)

    def _end(self) -> None:
        """End：光标移到当前逻辑行尾。

        方向2（_history_idx 重置修复）：历史浏览中按 End 退出历史导航（同
        _home 修复语义）。
        """
        with self._lock:
            if self._history_idx >= 0:
                self._history_idx = -1
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
        """Ctrl+左：向左跳一个词。

        方向2（_history_idx 重置修复）：历史浏览中按 Ctrl+左 退出历史导航（同
        _home 修复语义）。
        """
        with self._lock:
            if self._history_idx >= 0:
                self._history_idx = -1
            if self._cursor_pos <= 0:
                text = self._buffer
            else:
                pos = self._cursor_pos - 1
                while pos >= 0 and not (
                    self._buffer[pos].isalnum() or self._buffer[pos] == '_'
                ):
                    pos -= 1
                while pos >= 0 and (
                    self._buffer[pos].isalnum() or self._buffer[pos] == '_'
                ):
                    pos -= 1
                self._cursor_pos = pos + 1
                text = self._buffer
        self._echo(text)

    def _word_right(self) -> None:
        """Ctrl+右：向右跳一个词。

        方向2（_history_idx 重置修复）：历史浏览中按 Ctrl+右 退出历史导航（同
        _home 修复语义）。
        """
        with self._lock:
            if self._history_idx >= 0:
                self._history_idx = -1
            n = len(self._buffer)
            if self._cursor_pos >= n:
                text = self._buffer
            else:
                pos = self._cursor_pos
                while pos < n and not (
                    self._buffer[pos].isalnum() or self._buffer[pos] == '_'
                ):
                    pos += 1
                while pos < n and (
                    self._buffer[pos].isalnum() or self._buffer[pos] == '_'
                ):
                    pos += 1
                while pos < n and not (
                    self._buffer[pos].isalnum() or self._buffer[pos] == '_'
                ):
                    pos += 1
                self._cursor_pos = pos
                text = self._buffer
        self._echo(text)

    def _delete_word_right(self) -> None:
        """Alt+D：删除光标后的一个词（readline kill-word 语义）。

        对称于 ``_delete_word_left``（Ctrl+W）：跳过光标后的非词字符，再跳过
        一个词字符，删除 [光标, 词尾) 区间。方向2（_history_idx 重置修复）：
        历史浏览中按 Alt+D 退出历史导航（与其他编辑方法一致）。
        """
        with self._lock:
            if self._history_idx >= 0:
                self._history_idx = -1
            n = len(self._buffer)
            if self._cursor_pos >= n:
                text = self._buffer
            else:
                pos = self._cursor_pos
                while pos < n and not (
                    self._buffer[pos].isalnum() or self._buffer[pos] == '_'
                ):
                    pos += 1
                while pos < n and (
                    self._buffer[pos].isalnum() or self._buffer[pos] == '_'
                ):
                    pos += 1
                self._buffer = (
                    self._buffer[:self._cursor_pos]
                    + self._buffer[pos:]
                )
                text = self._buffer
        self._echo(text)

    def _down(self) -> None:
        """下箭头：多行下移一行；尾行或单行回退到历史浏览。"""
        # ── 阶段1：多行光标下移 ──
        text = None
        with self._lock:
            if '\n' in self._buffer:
                before_cursor = self._buffer[:self._cursor_pos]
                cur_line = before_cursor.count('\n')
                lines = self._buffer.split('\n')
                if cur_line < len(lines) - 1:
                    pos = sum(len(lines[i]) + 1 for i in range(cur_line))
                    col = self._cursor_pos - pos
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
                return
            elif self._history_idx > 0:
                self._history_idx -= 1
                self._buffer = self._history[self._history_idx]
            else:
                self._history_idx = -1
                self._buffer = self._saved_input_before_history
            self._cursor_pos = len(self._buffer)
            text = self._buffer
        self._echo(text)

    def _delete(self) -> None:
        """Del：删除光标后的字符。"""
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
        """Ctrl+W / Alt+Backspace：删除光标前的一个词。"""
        with self._lock:
            if self._history_idx >= 0:
                self._history_idx = -1
            if self._cursor_pos <= 0:
                text = self._buffer
            else:
                pos = self._cursor_pos - 1
                while pos >= 0 and not (
                    self._buffer[pos].isalnum() or self._buffer[pos] == '_'
                ):
                    pos -= 1
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
        """Ctrl+U：删除光标到当前逻辑行首。"""
        with self._lock:
            if self._history_idx >= 0:
                self._history_idx = -1
            if self._cursor_pos <= 0:
                text = self._buffer
            else:
                before_cursor = self._buffer[:self._cursor_pos]
                last_nl = before_cursor.rfind('\n')
                line_start = last_nl + 1
                self._buffer = (
                    self._buffer[:line_start]
                    + self._buffer[self._cursor_pos:]
                )
                self._cursor_pos = line_start
                text = self._buffer
        self._echo(text)

    def _kill_to_eol(self) -> None:
        """Ctrl+K：删除光标到当前逻辑行尾。"""
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
                text = self._buffer
        self._echo(text)

    @property
    def _history_indicator(self) -> str:
        """历史浏览状态指示器。"""
        if self._history_idx < 0:
            return ""
        total = len(self._history)
        current = self._history_idx + 1
        return f" [历史 {current}/{total}]"

    def _echo(self, text: str) -> None:
        """调用回显回调。"""
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

    # ═══════════════════════════════════════════════════════
    # 事件等待（方向A 步骤2：_input_ready 事件化）
    # ═══════════════════════════════════════════════════════

    def wait_until_ready(self, timeout: float | None = None) -> bool:
        """等待输入就绪事件（``_input_ready``）被设置。

        threading.Event 线程安全：``_enter`` 在 render 线程 set，
        等待方在 ``asyncio.to_thread`` worker 线程 wait，跨线程/跨 asyncio 兼容。

        Args:
            timeout: 超时秒数；None 表示无限等待。

        Returns:
            True — 事件已设置（可安全调用 ``get_queued_input`` 取文本）；
            False — 超时。
        """
        return self._input_ready.wait(timeout)


__all__ = ["InputBufferEditor", "_safe_disk_append", "_HistoryDiskWriter", "_HISTORY_DISK_WRITER"]
