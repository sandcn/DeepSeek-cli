from __future__ import annotations

import os
import sys
import asyncio
import errno as _errno
import logging
import re as _re
from .base import Func, tool_metadata, print_to_terminal

from ..core.constants import GREEN, RED, DIM, RESET
from ..api.interrupt_async import is_interrupted

logger = logging.getLogger(__name__)

# 尝试导入 pty（部分平台不支持）
try:
    import pty as _pty_mod
    _HAS_PTY = True
except ImportError:
    _HAS_PTY = False

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
import signal as _signal


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


@tool_metadata(
    parallel_safe=False,
    requires_network=False,
    requires_terminal=False,
    timeout_estimate=0,
    category="general",
    priority=30,
    tool_category="bash",
    description="执行shell命令",
)
class BashFunc(Func):
    name = "bash"
    _DEFAULT_TIMEOUT: int = 300

    @classmethod
    def to_tool_schema(cls):
        return {
            "type": "function",
            "function": {
                "name": "bash",
                "description": (
                    "执行shell命令。用途：编译构建、git操作、包管理、进程管理、系统信息查询。"
                    "禁止替代专用工具——搜索代码用search，查找文件用find，文件读写用read_file/write_file/update_file。"
                    f"命令默认有{cls._DEFAULT_TIMEOUT}秒超时限制，超时后强制终止并返回超时错误，"
                    "可通过 timeout 参数自定义超时秒数。"
                    "返回stdout+stderr合并输出。"
                    "\n\n"
                    "参数说明："
                    "\n- command（必填）：要执行的 shell 命令，支持管道、重定向、环境变量、&& 串联等完整 shell 语法"
                    "\n- cwd（可选）：指定命令的工作目录，省略时使用进程当前工作目录"
                    f"\n- timeout（可选）：命令超时秒数（整数）。默认 {cls._DEFAULT_TIMEOUT} 秒，"
                    "超时后强制终止整个进程树并返回超时错误；传 0 表示不设超时限制（慎用）"
                    "\n\n"
                    "参数关联："
                    "\n- cwd 影响命令中所有相对路径的解析基准（如 ./config、../scripts 等）"
                    "\n- command 中的环境变量在 cwd 指定的工作目录下生效"
                    "\n\n"
                    "【禁止替代专用工具】"
                    "\nbash 有安全沙盒保护，但专用工具提供更安全的沙盒、原子写入、结构化输出。"
                    "\n以下操作必须使用专用工具，禁止用 bash 替代："
                    "\n| 正确 | 错误（禁止） | 等级 |"
                    "\n|------|-------------|------|"
                    "\n| read_file | bash cat / head / tail | P0 绕过沙盒 |"
                    "\n| update_file | bash sed / perl -i | P0 绕过沙盒 |"
                    "\n| write_file | bash echo / tee / printf > | P0 绕过沙盒 |"
                    "\n| search | bash grep / rg / ag | P1 非结构化输出 |"
                    "\n| find / ls | bash find / ls | P2 非结构化输出 |"
                    "\n| mkdir | bash mkdir | P0 绕过沙盒 |"
                    "\n"
                    "\n例外：专用工具功能不足时（如search不支持正则多行匹配、二进制文件），"
                    "先多次组合专用工具仍不行，加注释 `# 例外原因：<原因>` 后可用 bash。"
                    "\n无对应专用工具的操作（测试/构建/git/pip/curl/tar）正常使用 bash。"
                    "\n\n"
                    "【边界信息】"
                    "\n- 工作目录(cwd)不存在时返回明确错误，不会在错误目录下执行"
                    "\n- 禁止运行交互式命令（vim/top/less等），会导致进程挂起"
                    "\n- 输出限制：超过1000行后截断，仅保留最后1000行（最新内容）"
                    "\n\n"
                    "【Android (Termux) 兼容】"
                    "\n- 不要在命令里使用 `timeout` 系统命令（行为差异可能导致孤儿进程），"
                    "需要限制执行时间时直接用 bash 工具的 timeout 参数"
                    "\n\n"
                    "【Git 操作限制】"
                    "\n- 禁止 git push -f / git reset --hard"
                    "\n- git commit / git push 需明确指令"
                    "\n- git clean -fd 须经 user_select 确认后执行"
                    "\n\n"
                    "【防幻觉】对 git 仓库状态做任何断言前，先用 bash git log/diff/status 验证实际状态，禁止凭记忆虚构变更历史。"
                    "\n\n"
                    "【安全红线】"
                    "\n- 禁止执行系统破坏操作：rm -rf、mkfs、dd、chmod 777、sudo、chown"
                    "\n- 不可以从根目录find / 东西"
                    "\n- 有大量输入的时候多用 cmd | grep * 或 cmd | tail 100 等"
                    "\n- 不会停的命令 用 bash 工具的 timeout 参数（而不是系统 timeout 命令）"
                    "\n- 此红线约束直接 shell 执行和通过脚本的间接执行路径"
                    "\n\n"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": (
                                "要执行的 shell 命令。支持完整的 shell 语法，包括："
                                "管道（|）、重定向（> / >> / <）、"
                                "环境变量赋值（KEY=value command）、"
                                "串联执行（&& / ||）、"
                                "子 shell（$(...)）、通配符（* / ?）等。"
                                "\n\n"
                                "输出处理："
                                "\n- stdout 和 stderr 自动拼接为单一字符串返回（stderr 追加在 stdout 之后）"
                                "\n- 无输出时返回 '(无输出)'"
                                "\n- 输出截断：超过1000行后截断，仅保留最后1000行（最新内容）"
                            )
                        },
                        "cwd": {
                            "type": "string",
                            "description": (
                                "命令的工作目录（可选）。"
                                "\n- 省略时：使用进程的当前工作目录（即启动时的 cwd）"
                                "\n- 指定绝对路径时：在该目录下执行命令"
                                "\n- 指定相对路径时：相对于进程当前工作目录解析"
                                "\n\n"
                                "目录不存在时的行为："
                                "\n- cwd 指定的目录不存在时，命令不会执行，直接返回 '(工作目录不存在: <路径>)' 错误信息"
                                "\n- 不会在错误目录下执行命令，也不会退回到默认目录"
                            )
                        },
                        "timeout": {
                            "type": "integer",
                            "description": (
                                "命令执行超时秒数（可选）。"
                                f"\n- 省略时使用默认 {cls._DEFAULT_TIMEOUT} 秒"
                                "\n- 超时后命令及其子进程树被强制终止，返回 '(命令执行超时，已强制终止)'"
                                "\n- 传 0 表示不设超时限制（慎用，可能导致命令永久挂起）"
                            )
                        }
                    },
                    "required": ["command"]
                }
            }
        }

    @classmethod
    def display_params(cls, arguments: dict, max_len: int = 80) -> str:
        cmd = arguments.get("command", "")
        display_cmd = cls._sanitize_display(cmd)
        return f"'{display_cmd}'"

    @classmethod
    def _get_subprocess_env(cls) -> dict:
        """返回子进程环境变量，添加反缓冲设置确保实时输出。"""
        env = os.environ.copy()
        env['PYTHONUNBUFFERED'] = '1'  # Python 脚本强制无缓冲
        env.pop('PAGER', None)         # 禁用分页器（PTY 模拟终端会触发 pager）
        env['GIT_PAGER'] = 'cat'       # git diff 等通过 PTY 运行时不启用分页器
        return env

    @classmethod
    def _is_pty_available(cls) -> bool:
        """检查 PTY 是否可用（用于子进程实时行缓冲输出）。"""
        return _HAS_PTY

    def __init__(self, command, cwd=None, timeout=None):
        super().__init__()
        self.command = command
        self.cwd = cwd
        # timeout 参数语义（seconds）：
        #   省略/None → 默认 _DEFAULT_TIMEOUT（300s）
        #   <=0       → 不设超时限制（asyncio.wait_for 无限等待，慎用）
        #   >0        → 自定义超时秒数
        if timeout is None:
            self.timeout = self._DEFAULT_TIMEOUT
        else:
            try:
                timeout = int(timeout)
            except (TypeError, ValueError):
                timeout = self._DEFAULT_TIMEOUT
            self.timeout = None if timeout <= 0 else timeout

    @classmethod
    async def _show_command_to_terminal(cls, command, cwd=None):
        """将命令打印到终端（绿色高亮，含 cwd 信息）。"""
        cwd_info = f" {DIM}(在 {cwd}){RESET}" if cwd else ""
        await print_to_terminal(f"{GREEN}$ {command}{cwd_info}{RESET}")

    @classmethod
    def _check_cwd_or_return(cls, cwd):
        """检查 cwd 是否存在，不存在时返回错误信息。"""
        if cwd and not os.path.isdir(cwd):
            return f"(工作目录不存在: {cwd})"
        return None

    # ── 输出截断 ────────────────────────────────────────
    # 返回给大模型的输出超过 MAX_LINES 行时自动截断，
    # 只保留尾部最新内容（大模型更需要命令最新的输出/错误信息），
    # 避免大模型上下文窗口被超长输出占满，影响推理质量。
    MAX_LINES = 1000

    @staticmethod
    def _truncate_output(output: str, max_lines: int | None = None) -> str:
        """将输出截断到指定行数（默认 MAX_LINES 行），超出时保留尾部最新行并追加截断标记。

        以 '(' 开头的输出视为错误/提示信息（如 "(无输出)"），不截断。
        """
        if max_lines is None:
            max_lines = BashFunc.MAX_LINES
        if not output or output.startswith('('):
            return output
        lines = output.split('\n')
        if len(lines) > max_lines:
            logger.debug("输出截断: %d 行 -> %d 行（保留尾部最新）", len(lines), max_lines)
            return '\n'.join(lines[-max_lines:]) + (
                f'\n...(输出已截断：超过 {max_lines} 行，仅展示最后 {max_lines} 行)'
            )
        return output

    # ── 共享读取循环 ─────────────────────────────────────

    @staticmethod
    async def _read_loop(reader, process, lines, publish_line_fn,
                         show_output=False, is_stderr=False,
                         kill_fn=None):
        """共享读取循环，消除 _run_pipe 和 _run_pty 中的重复代码（~80行×2）。

        从 reader 逐行读取字节，处理中断检测、超时/PTY EIO/超长行、
        UTF-8 解码、\\r\\n 规范化、ANSI 剥离终端输出和行发布回调。

        Args:
            reader: asyncio.StreamReader（PIPE stdout/stderr 或 PTY master）
            process: asyncio.subprocess.Process，用于中断时 kill（kill_fn 为 None 时回退）
            lines: list[str]，收集到的行追加到此列表
            publish_line_fn: 可选的 async (text, is_stderr) -> None 回调
            show_output: 是否实时打印 ANSI 剥离后的输出到终端
            is_stderr: 是否 stderr 流，控制终端输出的颜色
            kill_fn: 可选的无参回调，替代 process.kill() 用于进程树终止。
                    传入时使用自定义杀死逻辑；为 None 时回退到 process.kill()。

        Returns:
            bool: True 表示被 ESC 中断信号打断，False 表示正常读到 EOF
        """
        while True:
            if is_interrupted():
                if kill_fn is not None:
                    kill_fn()
                else:
                    process.kill()
                return True
            try:
                line = await asyncio.wait_for(
                    reader.readline(),
                    timeout=_INTERRUPT_CHECK_INTERVAL,
                )
            except asyncio.TimeoutError:
                continue  # 超时→回到循环头检查中断
            except OSError as e:
                if e.errno == _errno.EIO:  # PTY slave closed → EOF
                    break
                raise
            except ValueError:
                # LimitOverrunError: 超长行（找不到换行符且超出缓冲区限制）
                # 回退到 read() 读取大块数据，避免崩溃
                chunk = await reader.read(65536)
                if not chunk:
                    break
                line = chunk
            if not line:
                break
            decoded = line.decode('utf-8', errors='replace')
            # ★ 规范化行尾：PTY ONLCR 将 \n → \r\n，归一化为 \n，
            #   保留行内独立 \r（用于进度条）不动
            clean = decoded.replace('\r\n', '\n')
            lines.append(clean)
            if show_output:
                safe = _strip_ansi(clean)
                if not safe.endswith('\n'):
                    safe += '\n'
                if is_stderr:
                    await print_to_terminal(f"{RED}{safe}{RESET}")
                else:
                    await print_to_terminal(safe)
            if publish_line_fn:
                try:
                    await publish_line_fn(clean, is_stderr)
                except Exception:
                    pass
        return False

    # ── PIPE / PTY 执行 ────────────────────────────────

    async def _run_pipe(self, show_command=False, show_output=False,
                        publish_line_fn=None):
        """使用 PIPE 模式执行命令（fallback，PTY 不可用时使用）。

        子进程 stdout/stderr 连接到 PIPE，存在全缓冲问题，
        输出不会逐行实时刷新。仅在 PTY 不可用时使用。
        """
        if show_command:
            cwd_info = f" {DIM}(在 {self.cwd}){RESET}" if self.cwd else ""
            await print_to_terminal(f"\n{GREEN}$ {self.command}{cwd_info}{RESET}\n")

        process = await asyncio.create_subprocess_shell(
            self.command,
            cwd=self.cwd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            shell=True,
            close_fds=True,
            preexec_fn=lambda: os.setpgid(0, 0),
            env=self._get_subprocess_env(),
        )

        stdout_lines = []
        stderr_lines = []

        # 使用闭包确保 kill 只执行一次（双流并发时避免冗余 /proc 扫描）
        _kill_once = False

        def _kill_tree_once():
            nonlocal _kill_once
            if not _kill_once:
                _kill_once = True
                _kill_process_tree(process.pid)

        async def _read_pipe_stream(stream, lines_list, is_stderr):
            return await BashFunc._read_loop(
                stream, process, lines_list, publish_line_fn,
                show_output=show_output, is_stderr=is_stderr,
                kill_fn=_kill_tree_once,
            )

        try:
            results = await asyncio.gather(
                _read_pipe_stream(process.stdout, stdout_lines, False),
                _read_pipe_stream(process.stderr, stderr_lines, True),
                return_exceptions=True,
            )
            _interrupted = any(r for r in results if not isinstance(r, Exception))
            for r in results:
                if isinstance(r, Exception):
                    logger.warning("_read_pipe_stream 异常: %s", r)
        except asyncio.CancelledError:
            _kill_tree_once()
            raise
        finally:
            await process.wait()

        if _interrupted:
            return "(命令已被中断)"
        output = ''.join(stdout_lines)
        if stderr_lines:
            stderr_output = ''.join(stderr_lines)
            if output:
                output = output.rstrip('\n') + '\n' + stderr_output
            else:
                output = stderr_output
        return output.strip() or "(无输出)"

    async def _run_pty(self, show_command=False, show_output=False,
                       publish_line_fn=None, pty_ready_fn=None):
        """使用 PTY（伪终端）执行命令，强制子进程行缓冲输出。

        PTY 让子进程的 stdout 看起来像终端，C 运行时会使用行缓冲
        而非全缓冲，确保 \n 结尾的每一行都立即刷新到父进程。

        Args:
            show_command: 是否打印命令行到终端
            show_output: 是否实时打印输出到终端
            publish_line_fn: 可选的回调，每读到一行调用一次
                            publish_line_fn(line_text, is_stderr)
                            （PTY 模式下 stdout/stderr 合并，is_stderr 恒为 False）
            pty_ready_fn: 可选的回调，PTY 创建后调用
                          pty_ready_fn(master_fd)，用于外部获取 PTY master fd
                          以在终端 resize 时同步更新 PTY winsize。

        Returns:
            命令完整输出字符串
        """
        import fcntl
        import pty
        import struct
        import termios

        master_fd, slave_fd = pty.openpty()

        # 设置 PTY 终端大小（避免某些程序因 COLUMNS=0 而异常）
        try:
            fcntl.ioctl(slave_fd, termios.TIOCSWINSZ,
                        struct.pack('HHHH', 24, 120, 0, 0))
        except Exception:
            logger.debug("PTY TIOCSWINSZ 设置失败")

        # ★ pty_ready_fn 回调：通知外层 PTY master fd，
        #   用于终端 resize 时同步更新 PTY winsize。
        if pty_ready_fn is not None:
            try:
                pty_ready_fn(master_fd)
            except Exception:
                logger.debug("pty_ready_fn 回调异常")

        env = self._get_subprocess_env()
        env['TERM'] = 'xterm-256color'  # 告诉子进程这是终端

        _shell = 'bash' if sys.platform.startswith('linux') else 'sh'

        try:
            process = await asyncio.create_subprocess_exec(
                _shell, '-c', self.command,
                cwd=self.cwd,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=slave_fd,
                stderr=slave_fd,
                env=env,
                pass_fds=(slave_fd,),
                preexec_fn=lambda: os.setpgid(0, 0),
            )
        except Exception:
            os.close(master_fd)
            os.close(slave_fd)
            raise

        # 关闭父进程中的 slave 端，确保子进程退出后
        # master 端能收到 EOF
        os.close(slave_fd)

        # 将 master FD 包装为 asyncio StreamReader
        loop = asyncio.get_event_loop()
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)

        try:
            transport, _ = await loop.connect_read_pipe(
                lambda: protocol,
                os.fdopen(master_fd, 'rb', buffering=0),
            )
        except Exception:
            os.close(master_fd)
            raise

        lines = []
        try:
            _interrupted = await BashFunc._read_loop(
                reader, process, lines, publish_line_fn,
                show_output=show_output, is_stderr=False,
                kill_fn=lambda: _kill_process_tree(process.pid),
            )
        except OSError as e:
            if e.errno == _errno.EIO:
                pass  # treat as EOF, fall through to finally
            else:
                raise
        except asyncio.CancelledError:
            _kill_process_tree(process.pid)
            raise
        finally:
            try:
                transport.close()
            except OSError:
                pass
            await process.wait()

        if _interrupted:
            return "(命令已被中断)"
        output = ''.join(lines)
        return output.strip() or "(无输出)"

    async def _run_async(self, show_command=False, show_output=False):
        """异步执行命令，使用 asyncio.create_subprocess_shell（不阻塞事件循环）

        Args:
            show_command: 是否打印命令行
            show_output: 是否实时打印输出

        Returns:
            命令输出字符串
        """
        ret = self._check_cwd_or_return(self.cwd)
        if ret:
            return ret

        if show_command:
            await self._show_command_to_terminal(self.command, self.cwd)

        try:
            if _HAS_PTY:
                result = await asyncio.wait_for(
                    self._run_pty(
                        show_command=False,
                        show_output=show_output,
                        publish_line_fn=None,
                    ),
                    timeout=self.timeout,
                )
            else:
                result = await asyncio.wait_for(
                    self._run_pipe(
                        show_command=False,
                        show_output=show_output,
                        publish_line_fn=None,
                    ),
                    timeout=self.timeout,
                )
            return self._truncate_output(result)
        except asyncio.TimeoutError:
            return "(命令执行超时，已强制终止)"
        except asyncio.CancelledError:
            return "(命令已被取消)"
        except Exception as e:
            logger.exception("异步命令执行异常: %s", self.command[:200])
            return f"(执行出错: {e})"

    # ── 父类契约实现 ──
    # execute() → 无 UI 副作用，只返回结果
    # display() → 负责 UI 展示（实时输出到终端）
    # web_display() → WebUI 实时流式输出到前端

    async def execute(self) -> str:
        """异步执行命令并返回结果。

        有 UI 副作用（通过 _run_async(show_command=True) 将 cmd 输出到工具调用面板）。
        危险命令时在终端输出红色警告。
        使用 asyncio.create_subprocess_shell 避免阻塞事件循环。
        """
        # ★ P0 安全防护：运行时检查危险命令（schema 侧 + 运行时侧双保险）
        danger = _has_dangerous_command(self.command)
        if danger:
            await print_to_terminal(f"{RED}$ {self.command}{RESET}\n{RED}(拒绝执行危险命令: {danger}){RESET}\n")
            return f"(拒绝执行危险命令: {danger})"
        return await self._run_async(show_command=False, show_output=False)

    async def _run_with_line_callback(self, on_line) -> str:
        """共享的 UI 执行框架：检查 cwd、显示命令、执行 PTY/PIPE、异常处理。

        Args:
            on_line: 异步回调 async (text: str, is_stderr: bool) -> None

        Returns:
            命令输出字符串
        """
        ret = self._check_cwd_or_return(self.cwd)
        if ret:
            return ret

        await self._show_command_to_terminal(self.command, self.cwd)

        try:
            if _HAS_PTY:
                result = await asyncio.wait_for(
                    self._run_pty(
                        show_command=False, show_output=False,
                        publish_line_fn=on_line, pty_ready_fn=None,
                    ),
                    timeout=self.timeout,
                )
            else:
                result = await asyncio.wait_for(
                    self._run_pipe(
                        show_command=False, show_output=False,
                        publish_line_fn=on_line,
                    ),
                    timeout=self.timeout,
                )
            return self._truncate_output(result)
        except asyncio.TimeoutError:
            return "(命令执行超时，已强制终止)"
        except asyncio.CancelledError:
            return "(命令已被取消)"
        except Exception as e:
            logger.exception("异步命令执行异常: %s", self.command[:200])
            return f"(执行出错: {e})"

    async def display(self):
        """异步执行命令并实时输出到终端"""
        async def _on_line(text: str, is_stderr: bool) -> None:
            # ★ 终端模拟：先剥 ANSI，再兑现 \r 覆盖语义（进度条等行内刷新
            #   输出在工具卡片呈现与真实终端一致的最终状态，而非字面 \r）。
            safe = _simulate_terminal(_strip_ansi(text))
            if not safe.endswith('\n'):
                safe += '\n'
            if is_stderr:
                await print_to_terminal(f"{RED}{safe}{RESET}")
            else:
                await print_to_terminal(safe)

        return await self._run_with_line_callback(_on_line)

    async def web_display(self):
        """WebUI 模式：异步执行命令并实时流式输出到前端。

        ★ PTY 策略：使用伪终端执行子进程（_run_pty），
           让子进程认为 stdout 是终端，强制行缓冲输出。
           保证每条输出行都实时刷新，避免 PIPE 全缓冲问题。

        两个输出管道并行：
          - 终端：通过 sys.__stdout__ 直接打印（绕过 _SharedCapture 捕获）
          - Web：通过 EventBus 发布 ToolOutputChunkEvent 到前端
        """
        from ..tui.events.event_bus import DisplayEventBus
        from ..tui.events.event_types import ToolOutputChunkEvent
        bus = DisplayEventBus.get_default()

        # 获取当前工具自己的 label（由 ToolCallbackChain._run_tool_method 设置）
        tool_label: str | None = getattr(self, 'tool_label', None)

        async def _on_line(text: str, is_stderr: bool) -> None:
            # ★ 终端模拟：先剥 ANSI，再兑现 \r 覆盖语义（与 display() 一致，
            #   工具卡片/前端呈现与真实终端相同的最终状态）。
            clean = _simulate_terminal(_strip_ansi(text))
            safe = clean if clean.endswith('\n') else clean + '\n'
            if is_stderr:
                await print_to_terminal(f"{RED}{safe}{RESET}")
            else:
                await print_to_terminal(safe)
            if tool_label:
                bus.publish(ToolOutputChunkEvent(
                    label=tool_label, text=clean, source="agent",
                ))

        return await self._run_with_line_callback(_on_line)
