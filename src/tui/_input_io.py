"""InputIO — TUI 输入 I/O 读取层（提取自 _input.py，方向A 步骤1）。

将 Input 上帝类中的 stdin 读取原语与 I/O 状态机提取为独立类，逐行迁移，
保持零逻辑改动：
  - 读取原语: read_byte / read_with_timeout / read_utf8_char / try_read_paste
  - 残留排空: _flush_stdin_residual / flush_stdin_buffer
  - I/O 状态机: start_io / stop_io / pause_io / resume_io（_active / _stop / _interrupted）
  - 故障检测: _eof_count / _select_error_count / _exit_reason / _fd_status
  - 粘贴退避: _paste_skip_counter / _paste_skip_threshold

InputIO 持有 fd 与粘贴退避状态；``_interrupted`` 事件仍由 Input 公开属性
``interrupted`` 委托读取。``_UTF8_READ_TIMEOUT`` 从 _input_parser 导入。

设计模式: 单一职责（SRP）提取——读取层仅负责原始 I/O，不含缓冲/分发。

依赖方向:
  _input.py → _input_io.py 单向依赖；本模块不得 import _input（避免循环）。

模块级 ``import select`` / ``import os`` 供读取方法使用；可被
``patch("select.select", ...)`` / ``patch("os.read", ...)`` 全局拦截
（与 _input.py 原行为等价）。
"""

from __future__ import annotations

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

        # ── 粘贴退避优化 ──
        self._paste_skip_counter: int = 0
        self._paste_skip_threshold: int = 10

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

    def mark_fd_error(self) -> None:
        """os.read 异常时将 fd 标记为不可用。"""
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

    def read_byte(self) -> bytes:
        """从 fd 读取单个原始字节。

        Returns:
            读取到的单字节 bytes 对象；EOF/错误时返回空 bytes。
        """
        try:
            return os.read(self._fd, 1)
        except (ValueError, OSError, TypeError):
            return b""

    def read_with_timeout(self, timeout: float) -> bytes | None:
        """使用 select + os.read 读取单个字节，超时返回 None。"""
        try:
            ready, _, _ = select.select([self._fd], [], [], timeout)
        except (ValueError, OSError, TypeError, AttributeError):
            return None
        if not ready:
            return None
        try:
            raw = os.read(self._fd, 1)
            return raw if raw else None
        except (ValueError, OSError, TypeError):
            return None

    def try_read_paste(self, fd: int, first_chars: str) -> str:
        """检测并读取粘贴内容（退避 select 检测突发字符流）。"""
        # 快速路径：若近期均非粘贴，跳过退避检测
        if self._paste_skip_counter >= self._paste_skip_threshold:
            try:
                has_more, _, _ = select.select([fd], [], [], 0.0)
            except (ValueError, OSError, TypeError, AttributeError):
                return first_chars
            if not has_more:
                return first_chars
            # 有数据，重置计数器并进入粘贴检测
            self._paste_skip_counter = 0
        else:
            for delay in (0.0001, 0.002, 0.003):
                try:
                    has_more, _, _ = select.select([fd], [], [], delay)
                except (ValueError, OSError, TypeError, AttributeError):
                    return first_chars
                if not has_more:
                    self._paste_skip_counter += 1
                    return first_chars
        extra = b""
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
                    break
        except (ValueError, OSError, TypeError, AttributeError):
            pass
        if not extra:
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
            return text, buf[-cut:]
        # 前缀均无法严格解码（中部损坏）→ replace 兜底，残留全部丢弃
        return buf.decode("utf-8", errors="replace"), b""

    def read_utf8_char(self, fd: int, first_byte: int) -> str | None:
        """读取完整的多字节 UTF-8 字符序列。

        方向2（慢速多字节不丢字节）：续字节 select 超时/读取中断时，已读
        字节若可组成合法 UTF-8 前缀则存入 ``_utf8_partial`` 返回 None（待
        下次调用拼接补齐）；不可组成则清空返回 None（首字节调回 capture
        路径）。跨 read_stdin_once 调用保留 partial——慢速多字节不丢首字节。
        """
        if self._utf8_partial:
            # 有跨调用残留 partial——当前 first_byte 为续字节：以 partial
            # 首字节推总字节数（续字节本身无法判定长度），续读补齐。
            first = self._utf8_partial[0]
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
            self._utf8_partial = b""
            return None

        # 已读字节数（含 partial 与当前 first_byte）
        have = len(self._utf8_partial) + 1
        for _ in range(total_bytes - have):
            try:
                has_data, _, _ = select.select(
                    [fd], [], [], _UTF8_READ_TIMEOUT,
                )
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

        full = self._utf8_partial + buf
        try:
            text = full.decode("utf-8")
            self._utf8_partial = b""
            return text
        except UnicodeDecodeError:
            pass
        # 从尾部找最大合法前缀——可组成合法前缀（不完整序列）→ 存 partial
        # 返回 None（待下次补齐）；不可组成 → 清空返回 None（不产生 U+FFFD）。
        _text, partial = self._take_valid_prefix(full)
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
