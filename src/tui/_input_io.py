"""InputIO — TUI 输入 I/O 读取层（提取自 _input.py，方向A 步骤1）。

将 Input 上帝类中的 stdin 读取原语与 I/O 状态机提取为独立类，逐行迁移，
保持零逻辑改动：
  - 读取原语: read_byte / read_with_timeout / read_utf8_char / try_read_paste
  - 残留排空: _flush_stdin_residual / flush_stdin_buffer
  - I/O 状态机: start_io / stop_io / pause_io / resume_io（_active / _stop / _interrupted）
  - 故障检测: _eof_count / _select_error_count / _exit_reason / _fd_status

★ 批量读取优化（2026-08-14）：InputIO 持有 ``_pending`` 待处理字节缓冲——
  read_stdin_once 批量 ``os.read(fd, _READ_BATCH)`` 读入的剩余字节暂存于此；
  ``read_byte`` / ``read_with_timeout`` / ``read_utf8_char`` 优先消费 pending
  （零 syscall、零等待），pending 空时才 select+os.read。ESC 序列 / UTF-8
  多字节的后续字节若已在 pending 中，解析**不再 select 超时等待**（方向键、
  中文等跨字节序列零延迟）。原 ``_paste_skip_counter`` / ``_paste_skip_threshold``
  退避机制已移除——try_read_paste 依赖 pending 感知 + 单次短窗口确认
  （见 try_read_paste 注释），消除前 10 次按键每次 3 次 select 累计 5.1ms
  的固定打字延迟。

InputIO 持有 fd 与粘贴解码缓冲；``_interrupted`` 事件仍由 Input 公开属性
``interrupted`` 委托读取。``_UTF8_READ_TIMEOUT`` 从 _input_parser 导入。

设计模式: 单一职责（SRP）提取——读取层仅负责原始 I/O，不含缓冲/分发。

依赖方向:
  _input.py → _input_io.py 单向依赖；本模块不得 import _input（避免循环）。

模块级 ``import select`` / ``import os`` 供读取方法使用；可被
``patch("select.select", ...)`` / ``patch("os.read", ...)`` 全局拦截
（与 _input.py 原行为等价）。
"""

from __future__ import annotations

import errno
import logging
import os
import select
import threading
import time

from src._compat_termios import HAS_TERMIOS, termios
# P3-1 说明：从 escape_monitor._history 导入仅取常量（_EOF_THRESHOLD /
# _SELECT_ERROR_THRESHOLD），不导入历史 I/O 函数；阈值常量收敛在
# escape_monitor 模块（既有真源），不复制魔数。
from src.api.escape_monitor._history import (
    _EOF_THRESHOLD,
    _SELECT_ERROR_THRESHOLD,
)
from ._input_parser import _UTF8_READ_TIMEOUT

_logger = logging.getLogger(__name__)

#: 粘贴确认窗口（秒）：首字符后等待后续字节的最长时间。
#: 打字按键间隔 >> 1ms（人 >50ms），终端粘贴/IME 上屏的字符流 <1ms——单次
#: 短窗口即可区分，替代原 3 次退避 select（0.1+2+3ms 累计 5.1ms 固定延迟）。
_PASTE_CONFIRM_TIMEOUT = 0.001


# ═══════════════════════════════════════════════════════════
# InputIO — stdin 原始读取 + I/O 状态机
# ═══════════════════════════════════════════════════════════

class InputIO:
    """stdin 原始读取层 + I/O 状态机。

    仅负责原始 I/O（读取/排空/粘贴检测）与 I/O 标志位管理，
    不含缓冲编辑与事件分发（分别由 InputBufferEditor / InputDispatcher 承担）。

    由 Render 线程通过 Input.read_stdin_once()（委托 InputDispatcher）驱动。
    """

    def __init__(self, fd: int) -> None:
        self._fd = fd

        # ── I/O 状态控制 ──
        self._io_started: bool = False
        self._active = threading.Event()
        self._active.set()
        self._stop = threading.Event()
        self._interrupted = threading.Event()

        # ── 粘贴退避优化（2026-08-14 移除） ──
        # 原 ``_paste_skip_counter`` / ``_paste_skip_threshold`` 退避机制已删除：
        # 批量读取（read_stdin_once os.read(fd, _READ_BATCH)）将剩余字节存入
        # ``_pending``，try_read_paste 感知 pending 即突发输入（粘贴/IME 上屏），
        # 无需"连续 N 次非粘贴后走快速路径"的退避状态（见 try_read_paste 注释）。

        # ── 批量读取待处理字节缓冲（2026-08-14） ──
        # read_stdin_once 批量 os.read 读入的剩余字节暂存于此，后续调用优先
        # 消费（零 syscall）。ESC 序列 / UTF-8 多字节解析经 read_with_timeout
        # 优先取 pending（后续字节已在内存时零等待，不 select 超时）。
        self._pending: bytes = b""
        self._pending_pos: int = 0

        # ── 粘贴多字节解码缓冲（方向1 步骤1） ──
        # 跨 read_stdin_once 调用保留不完整的 UTF-8 尾部字节，配合
        # _decode_paste_bytes 实现截断粘贴无 U+FFFD 污染（方向1 B4）。
        self._paste_partial: bytes = b""

        # ── 慢速多字节 UTF-8 续读缓冲（方向2） ──
        # 跨 read_stdin_once 调用保留 read_utf8_char 读取到的合法 UTF-8 前缀
        # （续字节 select 超时/读取中断时），下次调用拼接补齐——慢速多字节
        # 中文不丢首字节（修复前解码失败返回 None → 首字节被 capture）。
        self._utf8_partial: bytes = b""

        # ── 故障检测 ──
        self._eof_count = 0
        self._select_error_count = 0
        self._exit_reason: str | None = None
        self._fd_status: str = "ok"

    # ── 状态访问（供 Input / InputDispatcher 委托） ────────

    @property
    def fd(self) -> int:
        """stdin 文件描述符。"""
        return self._fd

    @fd.setter
    def fd(self, value: int) -> None:
        """设置 fd（供测试 patch 与装配调整）。"""
        self._fd = value

    @property
    def is_io_running(self) -> bool:
        """I/O 是否处于激活状态（标志位管理，非线程存活检测）。"""
        return self._io_started

    @property
    def interrupted(self) -> bool:
        """中断标志是否被设置。"""
        return self._interrupted.is_set()

    @property
    def active(self) -> threading.Event:
        """I/O 激活事件（供 InputDispatcher.read_stdin_once 状态检查）。"""
        return self._active

    @property
    def stop(self) -> threading.Event:
        """I/O 停止事件（供 InputDispatcher.read_stdin_once 状态检查）。"""
        return self._stop

    @property
    def fd_status(self) -> str:
        """stdin 状态（"ok" / "error"）。"""
        return self._fd_status

    @property
    def select_error_count(self) -> int:
        """select 连续错误计数。"""
        return self._select_error_count

    @property
    def eof_count(self) -> int:
        """EOF 连续计数。"""
        return self._eof_count

    @property
    def exit_reason(self) -> str | None:
        """退出原因（"eof" / "select_error" / None）。"""
        return self._exit_reason

    # ── 中断事件操作（供 Input / InputDispatcher 委托） ────

    def set_interrupted(self) -> None:
        """设置中断标志（_do_interrupt 使用）。"""
        self._interrupted.set()

    def clear_interrupted(self) -> None:
        """清除中断标志（start_io / reset 使用）。"""
        self._interrupted.clear()

    # ── 故障记录（供 InputDispatcher.read_stdin_once 委托） ─

    def can_read(self) -> bool:
        """是否可以执行读取（fd 状态 + 激活/停止标志检查）。

        与 _input.py 原 read_stdin_once 状态检查等价：
          - _fd_status == "error" → False
          - _active 未设置 或 _stop 已设置 → False
        """
        if self._fd_status == "error":
            return False
        if not self._active.is_set() or self._stop.is_set():
            return False
        return True

    def record_select_error(self) -> None:
        """记录一次 select 错误；连续达阈值判定 stdin 不可用。

        与 _input.py 原 read_stdin_once 异常分支等价（仅增量 + 阈值判定，
        不改变返回语义——调用方一律返回 False）。
        """
        self._select_error_count += 1
        if self._select_error_count >= _SELECT_ERROR_THRESHOLD:
            _logger.warning(
                "select 错误连续 %d 次，判定 stdin 不可用",
                self._select_error_count,
            )
            self._exit_reason = "select_error"
            self._fd_status = "error"

    def reset_select_error(self) -> None:
        """select 成功后清零错误计数。"""
        self._select_error_count = 0

    def record_eof(self) -> None:
        """记录一次 EOF；连续达阈值判定 pty 已断开。

        与 _input.py 原 read_stdin_once EOF 分支等价（仅增量 + 阈值判定，
        不改变返回语义——调用方一律返回 False）。

        方向2（EOF 空转修复）：达阈值同时置 ``_fd_status = "error"``——修复前
        ``can_read()`` 恒 True → render 线程每帧 select+read 空转 + 日志刷屏；
        置位后 ``can_read()`` 返回 False（与 ``record_select_error`` 对称）。
        """
        self._eof_count += 1
        if self._eof_count >= _EOF_THRESHOLD:
            _logger.warning(
                "stdin EOF 连续 %d 次，判定 pty 已断开",
                self._eof_count,
            )
            self._exit_reason = "eof"
            self._fd_status = "error"

    def reset_eof(self) -> None:
        """读取成功后清零 EOF 计数。"""
        self._eof_count = 0

    def mark_fd_error(self, exc: BaseException | None = None) -> None:
        """os.read 异常时将 fd 标记为不可用。

        P3（2026-08-07）：区分瞬时错误与致命错误——EINTR（信号中断）/
        EWOULDBLOCK / EAGAIN（非阻塞无数据）为瞬时错误，仅记 debug 日志
        并继续（不置 error）——修复前任何 os.read 异常都置
        ``_fd_status="error"``，``can_read()`` 恒 False，I/O 永久停止。
        其他异常（EBADF 等致命错误）仍置 error。

        Args:
            exc: 触发标记的异常对象（含 errno）；None 表示无异常信息。
        """
        errno_val = getattr(exc, "errno", None) if exc is not None else None
        if errno_val in (errno.EINTR, errno.EWOULDBLOCK, errno.EAGAIN):
            _logger.debug("os.read 瞬时错误 (errno=%s)，继续读取", errno_val)
            return
        self._fd_status = "error"

    # ═══════════════════════════════════════════════════════
    # I/O 状态管理
    # ═══════════════════════════════════════════════════════

    def start_io(self) -> None:
        """激活 I/O 读取（标志位管理模式，不再创建 daemon 线程）。

        stdin 读取由 render 线程通过 ``read_stdin_once()`` 驱动，
        此方法仅重置状态标志位。调用前应确保终端已设置为 cbreak 模式
        （由 EscapeMonitor 保证）。幂等：重复调用仅重置标志位。
        """
        self._interrupted.clear()
        self._stop.clear()
        self._active.set()
        self._io_started = True
        self._eof_count = 0
        self._select_error_count = 0
        self._exit_reason = None
        self._fd_status = "ok"
        # 方向2：启动时清空慢速多字节续读缓冲（会话重启不携带旧 partial）
        self._utf8_partial = b""
        # P2-1：跨会话状态残留修复——同步清空粘贴多字节缓冲
        # （start_io/stop_io 重置后会话边界干净，避免旧粘贴尾字节泄漏到
        # 新会话）；批量读取 pending 一并清空（会话重启不携带旧待处理字节）。
        self._paste_partial = b""
        self._pending = b""
        self._pending_pos = 0

    def stop_io(self) -> None:
        """停用 I/O 读取（标志位管理模式，不再 join 线程）。

        设置 stop 和 active 标志位，render 线程中 ``read_stdin_once()``
        检测到后停止读取。幂等安全。
        """
        self._stop.set()
        self._active.set()  # 确保 read_stdin_once() 状态检查快速退出
        self._io_started = False
        self._fd_status = "ok"
        # 方向2：停止时清空慢速多字节续读缓冲（重启后从干净状态恢复）
        self._utf8_partial = b""
        # P2-1：与 start_io 对称——停止时同步清空粘贴缓冲与 pending
        # （会话边界干净，重启不携带旧状态）。
        self._paste_partial = b""
        self._pending = b""
        self._pending_pos = 0

    def pause_io(self) -> None:
        """暂停 I/O 读取（供 EscapeMonitor 的特殊按键回调使用）。

        暂停后 ``read_stdin_once()`` 在 render 线程中检测到 ``_active``
        未设置时跳过读取。
        """
        self._active.clear()

    def resume_io(self) -> None:
        """恢复 I/O 读取（供 EscapeMonitor 的特殊按键回调使用）。"""
        self._active.set()

    # ═══════════════════════════════════════════════════════
    # stdin 读取原语
    # ═══════════════════════════════════════════════════════

    # ── 批量读取 pending 缓冲（2026-08-14） ──
    # read_stdin_once 批量 os.read(fd, _READ_BATCH) 后将剩余字节 set_pending；
    # read_byte / read_with_timeout / read_utf8_char / try_read_paste 优先消费。
    # 状态跨 read_stdin_once 调用保留——同批读入的字节由后续调用逐字节分发。

    def has_pending(self) -> bool:
        """是否有批量读取的待处理字节。"""
        return self._pending_pos < len(self._pending)

    def take_pending_byte(self) -> bytes:
        """从 pending 缓冲取单个字节（bytes 类型；无则返回 b""）。"""
        if self._pending_pos >= len(self._pending):
            return b""
        b = self._pending[self._pending_pos:self._pending_pos + 1]
        self._pending_pos += 1
        if self._pending_pos >= len(self._pending):
            # 消费完立即释放缓冲（不保留已消费前缀）
            self._pending = b""
            self._pending_pos = 0
        return b

    def drain_pending(self) -> bytes:
        """取走全部待处理字节（供 try_read_paste 粘贴路径一次性取用）。"""
        if self._pending_pos >= len(self._pending):
            return b""
        data = self._pending[self._pending_pos:]
        self._pending = b""
        self._pending_pos = 0
        return data

    def set_pending(self, data: bytes) -> None:
        """存入批量读取的剩余字节（覆盖旧值——调用方先 has_pending 判空）。"""
        self._pending = data
        self._pending_pos = 0

    def prepend_pending(self, data: bytes) -> None:
        """将字节回写到 pending 缓冲前缀（供后续解析正常消费）。

        P2-1（review）：Alt+Backspace 排空检测误读的字节（如多字节 UTF-8
        首字节）回写——原 pending 剩余部分保留在后（保持原始顺序）。
        """
        if not data:
            return
        if self._pending_pos >= len(self._pending):
            self._pending = data
            self._pending_pos = 0
            return
        remaining = self._pending[self._pending_pos:]
        self._pending = data + remaining
        self._pending_pos = 0

    def read_byte(self) -> bytes:
        """从 fd 读取单个原始字节（优先消费 pending，零 syscall）。

        Returns:
            读取到的单字节 bytes 对象；EOF/错误时返回空 bytes。
        """
        if self.has_pending():
            return self.take_pending_byte()
        try:
            return os.read(self._fd, 1)
        except (ValueError, OSError, TypeError):
            return b""

    def read_with_timeout(self, timeout: float, fd: int | None = None) -> bytes | None:
        """使用 select + os.read 读取单个字节，超时返回 None。

        优化（2026-08-14 批量读取）：pending 缓冲有字节时**直接返回**（零
        等待、零 syscall）——批量读取的剩余字节已在内存在，无需 select；
        pending 空时才走 select+os.read。

        P2-4（review）：新增可选 ``fd`` 参数——``read_utf8_char`` 持有外部
        传入 fd（与 ``self._fd`` 可能不同，如 fd_override）时经此参数传递，
        避免 ``read_utf8_char`` 的 fd 参数被忽略（原实现一律读 ``self._fd``）。
        缺省 None 保持 ``self._fd``（既有调用方零变更）。
        """
        if self.has_pending():
            return self.take_pending_byte()
        target_fd = self._fd if fd is None else fd
        try:
            ready, _, _ = select.select([target_fd], [], [], timeout)
        except (ValueError, OSError, TypeError, AttributeError):
            return None
        if not ready:
            return None
        try:
            raw = os.read(target_fd, 1)
            return raw if raw else None
        except (ValueError, OSError, TypeError):
            return None

    def try_read_paste(self, fd: int, first_chars: str) -> str:
        """检测并读取粘贴内容（pending 感知 + 短窗口确认，无退避延迟）。

        优化（2026-08-14）：原实现 ``_paste_skip_counter`` 退避机制——前 10
        次按键每次 3 次 select（0.1+2+3ms）累计 5.1ms 固定打字延迟，达阈后才
        走快速路径。现改为：
          1. pending 非空（read_stdin_once 批量读取剩余字节）→ 突发输入
             （粘贴/IME 上屏），直接读取全部，无需等待；
          2. pending 空 → 单次 select(``_PASTE_CONFIRM_TIMEOUT``=1ms) 确认
             是否还有后续——打字按键间隔 >>1ms 不受影响，粘贴/IME 上屏
             字符流 <1ms 可捕捉。
        总延迟从 5.1ms 降至 ≤1ms，且无需退避状态。
        """
        # 批量读取剩余字节（突发输入）——有则无需等待，直接作为粘贴读全部
        extra = self.drain_pending()
        # ★ P2（review 2026-08-18）：短突发降级——渲染循环 10Hz 轮询下，
        #   同帧快速连击的 1-2 个 ASCII 可打印字符也会批量进 pending；
        #   原实现 pending 非空即判「粘贴」，快速连击被误判（usePaste 钩子
        #   存在时整段消费致输入丢失；单字符语义 handler 收到 char="ab"）。
        #   判定：extra ≤2 字节且全部为 ASCII 可打印（0x20-0x7E，无控制码/
        #   无多字节高位字节）→ 非粘贴，回写 pending 交由解析器逐字节分发，
        #   仅返回首字符（与下方 ESC 回写分支同模式）。粘贴/IME 上屏（多
        #   字节或更长突发）不受影响；2 字符纯 ASCII 粘贴被降级为逐字符
        #   输入，语义等价（无实质危害）。
        if extra and len(extra) <= 2 and all(0x20 <= b < 0x7F for b in extra):
            # 与单字符非粘贴路径对齐：清空跨调用残留的截断 UTF-8 尾部
            # （粘贴边界结束——上一粘贴的 partial 不与本次键入混淆）。
            self._paste_partial = b""
            self.prepend_pending(extra)
            return first_chars
        if not extra:
            # 单字符：短窗口确认是否还有后续（打字 1ms 无感知；粘贴可捕捉）
            try:
                has_more, _, _ = select.select(
                    [fd], [], [], _PASTE_CONFIRM_TIMEOUT,
                )
            except (ValueError, OSError, TypeError, AttributeError):
                return first_chars
            if not has_more:
                # P3（2026-08-07）：非粘贴路径（select 无数据）→ 粘贴边界
                # 结束——清空跨调用残留的截断 UTF-8 尾部，避免与下次独立
                # 粘贴的首字节拼接（两个粘贴边界混淆）。
                self._paste_partial = b""
                return first_chars
        # 有更多数据（pending 或 select 确认）→ 读取全部可读字节
        truncated = False
        try:
            while True:
                has_more, _, _ = select.select([fd], [], [], 0.01)
                if not has_more:
                    break
                more = os.read(fd, 65536)
                if not more:
                    break
                extra += more
                if len(extra) >= 262144:
                    # ★ P2（review）：粘贴上限静默截断修复——超出部分被
                    #   丢弃且无任何提示，粘贴大文件的用户会莫名丢失尾部
                    #   内容且无观测线索。达上限记 warning（含读取字节数，
                    #   可观测）；剩余缓冲字节由下一轮按键路径自然消费
                    #   （不再循环读取，防超大粘贴占满内存）。
                    truncated = True
                    break
        except (ValueError, OSError, TypeError, AttributeError):
            pass
        if truncated:
            _logger.warning(
                "粘贴内容超过 256KB 上限已截断（本次读取 %d 字节）",
                len(extra),
            )
        if not extra:
            return first_chars
        # P1-1（review）：批量读取/select 读入的 extra 若含 ESC（0x1b）——
        # 「普通字符 + 方向键/Home/End 等转义序列」同批读入场景——不得作为
        # 粘贴文本解码（ESC 序列会被当成粘贴内容存入 _paste_partial，方向键
        # 事件永久丢失且残留污染后续粘贴）。回写 pending 交由解析器逐字节
        # 消费（方向键等正常解析），本调用仅返回首字符（非粘贴）。
        if b"\x1b" in extra:
            self.set_pending(extra)
            return first_chars
        # 方向1 B4：粘贴正文经 _decode_paste_bytes 解码——extra 尾部若为截断
        # 多字节 UTF-8 序列，保留到 _paste_partial 留待下次补齐，不再产生
        # U+FFFD 污染（旧实现 extra.decode("utf-8", errors="replace") 直解
        # 截断字节 → U+FFFD）。
        # 边界说明：_paste_partial 状态跨 read_stdin_once 调用保留——粘贴
        # 结束无后续字节时残留不完整尾部（下次粘贴前拼接，语义正确）。
        return first_chars + self._decode_paste_bytes(extra)

    def _decode_paste_bytes(self, data: bytes) -> str:
        """解码粘贴字节流（处理跨调用截断的多字节 UTF-8 序列）。

        将上次残留的 ``_paste_partial`` 与本次 ``data`` 拼接后严格 decode；
        解码失败时经 ``_take_valid_prefix`` 从尾部（最多 3 字节）向前找最大
        合法前缀——前缀严格 decode（仍失败则 errors="replace" 兜底），尾部
        不完整序列存入 ``_paste_partial`` 留待下次拼接（连续多次截断累计正确）。

        假定粘贴字节流仅尾部可能不完整（终端粘贴为合法 UTF-8）；中部损坏
        字节仍以 replace 兜底（可接受）。

        Args:
            data: 本次读取到的粘贴字节。

        Returns:
            解码后的文本（截断部分不产生 U+FFFD，留待下次补齐）。
        """
        buf = self._paste_partial + data
        text, partial = self._take_valid_prefix(buf)
        self._paste_partial = partial
        return text

    @staticmethod
    def _take_valid_prefix(buf: bytes) -> tuple[str, bytes]:
        """从字节流中提取最大合法 UTF-8 前缀，返回 ``(text, partial)``。

        方向2（公共前缀 helper）：从 ``_decode_paste_bytes``「从尾部找最大合法
        前缀」思路提取——供粘贴解码与 ``read_utf8_char`` 慢速续读补齐共用
        （差异封装：单一实现，两处行为一致）。

        - 完整解码成功 → ``(text, b"")``；
        - 从尾部（最多 3 字节）向前找最大严格解码前缀 → ``(前缀文本, 尾部
          不完整序列)``（尾部为可补齐的截断 UTF-8）；
        - 前缀均无法严格解码（中部损坏）→ ``(errors="replace" 文本, b"")``
          （粘贴场景兜底；``read_utf8_char`` 场景调用方自行判定 partial 空
          时返回 None，不产出替换字符）。

        Args:
            buf: 待解码字节流。

        Returns:
            (text, partial) —— text 为严格/兜底解码文本，partial 为待下次
            拼接的不完整 UTF-8 尾部（无则不完整时为空字节串）。
        """
        try:
            return buf.decode("utf-8"), b""
        except UnicodeDecodeError:
            pass
        # 从尾部向前找最大合法前缀（不完整序列最多 3 字节）
        for cut in range(1, min(4, len(buf)) + 1):
            prefix = buf[:-cut] if cut < len(buf) else b""
            try:
                text = prefix.decode("utf-8")
            except UnicodeDecodeError:
                continue
            # P1-1（review）：尾部若含控制字节（0x00-0x1F，如 ESC 转义序列
            # 残留）不存入 partial——控制字节与后续粘贴字节拼接会产生错误
            # 解码/污染粘贴缓冲（修复前 ``b"ab\\xc2\\x1b"`` 的尾部
            # ``b"\\xc2\\x1b"`` 含 ESC 残留，被整段存为 partial，下次粘贴
            # 拼接后误解码）。
            tail = buf[-cut:]
            if any(b < 0x20 for b in tail):
                return text, b""
            return text, tail
        # 前缀均无法严格解码（中部损坏）→ replace 兜底，残留全部丢弃
        return buf.decode("utf-8", errors="replace"), b""

    def read_utf8_char(self, fd: int, first_byte: int) -> str | None:
        """读取完整的多字节 UTF-8 字符序列。

        方向2（慢速多字节不丢字节）：续字节 select 超时/读取中断时，已读
        字节若可组成合法 UTF-8 前缀则存入 ``_utf8_partial`` 返回 None（待
        下次调用拼接补齐）；不可组成则清空返回 None（首字节调回 capture
        路径）。跨 read_stdin_once 调用保留 partial——慢速多字节不丢首字节。

        优化（2026-08-14 批量读取）：续字节经 ``read_with_timeout`` 读取——
        pending 缓冲有字节时零等待直取（同批 read 的中文等多字节序列不再
        select 超时），pending 空时才 select 等待。
        """
        replaced_prefix = ""
        if self._utf8_partial:
            # 有跨调用残留 partial——校验当前 first_byte 是否为合法续字节
            # （0x80-0xBF）。续字节被新字符首字节打断（慢速输入间隔中插入
            # 新字符）时 partial 无效 → 清空后按新字符首字节重新解析（修复
            # 前把新首字节拼进旧序列 → 解码失败 → 两个字符均丢失）。
            if not (0x80 <= first_byte <= 0xBF):
                # P2-3（review）：被打断的 partial 不直接丢弃——以
                # errors="replace" 消费（不完整序列以 U+FFFD 呈现而非静默
                # 消失），作为前缀与本次新字符解析结果合并返回（调用方按
                # 连续文本分发，字节不丢）。
                replaced_prefix = self._utf8_partial.decode(
                    "utf-8", errors="replace",
                )
                self._utf8_partial = b""
            first = self._utf8_partial[0] if self._utf8_partial else first_byte
            buf = bytes([first_byte])
        else:
            first = first_byte
            buf = bytes([first_byte])

        if (first & 0xE0) == 0xC0:
            total_bytes = 2
        elif (first & 0xF0) == 0xE0:
            total_bytes = 3
        elif (first & 0xF8) == 0xF0:
            total_bytes = 4
        else:
            # P2-3：非 UTF-8 首字节（如打断后直接调用）——若已有 replace
            # 消费的前缀，返回前缀（不丢）；否则清空 partial 返回 None。
            self._utf8_partial = b""
            if replaced_prefix:
                return replaced_prefix
            return None

        # 已读字节数（含 partial 与当前 first_byte）
        have = len(self._utf8_partial) + 1
        for _ in range(total_bytes - have):
            # read_with_timeout：优先从 pending 取（零等待），空则 select
            # 等待 _UTF8_READ_TIMEOUT；超时/EOF 均返回 None → break。
            # P2-4（review）：透传外部 fd（read_utf8_char 的 fd 参数不再被
            # 忽略——原实现经 read_with_timeout 一律读 self._fd）。
            more = self.read_with_timeout(_UTF8_READ_TIMEOUT, fd)
            if more is None:
                break
            buf += more

        full = self._utf8_partial + buf
        try:
            text = full.decode("utf-8")
            self._utf8_partial = b""
            return replaced_prefix + text
        except UnicodeDecodeError:
            pass
        # 从尾部找最大合法前缀——可组成合法前缀（不完整序列）→ 存 partial
        # 返回 None（待下次补齐）；不可组成 → 清空返回 None（不产生 U+FFFD）。
        _text, partial = self._take_valid_prefix(full)
        if replaced_prefix:
            # P2-3：已有 replace 消费的打断前缀——不能返回 None（前缀会丢
            # 失）。本次未完整序列存 partial 留待补齐，返回前缀文本。
            self._utf8_partial = partial
            return replaced_prefix
        if partial:
            self._utf8_partial = partial
            return None
        self._utf8_partial = b""
        return None

    def _flush_stdin_residual(
        self, max_flush: int = 50, budget: float = 0.05
    ) -> None:
        """非阻塞清理 stdin 残留字节（总体时间预算 + 短超时非阻塞排空）。

        方向1 B8：旧实现每次 select 超时固定 0.05s——fd 恒可读（持续输入）时
        50 字节 × 0.05s 最坏阻塞 2.5s（render 线程卡顿）。改为总体时间预算
        （默认 50ms）+ 每次短超时（≤1ms）非阻塞排空：超预算即 break；无数据
        时 select 立即空快速返回（不消耗预算）。保留 ``max_flush`` 上限与
        ``stop`` 检查。调用方（``flush_stdin_buffer``/``_do_interrupt``）签名
        不变（新增可选参数默认值向后兼容）。termios 可用时
        ``flush_stdin_buffer`` 后续 tcflush 兜底刷洗内核队列（极端输入下少排
        若干字节语义安全）。
        """
        if self._fd_status == "error":
            return
        flushed = 0
        deadline = time.monotonic() + budget
        while flushed < max_flush:
            if self._stop.is_set():
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                ready, _, _ = select.select(
                    [self._fd], [], [], min(0.001, remaining),
                )
                if not ready:
                    break
                os.read(self._fd, 1)
                flushed += 1
            except (ValueError, OSError, TypeError, AttributeError):
                _logger.debug("排空 stdin 残留时异常", exc_info=True)
                break

    def flush_stdin_buffer(self, max_flush: int = 50) -> None:
        """公开方法：非阻塞清理 stdin 残留字节 + termios 缓冲区刷洗。

        先使用 select 排空可读字节（委托 _flush_stdin_residual），
        再通过 tcflush 刷洗内核输入队列（仅在 HAS_TERMIOS=True 时执行）。

        Args:
            max_flush: 最大排空字节数限制（传递给 _flush_stdin_residual）。
        """
        self._flush_stdin_residual(max_flush)
        if HAS_TERMIOS:
            try:
                termios.tcflush(self._fd, termios.TCIFLUSH)
            except Exception:
                _logger.debug("tcflush 失败", exc_info=True)


__all__ = ["InputIO"]
