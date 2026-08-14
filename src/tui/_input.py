"""Input — 统一 TUI 输入管理（薄外观，方向A 步骤1 拆分）。

原 Input 上帝类（约 1500 行，7 职责域内联）按职责拆分为三个独立类，
Input 保留全部公开 API 作为薄外观（Facade），方法体委托：

  - InputIO（_input_io.py）          — stdin 原始读取 + I/O 状态机（SRP 提取）
  - InputBufferEditor（_input_buffer.py）— 缓冲编辑 + 历史 + 队列 + 回显（Strategy 提取）
  - InputDispatcher（_input_dispatcher.py）— 事件分发胶水（Template Method 提取）

关键改动（方向A 步骤1）：
  - 移除对 ``src.api.interrupt_async`` 的直接 import：``_do_interrupt`` 改调
    注入回调（``set_interrupt_callback``，由 _loop.py _setup_monitor 注入
    ``lambda: request_interrupt_async()``）；未注入时记 debug 日志并跳过。
  - 历史写盘决策（2026-07-31）：``_append_history_locked`` 保持每 Enter 调
    ``_append_to_history_file``（**保持现状**，批量化风险 > 收益，见 _input_buffer.py）。
  - 历史写盘线程模型（2026-08-05 review 收敛）：每 Enter 创建 daemon 线程改为
    共享串行后台 writer（_HistoryDiskWriter，见 _input_buffer.py）；「不批量化」
    决策不变。

设计模式：外观（Facade）——薄外观保持公共 API；组合持有三个拆分组件。

模块级私有函数（``_TAB_WIDTH`` / ``_compute_cursor_visual_pos`` /
``_expand_tabs`` / ``_wrap_by_width`` / ``_tab_pos_to_expanded`` /
``_compute_input_layout`` / ``_cursor_visual_from_layout``）已拆分至
``_input_layout.py``（纯函数，输入区换行与光标视觉位置计算），本模块
re-export 保持旧导入路径兼容（tests 直接 ``from src.tui._input import
_compute_cursor_visual_pos`` 等）。

方向5（光标算法单一真源）：``_compute_input_layout`` /
``_cursor_visual_from_layout`` 原自 ``app/input_area.py`` 迁移至本模块，
再随模块边界拆分独立为 ``_input_layout.py``——input_area / session /
_cursor 均从同一实现复用，不再双实现。

线程模型：
  - Render 线程（daemon）：_drain_queue() 中每帧调用 process_events()，
    一次性处理所有 stdin 输入，统一处理 stdin 和渲染。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from src.api.escape_monitor._history import (
    _read_history_file,
    _append_to_history_file,
    _compact_history_file,
)
from src.tui._screen import TerminalWidthCache
# ★ 输入区布局计算（模块边界优化，2026-08-05）：_TAB_WIDTH/_expand_tabs/
#   _tab_pos_to_expanded/_compute_input_layout/_cursor_visual_from_layout/
#   _compute_cursor_visual_pos 迁至 _input_layout.py（纯函数）；本模块
#   re-export 保持旧导入路径兼容。**``_wrap_by_width`` 定义也已归位
#   ``_input_layout``**（2026-08-05 循环依赖消除：原定义保留于本文件导致
#   ``_input_layout._compute_input_layout`` 经函数内延迟 import 访问形成
#   ``_input → _input_layout → _input`` 隐性环；归位后本模块 re-export
#   保持旧导入路径兼容——``from src.tui._input import _wrap_by_width`` 仍
#   可用，测试 patch 目标已迁移至 ``src.tui._input_layout._wrap_by_width``）。
from ._input_layout import (
    _TAB_WIDTH,
    _expand_tabs,
    _tab_pos_to_expanded,
    _wrap_by_width,
    _compute_input_layout,
    _cursor_visual_from_layout,
    _compute_cursor_visual_pos,
)
from ._input_parser import (
    InputParser,
    KeyEvent,
)
from ._input_io import InputIO
from ._input_buffer import InputBufferEditor
from ._input_dispatcher import InputDispatcher

if TYPE_CHECKING:
    import threading

_logger = logging.getLogger(__name__)

# ── 常量 ──────────────────────────────────────────────────
# 解析超时常量（_CSI_READ_TIMEOUT / _SS3_READ_TIMEOUT / _UTF8_READ_TIMEOUT）
# 已随解析逻辑搬移至 _input_parser.py；_UTF8_READ_TIMEOUT 由 read_utf8_char
# 使用，随读取层搬移至 _input_io.py（本模块不再需要）。


# ═══════════════════════════════════════════════════════════
# _HistoryIO — 历史文件 I/O 适配器
# ═══════════════════════════════════════════════════════════

class _HistoryIO:
    """历史文件 I/O 适配器 — 经本模块命名空间解析历史读写函数。

    InputBufferEditor 通过构造注入本适配器，使其 ``load_history`` /
    ``_append_history_locked`` 调用的 ``_read_history_file`` /
    ``_append_to_history_file`` / ``_compact_history_file`` 在调用时从本模块
    命名空间解析——保证 tests 中 ``patch("src.tui._input._read_history_file", ...)``
    等仍可拦截（与拆分前行为一致）。
    """

    __slots__ = ()

    @staticmethod
    def read() -> tuple[str, bool]:
        return _read_history_file()

    @staticmethod
    def append(text: str) -> bool:
        return _append_to_history_file(text)

    @staticmethod
    def compact() -> bool:
        return _compact_history_file()


# ═══════════════════════════════════════════════════════════
# Input — 统一输入管理类（薄外观）
# ═══════════════════════════════════════════════════════════

class Input:
    """统一输入管理类（薄外观）。

    组合持有三个拆分组件并委托：
      - InputIO（stdin 读取 + I/O 状态机）
      - InputBufferEditor（缓冲编辑 + 历史 + 队列 + 回显）
      - InputDispatcher（事件分发）
      - InputParser（ANSI 解析）

    公开 API 与旧版完全兼容（零回归约束）。

    构造函数:
        fd: stdin 文件描述符（sys.stdin.fileno()）
        history_file: 历史文件路径
        term_width_cache: 可选，默认使用 TerminalWidthCache.get_default()
    """

    def __init__(
        self,
        fd: int,
        history_file: Path,
        term_width_cache: "TerminalWidthCache | None" = None,
    ) -> None:
        self._term_width_cache = (
            term_width_cache if term_width_cache is not None
            else TerminalWidthCache.get_default()
        )

        # ── 职责拆分（方向A 步骤1） ──
        self._io = InputIO(fd=fd)
        # ★ 批量读取优化（2026-08-14）：InputParser 注入 InputIO——ESC/UTF-8
        #   序列的后续字节经 io.read_with_timeout 读取（优先消费批量读取
        #   pending，零 select 超时）。
        self._parser = InputParser(io=self._io)
        self._buffer_editor = InputBufferEditor(
            history_file=history_file,
            history_io=_HistoryIO(),
        )
        self._dispatcher = InputDispatcher(
            io=self._io,
            buffer_editor=self._buffer_editor,
            parser=self._parser,
        )

    # ── 公开属性 ──────────────────────────────────────────

    @property
    def fd(self) -> int:
        """stdin 文件描述符。"""
        return self._io.fd

    @property
    def width(self) -> int:
        """终端宽度（列数），TTL 缓存。"""
        return self._term_width_cache.get_width()

    @property
    def height(self) -> int:
        """终端高度（行数），TTL 缓存。"""
        return self._term_width_cache.get_height()

    @property
    def is_io_running(self) -> bool:
        """I/O 是否处于激活状态（标志位管理，非线程存活检测）。"""
        return self._io.is_io_running

    @property
    def interrupted(self) -> bool:
        """中断标志是否被设置。"""
        return self._io.interrupted

    # ── 私有属性委托（保持拆分前测试/调用方访问路径） ──────

    @property
    def _fd(self) -> int:
        """stdin fd（委托 InputIO；tests 经 patch.object(inp, '_fd', ...) 覆盖）。"""
        return self._io.fd

    @_fd.setter
    def _fd(self, value: int) -> None:
        self._io.fd = value

    @_fd.deleter
    def _fd(self) -> None:
        """no-op deleter：支持 patch.object(inp, '_fd', ...) 退出时的 delattr。

        （unittest.mock 对非实例属性在 __exit__ 先 delattr；类 property 存在使
        hasattr 为 True，后续不会 setattr 恢复——但测试在 with 块内使用后不再
        复用实例，io.fd 保持打补丁值无副作用。）

        观察说明（P3-22）：patch.object 退出后 ``self._io.fd`` 保持打补丁值
        （deleter 为 no-op，不恢复原值）——对生产路径无影响（生产代码不在
        patch 上下文运行），测试约定 with 块内使用后不依赖恢复值。
        """
        pass

    @property
    def _buffer(self) -> str:
        """当前输入缓冲文本（委托 InputBufferEditor，供测试直接操作）。"""
        return self._buffer_editor._buffer

    @_buffer.setter
    def _buffer(self, value: str) -> None:
        self._buffer_editor._buffer = value

    @property
    def _cursor_pos(self) -> int:
        """光标位置（委托 InputBufferEditor，供测试直接操作）。"""
        return self._buffer_editor._cursor_pos

    @_cursor_pos.setter
    def _cursor_pos(self, value: int) -> None:
        self._buffer_editor._cursor_pos = value

    @property
    def _active(self) -> "threading.Event":
        """I/O 激活事件（委托 InputIO，供状态断言）。"""
        return self._io.active

    @property
    def _stop(self) -> "threading.Event":
        """I/O 停止事件（委托 InputIO，供状态断言）。"""
        return self._io.stop

    @property
    def _select_error_count(self) -> int:
        """select 连续错误计数（委托 InputIO，供故障断言）。"""
        return self._io.select_error_count

    @property
    def _history(self) -> list[str]:
        """内存历史（委托 InputBufferEditor，供测试直接操作）。"""
        return self._buffer_editor._history

    @_history.setter
    def _history(self, value: list[str]) -> None:
        self._buffer_editor._history = value

    @property
    def _history_idx(self) -> int:
        """历史导航索引（委托 InputBufferEditor）。"""
        return self._buffer_editor._history_idx

    @_history_idx.setter
    def _history_idx(self, value: int) -> None:
        self._buffer_editor._history_idx = value

    @property
    def _saved_input_before_history(self) -> str:
        """进入历史导航前保存的输入（委托 InputBufferEditor）。"""
        return self._buffer_editor._saved_input_before_history

    @_saved_input_before_history.setter
    def _saved_input_before_history(self, value: str) -> None:
        self._buffer_editor._saved_input_before_history = value

    @property
    def _history_indicator(self) -> str:
        """历史浏览状态指示器（委托 InputBufferEditor）。"""
        return self._buffer_editor._history_indicator

    @property
    def _input_ready(self) -> "threading.Event":
        """输入就绪事件（委托 InputBufferEditor；editmsg 插件与步骤 2 使用）。"""
        return self._buffer_editor._input_ready

    @property
    def _lock(self) -> "threading.Lock":
        """输入缓冲锁（委托 InputBufferEditor；editmsg 插件 finally 清理使用）。"""
        return self._buffer_editor._lock

    @property
    def _submitted_text(self) -> str:
        """已提交文本（委托 InputBufferEditor；editmsg 插件 finally 清理使用）。"""
        return self._buffer_editor._submitted_text

    @_submitted_text.setter
    def _submitted_text(self, value: str) -> None:
        self._buffer_editor._submitted_text = value

    @property
    def _dismiss_completion_callback(self):
        """补全弹窗关闭回调（委托 InputDispatcher；message_editor 替换使用）。

        message_editor.py 在 /editmsg（Ctrl+O）交互选择期间直接读写
        ``input_._dismiss_completion_callback``（保存原回调 → 替换为自定义回调
        → finally 恢复）；薄外观缺失此属性曾导致 AttributeError 被
        editmsg_plugin 捕获（用户只见「编辑失败」）。与 ``_input_ready``
        委托模式一致：get/set 均委托 ``self._dispatcher._dismiss_completion_callback``。
        """
        return self._dispatcher._dismiss_completion_callback

    @_dismiss_completion_callback.setter
    def _dismiss_completion_callback(self, value) -> None:
        self._dispatcher._dismiss_completion_callback = value

    # ═══════════════════════════════════════════════════════
    # I/O 状态管理（委托 InputIO）
    # ═══════════════════════════════════════════════════════

    def start_io(self) -> None:
        """激活 I/O 读取（委托 InputIO）。"""
        self._io.start_io()

    def stop_io(self) -> None:
        """停用 I/O 读取（委托 InputIO）。"""
        self._io.stop_io()

    def pause_io(self) -> None:
        """暂停 I/O 读取（委托 InputIO）。"""
        self._io.pause_io()

    def resume_io(self) -> None:
        """恢复 I/O 读取（委托 InputIO）。"""
        self._io.resume_io()

    # ═══════════════════════════════════════════════════════
    # 中断与特殊按键处理（委托 InputDispatcher）
    # ═══════════════════════════════════════════════════════

    def _do_interrupt(self) -> None:
        """内联中断处理（委托 InputDispatcher，interrupt 回调注入）。"""
        self._dispatcher._do_interrupt()

    def _handle_special_key(self, action: str) -> None:
        """处理特殊按键（Ctrl+G/O/N/R）（委托 InputDispatcher）。"""
        self._dispatcher._handle_special_key(action)

    def _flush_stdin_residual(self, max_flush: int = 50) -> None:
        """非阻塞清理 stdin 残留字节（委托 InputIO）。"""
        self._io._flush_stdin_residual(max_flush)

    def flush_stdin_buffer(self, max_flush: int = 50) -> None:
        """公开方法：非阻塞清理 stdin 残留字节 + termios 缓冲区刷洗（委托 InputIO）。"""
        self._io.flush_stdin_buffer(max_flush)

    # ═══════════════════════════════════════════════════════
    # stdin 直接读取（委托 InputDispatcher）
    # ═══════════════════════════════════════════════════════

    def read_stdin_once(self) -> bool:
        """单次非阻塞 stdin 读取 + 直接分发（委托 InputDispatcher）。"""
        return self._dispatcher.read_stdin_once()

    def process_events(self) -> None:
        """处理所有输入事件（委托 InputDispatcher）。"""
        self._dispatcher.process_events()

    def _dispatch_key_event(self, event: KeyEvent) -> None:
        """根据 KeyEvent.kind 分发到对应的输入处理器（委托 InputDispatcher）。"""
        self._dispatcher._dispatch_key_event(event)

    # ═══════════════════════════════════════════════════════
    # 辅助分发方法（委托 InputDispatcher）
    # ═══════════════════════════════════════════════════════

    def _handle_tab(self) -> None:
        """处理 Tab 键（委托 InputDispatcher）。"""
        self._dispatcher._handle_tab()

    def _handle_arrow_up(self) -> None:
        """处理上箭头（委托 InputDispatcher）。"""
        self._dispatcher._handle_arrow_up()

    def _handle_arrow_down(self) -> None:
        """处理下箭头（委托 InputDispatcher）。"""
        self._dispatcher._handle_arrow_down()

    def _dismiss_completion(self) -> None:
        """如果补全弹窗可见，关闭它（委托 InputDispatcher）。"""
        self._dispatcher._dismiss_completion()

    def _trigger_auto_completion(self) -> None:
        """获取当前文本并调用自动补全回调（委托 InputDispatcher）。"""
        self._dispatcher._trigger_auto_completion()

    # ═══════════════════════════════════════════════════════
    # 解析方法（委托 InputParser → _input_parser.py）
    # ═══════════════════════════════════════════════════════

    def feed_byte(self, byte: int) -> KeyEvent | None:
        """单字节推入解析状态机（委托 InputParser）。

        Args:
            byte: 单字节整数值 (0-255)。

        Returns:
            KeyEvent — 完整按键事件；None — 需要解析完整转义序列。
        """
        return self._parser.feed_byte(byte)

    def parse_sequence(self, fd_override: int | None = None) -> KeyEvent:
        """解析 ESC 转义序列（含 I/O，委托 InputParser）。

        在首字节已确认为 0x1b 后调用。

        Args:
            fd_override: 可选 fd 覆盖，默认使用 self._fd。

        Returns:
            解析后的 KeyEvent。
        """
        return self._parser.parse_sequence(
            fd_override if fd_override is not None else self._fd,
        )

    def _parse_escape_sequence(self, fd: int) -> KeyEvent:
        """读取并解析 ESC 转义序列（含 I/O，委托 InputParser）。"""
        return self._parser._parse_escape_sequence(fd)

    @staticmethod
    def _decode_control_char(byte: int) -> KeyEvent:
        """将 ASCII 控制字符 (0x00-0x1F / 0x7F) 解码为 KeyEvent（转发 InputParser）。"""
        return InputParser._decode_control_char(byte)

    def _read_csi_sequence(self, fd: int) -> KeyEvent:
        """读取 CSI 序列参数 + 终结符并解析为 KeyEvent（委托 InputParser）。"""
        return self._parser._read_csi_sequence(fd)

    def _read_ss3_sequence(self, fd: int) -> KeyEvent:
        """读取 SS3 序列（ESC O + 字符，通常为 F1-F4）（委托 InputParser）。"""
        return self._parser._read_ss3_sequence(fd)

    @staticmethod
    def _dispatch_csi(params: list[int], terminator: str) -> KeyEvent:
        """根据 CSI 参数和终结符分发到对应的 KeyEvent（转发 InputParser）。"""
        return InputParser._dispatch_csi(params, terminator)

    @staticmethod
    def _params_to_bytes(params: list[int]) -> bytes:
        """将参数列表转为 CSI 参数字节串（转发 InputParser）。"""
        return InputParser._params_to_bytes(params)

    # ═══════════════════════════════════════════════════════
    # I/O 辅助方法（委托 InputIO）
    # ═══════════════════════════════════════════════════════

    def read_byte(self) -> bytes:
        """从 fd 读取单个原始字节（委托 InputIO）。"""
        return self._io.read_byte()

    def read_with_timeout(self, timeout: float) -> bytes | None:
        """使用 select + os.read 读取单个字节，超时返回 None（委托 InputIO）。"""
        return self._io.read_with_timeout(timeout)

    def try_read_paste(self, fd: int, first_chars: str) -> str:
        """检测并读取粘贴内容（退避 select 检测突发字符流）（委托 InputIO）。"""
        return self._io.try_read_paste(fd, first_chars)

    def read_utf8_char(self, fd: int, first_byte: int) -> str | None:
        """读取完整的多字节 UTF-8 字符序列（委托 InputIO）。"""
        return self._io.read_utf8_char(fd, first_byte)

    # ═══════════════════════════════════════════════════════
    # 缓冲操作（委托 InputBufferEditor）
    # ═══════════════════════════════════════════════════════

    def handle_char(self, ch: str) -> None:
        """处理流式输入字符：插入到缓冲区光标位置并回显（委托 InputBufferEditor）。"""
        self._buffer_editor.handle_char(ch)

    def handle_chars(self, text: str) -> None:
        """批量处理多个字符（粘贴/预填场景）（委托 InputBufferEditor）。"""
        self._buffer_editor.handle_chars(text)

    def get_queued_input(self) -> str | None:
        """获取排队输入（Enter 提交的文本），返回 None 表示无排队输入。"""
        return self._buffer_editor.get_queued_input()

    def has_queued_input(self) -> bool:
        """是否有排队输入等待处理。"""
        return self._buffer_editor.has_queued_input()

    def get_current_text(self) -> str:
        """获取当前正在输入的文本（不消费）。"""
        return self._buffer_editor.get_current_text()

    def reset(self) -> None:
        """清空所有流式输入状态（缓冲区、提交文本、历史导航、中断标志）。"""
        self._dispatcher.reset()

    def drain_all(self) -> tuple[str | None, str]:
        """排出所有流式输入状态：返回 (submitted_text, buffer_text)。"""
        return self._buffer_editor.drain_all()

    def set_buffer(self, text: str) -> None:
        """设置缓冲区文本（用于预填），光标移到末尾。"""
        self._buffer_editor.set_buffer(text)

    def get_history_indicator(self) -> str:
        """历史浏览状态指示器，非导航模式返回空字符串。"""
        return self._buffer_editor.get_history_indicator()

    # ═══════════════════════════════════════════════════════
    # 历史管理（委托 InputBufferEditor）
    # ═══════════════════════════════════════════════════════

    @staticmethod
    def _unescape(line: str) -> str:
        """将文件中转义的 \\n 还原为真实换行符（转发 InputBufferEditor）。"""
        return InputBufferEditor._unescape(line)

    def load_history(self) -> None:
        """从 INPUT_HISTORY_FILE 加载历史行（多进程安全）。"""
        self._buffer_editor.load_history()

    def _append_history_locked(self, text: str) -> None:
        """保存输入到历史（需持 _lock，委托 InputBufferEditor）。

        历史写盘决策（方向A 步骤1 评估，2026-07-31）：保持每 Enter 调用一次
        ``_append_to_history_file``（**保持现状**），见 _input_buffer.py。
        """
        self._buffer_editor._append_history_locked(text)

    # ═══════════════════════════════════════════════════════
    # 缓冲编辑操作（委托 InputBufferEditor）
    # ═══════════════════════════════════════════════════════

    def _enter(self) -> None:
        """Enter：保存提交文本、标记就绪、清空缓冲区（委托 InputBufferEditor）。

        外观层注入自身 ``_append_history_locked``，保证 tests 对外观实例的
        ``patch.object(inp, "_append_history_locked", ...)`` 拦截路径有效。
        """
        self._buffer_editor._enter(append_history=self._append_history_locked)

    def _backspace(self) -> None:
        """退格：删除光标前一个字符（委托 InputBufferEditor）。"""
        self._buffer_editor._backspace()

    def _left(self) -> None:
        """左箭头：光标左移一格（委托 InputBufferEditor）。"""
        self._buffer_editor._left()

    def _right(self) -> None:
        """右箭头：光标右移一格（委托 InputBufferEditor）。"""
        self._buffer_editor._right()

    def _up(self) -> None:
        """上箭头：多行上移一行；首行或单行回退到历史浏览（委托 InputBufferEditor）。"""
        self._buffer_editor._up()

    def _home(self) -> None:
        """Home：光标移到当前逻辑行首（委托 InputBufferEditor）。"""
        self._buffer_editor._home()

    def _end(self) -> None:
        """End：光标移到当前逻辑行尾（委托 InputBufferEditor）。"""
        self._buffer_editor._end()

    def _word_left(self) -> None:
        """Ctrl+左：向左跳一个词（委托 InputBufferEditor）。"""
        self._buffer_editor._word_left()

    def _word_right(self) -> None:
        """Ctrl+右：向右跳一个词（委托 InputBufferEditor）。"""
        self._buffer_editor._word_right()

    def _down(self) -> None:
        """下箭头：多行下移一行；尾行或单行回退到历史浏览（委托 InputBufferEditor）。"""
        self._buffer_editor._down()

    def _delete(self) -> None:
        """Del：删除光标后的字符（委托 InputBufferEditor）。"""
        self._buffer_editor._delete()

    def _delete_word_left(self) -> None:
        """Ctrl+W / Alt+Backspace：删除光标前的一个词（委托 InputBufferEditor）。"""
        self._buffer_editor._delete_word_left()

    def _delete_word_right(self) -> None:
        """Alt+D：删除光标后的一个词（readline kill-word，委托 InputBufferEditor）。"""
        self._buffer_editor._delete_word_right()

    def _kill_to_bol(self) -> None:
        """Ctrl+U：删除光标到当前逻辑行首（委托 InputBufferEditor）。"""
        self._buffer_editor._kill_to_bol()

    def _kill_to_eol(self) -> None:
        """Ctrl+K：删除光标到当前逻辑行尾（委托 InputBufferEditor）。"""
        self._buffer_editor._kill_to_eol()

    # ═══════════════════════════════════════════════════════
    # 回调接口（委托 InputBufferEditor / InputDispatcher）
    # ═══════════════════════════════════════════════════════

    def set_echo_callback(self, cb) -> None:
        """设置流式输入回显回调。

        cb 签名: (display_text: str, cursor_pos: int) -> None
        """
        self._buffer_editor.set_echo_callback(cb)

    def set_special_key_callback(self, cb) -> None:
        """设置特殊按键回调（Ctrl+G/O/N/R/T/B 等组合键）。

        cb 签名: (action: str, current_text: str) -> str | None

        action 取值（当前分发）：``vim``（Ctrl+G）/ ``editmsg``（Ctrl+O）/
        ``retry``（Ctrl+R，反向搜索禁用时）/ ``toggle_theme``（Ctrl+T）/
        ``switch_model``（Ctrl+N）/ ``empty_mode``（Ctrl+B）。
        """
        self._dispatcher.set_special_key_callback(cb)

    def set_completion_callback(self, cb) -> None:
        """设置 Tab 补全回调。

        cb 签名: (text: str) -> str | None
        """
        self._dispatcher.set_completion_callback(cb)

    def set_dismiss_completion_callback(self, cb) -> None:
        """设置补全弹窗关闭回调。

        cb 签名: () -> None
        """
        self._dispatcher.set_dismiss_completion_callback(cb)

    def get_dismiss_completion_callback(self):
        """获取补全弹窗关闭回调（委托 InputDispatcher；公开访问器）。

        方向2（私有属性访问公开化）：与 ``set_dismiss_completion_callback``
        对称——message_editor 保存/恢复 dismiss 回调经公开 API（不直接读写
        私有字段）。
        """
        return self._dispatcher.get_dismiss_completion_callback()

    def set_completion_navigate_callback(self, cb) -> None:
        """设置补全弹窗上下导航回调。

        cb 签名: (delta: int, text: str) -> str | None
        """
        self._dispatcher.set_completion_navigate_callback(cb)

    def set_auto_completion_callback(self, cb) -> None:
        """设置自动补全回调。

        cb 签名: (text: str) -> None
        """
        self._dispatcher.set_auto_completion_callback(cb)

    def set_input_hook_router(self, router) -> None:
        """设置 input hook router（委托 InputDispatcher，ink useInput 钩子）。

        router 签名: ``(event: KeyEvent) -> bool`` —— True=消费（跳过旧回调
        路径），False=放行（走旧路径）。None 可清除注入。
        """
        self._dispatcher.set_input_hook_router(router)

    def set_reverse_search_enabled(self, enabled: bool) -> None:
        """设置 Ctrl+R 反向历史搜索启用标志（委托 InputDispatcher，方向D 步骤14）。

        由装配注入 ``TuiConfig.reverse_search_enabled``（默认 False 保持
        switch_model 语义）。
        """
        self._dispatcher.set_reverse_search_enabled(enabled)

    def set_reverse_search_callback(self, cb) -> None:
        """设置反向搜索状态同步回调（委托 InputDispatcher，方向D 步骤14）。

        cb 签名: ``(query: str, matches: list[str], index: int, active: bool) -> None``
        由装配注入（更新 model.history_search + 重绘）。
        """
        self._dispatcher.set_reverse_search_callback(cb)

    def set_esc_cancel_input(self, enabled: bool) -> None:
        """设置 Esc 取消输入启用标志（委托 InputDispatcher，方向D 步骤16）。

        由装配注入 ``TuiConfig.esc_cancel_input``（默认 False 保持中断语义）。
        """
        self._dispatcher.set_esc_cancel_input(enabled)

    def set_active_status_callback(self, fn) -> None:
        """设置活跃状态回调（委托 InputDispatcher，方向D 步骤16）。

        fn 签名: ``() -> bool`` —— True=生成中（Esc 不取消输入，走中断）。
        由装配注入（model.status.status_active）。
        """
        self._dispatcher.set_active_status_callback(fn)

    def set_clear_screen_callback(self, cb) -> None:
        """设置 Ctrl+L 清屏回调（委托 InputDispatcher，Claude TUI parity 3.1）。

        cb 签名: ``() -> None``（session.clear_screen）；None 可清除注入。
        """
        self._dispatcher.set_clear_screen_callback(cb)

    def set_interrupt_callback(self, cb) -> None:
        """设置中断回调（方向A 步骤1 注入点）。

        cb 签名: () -> None
        None 缺省时 ``_do_interrupt`` 记 debug 日志并跳过（测试兼容）。
        """
        self._dispatcher.set_interrupt_callback(cb)

    def set_suppress_enter(self, suppress: bool) -> None:
        """设置 Enter 抑制标志（用于 editmsg 消息选择期间）。

        当 suppress=True 时，_dispatch_key_event 中的 Enter 分支
        将跳过 _enter() 调用，防止选择确认 Enter 被误提交为输入。

        线程安全：使用 _suppress_enter_lock 保护。
        """
        self._dispatcher.set_suppress_enter(suppress)

    def get_suppress_enter(self) -> bool:
        """获取当前 Enter 抑制状态。线程安全。"""
        return self._dispatcher.get_suppress_enter()

    # ═══════════════════════════════════════════════════════
    # 便捷方法
    # ═══════════════════════════════════════════════════════

    def echo(self, text: str = "") -> None:
        """调用回显回调，自动获取当前文本如果未提供。"""
        if not text:
            text = self.get_current_text()
        self._buffer_editor._echo(text)

    def _echo(self, text: str) -> None:
        """调用回显回调（委托 InputBufferEditor，供测试直接调用）。"""
        self._buffer_editor._echo(text)

    def reset_and_echo(self) -> None:
        """重置缓冲区并回显空字符串（清空输入行视觉）。"""
        self._dispatcher.reset_and_echo()

    def capture_bytes(self, data: bytes) -> None:
        """追加原始字节到捕获缓冲区。线程安全。"""
        self._dispatcher.capture_bytes(data)

    def drain_captured(self) -> str:
        """排出并返回捕获的非可打印字符。"""
        return self._dispatcher.drain_captured()

    # ═══════════════════════════════════════════════════════
    # 事件等待（方向A 步骤2：_input_ready 事件化）
    # ═══════════════════════════════════════════════════════

    def wait_until_ready(self, timeout: float | None = None) -> bool:
        """等待输入就绪事件（``_input_ready``）被设置。

        线程安全（threading.Event）；timeout=None 表示无限等待。

        Args:
            timeout: 超时秒数；None 表示无限等待。

        Returns:
            True — 事件已设置（可安全调用 ``get_queued_input``）；
            False — 超时。
        """
        return self._buffer_editor.wait_until_ready(timeout)


# ── 模块导出 ──────────────────────────────────────────────

__all__ = [
    "Input",
    "KeyEvent",
    # 跨模块 re-export 的私有符号（供布局/光标计算外部模块导入）
    "_TAB_WIDTH",
    "_compute_cursor_visual_pos",
    "_expand_tabs",
    "_wrap_by_width",
    "_compute_input_layout",
    "_cursor_visual_from_layout",
]
