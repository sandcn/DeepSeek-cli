"""bash 工具的辅助支持模块（从 bash.py 拆分，2026-08-06 架构整理）。

职责：bash 工具运行时使用的**纯辅助函数与常量**——命令安全防护、
ANSI 剥离、PTY EIO 归一化、回车覆盖模拟、进程树管理。

与 ``BashFunc`` 工具类解耦后，``bash.py`` 专注工具类逻辑；
本模块保持 ``from src.tools.bash import X`` 兼容（bash.py re-export）。
"""

from __future__ import annotations
import asyncio
import errno as _errno
import logging
import os
import re as _re
import signal as _signal
import subprocess
import sys
import time

from .._compat import dataclass
from ._process_output import decode_output

logger = logging.getLogger(__name__)


# ── 危险命令模式（运行时安全防护） ───────────────────────
# ★ P0 安全防护：运行时检查命令内容，防止 LLM 忽略 schema 指令
# 执行系统破坏操作。schema 侧和运行时侧双保险。
_DANGEROUS_PATTERNS: list[tuple[str, str]] = [
    (r'\brm\s+(-rf|--recursive)\s+/\*', '递归删除根目录（通配符 /*）'),
    (r'\brm\s+(-rf|--recursive)\s+/', '递归删除根目录 /'),
    (r'\bmkfs\.', '格式化文件系统'),
    (r'\bdd\s+if=', '磁盘直接写入（dd）'),
    (r'\bsudo\b', 'sudo 提权'),
    (r'\bsu\b', 'su 提权'),
    (r'\bdoas\b', 'doas 提权'),
    (r'\bpkexec\b', 'pkexec 提权'),
    (r'\bchown\b', '修改文件所有者'),
    (r'\bchmod\s+.*777\b', 'chmod 777 权限开放'),
]
"""危险命令模式列表：每个条目为 (正则, 描述)。匹配时拒绝执行。"""


def _has_dangerous_command(command: str) -> str | None:
    """检查命令是否包含危险模式，返回描述或 None。

    覆盖的危险模式：
      - rm -rf / 及其通配符变体 rm -rf /*
      - 文件系统破坏：mkfs、dd
      - 权限提升：sudo、su、doas、pkexec
      - 权限开放：chmod 777、chown
    """
    for pattern, desc in _DANGEROUS_PATTERNS:
        if _re.search(pattern, command):
            return desc
    return None


# ── 中断检查间隔 ─────────────────────────────────
# _run_pty / _run_pipe 读取循环中每隔 N 秒检查一次 ESC 中断信号
# （is_interrupted）。200ms 平衡响应速度与 CPU 开销。
_INTERRUPT_CHECK_INTERVAL = 0.2

# ── 单次读取块大小 ────────────────────────────────
# _read_loop 每次从 StreamReader 读取的最大字节数。与 Python 标准库
# _UnixReadPipeTransport 的 max_size（256KB）一致：单次 read 通常能取到
# transport 一次到达的全部数据，减少循环次数；超长行/无换行大数据在本地
# bytearray 累积，不受 StreamReader 默认 64KB limit 限制（弃用 readline：
# 其 LimitOverrunError 处理会 clear 整个缓冲，导致超长行数据丢失）。
_READ_CHUNK_SIZE = 256 * 1024


class PipeFdWriter:
    """基于 ``os.pipe()`` 的后台任务 stdin 写端。

    为什么需要：Cygwin 的 ``asyncio.create_subprocess_exec(stdin=PIPE)``
    内部用 **socketpair** 实现 stdin，原生 Windows 控制台程序（cmd /
    more.com / findstr / sort 等）无法从 socket 句柄读取 stdin——表现为
    进程立即退出、``bash_opt stdin`` 报「管道已关闭」。``os.pipe()`` 创建
    的是真正的管道句柄，原生程序可以正常读取。

    接口与 ``asyncio.StreamWriter`` 的常用子集一致（``write`` / ``drain`` /
    ``close`` / ``wait_closed``），供 ``bash_opt._write_unlocked`` 直接复用。
    ``drain`` 在线程池中完成实际写入，避免阻塞事件循环；写缓冲满时按
    ``BlockingIOError`` 短暂等待，总超时 ``timeout`` 秒后抛 ``TimeoutError``
    （``OSError`` 子类，调用方会转成可读错误）。
    """

    def __init__(self, fd: int, *, timeout: float = 5.0):
        self._fd = int(fd)
        self._timeout = float(timeout)
        self._buffer = bytearray()
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    def write(self, data: bytes) -> None:
        if self._closed:
            raise ValueError("write to closed stdin")
        self._buffer.extend(data)

    async def drain(self) -> None:
        if self._closed or not self._buffer:
            return
        data = bytes(self._buffer)
        self._buffer.clear()
        await asyncio.get_running_loop().run_in_executor(
            None, self._write_all, data)

    def _write_all(self, data: bytes) -> None:
        """阻塞式写完整块（在线程池中执行；带总超时）。"""
        view = memoryview(data)
        deadline = time.monotonic() + self._timeout
        while view:
            try:
                written = os.write(self._fd, view)
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("写入 stdin 超时（目标进程未读取输入）")
                time.sleep(0.01)
                continue
            if written <= 0:
                raise OSError("写入 stdin 失败：write 返回 0")
            view = view[written:]

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._buffer:
            try:
                self._write_all(bytes(self._buffer))
            except OSError:
                logger.debug("关闭 stdin 前刷新残留数据失败", exc_info=True)
            self._buffer.clear()
        try:
            os.close(self._fd)
        except OSError:
            pass

    async def wait_closed(self) -> None:
        """兼容 ``StreamWriter.wait_closed``：close() 已同步完成。"""
        return None


def open_interactive_stdin() -> tuple[int, PipeFdWriter]:
    """创建供原生 Windows 子进程读取的 stdin 管道（Cygwin / MSYS2 用）。

    Returns:
        ``(read_fd, writer)``：``read_fd`` 传给子进程作为 stdin（启动后父进程
        需关闭它），``writer`` 交给 ``bash_opt`` 写入。
    """
    read_fd, write_fd = os.pipe()
    os.set_blocking(write_fd, False)
    return read_fd, PipeFdWriter(write_fd)


# 模块级预编译正则（消除 _strip_ansi 每次调用的 re.compile 开销）
_ANSI_STRIP_RE = _re.compile(
    r'\x1B(?:'
    r'[\]PX^_].*?(?:\x1b\\|\x07)|'     # DCS/OSC/PM/APC 字符串序列
    r'[ -/]*[0-Z\\\]-~]|'               # 非 CSI：ESC + 中间字节* + 终结字节
    r'\[[0-?]*[ -/]*[@-~]'              # CSI：ESC [ + 参数* + 中间* + 终结
    r')'
)
_CTRL_CHAR_RE = _re.compile(r'[\x08\x0b\x0c]')


def _strip_ansi(text: str) -> str:
    """剥离所有 ANSI 转义序列和破坏终端布局的控制字符。

    使用 ECMA-48 完整模式匹配所有 ANSI 转义序列：
      - CSI 序列：\\x1b[ 参数字节(0x30-0x3F) 中间字节(0x20-0x2F) 终结字节(0x40-0x7E)
        → 覆盖 \\x1b[31m、\\x1b[2J、\\x1b[?25l、\\x1b[?1049h 等
      - 非 CSI 序列：\\x1b [中间字节(0x20-0x2F)]* 终结字节(0x30-0x7E, 排除 0x5B=[)
        → 覆盖 \\x1b7(DECSC)、\\x1b8(DECRC)、\\x1bM(RI)、\\x1bD(IND)、
          \\x1b(B 字符集选择等
      - 字符串序列（DCS/OSC/PM/APC）：\\x1b [\\]PX^_] 数据 ST(\\x1b\\ 或 \\x07)
        → 覆盖 \\x1b]0;title\\x07(设标题)、\\x1b]8;;url\\x1b\\(超链接) 等

    额外剥离以下光标/显示破坏性控制字符（常见于进度条/工具输出）：
      - \\b (0x08)：退格，光标左移 → 可越界写入相邻区域
      - \\x0b (0x0B)：垂直制表符，光标下移 → 跳过行，破坏布局
      - \\x0c (0x0C)：换页 → 某些终端清屏
    \\r (0x0D) 故意保留，用于进度条行内覆盖效果（如 wget 进度）。

    PTY 模式下子进程输出包含各种 ANSI 序列（颜色/光标移动/清屏/滚动区设
    置、超链接、标题设置等），这些序列会破坏终端 UI 布局，必须全部剥离。
    """
    # 1. 剥离 ANSI 转义序列
    #    优先级：字符串序列 > 非 CSI > CSI
    #    字符串序列（DCS/OSC/PM/APC）：\x1b [\]PX^_] 数据 (?:\x1b\\|\x07)
    #      → 必须放在非 CSI 前，防止 \x1b]/\x1bP 被截断为 2 字节
    #    非 CSI 序列：\x1b [中间字节(0x20-0x2F)]* 终结字节(0x30-0x7E, 排除 0x5B=[)
    #      → 覆盖 DECSC/DECRC/\x1b(B 字符集选择等
    #    CSI 序列：\x1b[ + 参数(0x30-0x3F)* + 中间(0x20-0x2F)* + 终结(0x40-0x7E)
    result = _ANSI_STRIP_RE.sub('', text)
    # 2. 剥离光标/显示破坏性控制字符（\b\x0b\x0c）
    #    保留 \t(0x09)、\n(0x0A)、\r(0x0D→进度条行内覆盖) 等不影响终端布局的字符。
    result = _CTRL_CHAR_RE.sub('', result)
    return result


class _PtyEioAsEofProtocol(asyncio.StreamReaderProtocol):
    """PTY master 端读到 EIO（slave 关闭）时归一化为正常 EOF。

    PTY 场景下，子进程退出会关闭 slave 端，此时 master 端 read 返回
    EIO（OSError errno=EIO）。但用户空间 StreamReader 的缓冲中可能还有
    未消费的数据——子进程一次性写入多行后立刻退出（echo/seq/printf 等
    快速命令），数据整体到达缓冲，随后 EIO 才到达。

    默认 ``StreamReaderProtocol.connection_lost`` 会把非 None 异常
    ``set_exception`` 到 reader，导致后续 ``readline()`` 直接抛 EIO，
    ``_read_loop`` 把 EIO 误当 EOF break，丢弃缓冲中剩余的行
    （用户侧现象：多行输出只返回第一行）。

    这里把 EIO 归一化为 ``feed_eof()``：缓冲中剩余数据先被 ``readline()``
    消费完，再返回 EOF（b''），与真实终端「读完缓冲再遇 EOF」一致。
    """
    def connection_lost(self, exc):
        if exc is not None and getattr(exc, 'errno', None) == _errno.EIO:
            exc = None  # PTY slave 关闭 → 正常 EOF（先消费缓冲剩余数据）
        super().connection_lost(exc)


#: ANSI 重置码（\x1b[0m）——_wrap_colored_line 颜色包裹行尾使用
_ANSI_RESET = "\x1b[0m"


def _wrap_colored_line(safe: str, color: str) -> str:
    """颜色包裹工具输出行：行尾 ``\\n`` 保持在 RESET 之外（BUG-79）。

    工具输出行（``_read_loop._handle_line`` 按行收集）自带行尾 ``\\n``。
    若按 ``f"{color}{safe}{RESET}"`` 包裹，``\\n`` 被夹在 color 与 RESET
    之间——下游 ``EventDispatcher._on_tool_output`` 的 ``rstrip("\\n")``
    与 ``_ToolOutputMixin.append_tool_output`` 的「剔除尾空 segment」
    （BUG-78）都因文本以 ``\\x1b[0m`` 结尾而失效 → split 出纯 RESET 空
    segment → 工具卡每个 stderr 行多渲染一个空白行（用户报障「调用 bash
    工具后 TUI 显示空白行」的根因）。本函数把行尾 ``\\n`` 移到 RESET
    之后，恢复下游尾部换行剥离链。safe 须为已剥 ANSI 的纯文本（调用方
    保证；与 ``_simulate_terminal`` 同契约）。

    Args:
        safe: 已剥 ANSI 的纯文本行（可含行尾 \\n；\\r 覆盖语义已兑现）。
        color: 前景色转义码（如 ``\\x1b[31m``）。

    Returns:
        颜色包裹后的文本：``<color><内容><RESET>``；行尾 \\n 位于 RESET
        之后（无行尾 \\n 时原样包裹）。
    """
    if safe.endswith('\n'):
        return f"{color}{safe[:-1]}{_ANSI_RESET}\n"
    return f"{color}{safe}{_ANSI_RESET}"


def _simulate_terminal(text: str) -> str:
    """模拟终端回车（\\r）语义：\\r 使光标回到当前行首，后续字符覆盖。

    终端输出中的 \\r（0x0D）不产生新行，而是将光标移回当前行首，随后写入
    的字符从行首开始覆盖已有内容。例如进度条 ``10%\\r20%\\r30%`` 在真实终端
    只显示 ``30%``；``abc\\rXY`` 显示为 ``XYc``（XY 覆盖前两字符，c 保留）。
    工具卡片（toolcard）若把 \\r 当普通字符渲染会出现乱码/宽度异常，这里
    预先兑现 \\r 的覆盖语义，使卡片呈现与真实终端一致。

    按 ``\\n`` 分段处理（\\r 只影响当前行内位置，不跨行）；不含 \\r 时原样
    返回（零开销快路径）。含 ANSI 转义序列的文本结果不确定——调用方须先经
    ``_strip_ansi`` 剥离（bash 输出显示路径已保证）。

    Args:
        text: 工具输出文本（可含 \\n）。

    Returns:
        应用回车覆盖后的文本。
    """
    if '\r' not in text:
        return text
    parts = text.split('\n')
    for i, part in enumerate(parts):
        if '\r' not in part:
            continue
        chars: list[str] = []
        col = 0
        for ch in part:
            if ch == '\r':
                col = 0
            elif col < len(chars):
                chars[col] = ch
                col += 1
            else:
                chars.append(ch)
                col += 1
        parts[i] = ''.join(chars)
    return '\n'.join(parts)


# ── 进程树杀死 ─────────────────────────────────────

@dataclass(frozen=True)
class KillResult:
    """进程树杀死结果（含杀后校验结论，供调用方反馈 / 记日志）。

    Attributes:
        pid: 进程树根 PID（非法入参时为 0）。
        attempts: 实际执行的「收集 + 杀死 + 校验」轮数（>=1）。
        killed_pids: 各轮收集到的进程树 PID（根 + 递归后代，去重排序）。
        killed_win_pids: Cygwin / MSYS2 下各轮收集到的 Windows PID（原生
            进程树 / Job 成员 / Store 应用兜底；去重排序）。
        remaining_pids: 最后一轮校验时仍存活的 PID（空 = 已全部退出）。
        remaining_win_pids: 最后一轮校验时仍存活的 Windows PID。
        verified: 是否执行了杀后校验（``verify=False`` 时为 False）。
        pid_reused: 记录的 PID 已被系统复用（创建时间不匹配）→ 为避免误杀
            无关进程，**未执行**进程树杀（仅 Windows 可判定）。
        job_remaining: 校验时 Windows Job 内仍存活的进程数（0 = Job 已清空；
            Job 成员可能不在 ``remaining_pids`` 里——中间父进程退出后
            Toolhelp32 链断裂，只能通过 Job 计数确认）。
    """

    pid: int
    attempts: int = 0
    killed_pids: tuple[int, ...] = ()
    killed_win_pids: tuple[int, ...] = ()
    remaining_pids: tuple[int, ...] = ()
    remaining_win_pids: tuple[int, ...] = ()
    verified: bool = False
    pid_reused: bool = False
    job_remaining: int = 0

    @property
    def success(self) -> bool:
        """进程树是否已被确认全部退出（未校验 / pid 被复用时恒为 False）。"""
        return (self.verified and not self.remaining_pids
                and not self.remaining_win_pids
                and not self.pid_reused and not self.job_remaining)


#: 杀进程树的默认最大尝试轮数（首杀 + 校验未通过时的补杀）
KILL_MAX_ATTEMPTS: int = 3
#: 每轮杀死后等待进程退出的校验超时（秒）——轮询校验，全部退出即提前返回
_KILL_VERIFY_TIMEOUT: float = 0.05
#: 校验轮询间隔（秒）
_KILL_VERIFY_POLL: float = 0.005
#: 后代收集的最大深度（防内核异常 / PPid 成环导致的死循环）
_DESCENDANT_MAX_DEPTH: int = 10
#: 辅助命令（ps / taskkill）的超时（秒）
_PROC_TOOL_TIMEOUT: float = 5.0


#: ``/proc`` 为「按需模拟」的慢速平台的 ``sys.platform`` 前缀。
#: 此类平台读取 ``/proc/<pid>/status`` 单进程需数百 ms（实测 Cygwin 约
#: 0.17s/进程，整表 6~8s），而 ``ps`` 一次仅约 0.17s。
_SLOW_PROC_PLATFORMS: tuple[str, ...] = ("cygwin", "msys")


def _prefer_ps_process_tree() -> bool:
    """进程树收集是否优先用 ``ps``（代替慢速模拟的 ``/proc``）。

    Cygwin / MSYS2 的 ``/proc`` 由运行时按需模拟：``os.listdir`` 很快，但
    逐个读取 ``/proc/<pid>/status`` 会触发昂贵的模拟层查询（实测 Cygwin
    约 0.17s/进程，整表 6~8s）。进程树收集位于 **ESC 中断**等同步热路径上
    ——若沿用 ``/proc``，中断时要等 6~8s 才杀进程，用户按一次 ESC 看起来
    「毫无反应」，需反复按多次（本函数修复的根因）。此类平台改走 ``ps``
    （单次约 0.17s），把中断响应拉回亚秒级。

    非此类平台（Linux / macOS / *BSD / Android Termux）保持原有
    ``/proc`` 优先策略（Linux 上 /proc 单次遍历 O(N) 且最快）。
    """
    return sys.platform.startswith(_SLOW_PROC_PLATFORMS)


def _is_windows() -> bool:
    """是否 Windows **原生**环境（Cygwin 的 ``os.name`` 为 ``posix``，不算）。"""
    return os.name == 'nt'


def _is_cygwin_like() -> bool:
    """是否 Cygwin / MSYS2（POSIX 进程表 + Windows 内核）。"""
    return (sys.platform or '').startswith(('cygwin', 'msys'))


def _is_windows_like() -> bool:
    """是否能调用 Win32 进程 API（原生 Windows / Cygwin / MSYS2）。"""
    return _is_windows() or _is_cygwin_like()


def _winpid_of(pid: int, *, retries: int = 0) -> int | None:
    """把进程号换算成 Windows PID（Cygwin 读 ``/proc/<pid>/winpid``）。"""
    try:
        from . import _win_proc
        return _win_proc.to_winpid(int(pid), retries=retries)
    except Exception:  # pragma: no cover - 依赖系统调用
        logger.debug("POSIX -> Windows PID 换算失败: %s", pid, exc_info=True)
        return None


def _self_winpid() -> int | None:
    """当前 Python 进程的 Windows PID（用于杀树时排除自身，防误杀）。"""
    return _winpid_of(os.getpid())


def _run_quiet(argv: list[str]) -> str:
    """执行辅助命令并返回 stdout 文本（失败 / 超时返回空串，绝不抛异常）。"""
    try:
        completed = subprocess.run(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=_PROC_TOOL_TIMEOUT,
            check=False,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
    except (OSError, subprocess.SubprocessError):
        return ''
    # ★ 编码自适应（Windows 乱码修复）：辅助命令（ps / taskkill 等）在
    #   Windows 上同样按控制台代码页输出，修复前按 UTF-8 解码会得到
    #   U+FFFD（进程树解析虽只用数字，但本地化错误文本/进程名会乱码）。
    return decode_output(completed.stdout)


def _read_ppid(pid: int) -> int | None:
    """读取 ``/proc/<pid>/status`` 的 ``PPid`` 字段（Linux / Cygwin 通用）。

    读不到（进程已退出 / 无权限 / 无该字段）返回 ``None``。
    """
    try:
        with open(f'/proc/{pid}/status', encoding='utf-8', errors='replace') as f:
            for line in f:
                if line.startswith('PPid:'):
                    parts = line.split()
                    if len(parts) >= 2:
                        return int(parts[1])
                    return None
    except (OSError, ValueError):
        return None
    return None


def _parse_ps_output(output: str) -> dict[int, list[int]]:
    """解析 ``ps`` 输出为 ``PPid → [child_pid]``。

    两种格式都能解析：
      - 带表头（``ps -ef`` / ``ps ax``）：按 ``PID`` / ``PPID`` 列名定位列；
      - 无表头（``ps -eo pid=,ppid=``）：取前两个数字列。
    非法行（表头 / 空行 / 非数字）自动跳过。
    """
    mapping: dict[int, list[int]] = {}
    header: tuple[int, int] | None = None
    for line in output.splitlines():
        parts = line.split()
        if not parts:
            continue
        if header is None:
            upper = [part.upper() for part in parts]
            if 'PID' in upper and 'PPID' in upper:
                header = (upper.index('PID'), upper.index('PPID'))
                continue
        if header is not None:
            pid_index, ppid_index = header
            if max(pid_index, ppid_index) >= len(parts):
                continue
            columns = (parts[pid_index], parts[ppid_index])
        else:
            if len(parts) < 2:
                continue
            columns = (parts[0], parts[1])
        try:
            child, parent = int(columns[0]), int(columns[1])
        except ValueError:
            continue
        mapping.setdefault(parent, []).append(child)
    return mapping


def _children_map_from_ps() -> dict[int, list[int]]:
    """用 ``ps`` 构建 ``PPid → [child_pid]``（无 /proc 平台的兜底通道）。

    依次尝试多套命令形态——GNU/BSD 的 ``ps -eo pid=,ppid=``（无表头）、
    通用性最好的 ``ps -ef`` / ``ps ax``（带 ``PID``/``PPID`` 表头，
    Cygwin 的 procps 只支持这类）；首个解析出结果的形态即采用，全部
    不可用时返回空表（调用方自然退化为「无后代可收集」）。
    """
    for argv in (['ps', '-eo', 'pid=,ppid='], ['ps', '-ef'], ['ps', 'ax']):
        mapping = _parse_ps_output(_run_quiet(argv))
        if mapping:
            return mapping
    return {}


def _children_map_from_windows() -> dict[int, list[int]]:
    """Windows 原生进程表（Toolhelp32）→ ``PPid → [child_pid]``。

    修复根因：Windows 没有 ``/proc``，也没有原生 ``ps``——修复前
    ``_children_map`` 在原生 Windows 上只能退回 ``ps``（通常不存在，返回
    空表），``_collect_tree_pids`` 因此**永远只返回根 PID**，op=kill 完全
    依赖 ``taskkill /F /T`` 的隐式树遍历。一旦根进程先退出（后台 shell
    结束、其启动的常驻子进程成为孤儿），``taskkill /PID 根`` 找不到进程，
    后代全部残留——用户侧现象即「bash_opt kill 不能杀死整个进程树」。

    Toolhelp32 的 ``th32ParentProcessID`` 是**创建时**的父 PID，父进程退出后
    保持不变，因此用本函数仍能定位根进程已退出后残留的孤儿后代。

    收集失败（快照不可用）返回空表；调用方据此退化（杀树时由
    ``_kill_tree_once`` 退回 ``taskkill /T`` 隐式遍历）。
    """
    try:
        from . import _win_proc
    except Exception:  # pragma: no cover - 依赖链异常（按不可用处理）
        logger.debug("导入 _win_proc 失败", exc_info=True)
        return {}
    try:
        processes = _win_proc.list_processes()
    except Exception:  # pragma: no cover - 依赖系统调用
        logger.debug("Windows 进程表读取失败", exc_info=True)
        return {}
    mapping: dict[int, list[int]] = {}
    for pid, ppid, _name in processes:
        if pid == ppid:
            # 防御：异常记录（PPID 指向自身）会在 DFS 中形成自环
            continue
        mapping.setdefault(ppid, []).append(pid)
    return mapping


def _children_map() -> dict[int, list[int]]:
    """构建 ``PPid → [child_pid]`` 映射（跨平台多策略）。

    优先解析 ``/proc/<pid>/status`` 的 ``PPid`` 字段（Linux / Android
    Termux，单次遍历 O(N) 且最快）；``/proc`` 不可用或未解析到任何父子
    关系时回退 ``ps``（macOS / BSD）。``/proc`` 为慢速模拟层的平台
    （Cygwin / MSYS2，见 ``_prefer_ps_process_tree``）**直接走 ps**——
    避免进程树收集（ESC 中断热路径）被逐进程 0.17s、整表 6~8s 的
    ``/proc/<pid>/status`` 读取阻塞。原生 Windows 走 Toolhelp32 进程表
    （没有 /proc；**不回退 ps**——Windows PATH 里的 ``ps`` 多为 Cygwin/Git
    Bash 的 POSIX 实现，PID 空间与 Windows PID 不同，误用会收集到错误
    后代甚至误杀。快照失败时返回空表，由 ``_kill_tree_once`` 退回
    ``taskkill /T`` 兜底）。
    """
    if _prefer_ps_process_tree():
        return _children_map_from_ps()
    if _is_windows():
        return _children_map_from_windows()
    mapping: dict[int, list[int]] = {}
    if os.path.isdir('/proc'):
        try:
            entries = os.listdir('/proc')
        except OSError:
            entries = []
        for entry in entries:
            if not entry.isdigit():
                continue
            parent = _read_ppid(int(entry))
            if parent is not None:
                mapping.setdefault(parent, []).append(int(entry))
    if mapping:
        return mapping
    return _children_map_from_ps()


def _collect_descendants(root_pid: int, result: list[int],
                         max_depth: int = _DESCENDANT_MAX_DEPTH) -> None:
    """递归收集所有后代进程 PID（跨平台）。

    实现策略（单次扫描 + DFS 查表）：
      1. 一次扫描构建 PPid → [child_pids] 映射表
         （/proc/<pid>/status 优先，``ps -eo pid=,ppid=`` 兜底）；
      2. DFS（栈实现）查表收集所有后代，复杂度 O(N)（N=系统进程数），
         相比逐层遍历 O(N×D) 减少约 10x 的进程表读取。

    Args:
        root_pid: 进程树根 PID。
        result: 输出列表，收集到的后代 PID 追加到此（本函数内去重）。
        max_depth: 最大递归深度（防内核故障 / PPid 成环导致的死循环）。
    """
    if max_depth <= 0:
        return
    children = _children_map()
    if not children:
        return
    seen = {root_pid}
    stack: list[tuple[int, int]] = [(root_pid, 0)]
    while stack:
        pid, depth = stack.pop()
        if depth >= max_depth:
            continue
        for child in children.get(pid, []):
            if child in seen:
                continue
            seen.add(child)
            result.append(child)
            stack.append((child, depth + 1))


def _pid_state(pid: int) -> str | None:
    """返回进程状态字母（``R/S/D/Z/T`` 等）；无法读取时返回 ``None``。

    Linux / Cygwin 读 ``/proc/<pid>/status`` 的 ``State`` 行（首字符）；
    无 ``/proc`` 平台回退 ``ps -o state= -p <pid>``。

    注：此处的 ``/proc`` 读取是**单进程**查询（非整表枚举），代价约
    0.1s/pid，仅在 ``kill_process_tree`` 的存活校验路径（每轮对进程树内
    各 PID 调用）出现；中断快路径 ``_kill_process_tree`` 不做校验，不受
    影响。整表枚举的慢速 ``/proc`` 只在 ``_children_map`` 中规避（见
    ``_prefer_ps_process_tree``）——那里是真凶（6~8s），且此处保留
    ``/proc`` 可保住僵尸判定（Cygwin 的 ``ps`` 不支持 ``state`` 列）。
    """
    if _is_windows():
        return None
    if os.path.isdir('/proc'):
        try:
            with open(f'/proc/{pid}/status', encoding='utf-8', errors='replace') as f:
                for line in f:
                    if line.startswith('State:'):
                        parts = line.split()
                        if len(parts) >= 2 and parts[1]:
                            return parts[1][:1].upper()
                        return None
        except (OSError, ValueError):
            return None
        return None
    output = _run_quiet(['ps', '-o', 'state=', '-p', str(pid)])
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if not lines:
        return None
    return lines[0][:1].upper() or None


def _win_pid_alive(pid: int) -> bool:
    """Windows 原生存活判定：``OpenProcess`` + ``GetExitCodeProcess``。

    权限不足（``ERROR_ACCESS_DENIED``）说明进程**仍然存在** → 判存活
    （见 ``_win_proc.is_process_alive``）：修复前一律判死，杀后校验会把
    「杀不掉的高权限进程」谎报成「无残留」。
    """
    try:
        from . import _win_proc
        return _win_proc.is_process_alive(pid)
    except Exception:  # pragma: no cover - 依赖系统调用
        logger.debug("Windows 进程存活判定失败: pid=%s", pid, exc_info=True)
        return True  # 无法判定时保守视为存活，不谎报「已全部退出」


def _pid_alive(pid: int) -> bool:
    """判断进程是否仍存活（已终止的僵尸进程视为「已死」）。

    - POSIX：``os.kill(pid, 0)`` 判定存在性，再查状态字符——``Z``（僵尸）
      已终止（仅等待父进程收尸，SIGKILL 也无法再改变其状态）→ 视为已死；
    - Windows 原生：``OpenProcess`` + ``GetExitCodeProcess`` 判 ``STILL_ACTIVE``；
    - 权限不足（``PermissionError``）视为「存在」（真实存在但不可操作）。
    """
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if _is_windows():
        return _win_pid_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return _pid_state(pid) != 'Z'


def _kill_pid(pid: int) -> None:
    """杀死单个进程（POSIX 用 SIGKILL；Windows 原生用 ``TerminateProcess``）。

    Windows 上先 ``TerminateProcess``（快、不受进程名 / 命令行转义影响），
    失败（权限不足 / 受保护进程）才回退 ``taskkill /F /T``。已退出的 PID
    直接跳过——否则每个已死 PID 都会额外拉起一次 ``taskkill`` 进程，杀
    大进程树时白白多花数百毫秒（进程树杀位于 ESC 中断 / op=kill 热路径）。
    """
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return
    if pid <= 0:
        return
    if _is_windows():
        if not _pid_alive(pid):
            return
        try:
            from . import _win_proc
            if _win_proc.terminate_process(pid):
                return
        except Exception:  # pragma: no cover - 依赖系统调用
            logger.debug("TerminateProcess 失败: pid=%s", pid, exc_info=True)
        _run_quiet(['taskkill', '/F', '/T', '/PID', str(pid)])
        return
    try:
        os.kill(pid, _signal.SIGKILL)
    except OSError:
        pass  # 进程已退出 / 无权限（尽力而为）


def _kill_process_group(pid: int) -> None:
    """杀死进程组（POSIX ``killpg``；Windows 原生无进程组概念，跳过）。"""
    killpg = getattr(os, 'killpg', None)
    if killpg is None:
        return
    try:
        killpg(pid, _signal.SIGKILL)
    except OSError:
        pass


def _kill_win_pid(pid: int) -> None:
    """杀死单个 **Windows PID**（``TerminateProcess``，失败回退 taskkill）。

    与 :func:`_kill_pid` 的区别：后者在 POSIX 平台用 ``os.kill``，Cygwin 下
    的 POSIX pid 与 Windows pid 是两套编号，混用会误杀无关进程。这里只接受
    Windows pid，供 Cygwin 的原生进程树 / Job 成员补杀使用。
    """
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return
    if pid <= 0:
        return
    self_winpid = _self_winpid()
    if self_winpid and pid == self_winpid:
        logger.warning("拒绝杀死当前进程自身的 Windows PID: %s", pid)
        return
    try:
        from . import _win_proc
    except Exception:  # pragma: no cover - 依赖系统调用
        return
    if not _win_proc.is_supported():
        return
    if not _win_proc.is_process_alive(pid):
        return
    try:
        if _win_proc.terminate_process(pid):
            return
    except Exception:  # pragma: no cover - 依赖系统调用
        logger.debug("TerminateProcess 失败: winpid=%s", pid, exc_info=True)
    _run_quiet(['taskkill', '/F', '/T', '/PID', str(pid)])


def _win_children_map() -> dict[int, list[int]]:
    """Windows 进程表 → ``父 Windows pid → [子 Windows pid]``（失败返回空表）。"""
    try:
        from . import _win_proc
        processes = _win_proc.list_processes()
    except Exception:  # pragma: no cover - 依赖系统调用
        logger.debug("Windows 进程表读取失败", exc_info=True)
        return {}
    mapping: dict[int, list[int]] = {}
    for pid, ppid, _name in processes:
        if pid == ppid:
            continue
        mapping.setdefault(int(ppid), []).append(int(pid))
    return mapping


def _collect_win_tree(root_pid: int | None) -> list[int]:
    """返回 Windows 进程树（根 + 递归后代，去重；``None`` / 非法返回空表）。

    只用于 Cygwin / MSYS2：这些平台的 POSIX 进程表（``ps``）看不到部分原生
    Windows 子进程（GUI / detached），必须再沿 Windows PPID 链收集一遍。
    """
    if not root_pid or int(root_pid) <= 0:
        return []
    root = int(root_pid)
    children = _win_children_map()
    result = [root]
    seen = {root}
    stack = [(root, 0)]
    while stack:
        pid, depth = stack.pop()
        if depth >= _DESCENDANT_MAX_DEPTH:
            continue
        for child in children.get(pid, ()):
            if child in seen:
                continue
            seen.add(child)
            result.append(child)
            stack.append((child, depth + 1))
    return result


def _wait_win_until_exit(pids: list[int],
                         timeout: float = _KILL_VERIFY_TIMEOUT) -> list[int]:
    """轮询等待给定 Windows PID 全部退出；返回超时后仍存活的 PID 列表。"""
    if not pids:
        return []
    try:
        from . import _win_proc
    except Exception:  # pragma: no cover - 依赖系统调用
        return list(pids)
    deadline = time.monotonic() + max(timeout, 0.0)
    while True:
        remaining = [pid for pid in pids if _win_proc.is_process_alive(pid)]
        if not remaining or time.monotonic() >= deadline:
            return remaining
        time.sleep(_KILL_VERIFY_POLL)


def _filter_reused_win_pids(pids, create_times) -> list[int]:
    """过滤掉「Windows PID 已被系统复用」的候选（无法判定时保留）。"""
    if not pids:
        return []
    times = create_times or {}
    safe: list[int] = []
    for pid in pids:
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            continue
        create_time = times.get(pid)
        if create_time:
            try:
                from . import _win_proc
                if _win_proc.is_pid_reused(pid, create_time):
                    logger.debug("Windows 候选进程 %s 的 PID 已被复用，跳过", pid)
                    continue
            except Exception:  # pragma: no cover - 依赖系统调用
                pass
        safe.append(pid)
    return safe


def _collect_tree_pids(root_pid: int) -> list[int]:
    """返回整棵进程树的 PID 列表（根 + 全部后代，已去重）。

    跨平台：Linux 读 ``/proc``、Cygwin 走 ``ps``、Windows 原生走 Toolhelp32
    进程表（父 PID 是创建时的值，根进程退出后仍能定位孤儿后代）。
    """
    descendants: list[int] = []
    _collect_descendants(root_pid, descendants)
    pids = [root_pid]
    seen = {root_pid}
    for pid in descendants:
        if pid not in seen:
            seen.add(pid)
            pids.append(pid)
    return pids


class ProcessTreeTracker:
    """后台任务进程树跟踪器（跨采样累积父子关系，跨越中间层退出的断链）。

    为什么需要（Windows 实测）：PPID 是「创建时的父 PID」，中间层退出后
    链条断裂——``root shell → 已退出的子进程 → 存活的孙进程`` 时，单次
    Toolhelp32 快照从根 PID 收集不到孙进程；Windows Job 对 Store 应用
    （App Execution Alias，如 Store 版 python）也不保证继承。跟踪器保存
    **历史** PPID 关系：只要在中间层存活期间采样到它一次，之后孙进程
    （其 PPID 指向该中间层 PID）仍能被判定为后代。

    采样结果分两份：
      - ``descendants``：历史后代集合（含已退出的中间层，供链条关联）；
      - ``observed``：**实际观察到存活**的后代 → 创建时间。kill 只杀
        ``observed`` 中的 PID（并用创建时间防 pid 复用），已退出 / 只出现
        在历史里的 PID 不作为杀目标，避免误杀复用了旧 PID 的新进程。
    """

    def __init__(self, root_pid: int = 0):
        self.root_pid = int(root_pid or 0)
        self.parents: dict[int, int] = {}
        self.descendants: set[int] = set()
        self.observed: dict[int, int | None] = {}

    def observe(self) -> set[int]:
        """采集一次进程表，更新历史关系并返回当前已知后代集合。"""
        if not self.root_pid:
            return set()
        try:
            from . import _win_proc
            processes = _win_proc.list_processes()
        except Exception:  # pragma: no cover - 依赖系统调用
            logger.debug("进程树跟踪采样失败", exc_info=True)
            processes = []
        current: dict[int, int] = {}
        for pid, ppid, _name in processes:
            current[pid] = ppid
            # 保留首次看到的父关系：中间层退出后，其历史 PPID 仍用于关联
            # 后代；pid 复用由 observed 的创建时间校验兜底
            self.parents.setdefault(pid, ppid)
        self.descendants.add(self.root_pid)
        changed = True
        while changed:
            changed = False
            for pid, ppid in self.parents.items():
                if pid not in self.descendants and ppid in self.descendants:
                    self.descendants.add(pid)
                    changed = True
        # 只记录「当前确实存活」的后代及其创建时间（kill 目标集合）
        for pid in current:
            if pid in self.descendants and pid not in self.observed:
                try:
                    self.observed[pid] = _win_proc.process_create_time(pid)
                except Exception:  # pragma: no cover - 依赖系统调用
                    self.observed[pid] = None
        return set(self.descendants)


def _filter_reused_pids(pids, create_times) -> list[int]:
    """过滤掉「PID 已被系统复用」的候选（无法判定时保留，交给存活判定）。"""
    if not pids:
        return []
    times = create_times or {}
    safe: list[int] = []
    for pid in pids:
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            continue
        create_time = times.get(pid)
        if create_time and _pid_reused(pid, create_time):
            logger.debug("候选进程 %s 的 PID 已被复用，跳过", pid)
            continue
        safe.append(pid)
    return safe


def _kill_tree_once(root_pid: int, collected: set[int] | None = None,
                    extra_pids=None, *,
                    win_root: int | None = None,
                    extra_win_pids=None,
                    collected_win: set[int] | None = None) -> list[int]:
    """对进程树执行一轮杀死（进程组 + 递归后代），返回本轮涉及的 PID。

    先杀后代（倒序，叶方向优先）再杀根：SIGKILL / TerminateProcess 下父
    进程来不及再 fork 新子进程，且根进程最后消失便于存活校验。

    ``extra_pids``：任务运行期采样累积的后代（可能已脱离当前 PPID 链，
    Toolhelp32 收集不到）——一并纳入本轮杀与校验。

    ``win_root`` / ``extra_win_pids``：Cygwin / MSYS2 下额外沿 **Windows
    PID** 收集并杀死原生进程树（POSIX ``ps`` 看不到 GUI / detached 子进程）。
    ``collected_win`` 收集本轮涉及的 Windows PID，供杀后校验。

    Windows 特例：进程表不可用（Toolhelp32 快照失败、收集不到任何后代）时
    退回 ``taskkill /F /T`` 的隐式树遍历——只杀根会让后代全部残留。
    """
    tree = _collect_tree_pids(root_pid)
    if extra_pids:
        known = set(tree)
        for pid in extra_pids:
            if pid not in known:
                known.add(pid)
                tree.append(pid)
    if collected is not None:
        collected.update(tree)
    _kill_process_group(root_pid)
    for pid in reversed(tree):
        if pid != root_pid:
            _kill_pid(pid)
    if _is_windows() and len(tree) <= 1 and not _children_map():
        # 进程表不可用才是「收集不到后代」——用 taskkill /T 隐式遍历兜底；
        # 快照正常但根确实无子进程时走快速的 TerminateProcess，不白拉
        # 一个 taskkill 进程（ESC 中断热路径）。
        _run_quiet(['taskkill', '/F', '/T', '/PID', str(root_pid)])
    else:
        _kill_pid(root_pid)

    # ── Cygwin / MSYS2：再杀一遍 Windows 进程树 ──
    # POSIX ps 只覆盖 Cygwin 进程表；原生 GUI / detached 进程（notepad、
    # regedit 等）不在其中，必须沿 Windows PPID 链单独收集并用
    # TerminateProcess / taskkill 终止。
    if _is_cygwin_like():
        win_tree = _collect_win_tree(win_root)
        if extra_win_pids:
            known_win = set(win_tree)
            for pid in extra_win_pids:
                try:
                    pid = int(pid)
                except (TypeError, ValueError):
                    continue
                if pid > 0 and pid not in known_win:
                    known_win.add(pid)
                    win_tree.append(pid)
        if collected_win is not None:
            collected_win.update(win_tree)
        for pid in reversed(win_tree):
            _kill_win_pid(pid)
    return tree


def _wait_until_exit(pids: list[int],
                     timeout: float = _KILL_VERIFY_TIMEOUT) -> list[int]:
    """轮询等待给定 PID 全部退出；返回超时后仍存活的 PID 列表。"""
    deadline = time.monotonic() + max(timeout, 0.0)
    while True:
        remaining = [pid for pid in pids if _pid_alive(pid)]
        if not remaining or time.monotonic() >= deadline:
            return remaining
        time.sleep(_KILL_VERIFY_POLL)


def _kill_process_tree(pid: int, job_handle: int | None = None) -> None:
    """杀死进程及其所有后代（**单次快速杀**，不校验、不重试）。

    供中断 / 读取循环等同步快路径调用（首要目标是不阻塞事件循环）；
    需要「杀后校验、未死补杀」时用公开 API :func:`kill_process_tree`。

    策略：Windows 有 Job 句柄时先 TerminateJobObject（覆盖中间父进程已
    退出的孤儿后代）；随后 killpg（POSIX）或 Toolhelp32 递归收集全部后代
    （后台作业、管道独立 PGID 进程、Windows 子进程）逐个补杀。
    """
    try:
        root_pid = int(pid)
    except (TypeError, ValueError):
        return
    if root_pid <= 0:
        return
    terminate_process_job(job_handle)
    win_root = _winpid_of(root_pid) if _is_cygwin_like() else None
    _kill_tree_once(root_pid, win_root=win_root)


def process_create_time(pid: int) -> int | None:
    """返回进程创建时间（Windows ``FILETIME`` 100ns 计数）。

    仅用于 **pid 复用防护**：后台任务启动时记录根进程创建时间（``bash.py``
    的 ``_on_ready``），``kill_process_tree`` 杀树前比对——任务早已结束、
    PID 被系统分配给新进程时（Windows PID 复用很快），据此拒绝杀，避免
    「杀一个已结束的后台任务」误杀无关进程树。

    非 Windows（没有统一的进程创建时间读取路径）或读不到时返回 ``None``
    （调用方按「无法判定」处理，不启用防护，保持原行为）。
    """
    if not _is_windows_like():
        return None
    try:
        from . import _win_proc
        winpid = _winpid_of(pid, retries=0) if _is_cygwin_like() else int(pid)
        if not winpid:
            return None
        return _win_proc.process_create_time(winpid)
    except Exception:  # pragma: no cover - 依赖系统调用
        logger.debug("读取进程创建时间失败: pid=%s", pid, exc_info=True)
        return None


def _pid_reused(pid: int, create_time: int | None) -> bool:
    """记录过创建时间的 PID 是否已被系统复用（无法判定时返回 False）。"""
    if not create_time or not _is_windows_like():
        return False
    try:
        from . import _win_proc
        winpid = _winpid_of(pid) if _is_cygwin_like() else int(pid)
        if not winpid:
            # POSIX 进程已退出（Cygwin exec 后 /proc 可能不存在）：无法用
            # 创建时间判定，交给 Job / Windows 进程树兜底，不阻止杀。
            return False
        return bool(_win_proc.is_pid_reused(winpid, create_time))
    except Exception:  # pragma: no cover - 依赖系统调用
        logger.debug("pid 复用判定失败: pid=%s", pid, exc_info=True)
        return False


# ── Windows Job 对象（可靠的整树终止） ──────────────────
#
# 为什么在 Toolhelp32 之外还要 Job：PPID 链在**中间父进程退出后断裂**——
# `根 shell → 子进程（已退出）→ 孙进程（存活）` 时，从根 PID 沿 Toolhelp32
# 收集不到孙进程（实测：bash 后台任务由 PowerShell 启动 Python、Python 再
# 启动常驻子进程，PowerShell/Python 退出后孙进程残留且普通收集看不到）。
# Job 的成员关系由内核维护、父进程退出不影响，TerminateJobObject 一次杀光。
#
# 生命周期：进程创建后立即 create_process_job + 记录句柄；kill / 中断时
# terminate_process_job；任务记录移除时 close_process_job（仅关句柄，不杀）。

def create_process_job(pid: int) -> int | None:
    """为刚创建的子进程 ``pid`` 创建 Job 并加入（失败 / 非 Windows → None）。

    越早调用覆盖越全（进程创建后立刻加入，其后代自动成为成员）。
    Cygwin 下 ``pid`` 是 POSIX pid，先经 ``/proc/<pid>/winpid`` 换算；
    换算失败（极短暂的就绪窗口）时做几次短重试。
    Assign 失败（父进程受 Job 限制等）时关闭句柄返回 None，调用方回退
    Toolhelp32 + ``taskkill /T``。
    """
    if not _is_windows_like():
        return None
    try:
        from . import _win_proc
        winpid = _winpid_of(pid, retries=5)
        if not winpid:
            return None
        job = _win_proc.create_job()
        if not job:
            return None
        if _win_proc.assign_process_to_job(job, winpid):
            return job
        _win_proc.close_handle(job)
        logger.debug("AssignProcessToJobObject 失败: pid=%s winpid=%s", pid, winpid)
    except Exception:  # pragma: no cover - 依赖系统调用
        logger.debug("创建进程 Job 失败: pid=%s", pid, exc_info=True)
    return None


def terminate_process_job(job_handle: int | None) -> bool:
    """终止 Job 内全部进程（无句柄 / 非 Windows 返回 False）。"""
    if not job_handle or not _is_windows_like():
        return False
    try:
        from . import _win_proc
        return _win_proc.terminate_job(job_handle)
    except Exception:  # pragma: no cover - 依赖系统调用
        logger.debug("终止 Job 失败: %s", job_handle, exc_info=True)
        return False


def job_active_processes(job_handle: int | None) -> int | None:
    """Job 内当前活动进程数（无句柄 / 非 Windows / 查询失败返回 None）。"""
    if not job_handle or not _is_windows_like():
        return None
    try:
        from . import _win_proc
        return _win_proc.job_active_processes(job_handle)
    except Exception:  # pragma: no cover - 依赖系统调用
        logger.debug("查询 Job 活动进程数失败: %s", job_handle, exc_info=True)
        return None


def close_process_job(job_handle: int | None) -> None:
    """关闭 Job 句柄（不终止成员；幂等）。"""
    if not job_handle or not _is_windows_like():
        return
    try:
        from . import _win_proc
        _win_proc.close_handle(job_handle)
    except Exception:  # pragma: no cover - 依赖系统调用
        logger.debug("关闭 Job 句柄失败: %s", job_handle, exc_info=True)


def _wait_job_empty(job_handle: int | None,
                    timeout: float = _KILL_VERIFY_TIMEOUT) -> int:
    """轮询等待 Job 内活动进程数归零；返回超时后的剩余数量（无法查询按 0）。"""
    if not job_handle:
        return 0
    deadline = time.monotonic() + max(timeout, 0.0)
    while True:
        active = job_active_processes(job_handle)
        if not active or time.monotonic() >= deadline:
            return int(active or 0)
        time.sleep(_KILL_VERIFY_POLL)


#: 不承载「待操作 GUI 窗口」的 shell / 解释器名字（去 ``.exe``、去版本
#: 后缀后的 stem）。Cygwin / MSYS2 下每个后台任务都至少有一个 bash.exe；
#: 若把这类通用名当 ``win_hint`` 做「同名进程」兜底，就会把**其它任务**的
#: shell 及其 Windows 后代（别的任务的窗口）算进本任务——实测
#: ``op=windows`` / ``op=screenshot`` / ``op=type`` 会串到另一个任务的
#: WinForms 窗口，``op=kill`` 也会误杀用户开着的同名程序。
_SHELL_LIKE_STEMS = frozenset({
    "bash", "sh", "dash", "ash", "zsh", "ksh", "mksh", "fish", "csh", "tcsh",
    "cmd", "powershell", "pwsh", "wsl", "conhost", "winpty", "env",
    "nohup", "setsid", "timeout", "nice", "xargs", "script", "su", "sudo",
    "python", "python2", "python3", "py", "node", "nodejs", "ruby", "perl",
    "php", "java", "dotnet", "mono", "busybox",
})


def is_shell_like_exe(name: str | None) -> bool:
    """可执行名是否属于 shell / 解释器（这类进程不做同名窗口兜底）。

    Args:
        name: 可执行名（可带路径、``.exe`` 后缀、版本后缀，如
            ``C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe``
            或 ``python3.9``）。

    Returns:
        是 shell / 解释器返回 ``True``；空名或普通程序名返回 ``False``。
    """
    text = str(name or "").strip().lower().replace("\\", "/")
    if text.endswith(".exe"):
        text = text[:-4]
    # 按两种分隔符取 basename（Cygwin 的 os.path.basename 不认反斜杠）
    text = text.rsplit("/", 1)[-1]
    if not text:
        return False
    if text in _SHELL_LIKE_STEMS:
        return True
    # python3.9 / node18 / perl5.36 这类带版本后缀的名字
    stem = _re.sub(r"[\d._-]+$", "", text)
    return bool(stem) and stem in _SHELL_LIKE_STEMS


def _win_pids_for_hint(hint: str | None, start_time: int | None) -> list[int]:
    """按可执行名 + 启动时间筛出「任务启动后新出现」的 Windows 进程。

    Cygwin 下 App Execution Alias（Store 应用）或经 explorer 启动的窗口，
    其进程既不在任务 Job 内，也不在 Toolhelp32 父子链上；只能靠命令行首个
    token 的 exe 名 + 创建时间兜底。start_time 为 0/None 时不做时间过滤。

    shell / 解释器名字直接返回空表（见 :func:`is_shell_like_exe`）；
    创建时间读不到的候选也跳过——无法排除「用户早就开着的同名进程」，
    宁可漏杀 App Execution Alias，也不误杀用户程序。
    """
    if not hint or is_shell_like_exe(hint):
        return []
    try:
        from . import _win_proc
        candidates = _win_proc.find_processes_by_name(hint)
    except Exception:  # pragma: no cover - 依赖系统调用
        logger.debug("按名称查找 Windows 进程失败: %s", hint, exc_info=True)
        return []
    if not start_time:
        return candidates
    result: list[int] = []
    for pid in candidates:
        try:
            create_time = _win_proc.process_create_time(pid)
        except Exception:  # pragma: no cover - 依赖系统调用
            create_time = None
        if create_time is None:
            # 读不到创建时间就无法排除「用户早就开着的同名进程」：
            # 宁可漏杀 App Execution Alias，也不误杀用户程序。
            logger.debug("进程 %s 创建时间不可读，跳过按名兜底", pid)
            continue
        # 允许 1 秒的时钟粒度误差（FILETIME 是 100ns，但系统计时可能滞后）
        if int(create_time) + 10_000_000 >= int(start_time):
            result.append(pid)
    return result


def kill_process_tree(pid: int, *, attempts: int = KILL_MAX_ATTEMPTS,
                      verify: bool = True,
                      create_time: int | None = None,
                      job_handle: int | None = None,
                      extra_pids=None,
                      extra_create_times=None,
                      winpid: int | None = None,
                      extra_win_pids=None,
                      extra_win_create_times=None,
                      win_hint: str | None = None,
                      win_start_time: int | None = None) -> KillResult:
    """杀死进程及其所有后代，**杀后校验、未死补杀**（公开 API）。

    供 bash_opt（op=kill）与 ESC 中断兜底调用。流程：

      1. Windows 有 Job 句柄时先 ``TerminateJobObject``（覆盖中间父进程已
         退出、Toolhelp32 PPID 链断裂的孤儿后代）；
      2. 每轮重新收集整棵进程树（进程组 + 递归后代，可捕获本轮新 fork
         出来的后代），并并入运行期采样累积的 ``extra_pids``；
      3. Cygwin / MSYS2 再沿 **Windows PID** 收集一遍原生进程树（含 Job
         成员与「按 exe 名 + 创建时间」匹配的 Store 应用），逐个
         ``TerminateProcess`` / ``taskkill``；
      4. **校验**：轮询确认上述进程全部退出（僵尸视为已终止），并确认
         Job 内活动进程数归零；
      5. 仍有存活 → **再杀一次**（最多 ``attempts`` 轮）；
      6. 返回 :class:`KillResult`，含校验时仍存活的残留 PID / Job 计数。

    Windows 专用的 pid 复用防护：``create_time`` 为任务启动时记录的根进程
    创建时间，每轮杀之前比对；PID 已被系统复用时（创建时间不同）**不杀**，
    结果标记 ``pid_reused=True``——否则会误杀恰好复用了该 PID 的新进程树。
    Job 内仍有活动成员时不做该拒绝（Job 是任务的权威归属）。

    Args:
        pid: 进程树根 PID（Cygwin 下为 POSIX pid）。
        attempts: 最大「收集 + 杀死 + 校验」轮数（>=1，默认 3）。
        verify: 是否执行杀后校验（``False`` 时仅杀一轮、不等待不重试，
            兼容旧的「快速杀」语义）。
        create_time: 根进程创建时间（Windows FILETIME；``None`` 表示未记录，
            不启用复用防护）。
        job_handle: 创建进程时为它建立的 Windows Job 句柄（``None`` 表示
            未启用；非 Windows 忽略）。
        extra_pids: 任务运行期采样累积的 POSIX 后代 PID（可覆盖已脱离
            PPID 链的孤儿）；非空时一并杀与校验。
        extra_create_times: ``extra_pids`` 对应的创建时间（pid 复用防护；
            不匹配的候选会被跳过）。
        winpid: 根进程的 Windows PID（Cygwin 下由 ``_on_ready`` 记录；
            不传时尝试用 ``/proc/<pid>/winpid`` 现读）。
        extra_win_pids: 任务运行期采样累积的 Windows 后代 PID。
        extra_win_create_times: ``extra_win_pids`` 对应的创建时间。
        win_hint: 命令行首个 token 的 exe 名（小写 basename），用于
            App Execution Alias / explorer 启动的 Store 应用兜底。
        win_start_time: 任务启动时间（Windows FILETIME），与 ``win_hint``
            一起过滤「任务启动前就存在的同名进程」。

    Returns:
        KillResult；``remaining_pids`` / ``remaining_win_pids`` 非空表示仍有
        进程未杀死（可能权限不足或进程处于不可中断状态）；``pid_reused``
        为真表示因 PID 被复用而未执行杀；``job_remaining`` 非零表示 Job 内
        仍有活动进程。
    """
    try:
        root_pid = int(pid)
    except (TypeError, ValueError):
        return KillResult(pid=0)
    if root_pid <= 0:
        return KillResult(pid=0)
    try:
        rounds = max(1, int(attempts))
    except (TypeError, ValueError):
        rounds = KILL_MAX_ATTEMPTS

    is_cyg = _is_cygwin_like()
    if is_cyg:
        win_root = int(winpid) if winpid else _winpid_of(root_pid)
    else:
        win_root = root_pid if _is_windows() else None

    # Job 活动成员是任务的权威归属：先查一次，再终止（一次全体生效）
    job_was_active = 0
    if job_handle:
        try:
            job_was_active = int(job_active_processes(job_handle) or 0)
        except Exception:  # pragma: no cover - 依赖系统调用
            job_was_active = 0
        terminate_process_job(job_handle)
    safe_extra = _filter_reused_pids(extra_pids, extra_create_times)
    safe_extra_win = _filter_reused_win_pids(
        extra_win_pids, extra_win_create_times)

    seen: set[int] = set()
    seen_win: set[int] = set()
    remaining: list[int] = []
    remaining_win: list[int] = []
    job_remaining = 0
    used = 0
    for index in range(1, rounds + 1):
        reused = _pid_reused(root_pid, create_time) and not job_was_active
        if reused:
            logger.warning(
                "进程 %s 的 PID 已被系统复用（创建时间不匹配），跳过杀进程树",
                root_pid,
            )
            return KillResult(
                pid=root_pid,
                attempts=used,
                killed_pids=tuple(sorted(seen)),
                killed_win_pids=tuple(sorted(seen_win)),
                verified=verify,
                pid_reused=True,
                job_remaining=job_remaining,
            )
        used = index

        # 每轮重新收集 Windows 候选：Job 成员 + 根 Windows 树的当前后代 +
        # 运行期采样 + Store 应用兜底（按 exe 名 + 启动时间）。
        round_win: list[int] = list(safe_extra_win)
        if is_cyg:
            if win_root:
                round_win.extend(_collect_win_tree(win_root))
            if job_handle:
                try:
                    from . import _win_proc
                    round_win.extend(_win_proc.job_processes(job_handle))
                except Exception:  # pragma: no cover - 依赖系统调用
                    pass
            round_win.extend(_win_pids_for_hint(win_hint, win_start_time))
        # 去重、过滤自身
        self_winpid = _self_winpid() if is_cyg else None
        dedup_win: list[int] = []
        for candidate in round_win:
            try:
                candidate = int(candidate)
            except (TypeError, ValueError):
                continue
            if candidate <= 0 or candidate in seen_win:
                continue
            if self_winpid and candidate == self_winpid:
                continue
            seen_win.add(candidate)
            dedup_win.append(candidate)

        if is_cyg:
            _kill_tree_once(
                root_pid, seen, safe_extra,
                win_root=win_root,
                extra_win_pids=dedup_win,
                collected_win=seen_win,
            )
        else:
            _kill_tree_once(root_pid, seen, safe_extra)
        if not verify:
            break
        remaining = _wait_until_exit(sorted(seen))
        if is_cyg:
            remaining_win = _wait_win_until_exit(sorted(seen_win))
        job_remaining = _wait_job_empty(job_handle)
        if not remaining and not remaining_win and not job_remaining:
            break
        logger.debug(
            "进程树 %s 第 %d 轮杀死后仍有存活进程（pids=%s, winpids=%s, job=%s）",
            root_pid, index, remaining, remaining_win, job_remaining,
        )
    return KillResult(
        pid=root_pid,
        attempts=used,
        killed_pids=tuple(sorted(seen)),
        killed_win_pids=tuple(sorted(seen_win)),
        remaining_pids=tuple(remaining),
        remaining_win_pids=tuple(remaining_win),
        verified=verify,
        job_remaining=job_remaining,
    )
