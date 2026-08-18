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
import sys

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

def _collect_descendants(root_pid: int, result: list[int], max_depth: int = 10) -> None:
    """递归收集所有后代进程 PID（通过 /proc/<pid>/status 的 PPid 字段）。

    仅 Linux 系统有效（含 Android Termux），非 Linux 系统静默跳过。
    max_depth 防止内核故障导致的死循环。

    实现策略（单次扫描 + DFS 查表）：
      1. 一次遍历 /proc，构建 PPid → [child_pids] 映射表
      2. DFS（栈实现）查表收集所有后代，复杂度 O(N)（N=系统进程数）
      相比逐层遍历 O(N×D) 减少 ~10x 的 /proc 文件读取。

    注意：此函数执行同步 /proc I/O，仅在 ESC 中断路径调用，不影响正常执行路径。
    """
    if not sys.platform.startswith('linux'):
        return
    if max_depth <= 0:
        return
    # 单次扫描: 构建 PPid → [child_pids] 映射
    parent_map: dict[int, list[int]] = {}
    try:
        for entry in os.listdir('/proc'):
            if not entry.isdigit():
                continue
            try:
                with open(f'/proc/{entry}/status') as f:
                    for line in f:
                        if line.startswith('PPid:'):
                            child = int(entry)
                            p = int(line.split()[1])
                            parent_map.setdefault(p, []).append(child)
                            break
            except (IOError, ValueError, OSError):
                continue
    except OSError:
        return
    # DFS 查表收集后代（栈实现）
    stack: list[tuple[int, int]] = [(root_pid, 0)]
    while stack:
        pid, depth = stack.pop()
        if depth >= max_depth:
            continue
        for child in parent_map.get(pid, []):
            result.append(child)
            stack.append((child, depth + 1))


def _kill_process_tree(pid: int) -> None:
    """杀死进程及其所有后代。

    策略（两阶段）：
      1. killpg：先杀死进程组（shell + 前台子进程），快路径覆盖
      2. /proc 递归：剩余后代（后台作业、管道独立 PGID 进程）逐个补杀

    非 Linux 系统：仅 killpg（安全降级）。
    """
    # 阶段 1：killpg 杀进程组
    try:
        os.killpg(pid, _signal.SIGKILL)
    except OSError:
        pass

    # 阶段 2：/proc 递归补杀剩余后代（仅 Linux）
    if sys.platform.startswith('linux'):
        descendants: list[int] = []
        _collect_descendants(pid, descendants)
        for child_pid in descendants:
            try:
                os.kill(child_pid, _signal.SIGKILL)
            except OSError:
                pass  # 进程已死或无权限


def kill_process_tree(pid: int) -> None:
    """杀死进程及其所有后代（公开 API，供 bash_opt 工具按 task_id 操作）。

    策略与 _kill_process_tree 相同（两阶段）：
      1. killpg：先杀死进程组（shell + 前台子进程），快路径覆盖
      2. /proc 递归：剩余后代（后台作业、管道独立 PGID 进程）逐个补杀
    """
    _kill_process_tree(pid)
