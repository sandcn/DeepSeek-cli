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
import time

from .._compat import dataclass

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
        remaining_pids: 最后一轮校验时仍存活的 PID（空 = 已全部退出）。
        verified: 是否执行了杀后校验（``verify=False`` 时为 False）。
    """

    pid: int
    attempts: int = 0
    killed_pids: tuple[int, ...] = ()
    remaining_pids: tuple[int, ...] = ()
    verified: bool = False

    @property
    def success(self) -> bool:
        """进程树是否已被确认全部退出（未校验时恒为 False）。"""
        return self.verified and not self.remaining_pids


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


def _is_windows() -> bool:
    """是否 Windows **原生**环境（Cygwin 的 ``os.name`` 为 ``posix``，不算）。"""
    return os.name == 'nt'


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
    return completed.stdout.decode('utf-8', errors='replace')


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


def _children_map() -> dict[int, list[int]]:
    """构建 ``PPid → [child_pid]`` 映射（跨平台多策略）。

    优先解析 ``/proc/<pid>/status`` 的 ``PPid`` 字段（Linux / Cygwin /
    Android Termux，单次遍历 O(N)）；``/proc`` 不可用或未解析到任何父子
    关系时回退 ``ps``（macOS / BSD）。
    """
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
    """Windows 原生存活判定：``OpenProcess`` + ``GetExitCodeProcess``。"""
    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:
        return False
    kernel32 = ctypes.windll.kernel32
    process_query_limited_information = 0x1000
    still_active = 259
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        if kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return code.value == still_active
        return True
    finally:
        kernel32.CloseHandle(handle)


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
    """杀死单个进程（POSIX 用 SIGKILL；Windows 原生用 ``taskkill /F /T``）。"""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return
    if pid <= 0:
        return
    if _is_windows():
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


def _collect_tree_pids(root_pid: int) -> list[int]:
    """返回整棵进程树的 PID 列表（根 + 全部后代，已去重）。"""
    descendants: list[int] = []
    _collect_descendants(root_pid, descendants)
    pids = [root_pid]
    seen = {root_pid}
    for pid in descendants:
        if pid not in seen:
            seen.add(pid)
            pids.append(pid)
    return pids


def _kill_tree_once(root_pid: int, collected: set[int] | None = None) -> list[int]:
    """对进程树执行一轮杀死（进程组 + 递归后代），返回本轮涉及的 PID。

    先杀后代（倒序，叶方向优先）再杀根：SIGKILL 下父进程来不及再 fork
    新子进程，且根进程最后消失便于存活校验。
    """
    tree = _collect_tree_pids(root_pid)
    if collected is not None:
        collected.update(tree)
    _kill_process_group(root_pid)
    for pid in reversed(tree):
        if pid != root_pid:
            _kill_pid(pid)
    _kill_pid(root_pid)
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


def _kill_process_tree(pid: int) -> None:
    """杀死进程及其所有后代（**单次快速杀**，不校验、不重试）。

    供中断 / 读取循环等同步快路径调用（首要目标是不阻塞事件循环）；
    需要「杀后校验、未死补杀」时用公开 API :func:`kill_process_tree`。

    策略：killpg 杀死进程组（shell + 前台子进程）后，递归收集全部后代
    （后台作业、管道独立 PGID 进程）逐个补杀。跨平台（见模块内说明）。
    """
    try:
        root_pid = int(pid)
    except (TypeError, ValueError):
        return
    if root_pid <= 0:
        return
    _kill_tree_once(root_pid)


def kill_process_tree(pid: int, *, attempts: int = KILL_MAX_ATTEMPTS,
                      verify: bool = True) -> KillResult:
    """杀死进程及其所有后代，**杀后校验、未死补杀**（公开 API）。

    供 bash_opt（op=kill）与 ESC 中断兜底调用。流程：

      1. 每轮重新收集整棵进程树（进程组 + 递归后代，可捕获本轮新 fork
         出来的后代）；
      2. 先杀进程组（killpg），再逐个 SIGKILL 后代与根进程；
      3. **校验**：轮询确认上述进程全部退出（僵尸视为已终止）；
      4. 仍有存活 → **再杀一次**（最多 ``attempts`` 轮）；
      5. 返回 :class:`KillResult`，含校验时仍存活的残留 PID。

    Args:
        pid: 进程树根 PID。
        attempts: 最大「收集 + 杀死 + 校验」轮数（>=1，默认 3）。
        verify: 是否执行杀后校验（``False`` 时仅杀一轮、不等待不重试，
            兼容旧的「快速杀」语义）。

    Returns:
        KillResult；``remaining_pids`` 非空表示仍有进程未杀死
        （可能权限不足或进程处于不可中断状态）。
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

    seen: set[int] = set()
    remaining: list[int] = []
    used = 0
    for index in range(1, rounds + 1):
        used = index
        _kill_tree_once(root_pid, seen)
        if not verify:
            break
        remaining = _wait_until_exit(sorted(seen))
        if not remaining:
            break
        logger.debug("进程树 %s 第 %d 轮杀死后仍有存活进程: %s",
                     root_pid, index, remaining)
    return KillResult(
        pid=root_pid,
        attempts=used,
        killed_pids=tuple(sorted(seen)),
        remaining_pids=tuple(remaining),
        verified=verify,
    )
