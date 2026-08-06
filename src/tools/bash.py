from __future__ import annotations

import asyncio
import errno as _errno
import json
import logging
import os
import sys
import time
import uuid

from .base import Func, tool_metadata, print_to_terminal
from ._bash_support import (
    _has_dangerous_command,
    _INTERRUPT_CHECK_INTERVAL,
    _READ_CHUNK_SIZE,
    _strip_ansi,
    _PtyEioAsEofProtocol,
    _simulate_terminal,
    _kill_process_tree,
    kill_process_tree,  # noqa: F401  # 公开 API：bash_task 与测试经 bash 模块导入
)

from ..core.constants import GREEN, RED, DIM, RESET
from ..api.interrupt_async import is_interrupted

logger = logging.getLogger(__name__)

# 尝试导入 pty（部分平台不支持）
try:
    import pty as _pty_mod  # noqa: F401  # 仅用于探测平台 PTY 可用性（_HAS_PTY）
    _HAS_PTY = True
except ImportError:
    _HAS_PTY = False


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
    # 前台命令超过该秒数未完成 → 自动转后台执行（不终止进程），
    # 返回 {"task_id": ...} JSON，可用 bash_task 工具继续管理。
    _AUTO_BG_TIMEOUT: int = 60

    @classmethod
    def to_tool_schema(cls):
        return {
            "type": "function",
            "function": {
                "name": "bash",
                "description": (
                    "执行shell命令。用途：编译构建、git操作、包管理、进程管理、系统信息查询。"
                    "禁止替代专用工具——搜索代码用search，查找文件用find，文件读写用read_file/write_file/update_file。"
                    f"前台执行（background 缺省/False）超过 {cls._AUTO_BG_TIMEOUT} 秒未完成时"
                    "自动转后台：命令不终止，立即返回 {\"task_id\": \"bg-xxx\", \"status\": \"running\", \"command\": \"...\"} JSON，"
                    "可用 bash_task 工具按 task_id 继续管理（wait/kill/stdin/keys）。"
                    "返回stdout+stderr合并输出。"
                    "\n\n"
                    "参数说明："
                    "\n- command（必填）：要执行的 shell 命令，支持管道、重定向、环境变量、&& 串联等完整 shell 语法"
                    "\n- cwd（可选）：指定命令的工作目录，省略时使用进程当前工作目录"
                    "\n- background（可选）：是否后台执行（布尔，默认 false）。true 时命令在后台异步运行，"
                    "立即返回 {\"task_id\": \"...\", \"status\": \"running\", \"command\": \"...\"} JSON，"
                    "不阻塞当前工具调用；后台任务完成后系统会自动将结果（JSON 含 task_id 和命令输出）"
                    "作为用户消息插入对话，让大模型继续处理。适用于编译、打包、长时下载等耗时命令。"
                    "false（默认）时前台执行：超过 1 分钟自动转后台，行为同上（返回 task_id JSON）"
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
                    f"\n- 前台执行超过 {cls._AUTO_BG_TIMEOUT} 秒自动转后台：命令不终止，"
                    "立即返回 {\"task_id\": \"bg-xxx\", \"status\": \"running\", \"command\": \"...\"} JSON，"
                    "可用 bash_task 工具继续管理（wait/kill/stdin/keys）"
                    "\n\n"
                    "【Android (Termux) 兼容】"
                    "\n- 不要在命令里使用 `timeout` 系统命令（行为差异可能导致孤儿进程），"
                    "长时命令会自动转后台，无需手动设置超时"
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
                    "\n- 不会停的命令：前台执行超过 1 分钟会自动转后台（返回 task_id），无需手动设置超时"
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
                        "background": {
                            "type": "boolean",
                            "description": (
                                "是否后台执行（可选，默认 false）。"
                                "\n- true：命令在后台异步运行，立即返回 "
                                "'{\"task_id\": \"bg-xxx\", \"status\": \"running\", \"command\": \"...\"}' JSON，"
                                "不阻塞当前工具调用；后台任务完成后系统自动把结果（JSON 含 task_id 和命令输出）"
                                "作为用户消息插入对话，让大模型继续处理。"
                                "\n- 后台任务无限运行（适合编译/打包/长时下载等耗时命令）。"
                                "\n- false（默认）：前台执行并等待命令完成；"
                                "超过 1 分钟自动转后台（命令不终止，返回 task_id JSON，"
                                "可用 bash_task 工具继续管理）。"
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

    def __init__(self, command, cwd=None, background=False):
        super().__init__()
        self.command = command
        self.cwd = cwd
        self.background = bool(background)
        # 无 timeout 参数：前台执行超过 _AUTO_BG_TIMEOUT 秒自动转后台
        # （不终止进程，返回 task_id JSON）；后台任务无限运行。

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

        行数统计基于**逻辑行**：split 后丢弃末尾换行符产生的空元素——
        修复：``'line1\\nline2\\n'``（2 行）直接 split 出 3 个元素（尾 \\n
        多出 1 个空串），恰好 max_lines 行时被误判超限触发截断（用户侧表现
        为「没到 1000 行就被截断」）。

        超长输出一律截断（无论是否以 '(' 开头）——错误提示（如 "(无输出)"）
        本身仅 1 行，远低于 max_lines，不会触发截断；移除旧的
        ``startswith('(')`` 特例避免命令真实输出以 '(' 开头时超长内容
        绕过截断撑爆上下文。
        """
        if max_lines is None:
            max_lines = BashFunc.MAX_LINES
        if not output:
            return output
        lines = output.split('\n')
        # 尾换行产生 1 个空元素，不计入逻辑行数（终端显示不把尾换行当独立行）
        if lines and lines[-1] == '':
            lines.pop()
        if len(lines) > max_lines:
            logger.debug("输出截断: %d 行 -> %d 行（保留尾部最新）", len(lines), max_lines)
            tail = '\n'.join(lines[-max_lines:])
            # ★ join 尾部可能残留换行（截断点恰在换行前/保留行以空行结尾），
            #   rstrip 保证标记前无多余空行、内容恰好 max_lines 行。
            return tail.rstrip('\n') + (
                f'\n...(输出已截断：超过 {max_lines} 行，仅展示最后 {max_lines} 行)'
            )
        return output

    # ── 共享读取循环 ─────────────────────────────────────

    @staticmethod
    async def _read_loop(reader, process, lines, publish_line_fn,
                         show_output=False, is_stderr=False,
                         kill_fn=None):
        """共享读取循环，消除 _run_pipe 和 _run_pty 中的重复代码（~80行×2）。

        从 reader 读取字节，处理中断检测、超时/PTY EIO/超长行、
        UTF-8 解码、\\r\\n 规范化、ANSI 剥离终端输出和行发布回调。

        实现说明（★ 超长行修复）：
        不使用 ``StreamReader.readline()``。readline 内部基于 readuntil，
        当某行超过 StreamReader 默认 limit（64KB）时 readuntil 抛
        ``LimitOverrunError``，而 ``readline()`` 捕获后会把整个内部缓冲
        ``clear()``（数据丢失）；随后 ``_read_loop`` 再调 ``reader.read()``
        只能读到清空后新到达的数据 → 超长行/大块无换行输出被截断
        （用户侧现象：如 ``print('X'*200000)`` 只返回前几 KB）。

        改为循环 ``reader.read(CHUNK)`` 取块 + 本地 bytearray 累积，手动按
        ``\\n`` 切行：
          - 超长行/无换行数据安全累积在本地缓冲，不受 StreamReader limit 限制；
          - ``read()`` 消费 StreamReader 缓冲后自动 ``_maybe_resume_transport``，
            避免缓冲超 limit 后 transport 暂停导致子进程写阻塞（死锁）；
          - 正常行/超长行/EOF 残留统一走 ``_handle_line``，行尾语义与旧
            readline 一致（含 \\n 的行原样保留，EOF 残留无 \\n）。

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
        # 本地累积缓冲：跨块数据/超长行先累积，按 \n 切分后处理
        buffer = bytearray()
        interrupted = False

        async def _handle_line(raw: bytes) -> None:
            """处理一个完整行（含 \\n）或 EOF 残留（无 \\n）。"""
            decoded = raw.decode('utf-8', errors='replace')
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

        while True:
            if is_interrupted():
                if kill_fn is not None:
                    kill_fn()
                else:
                    process.kill()
                interrupted = True
                break
            try:
                chunk = await asyncio.wait_for(
                    reader.read(_READ_CHUNK_SIZE),
                    timeout=_INTERRUPT_CHECK_INTERVAL,
                )
            except asyncio.TimeoutError:
                continue  # 超时→回到循环头检查中断
            except OSError as e:
                if e.errno == _errno.EIO:  # PTY slave closed → EOF
                    break
                raise
            except ValueError:
                # 防御分支：StreamReader.read() 正常不抛 ValueError
                # （readline 的 LimitOverrunError 才抛）；reader._exception
                # 为非 OSError 异常时避免崩溃，向上传播由上层处理。
                raise
            if not chunk:
                break  # EOF
            buffer.extend(chunk)
            # 按 \n 切分完整行（每行保留换行符）
            while True:
                nl = buffer.find(b'\n')
                if nl == -1:
                    break
                await _handle_line(bytes(buffer[:nl + 1]))
                del buffer[:nl + 1]
        # EOF：处理残留的不完整行（超长行无尾换行 / 最后一块无 \n）
        if buffer:
            await _handle_line(bytes(buffer))
            buffer.clear()
        return interrupted

    # ── PIPE / PTY 执行 ────────────────────────────────

    async def _run_pipe(self, show_command=False, show_output=False,
                        publish_line_fn=None, interactive=False,
                        interactive_ready_fn=None):
        """使用 PIPE 模式执行命令（fallback，PTY 不可用时使用）。

        子进程 stdout/stderr 连接到 PIPE，存在全缓冲问题，
        输出不会逐行实时刷新。仅在 PTY 不可用时使用。

        Args:
            show_command: 是否打印命令行到终端
            show_output: 是否实时打印输出到终端
            publish_line_fn: 可选的行回调
            interactive: 是否启用交互式输入。True 时子进程 stdin 使用
                         PIPE（可通过 process.stdin 写入），False 时 DEVNULL。
            interactive_ready_fn: 可选的回调，子进程创建后调用
                          interactive_ready_fn(process, "pipe", stdin_writer=process.stdin)
                          供后台任务记录 stdin 写端（bash_task 工具写入输入用）。
        """
        if show_command:
            cwd_info = f" {DIM}(在 {self.cwd}){RESET}" if self.cwd else ""
            await print_to_terminal(f"\n{GREEN}$ {self.command}{cwd_info}{RESET}\n")

        process = await asyncio.create_subprocess_shell(
            self.command,
            cwd=self.cwd,
            stdin=asyncio.subprocess.PIPE if interactive else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            shell=True,
            close_fds=True,
            preexec_fn=lambda: os.setpgid(0, 0),
            env=self._get_subprocess_env(),
        )

        # ★ interactive_ready_fn 回调：提供 stdin 写端供 bash_task 工具写入。
        if interactive_ready_fn is not None:
            try:
                interactive_ready_fn(process, "pipe", stdin_writer=process.stdin)
            except Exception:
                logger.debug("interactive_ready_fn 回调异常")

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
                       publish_line_fn=None, pty_ready_fn=None,
                       interactive=False, interactive_ready_fn=None):
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
            interactive: 是否启用交互式输入。True 时子进程 stdin 连接到
                          PTY slave（从 master 端写入的数据进入子进程 stdin），
                          False 时 stdin 为 DEVNULL（非交互命令）。
            interactive_ready_fn: 可选的回调，子进程创建后调用
                          interactive_ready_fn(process, "pty", master_fd=master_fd)
                          供后台任务记录进程句柄（bash_task 工具写入输入用）。

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
                stdin=slave_fd if interactive else asyncio.subprocess.DEVNULL,
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

        # ★ interactive_ready_fn 回调：通知外层子进程已创建，提供交互句柄。
        #   在 master 包装为 asyncio 读管道前调用，保证 bash_task 工具
        #   能尽早向 PTY master 写入输入。
        if interactive_ready_fn is not None:
            try:
                interactive_ready_fn(process, "pty", master_fd=master_fd)
            except Exception:
                logger.debug("interactive_ready_fn 回调异常")

        # 将 master FD 包装为 asyncio StreamReader
        # ★ 使用 _PtyEioAsEofProtocol：PTY 子进程退出关闭 slave 端后 master
        #   read 返回 EIO，但缓冲中可能还有未消费的数据（一次性到达的多行
        #   输出）。该 protocol 把 EIO 归一化为 EOF，先消费完缓冲再结束，
        #   避免「多行输出只返回第一行」。
        loop = asyncio.get_event_loop()
        reader = asyncio.StreamReader()
        protocol = _PtyEioAsEofProtocol(reader)

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

    async def _run_async(self, show_command=False, show_output=False,
                         publish_line_fn=None):
        """前台执行命令：最多等待 _AUTO_BG_TIMEOUT 秒，超过自动转后台。

        与旧版（asyncio.wait_for 超时强杀进程）的区别：
        - 使用 asyncio.wait 观察执行任务，超时**不取消**任务；
        - 超过 _AUTO_BG_TIMEOUT 秒未完成 → 自动转后台任务：
          命令继续运行，注册到 agent._background_tasks，返回
          {"task_id": ..., "status": "running", "command": ...} JSON，
          大模型可用 bash_task 工具按 task_id 继续管理（wait/kill/stdin/keys）。

        Args:
            show_command: 是否打印命令行到终端
            show_output: 是否实时打印输出到终端（_read_loop 内部）
            publish_line_fn: 可选的行回调（display/web_display 用）

        Returns:
            命令输出字符串（已截断）；超时转后台时返回 task_id JSON 字符串
        """
        ret = self._check_cwd_or_return(self.cwd)
        if ret:
            return ret

        # ★ 子进程句柄记录：自动转后台时写入任务记录，供 bash_task 工具操作
        #   （wait 等待 / kill 杀进程树 / stdin、keys 交互）。
        holder: dict = {}

        def _on_ready(process, mode, master_fd=None, stdin_writer=None):
            holder["process"] = process
            holder["mode"] = mode
            holder["master_fd"] = master_fd
            holder["stdin_writer"] = stdin_writer

        # ★ 行回调代理（可变引用）：自动转后台后把 fn 置 None，断开实时输出，
        #   避免转后台后命令输出继续污染已闭合的工具卡片（工具已返回
        #   task_id JSON，后续输出由 bash_task wait 获取完整结果）。
        line_cb: dict = {"fn": publish_line_fn}

        async def _line_proxy(text: str, is_stderr: bool) -> None:
            fn = line_cb["fn"]
            if fn is not None:
                await fn(text, is_stderr)

        async def _run_exec():
            if _HAS_PTY:
                result = await self._run_pty(
                    show_command=False,
                    show_output=show_output,
                    publish_line_fn=_line_proxy,
                    interactive=True,
                    interactive_ready_fn=_on_ready,
                )
            else:
                result = await self._run_pipe(
                    show_command=False,
                    show_output=show_output,
                    publish_line_fn=_line_proxy,
                    interactive=True,
                    interactive_ready_fn=_on_ready,
                )
            return self._truncate_output(result)

        exec_task = asyncio.ensure_future(_run_exec())
        done, _pending = await asyncio.wait(
            {exec_task}, timeout=self._AUTO_BG_TIMEOUT,
        )
        if exec_task in done:
            try:
                result = exec_task.result()
            except asyncio.CancelledError:
                return "(命令已被取消)"
            except Exception as e:
                logger.exception("异步命令执行异常: %s", self.command[:200])
                return f"(执行出错: {e})"
            return result

        # 超过 _AUTO_BG_TIMEOUT 秒 → 自动转后台执行（不终止进程）。
        # ★ 断开实时行回调：工具即将返回 task_id JSON（工具卡片闭合），
        #   转后台后的输出不再发布到终端/前端。
        line_cb["fn"] = None
        return await self._promote_to_background(exec_task, holder)

    async def _promote_to_background(self, exec_task, holder: dict) -> str:
        """把已运行超过 _AUTO_BG_TIMEOUT 秒的前台命令转为后台任务。

        前台命令超过 1 分钟仍未完成时调用（由 _run_async 触发）：
          - **不终止进程**（asyncio.wait 观察而非 wait_for 干预）；
          - 生成 task_id，把执行中的任务注册到 agent._background_tasks；
          - 任务完成后结果写入任务记录（bash_task wait / 对话轮次自动
            插入用户消息消费）；
          - 返回 {"task_id": ..., "status": "running", "command": ...} JSON，
            大模型可用 bash_task 工具继续管理。

        Args:
            exec_task: 正在运行的命令执行任务（asyncio.Task）
            holder: 子进程句柄记录（process/mode/master_fd/stdin_writer）

        Returns:
            task_id JSON 字符串
        """
        agent = getattr(self, 'agent', None)
        if agent is None or not hasattr(agent, '_register_background_task'):
            return (f"(命令执行超过 {self._AUTO_BG_TIMEOUT} 秒，但当前未关联 "
                    f"Agent 上下文，无法自动转后台管理)")

        task_id = f"bg-{uuid.uuid4().hex[:12]}"
        process = holder.get("process")

        agent._register_background_task(task_id, {
            "task": exec_task,
            "command": self.command,
            "cwd": self.cwd,
            "created_at": time.time(),
            "done": False,
            "result": "",
            "status": "running",
            # ── 交互控制字段（bash_task 工具按 task_id 操作） ──
            "process": process,
            "pid": process.pid if process is not None else None,
            "mode": holder.get("mode"),
            "master_fd": holder.get("master_fd"),
            "stdin_writer": holder.get("stdin_writer"),
            "io_lock": asyncio.Lock(),
        })

        def _on_done(t: asyncio.Task) -> None:
            """任务完成回调：把结果写入后台任务记录（与 _run_background_task 一致）。"""
            try:
                result = t.result()
            except asyncio.CancelledError:
                result = "(后台命令已被取消)"
            except Exception as e:
                logger.exception("自动转后台命令执行异常: %s", self.command[:200])
                result = f"(后台命令执行出错: {e})"
            if agent is not None and hasattr(agent, '_complete_background_task'):
                try:
                    agent._complete_background_task(task_id, result)
                except Exception:
                    logger.exception("自动转后台任务结果写入失败")

        exec_task.add_done_callback(_on_done)

        await print_to_terminal(
            f"{DIM}[命令执行超过 {self._AUTO_BG_TIMEOUT} 秒，已自动转后台任务 "
            f"{task_id}: {self.command[:80]}{'...' if len(self.command) > 80 else ''}]{RESET}\n"
        )

        return json.dumps({
            "task_id": task_id,
            "status": "running",
            "command": self.command,
        }, ensure_ascii=False)

    async def _run_interactive_async(self, on_ready=None):
        """交互模式异步执行：启用 stdin（PTY slave / PIPE），供后台任务操作。

        与 _run_async 的区别：
          - **后台任务执行体（background=True）专用**：无限运行，不设超时、
            不自动转后台（本身已是后台任务）；
          - interactive=True：子进程 stdin 连接 PTY slave（或 PIPE），
            外部可通过任务记录中的 master_fd / stdin_writer 写入输入；
          - on_ready 回调在子进程创建后立即调用，把 process/pid/master_fd
            写入任务记录，供 bash_task 工具按 task_id 操作。

        Args:
            on_ready: 可选回调 on_ready(process, mode, master_fd=None, stdin_writer=None)

        Returns:
            命令完整输出字符串（已截断）
        """
        ret = self._check_cwd_or_return(self.cwd)
        if ret:
            return ret

        try:
            if _HAS_PTY:
                result = await self._run_pty(
                    show_command=False, show_output=False,
                    publish_line_fn=None,
                    interactive=True, interactive_ready_fn=on_ready,
                )
            else:
                result = await self._run_pipe(
                    show_command=False, show_output=False,
                    publish_line_fn=None,
                    interactive=True, interactive_ready_fn=on_ready,
                )
            return self._truncate_output(result)
        except asyncio.CancelledError:
            return "(命令已被取消)"
        except Exception as e:
            logger.exception("交互式命令执行异常: %s", self.command[:200])
            return f"(执行出错: {e})"

    # ── 后台执行模式 ────────────────────────────────────────
    # background=True 时：命令在 asyncio 后台任务中运行，工具立即返回
    # {"task_id": ..., "status": "running"} JSON；任务记录注册到当前
    # Agent 的 _background_tasks 成员（tasklist）。一轮对话完成后，
    # Agent 主循环检查后台任务：已完成的把结果（JSON：task_id + 命令输出）
    # 作为用户消息插入对话继续处理；未完成的则等待全部完成后再次插入。

    async def _execute_background(self) -> str:
        """后台执行命令：生成 task_id、注册到 agent 后台任务列表，立即返回 JSON。

        需要当前 BashFunc 实例关联了 Agent（registry.dispatch 会自动 set_agent）。
        返回 JSON 字符串（task_id/status/command），供大模型识别后台任务。
        """
        # ★ 危险命令检查：display() 路径（主 Agent）进入后台前也需运行时防护
        danger = _has_dangerous_command(self.command)
        if danger:
            return f"(拒绝执行危险命令: {danger})"

        agent = getattr(self, 'agent', None)
        if agent is None or not hasattr(agent, '_register_background_task'):
            return "(后台执行需要关联 Agent 上下文，当前未关联)"

        # 显示启动命令（后台任务本身不再重复打印）

        task_id = f"bg-{uuid.uuid4().hex[:12]}"

        # 后台任务无限运行（不设超时，避免误杀编译/下载等长时命令）
        bg = BashFunc(command=self.command, cwd=self.cwd)

        task = asyncio.ensure_future(self._run_background_task(bg, task_id))

        # ── tasklist 放入对应 agent 的成员 _background_tasks ──
        # ★ 交互控制字段（bash_task 工具按 task_id 操作后台任务）：
        #   process/pid/mode/master_fd/stdin_writer 由 _run_background_task
        #   的子进程创建回调（_on_ready）填充；io_lock 串行化 stdin/keys 写入。
        agent._register_background_task(task_id, {
            "task": task,
            "command": self.command,
            "cwd": self.cwd,
            "created_at": time.time(),
            "done": False,
            "result": "",
            "status": "running",
            # ── 交互控制字段 ──
            "process": None,        # asyncio.subprocess.Process（创建后填充）
            "pid": None,            # 子进程 PID（kill 进程树用）
            "mode": None,           # "pty" / "pipe"（写入方式）
            "master_fd": None,      # PTY master fd（mode="pty" 时，os.write 写入）
            "stdin_writer": None,   # PIPE stdin StreamWriter（mode="pipe" 时）
            "io_lock": asyncio.Lock(),  # stdin/keys 写入串行化
        })

        await print_to_terminal(
            f"{DIM}[后台任务 {task_id} 已启动: {self.command[:80]}{'...' if len(self.command) > 80 else ''}]{RESET}\n"
        )

        return json.dumps({
            "task_id": task_id,
            "status": "running",
            "command": self.command,
        }, ensure_ascii=False)

    async def _run_background_task(self, bg: "BashFunc", task_id: str) -> None:
        """后台任务执行体：运行命令并把结果写入 agent 的后台任务记录。

        ★ 后台任务无限运行（_run_interactive_async 不设超时、不自动转后台），
        配合 bash_task 工具按 task_id 操作（wait/kill/stdin/keys）。
        ★ 交互模式：后台任务启用 stdin（PTY slave / PIPE），子进程创建后
        通过 on_ready 回调把 process/pid/master_fd 写入任务记录，供
        bash_task 工具按 task_id 发送输入 / 杀死进程树 / 等待完成。

        Args:
            bg: 实际执行命令的 BashFunc 实例
            task_id: 后台任务 ID
        """
        agent = getattr(self, 'agent', None)

        def _on_ready(process, mode, master_fd=None, stdin_writer=None):
            """子进程创建回调：把进程句柄写入任务记录（bash_task 工具使用）。"""
            if agent is None or not hasattr(agent, '_background_tasks'):
                return
            rec = agent._background_tasks.get(task_id)
            if rec is None:
                return
            rec["process"] = process
            rec["pid"] = process.pid
            rec["mode"] = mode
            rec["master_fd"] = master_fd
            rec["stdin_writer"] = stdin_writer

        try:
            result = await bg._run_interactive_async(_on_ready)
        except asyncio.CancelledError:
            result = "(后台命令已被取消)"
        except Exception as e:
            logger.exception("后台命令执行异常: %s", self.command[:200])
            result = f"(后台命令执行出错: {e})"

        if agent is not None and hasattr(agent, '_complete_background_task'):
            try:
                agent._complete_background_task(task_id, result)
            except Exception:
                logger.exception("后台任务结果写入失败")
        else:
            logger.warning("后台任务 %s 完成但 agent 已不可用，结果丢弃", task_id)
            return

        # ★ 完成提示发布为**普通输出**（OutputEvent），而非工具输出
        #   （ToolOutputChunkEvent）：此时工具上下文已退出（contextvar 中
        #   tool_id 已清除），走工具输出路径会以 label="assistant" 触发
        #   append_tool_output 兜底创建一个永不闭合的空「工具」卡
        #   （┌─ ● ⚙ 工具），这里改为普通文本行显示，避免空工具卡。
        try:
            from ..core.display_target import get_output_publisher
            publisher = get_output_publisher()
            if publisher is not None:
                publisher(f"[后台任务 {task_id} 已完成]", level="info", source="agent")
        except Exception:
            logger.debug("后台任务完成提示发布失败", exc_info=True)

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
        # ── 后台模式：不等待命令完成，立即返回 task_id JSON ──
        if self.background:
            return await self._execute_background()
        return await self._run_async(show_command=False, show_output=False)

    async def _run_with_line_callback(self, on_line) -> str:
        """共享的 UI 执行框架：检查 cwd、显示命令、执行 PTY/PIPE、异常处理。

        前台执行与 _run_async 一致：超过 _AUTO_BG_TIMEOUT 秒自动转后台
        （命令不终止，返回 task_id JSON）；publish_line_fn 持续接收实时输出行。

        Args:
            on_line: 异步回调 async (text: str, is_stderr: bool) -> None

        Returns:
            命令输出字符串（已截断）；超时转后台时返回 task_id JSON 字符串
        """
        # ── 后台模式：display/web_display 路径同样不等待命令完成 ──
        if self.background:
            return await self._execute_background()

        ret = self._check_cwd_or_return(self.cwd)
        if ret:
            return ret


        return await self._run_async(
            show_command=False, show_output=False,
            publish_line_fn=on_line,
        )

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
