from __future__ import annotations

import asyncio
import base64
import errno as _errno
import json
import logging
import os
import shutil
import subprocess
import sys
import time
import uuid

from .base import Func, print_to_terminal
from ._bash_support import (
    _has_dangerous_command,
    _INTERRUPT_CHECK_INTERVAL,
    _READ_CHUNK_SIZE,
    _strip_ansi,
    _PtyEioAsEofProtocol,
    _simulate_terminal,
    _wrap_colored_line,
    _kill_process_tree,
    kill_process_tree,  # noqa: F401  # 公开 API：bash_opt 与测试经 bash 模块导入
)
from ._process_output import OutputLineDecoder

from ..core.constants import GREEN, RED, DIM, RESET
from ..api.interrupt_async import is_interrupted
from ..prompt_builder.shell_info import SHELL_ENV_MARKER, detect_shell

logger = logging.getLogger(__name__)

# 尝试探测平台 PTY 可用性（不实际导入模块，避免无谓开销）
try:
    import importlib.util as _importlib_util

    # ★ Windows 修复（2026-10-20）：Windows 版 CPython 发行包同样附带
    #   ``pty.py``（模块存在、可导入），但其内部依赖的 ``os.openpty`` /
    #   ``fcntl`` / ``termios`` 在原生 Windows 上不存在。修复前只按「模块
    #   是否存在」判定 → Windows 上 ``_HAS_PTY`` 恒为 True：后台任务
    #   （``_run_interactive_async``）误走 PTY 分支，在 ``_run_pty`` 内
    #   ``import fcntl`` 处抛 ModuleNotFoundError，后台命令直接失败
    #   （bash_opt wait 只拿到 returncode=-1）。PTY 可用性须同时满足：
    #   非原生 Windows + ``os.openpty`` 存在 + ``pty``/``fcntl`` 可导入。
    _HAS_PTY = (
        os.name != "nt"
        and hasattr(os, "openpty")
        and _importlib_util.find_spec("pty") is not None
        and _importlib_util.find_spec("fcntl") is not None
    )
except Exception:  # pragma: no cover - 极端环境下探测失败按不可用处理
    _HAS_PTY = False


# ── 按「启动本程序的交互 shell」选择解释器 ──────────────
# 系统提示词里的「命令行」是按父进程链识别出的真实交互 shell（powershell /
# cmd / bash / zsh / fish ...）。命令执行必须与之一致：写命令的模型是按那个
# shell 的语法写的（`ls` / `Get-ChildItem` / `[[ ... ]]` / `dir`），换成另一种
# 解释器就会判成「不是内部或外部命令」或语法错误。
#   - PowerShell：用 -EncodedCommand 传脚本（base64 UTF-16LE），彻底避开
#     命令行引号 / 换行 / 中文转义问题；
#   - 其余 POSIX 系 shell：`[<shell>, "-c", command]` 以 argv 传命令，不经
#     任何中间 shell 二次转义；
#   - cmd 与检测失败：走平台默认 shell=True（Windows → ComSpec=cmd.exe，
#     POSIX → /bin/sh）。

#: 优先使用的 PowerShell 可执行文件（PowerShell 7 优先，回退 Windows PowerShell 5.1）
_POWERSHELL_EXECUTABLES = ("pwsh", "powershell")


def _find_powershell_executable() -> str | None:
    """定位 PowerShell 可执行文件（pwsh 优先，回退系统自带 powershell.exe）。"""
    for name in _POWERSHELL_EXECUTABLES:
        path = shutil.which(name)
        if path:
            return path
    if os.name == "nt":
        system_root = os.environ.get("SystemRoot") or r"C:\Windows"
        candidate = os.path.join(
            system_root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe",
        )
        if os.path.isfile(candidate):
            return candidate
    return None


#: 归一化 shell 名 → 可执行文件名候选（未登记的名字与归一化名同名）。
#: ``shell_info`` 把 ``nu`` 归一化为 ``nushell``，可执行文件名仍是 ``nu``。
_SHELL_EXECUTABLE_CANDIDATES: dict = {
    "nushell": ("nu", "nushell"),
}

#: Windows 上名为 bash、但**不是**本机交互 shell 的启动器：``System32\bash.exe``
#: （旧 WSL 启动器）与 ``WindowsApps\bash.exe``（商店应用执行别名）。它们把命令
#: 交给 WSL 发行版执行——工作目录 / 文件系统与当前 Windows 会话不在一棵树里，
#: 拿来执行本机命令会「命令能跑、文件找不到」，不能作为当前环境的 bash。
_WSL_BASH_STUBS = ("\\system32\\bash.exe", "\\windowsapps\\bash.exe")


def _is_wsl_bash_stub(path: str) -> bool:
    """Windows 上该 bash 路径是否只是 WSL 启动器（不是本机 bash）。"""
    return (path or "").replace("/", "\\").lower().endswith(_WSL_BASH_STUBS)


def _shell_basename(value: str) -> str:
    """路径 / 进程名 → 小写 basename（去掉 ``.exe`` 后缀）。"""
    name = (value or "").strip().strip('"').strip("'").replace("\\", "/").rstrip("/")
    name = name.rsplit("/", 1)[-1]
    if name.lower().endswith(".exe"):
        name = name[:-4]
    return name.lower()


def _find_shell_executable(shell: str) -> str | None:
    """按归一化 shell 名定位其可执行文件（找不到返回 None）。

    - ``powershell``：``pwsh`` 优先，回退系统自带 ``powershell.exe``；
    - 其他 shell（bash / zsh / fish / ksh / dash / csh / tcsh / nushell /
      xonsh / elvish / sh）：POSIX 上优先用 ``$SHELL``——它是用户登录 shell
      的绝对路径，与检测结果一致时比在 PATH 里按名字找更精确（同名不同版本、
      或 PATH 未包含该 shell 所在目录）；否则在 PATH 中查找。Windows 上名为
      ``bash`` 的 WSL 启动器（``System32`` / ``WindowsApps``）不算本机 bash，
      会跳过（见 :func:`_is_wsl_bash_stub`）。
    """
    if not shell:
        return None
    if shell == "powershell":
        return _find_powershell_executable()
    names = _SHELL_EXECUTABLE_CANDIDATES.get(shell, (shell,))
    if os.name != "nt":
        login_shell = (os.environ.get("SHELL") or "").strip()
        if login_shell and _shell_basename(login_shell) in names and os.path.isfile(login_shell):
            return login_shell
    for name in names:
        path = shutil.which(name)
        if not path:
            continue
        if name == "bash" and os.name == "nt" and _is_wsl_bash_stub(path):
            logger.debug("%s 是 WSL 启动器（非本机 bash），不作为当前 shell 执行", path)
            continue
        return path
    return None


def _powershell_script(command: str) -> str:
    """把用户命令包装成 PowerShell 脚本。

    - 关闭进度流（``$ProgressPreference``）：stderr 被重定向时 PowerShell
      会把进度记录序列化成 CLIXML（``#< CLIXML <Objs ... progress ...``）
      写进 stderr，工具卡片里是一大段噪声；
    - 错误流改 ``Stop`` + ``catch``：stderr 重定向时 PowerShell 默认把
      错误记录也序列化成 CLIXML（``<S S="Error">...``），模型看到的
      不是可读错误；显式捕获后按纯文本写回 stderr；
    - 输出编码固定 UTF-8（无控制台 / 中文代码页下 PowerShell 默认按系统
      OEM 代码页输出，工具卡片会乱码；``OutputLineDecoder`` 仍做回退兜底）；
    - 命令前重置 ``$LASTEXITCODE``，命令后按原生命令退出码退出——保持
      bash 工具「returncode = 命令退出码」的契约；命令是纯 cmdlet 时
      用 ``$?`` 兜底（PowerShell 的 cmdlet 错误默认不设置 ``$LASTEXITCODE``，
      如 ``Get-Item nope`` 在 bash 语义下应为非零）；
    - ``exit`` 由用户命令自己触发时不会走到末尾判断，语义不变。
    """
    return (
        "$ProgressPreference = 'SilentlyContinue'\n"
        "$ErrorActionPreference = 'Stop'\n"
        "try {\n"
        "    try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }\n"
        "    $global:LASTEXITCODE = 0\n"
        f"{command}\n"
        "    $ok = $?\n"
        "    if ($null -ne $LASTEXITCODE -and $LASTEXITCODE -ne 0) { exit $LASTEXITCODE }\n"
        "    if (-not $ok) { exit 1 }\n"
        "} catch {\n"
        "    [Console]::Error.WriteLine($_.Exception.Message)\n"
        "    exit 1\n"
        "}\n"
    )


def _encode_powershell_command(script: str) -> str:
    """PowerShell ``-EncodedCommand`` 参数（UTF-16LE + base64）。"""
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def _resolve_shell_launch(command: str) -> tuple:
    """按「启动本程序的交互 shell」决定命令启动方式。

    检测到的交互 shell 就是系统提示词里声明的「命令行」，命令必须由同一个
    解释器执行（模型按该 shell 的语法写命令）：

    Returns:
        ``(command_arg, use_shell, shell_name)``：
          - ``powershell``：``([pwsh/powershell, -NoLogo, -NoProfile,
            -EncodedCommand, <base64>], False, "powershell")``；
          - 其他 POSIX 系 shell（bash / zsh / fish / ksh / dash / csh /
            tcsh / nushell / xonsh / elvish / sh）：
            ``([<shell 可执行文件>, "-c", command], False, shell)``；
          - ``cmd``：``(command, True, "cmd")``——Windows 上 ``shell=True``
            即 ``ComSpec``（cmd.exe），语义一致且免去引号重写；
          - 检测失败 / 找不到 shell 可执行文件：``(command, True, shell)``
            走平台默认 shell（Windows → cmd.exe，POSIX → /bin/sh）。
    """
    shell = detect_shell() or ""
    if shell == "powershell":
        executable = _find_powershell_executable()
        if executable:
            script = _powershell_script(command)
            return (
                [
                    executable,
                    "-NoLogo",
                    "-NoProfile",
                    "-EncodedCommand",
                    _encode_powershell_command(script),
                ],
                False,
                shell,
            )
        logger.warning(
            "检测到 PowerShell 交互 shell，但未找到 pwsh/powershell 可执行文件，回退平台默认 shell")
    elif shell == "cmd":
        return command, True, shell
    else:
        executable = _find_shell_executable(shell)
        if executable:
            return [executable, "-c", command], False, shell
        if shell:
            logger.warning(
                "检测到 %s 交互 shell，但未找到其可执行文件，回退平台默认 shell", shell)
    return command, True, shell


def _default_posix_shell() -> str:
    """检测不到具体 shell 时的默认 POSIX shell（沿用旧行为：Linux bash、其余 sh）。"""
    return 'bash' if sys.platform.startswith('linux') else 'sh'


def _pty_launch_argv(command: str) -> list:
    """PTY 分支的命令 argv：与 PIPE 分支共用同一套交互 shell 解析结果。

    PTY 需要 argv 形式（控制终端设置 / ``pass_fds`` 都依赖 exec 而非 shell
    解析）；``_resolve_shell_launch`` 回退到 ``shell=True`` 时，这里用默认
    POSIX shell 显式包一层 ``-c``，保持「命令由某个 shell 解释器执行」的语义。
    """
    launch_arg, use_shell, _shell_name = _resolve_shell_launch(command)
    if not use_shell and isinstance(launch_arg, (list, tuple)):
        return list(launch_arg)
    return [_default_posix_shell(), "-c", command]


#: 后台任务 read_buffer 的字符上限（bash_opt read 消费）。
#: 长时任务（编译日志 / 下载进度 / ``yes`` 类命令）会持续产生输出，若模型
#: 一直不 read，缓冲会无限累积直至耗尽内存；超过上限时**丢弃最旧的内容**、
#: 保留最新（尾部）——最新输出对判断任务进展更有价值——并把丢弃字符数累计
#: 到 ``read_buffer_dropped``，由 bash_opt read 在结果中如实回报。
_READ_BUFFER_MAX_CHARS = 200_000

#: read_buffer 丢弃字符数的记录键（bash_opt read 消费后清零）
READ_BUFFER_DROPPED_KEY = "read_buffer_dropped"


def _trim_read_buffer(rec: dict) -> None:
    """把 read_buffer 裁剪到上限（保留最新内容，累计丢弃字符数）。

    在 io_lock 保护下由 :func:`_append_read_buffer` 调用；不持锁时也可安全
    调用（纯读改写，调用方保证串行）。
    """
    buffer = rec.get("read_buffer") or ""
    if len(buffer) <= _READ_BUFFER_MAX_CHARS:
        return
    dropped = len(buffer) - _READ_BUFFER_MAX_CHARS
    rec["read_buffer"] = buffer[-_READ_BUFFER_MAX_CHARS:]
    rec[READ_BUFFER_DROPPED_KEY] = int(
        rec.get(READ_BUFFER_DROPPED_KEY, 0) or 0) + dropped


async def _append_read_buffer(rec: dict, text: str) -> None:
    """把输出行追加到任务记录 read_buffer（bash_opt read 操作消费）。

    read_buffer 保存后台任务运行期间**已产生但尚未被 bash_opt read 消费**
    的输出；read 操作读取后清空（增量语义）。用 io_lock 串行化并发访问
    （PIPE 双流 publish 与 bash_opt read 可能并发）。

    追加后按 :data:`_READ_BUFFER_MAX_CHARS` 裁剪（内存上限保护，见常量说明）。
    """
    lock = rec.get("io_lock")
    if lock is not None:
        async with lock:
            rec["read_buffer"] = rec.get("read_buffer", "") + text
            _trim_read_buffer(rec)
    else:
        rec["read_buffer"] = rec.get("read_buffer", "") + text
        _trim_read_buffer(rec)


class BashFunc(Func):
    name = "bash"
    # 前台命令超过该秒数未完成 → 自动转后台执行（不终止进程），
    # 返回 {"task_id": ...} JSON，可用 bash_opt 工具继续管理。
    _AUTO_BG_TIMEOUT: int = 60

    @classmethod
    def to_tool_schema(cls):
        return {
            "type": "function",
            "function": {
                "name": "bash",
                "description": (
                    "执行 shell 命令，返回 JSON：{\"stdout\":..., \"stderr\":..., \"returncode\": 退出码}"
                    "（stdout 与 stderr 分离收集，各自超 1000 行截断保留尾部；"
                    "returncode 是命令的退出码，等价 bash 的 $?：0 成功、非 0 命令失败、被信号杀死为 128+sig、"
                    "-1 工具级错误——错误说明在 stderr 字段）。"
                    "命令由当前命令行（见「当前执行环境」的「命令行」）对应的 shell 解释器执行"
                    "（bash / zsh / fish / cmd / powershell 等），请按该 shell 的语法书写。"
                    "用途：编译构建、git、包管理、进程管理、系统信息查询。"
                    "禁止替代专用工具：读文件用 read、改文件用 edit、写文件用 write、搜索用 grep、找文件用 glob/ls、建目录用 mkdir。"
                    "仅当专用工具功能不足（如正则多行匹配、二进制）时才用 bash，并加注释 `# 例外原因：<原因>`。"
                    f"前台执行超过 {cls._AUTO_BG_TIMEOUT} 秒自动转后台，返回 task_id JSON，用 bash_opt 继续管理。"
                    "禁止交互式命令（vim/top/less）；禁止 git push -f / reset --hard；"
                    "禁止 rm -rf / mkfs / dd / chmod 777 / sudo / chown。对 git 状态断言前先 git log/diff/status 验证。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": (
                                "要执行的 shell 命令，由当前命令行对应的解释器执行（bash/zsh/fish/cmd/powershell 等），"
                                "语法按其支持情况书写（管道 |、重定向 > >> <、环境变量、&& || 串联、$(...) 子 shell、通配符等）。"
                                "返回 JSON：stdout 与 stderr 分离，无输出时对应字段为空字符串；"
                                "returncode 为退出码；stdout/stderr 各自超 1000 行截断保留尾部。"
                            )
                        },
                        "background": {
                            "type": "boolean",
                            "description": (
                                "是否后台执行（默认 false）。true 立即返回 task_id JSON，后台完成自动注入对话；"
                                "false 前台执行，超 1 分钟自动转后台。"
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
        """返回子进程环境变量，添加反缓冲设置确保实时输出。

        ★ 同时注入 :data:`SHELL_ENV_MARKER`（真实交互 shell）：子进程的父链
        首项是工具自己的执行外壳（``cmd.exe /c``、``bash -c``、``powershell
        -EncodedCommand`` ...），单看进程名会把 PowerShell 会话误判成 cmd、
        把 bash 会话误判成 sh；本进程知道自己是从哪个交互 shell 启动的，写进
        环境变量后子进程检测可直接命中（见 ``shell_info``）。
        """
        env = os.environ.copy()
        env['PYTHONUNBUFFERED'] = '1'  # Python 脚本强制无缓冲
        env.pop('PAGER', None)         # 禁用分页器（PTY 模拟终端会触发 pager）
        env['GIT_PAGER'] = 'cat'       # git diff 等通过 PTY 运行时不启用分页器
        shell = detect_shell()
        if shell:
            env[SHELL_ENV_MARKER] = shell
        return env

    @classmethod
    def _is_pty_available(cls) -> bool:
        """检查 PTY 是否可用（用于子进程实时行缓冲输出）。"""
        return _HAS_PTY

    def __init__(self, command, background=False):
        super().__init__()
        self.command = command
        self.background = bool(background)
        # 无 cwd 参数：命令始终在当前工作目录执行。
        # 无 timeout 参数：前台执行超过 _AUTO_BG_TIMEOUT 秒自动转后台
        # （不终止进程，返回 task_id JSON）；后台任务无限运行。

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

        超长输出一律截断（无论是否以 '(' 开头）——三元 JSON 返回结构中
        stdout / stderr 各自独立调用本方法截断。
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

    # ── 三元 JSON 结果组装 ─────────────────────────────
    # 返回给大模型的结构：{"stdout": ..., "stderr": ..., "returncode": N}
    #   - stdout / stderr：各自截断（MAX_LINES 行，保留尾部）后输出
    #   - returncode：**命令的退出码（bash $? 语义）**——正常退出即 shell
    #     进程退出码；被信号杀死转换为 128+sig（如 SIGKILL → 137，与 bash
    #     一致）；-1 仅用于工具级错误（命令未真正执行：拒绝/取消/异常）

    @staticmethod
    def _bash_rc(returncode: "int | None") -> int:
        """把子进程退出码规范化为 bash ``$?`` 语义。

        - ``returncode >= 0``：正常退出，原样返回（即命令退出码）；
        - ``returncode < 0``：子进程被信号杀死（asyncio 约定取负值），
          转换为 bash 风格 ``128 + sig``（SIGTERM/-15 → 143、SIGKILL/-9
          → 137），与真实终端 ``$?`` 一致；
        - ``None``（进程未收尸等异常时序）：返回 -1。
        """
        if returncode is None:
            return -1
        if returncode < 0:
            return 128 - returncode  # 128 + sig
        return returncode

    @staticmethod
    def _format_result(res: dict) -> str:
        """把结构化执行结果 dict 组装为返回给大模型的三元 JSON 字符串。

        Args:
            res: {"stdout": str, "stderr": str, "returncode": int}

        Returns:
            JSON 字符串：stdout/stderr 分别截断（超 MAX_LINES 行保留尾部），
            returncode 原样输出。
        """
        stdout = BashFunc._truncate_output(res.get("stdout", "") or "")
        stderr = BashFunc._truncate_output(res.get("stderr", "") or "")
        return json.dumps({
            "stdout": stdout,
            "stderr": stderr,
            "returncode": res.get("returncode"),
        }, ensure_ascii=False)

    @staticmethod
    def _error_result(message: str) -> str:
        """工具级错误的三元 JSON（未真正执行命令时使用）。

        Args:
            message: 错误说明（如 "(拒绝执行危险命令: ...)"）

        Returns:
            {"stdout": "", "stderr": message, "returncode": -1}
        """
        return json.dumps({
            "stdout": "",
            "stderr": message,
            "returncode": -1,
        }, ensure_ascii=False)

    # ── 共享读取循环 ─────────────────────────────────────

    @staticmethod
    async def _read_loop(reader, process, lines, publish_line_fn,
                         show_output=False, is_stderr=False,
                         kill_fn=None, decoder=None):
        """共享读取循环，消除 _run_pipe 和 _run_pty 中的重复代码（~80行×2）。

        从 reader 读取字节，处理中断检测、超时/PTY EIO/超长行、**编码自适应
        解码**（``OutputLineDecoder``）、\\r\\n 规范化、ANSI 剥离 + \\r 覆盖
        模拟（终端视角统一）和行发布回调。

        ★ 编码自适应（Windows 乱码修复，2026-10-20）：修复前此处把子进程
        字节一律 ``decode('utf-8', errors='replace')``——Windows 原生（命令经
        ``cmd.exe`` 执行）下 cmd 内建命令与多数控制台程序按控制台代码页
        （简体中文 cp936/GBK）输出，UTF-8 解码失败被替换成 U+FFFD，工具卡片
        显示乱码。现由 ``OutputLineDecoder`` 逐行自适应（UTF-8 优先 → 本机
        代码页回退 → replace 兜底，并支持 UTF-16 宽字符流），详见
        ``src/tools/_process_output.py``。

        实现说明（★ 超长行修复）：
        不使用 ``StreamReader.readline()``。readline 内部基于 readuntil，
        当某行超过 StreamReader 默认 limit（64KB）时 readuntil 抛
        ``LimitOverrunError``，而 ``readline()`` 捕获后会把整个内部缓冲
        ``clear()``（数据丢失）；随后 ``_read_loop`` 再调 ``reader.read()``
        只能读到清空后新到达的数据 → 超长行/大块无换行输出被截断
        （用户侧现象：如 ``print('X'*200000)`` 只返回前几 KB）。

        改为循环 ``reader.read(CHUNK)`` 取块 + 解码器内部缓冲累积，手动按
        换行切分：
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
            decoder: 可选的 ``OutputLineDecoder``（测试/自定义编码候选注入；
                    省略时按本机编码新建）。每个流（stdout/stderr）独立一个。

        Returns:
            bool: True 表示被 ESC 中断信号打断，False 表示正常读到 EOF
        """
        # 本地解码器：跨块数据/超长行先在其内部缓冲，按换行切分后处理
        decoder = decoder or OutputLineDecoder()
        interrupted = False

        async def _handle_line(text: str) -> None:
            """处理一个已解码的完整行（含 \\n）或 EOF 残留（无 \\n）。

            ★ 终端视角统一（返回内容与终端显示一致）：在数据源头统一
            剥离 ANSI（_strip_ansi）并兑现 \\r 覆盖语义（_simulate_terminal）
            ——lines（最终返回给大模型的输出）、show_output（终端实时打印）
            与 publish_line_fn（display / 后台任务 read_buffer）
            三方拿到同一份「终端视角」文本：进度条/行内刷新（如
            ``10%\\r20%\\r30%``）折叠为最终状态 ``30%``，不再把字面 \\r
            传给大模型，返回的行数不会多于真实终端显示。
            """
            # ★ 规范化行尾：PTY ONLCR 将 \n → \r\n，归一化为 \n，
            #   保留行内独立 \r（用于进度条）不动
            clean = text.replace('\r\n', '\n')
            # ★ 先剥 ANSI 再兑现 \r 覆盖（顺序契约：_simulate_terminal 对
            #   含 ANSI 文本结果不确定，须先 _strip_ansi；与 display() 的
            #   _on_line 同一处理，此处提前到数据源头使
            #   返回给大模型的输出同样生效）
            clean = _simulate_terminal(_strip_ansi(clean))
            lines.append(clean)
            if show_output:
                safe = clean  # 已剥 ANSI、已兑现 \r 覆盖
                if not safe.endswith('\n'):
                    safe += '\n'
                if is_stderr:
                    # ★ BUG-79：行尾 \n 须保持在 RESET 之后（_wrap_colored_line
                    #   保证）——修复前 ``f"{RED}{safe}{RESET}"`` 把 \n 夹在
                    #   color 与 RESET 中间，下游 rstrip(\n)/尾空 segment 剔除
                    #   失效 → 工具卡每个 stderr 行多渲染一个空白行。
                    await print_to_terminal(_wrap_colored_line(safe, RED))
                else:
                    await print_to_terminal(safe)
            if publish_line_fn:
                try:
                    await publish_line_fn(clean, is_stderr)
                except Exception:
                    logger.debug("忽略异常（best-effort 路径）", exc_info=True)

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
            # 解码器按行返回（普通流按 b'\n' 切行后逐行自适应解码；
            # UTF-16 等宽字符流整块增量解码后在文本层切行）
            for text in decoder.feed(chunk):
                await _handle_line(text)
        # EOF：处理残留的不完整行（超长行无尾换行 / 最后一块无 \n）
        for text in decoder.flush():
            await _handle_line(text)
        return interrupted

    # ── PIPE / PTY 执行 ────────────────────────────────

    async def _run_pipe(self, show_command=False, show_output=False,
                        publish_line_fn=None, interactive=False,
                        interactive_ready_fn=None):
        """使用 PIPE 模式执行命令（fallback，PTY 不可用时使用）。

        子进程 stdout/stderr 连接到 PIPE，存在全缓冲问题，
        输出不会逐行实时刷新。仅在 PTY 不可用时使用。

        Returns:
            结构化结果 dict：{"stdout": str, "stderr": str, "returncode": int}
            （stdout / stderr 分离收集；returncode 为子进程退出码；
            被中断时 stderr 放中断提示、returncode 为 -1）

        Args:
            show_command: 是否打印命令行到终端
            show_output: 是否实时打印输出到终端
            publish_line_fn: 可选的行回调
            interactive: 是否启用交互式输入。True 时子进程 stdin 使用
                         PIPE（可通过 process.stdin 写入），False 时 DEVNULL。
            interactive_ready_fn: 可选的回调，子进程创建后调用
                          interactive_ready_fn(process, "pipe", stdin_writer=process.stdin)
                          供后台任务记录 stdin 写端（bash_opt 工具写入输入用）。
        """
        if show_command:
            await print_to_terminal(f"\n{GREEN}$ {self.command}{RESET}\n")

        # ★ Windows 兼容（review 方向）：非 PTY 回退路径恰恰是 PTY 不可用
        #   时（如 Windows）使用，修复前无条件传 preexec_fn=os.setpgid——
        #   os.name == 'nt' 时 subprocess 抛 ValueError("preexec_fn is not
        #   supported on Windows platforms") 且 os.setpgid 不存在，bash 工具
        #   在 Windows 上完全不可用。Windows 用 creationflags 建新进程组。
        popen_kwargs: dict = {}
        if os.name != "nt":
            popen_kwargs["preexec_fn"] = lambda: os.setpgid(0, 0)
        else:
            popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

        from ..core.adapters.kernel_runtime import spawn_process

        # ★ 命令解释器按「启动本程序的交互 shell」选择（见 _resolve_shell_launch）：
        #   PowerShell / bash / zsh / fish 等由各自解释器执行，cmd 与检测失败
        #   走平台默认 shell=True——系统提示词声明的「命令行」与实际执行环境
        #   一致，`ls` / `Get-ChildItem` / `[[ ... ]]` 这类按当前 shell 语法写
        #   的命令不会被另一种 shell 判成「不是内部或外部命令」或语法错误。
        launch_arg, use_shell, shell_name = _resolve_shell_launch(self.command)
        if shell_name:
            logger.debug("按交互 shell 执行命令: %s", shell_name)

        process = await spawn_process(
            launch_arg,
            shell=use_shell,
            stdin=asyncio.subprocess.PIPE if interactive else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            close_fds=True,
            env=self._get_subprocess_env(),
            **popen_kwargs,
        )

        # ★ interactive_ready_fn 回调：提供 stdin 写端供 bash_opt 工具写入。
        if interactive_ready_fn is not None:
            try:
                interactive_ready_fn(process, "pipe", stdin_writer=process.stdin)
            except Exception:
                logger.debug("interactive_ready_fn 回调异常")

        stdout_lines = []
        stderr_lines = []

        # 使用闭包确保 kill 只执行一次（双流并发时避免冗余进程表扫描）
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
            return {"stdout": "", "stderr": "(命令已被中断)",
                    "returncode": self._bash_rc(process.returncode)}
        return {
            "stdout": ''.join(stdout_lines).strip(),
            "stderr": ''.join(stderr_lines).strip(),
            "returncode": self._bash_rc(process.returncode),
        }

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
                          供后台任务记录进程句柄（bash_opt 工具写入输入用）。

        Returns:
            结构化结果 dict：{"stdout": str, "stderr": str, "returncode": int}
            （PTY 模式 stdout/stderr 物理合并到同一 master fd，无法分离，
            全部输出归入 stdout、stderr 恒为空字符串；returncode 为子进程
            退出码；被中断时 stderr 放中断提示、returncode 为 -1）
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

        # ★ 与 PIPE 分支同一套「交互 shell」解析结果（bash / zsh / fish /
        #   powershell ...）；回退平台默认 POSIX shell 的逻辑在 _pty_launch_argv。
        launch_argv = _pty_launch_argv(self.command)

        def _setup_controlling_terminal() -> None:
            """子进程 exec 前建立「会话 + 控制终端」（preexec_fn 内执行）。

            ★ 只调用 ``os.setpgid(0, 0)``（旧实现）只创建了新的进程组，
            PTY slave 仍是普通 fd——它没有成为子进程的控制终端，因此
            line discipline 的 ``ISIG`` 没有可投递信号的前台进程组：向
            master 写入 ``\\x03``（ctrl+c）时，内核只会把该字节当作普通
            输入传给程序，不会产生 SIGINT。``bash_opt op=keys key='ctrl+c'``
            因此在 PTY 模式下「发送成功但无法中断进程」。

            这里补上标准三件套：
              1. ``setsid()``：子进程成为新会话的会话首进程（同时是新的
                 进程组组长 / 前台进程组）；
              2. ``TIOCSCTTY``：把继承来的 PTY slave 设为该会话的控制终端；
              3. ``setpgid`` 兜底（setsid 已设置，失败时保持旧行为）。

            preexec_fn 在 fork 之后、exec 之前运行，不能依赖日志 / 锁
            （多线程下可能死锁），失败一律静默：最坏退回旧行为，不应阻止
            命令执行。
            """
            try:
                os.setsid()
            except OSError:
                pass
            try:
                fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)
            except OSError:
                pass
            try:
                os.setpgid(0, 0)
            except OSError:
                pass

        try:
            from ..core.adapters.kernel_runtime import spawn_process

            process = await spawn_process(
                launch_argv,
                shell=False,
                stdin=slave_fd if interactive else asyncio.subprocess.DEVNULL,
                stdout=slave_fd,
                stderr=slave_fd,
                env=env,
                pass_fds=(slave_fd,),
                preexec_fn=_setup_controlling_terminal,
            )
        except Exception:
            os.close(master_fd)
            os.close(slave_fd)
            raise

        # 关闭父进程中的 slave 端，确保子进程退出后
        # master 端能收到 EOF
        os.close(slave_fd)

        # ★ interactive_ready_fn 回调：通知外层子进程已创建，提供交互句柄。
        #   在 master 包装为 asyncio 读管道前调用，保证 bash_opt 工具
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
            return {"stdout": "", "stderr": "(命令已被中断)",
                    "returncode": self._bash_rc(process.returncode)}
        return {
            "stdout": ''.join(lines).strip(),
            # PTY 模式 stdout/stderr 合并到同一 master fd，无法分离
            "stderr": "",
            "returncode": self._bash_rc(process.returncode),
        }

    async def _run_async(self, show_command=False, show_output=False,
                         publish_line_fn=None):
        """前台执行命令：最多等待 _AUTO_BG_TIMEOUT 秒，超过自动转后台。

        与旧版（asyncio.wait_for 超时强杀进程）的区别：
        - 使用 asyncio.wait 观察执行任务，超时**不取消**任务；
        - 超过 _AUTO_BG_TIMEOUT 秒未完成 → 自动转后台任务：
          命令继续运行，注册到 agent._background_tasks，返回
          {"task_id": ..., "status": "running"} JSON，
          大模型可用 bash_opt 工具按 task_id 继续管理
          （read/wait/kill/stdin/keys/screenshot 与窗口输入
          move/click/drag/scroll/key/type）。

        ★ 前台统一使用 PIPE 分离模式（stdout/stderr 双流独立收集）：
        返回给大模型的三元 JSON 中 stdout、stderr 真实分离，returncode
        为子进程退出码。PIPE 双流并发读取 + 行回调仍实时；代价是部分
        程序在非终端下使用全缓冲（实时性略降），交互能力经 stdin PIPE
        （stdin_writer）保留——超时转后台后 bash_opt stdin/keys 仍可用。
        PTY 模式保留给后台交互任务（_run_interactive_async）。

        Args:
            show_command: 是否打印命令行到终端
            show_output: 是否实时打印输出到终端（_read_loop 内部）
            publish_line_fn: 可选的行回调（display 用）

        Returns:
            三元 JSON 字符串 {"stdout", "stderr", "returncode"}（已截断）；
            超时转后台时返回 task_id JSON 字符串
        """
        # ★ 子进程句柄记录：自动转后台时写入任务记录，供 bash_opt 工具操作
        #   （wait 等待 / kill 杀进程树 / stdin、keys 交互）。
        holder: dict = {}

        def _on_ready(process, mode, master_fd=None, stdin_writer=None):
            holder["process"] = process
            holder["mode"] = mode
            holder["master_fd"] = master_fd
            holder["stdin_writer"] = stdin_writer

        # ★ 行回调代理（可变引用）：自动转后台后把 fn 置 None，断开实时输出，
        #   避免转后台后命令输出继续污染已闭合的工具卡片（工具已返回
        #   task_id JSON，后续输出由 bash_opt wait 获取完整结果）。
        #   fn 置 None 后，后续输出行写入任务记录 read_buffer（bash_opt
        #   read 操作实时消费，见 _promote_to_background 填充 line_cb）。
        line_cb: dict = {"fn": publish_line_fn}

        async def _line_proxy(text: str, is_stderr: bool) -> None:
            # ★ 转后台后（rec 已设置）：输出写入任务记录 read_buffer
            #   （bash_opt read 实时消费），不再发布到 UI/回调。
            rec = line_cb.get("rec")
            if rec is not None:
                await _append_read_buffer(rec, text)
                return
            fn = line_cb["fn"]
            if fn is not None:
                await fn(text, is_stderr)

        async def _run_exec():
            # ★ 前台统一 PIPE 分离模式（见 _run_async docstring）：
            #   stdout/stderr 独立收集 → 三元 JSON 真实分离。
            result = await self._run_pipe(
                show_command=False,
                show_output=show_output,
                publish_line_fn=_line_proxy,
                interactive=True,
                interactive_ready_fn=_on_ready,
            )
            return self._format_result(result)

        exec_task = asyncio.ensure_future(_run_exec())
        try:
            done, _pending = await asyncio.wait(
                {exec_task}, timeout=self._AUTO_BG_TIMEOUT,
            )
        except asyncio.CancelledError:
            # ★ 修复（review 方向）：等待窗口内被取消（中断/任务取消）时，
            #   必须取消 exec_task 并终止子进程——修复前 exec_task 被遗弃，
            #   命令子进程脱离控制继续运行（孤儿进程无法再被 kill）。
            exec_task.cancel()
            process = holder.get("process")
            if process is not None:
                try:
                    _kill_process_tree(process.pid)
                except Exception:
                    logger.debug("取消时清理子进程异常（非关键）", exc_info=True)
            raise
        if exec_task in done:
            try:
                result = exec_task.result()
            except asyncio.CancelledError:
                return self._error_result("(命令已被取消)")
            except Exception as e:
                logger.exception("异步命令执行异常: %s", self.command[:200])
                return self._error_result(f"(执行出错: {e})")
            return result

        # 超过 _AUTO_BG_TIMEOUT 秒 → 自动转后台执行（不终止进程）。
        # ★ 断开实时行回调：工具即将返回 task_id JSON（工具卡片闭合），
        #   转后台后的输出不再发布到终端/前端，改由 bash_opt read 实时读取。
        line_cb["fn"] = None
        return await self._promote_to_background(exec_task, holder, line_cb)

    async def _promote_to_background(self, exec_task, holder: dict,
                                     line_cb: dict | None = None) -> str:
        """把已运行超过 _AUTO_BG_TIMEOUT 秒的前台命令转为后台任务。

        前台命令超过 1 分钟仍未完成时调用（由 _run_async 触发）：
          - **不终止进程**（asyncio.wait 观察而非 wait_for 干预）；
          - 生成 task_id，把执行中的任务注册到 agent._background_tasks；
          - 任务完成后结果写入任务记录（bash_opt wait / 对话轮次自动
            插入用户消息消费）；
          - 返回 {"task_id": ..., "status": "running"} JSON，
            大模型可用 bash_opt 工具继续管理。

        Args:
            exec_task: 正在运行的命令执行任务（asyncio.Task）
            holder: 子进程句柄记录（process/mode/master_fd/stdin_writer）
            line_cb: 行回调代理 dict（含 fn）。传入时把 task_id 与任务记录
                     引用写入，供 _line_proxy 在转后台后把输出追加到
                     read_buffer（bash_opt read 实时读取）。

        Returns:
            task_id JSON 字符串
        """
        agent = getattr(self, 'agent', None)
        if agent is None or not hasattr(agent, '_register_background_task'):
            return self._error_result(
                f"(命令执行超过 {self._AUTO_BG_TIMEOUT} 秒，但当前未关联 "
                f"Agent 上下文，无法自动转后台管理)")

        task_id = f"bg-{uuid.uuid4().hex[:12]}"
        process = holder.get("process")

        rec = {
            "task": exec_task,
            "created_at": time.time(),
            "done": False,
            "result": "",
            "status": "running",
            # ── 交互控制字段（bash_opt 工具按 task_id 操作） ──
            "process": process,
            "pid": process.pid if process is not None else None,
            "mode": holder.get("mode"),
            "master_fd": holder.get("master_fd"),
            "stdin_writer": holder.get("stdin_writer"),
            "io_lock": asyncio.Lock(),
            # ── 实时输出缓冲（bash_opt read 读取后清空） ──
            "read_buffer": "",
            "read_buffer_dropped": 0,  # 因缓冲上限被丢弃的字符数（read 回报）
        }
        agent._register_background_task(task_id, rec)

        # ★ 转后台后输出改走 read_buffer：把任务记录引用写入 line_cb，
        #   后续 _read_loop 的每行输出经 _line_proxy 追加到 read_buffer。
        if line_cb is not None:
            line_cb["task_id"] = task_id
            line_cb["rec"] = rec

        def _on_done(t: asyncio.Task) -> None:
            """任务完成回调：把结果写入后台任务记录（与 _run_background_task 一致）。"""
            try:
                result = t.result()
            except asyncio.CancelledError:
                result = self._error_result("(后台命令已被取消)")
            except Exception as e:
                logger.exception("自动转后台命令执行异常: %s", self.command[:200])
                result = self._error_result(f"(后台命令执行出错: {e})")
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
        }, ensure_ascii=False)

    async def _run_interactive_async(self, on_ready=None, publish_line_fn=None):
        """交互模式异步执行：启用 stdin（PTY slave / PIPE），供后台任务操作。

        与 _run_async 的区别：
          - **后台任务执行体（background=True）专用**：无限运行，不设超时、
            不自动转后台（本身已是后台任务）；
          - interactive=True：子进程 stdin 连接 PTY slave（或 PIPE），
            外部可通过任务记录中的 master_fd / stdin_writer 写入输入；
          - on_ready 回调在子进程创建后立即调用，把 process/pid/master_fd
            写入任务记录，供 bash_opt 工具按 task_id 操作；
          - publish_line_fn 可选：每行输出实时回调，后台任务用它把输出
            追加到任务记录 read_buffer（bash_opt read 操作实时消费）。

        Args:
            on_ready: 可选回调 on_ready(process, mode, master_fd=None, stdin_writer=None)
            publish_line_fn: 可选 async (text, is_stderr) -> None 行回调

        Returns:
            三元 JSON 字符串 {"stdout", "stderr", "returncode"}（已截断）。
            PTY 模式下 stdout/stderr 物理合并，输出归 stdout、stderr 为空。
        """
        try:
            if _HAS_PTY:
                result = await self._run_pty(
                    show_command=False, show_output=False,
                    publish_line_fn=publish_line_fn,
                    interactive=True, interactive_ready_fn=on_ready,
                )
            else:
                result = await self._run_pipe(
                    show_command=False, show_output=False,
                    publish_line_fn=publish_line_fn,
                    interactive=True, interactive_ready_fn=on_ready,
                )
            return self._format_result(result)
        except asyncio.CancelledError:
            return self._error_result("(后台命令已被取消)")
        except Exception as e:
            logger.exception("交互式命令执行异常: %s", self.command[:200])
            return self._error_result(f"(执行出错: {e})")

    # ── 后台执行模式 ────────────────────────────────────────
    # background=True 时：命令在 asyncio 后台任务中运行，工具立即返回
    # {"task_id": ..., "status": "running"} JSON；任务记录注册到当前
    # Agent 的 _background_tasks 成员（bash 专用表，task_id 恒为 "bg-xxx"；
    # subagent 后台任务独立注册在 _subagent_tasks）。一轮对话完成后，
    # Agent 主循环检查 bash 后台任务：已完成的把结果（JSON：task_id +
    # 命令输出按 stdout/stderr/returncode 三元展开）作为用户消息插入对话
    # 继续处理；未完成的则等待全部完成后再次插入。

    async def _execute_background(self) -> str:
        """后台执行命令：生成 task_id、注册到 agent 后台任务列表，立即返回 JSON。

        需要当前 BashFunc 实例关联了 Agent（registry.dispatch 会自动 set_agent）。
        返回 JSON 字符串（task_id/status），供大模型识别后台任务。
        """
        # ★ 危险命令检查：display() 路径（主 Agent）进入后台前也需运行时防护
        danger = _has_dangerous_command(self.command)
        if danger:
            return self._error_result(f"(拒绝执行危险命令: {danger})")

        agent = getattr(self, 'agent', None)
        if agent is None or not hasattr(agent, '_register_background_task'):
            return self._error_result("(后台执行需要关联 Agent 上下文，当前未关联)")

        # 显示启动命令（后台任务本身不再重复打印）

        task_id = f"bg-{uuid.uuid4().hex[:12]}"

        # 后台任务无限运行（不设超时，避免误杀编译/下载等长时命令）
        bg = BashFunc(command=self.command)

        task = asyncio.ensure_future(self._run_background_task(bg, task_id))

        # ── 任务记录注册到 agent 的 bash 专用表 _background_tasks ──
        # ★ 与 subagent 后台任务（_subagent_tasks）分表独立：bash_opt 仅操作
        #   本表，subagent_opt 无法触达；交互控制字段（bash_opt 工具按
        #   task_id 操作后台任务）：process/pid/mode/master_fd/stdin_writer
        #   由 _run_background_task 的子进程创建回调（_on_ready）填充；
        #   io_lock 串行化 stdin/keys 写入。
        agent._register_background_task(task_id, {
            "task": task,
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
            # ── 实时输出缓冲（bash_opt read 读取后清空） ──
            "read_buffer": "",      # 运行期间已产生、尚未被 read 消费的输出
            "read_buffer_dropped": 0,  # 因缓冲上限被丢弃的字符数（read 回报）
        })

        await print_to_terminal(
            f"{DIM}[后台任务 {task_id} 已启动: {self.command[:80]}{'...' if len(self.command) > 80 else ''}]{RESET}\n"
        )

        return json.dumps({
            "task_id": task_id,
            "status": "running",
        }, ensure_ascii=False)

    async def _run_background_task(self, bg: "BashFunc", task_id: str) -> None:
        """后台任务执行体：运行命令并把结果写入 agent 的后台任务记录。

        ★ 后台任务无限运行（_run_interactive_async 不设超时、不自动转后台），
        配合 bash_opt 工具按 task_id 操作
        （read/wait/kill/stdin/keys/screenshot 与窗口输入
        move/click/drag/scroll/key/type）。
        ★ 交互模式：后台任务启用 stdin（PTY slave / PIPE），子进程创建后
        通过 on_ready 回调把 process/pid/master_fd 写入任务记录，供
        bash_opt 工具按 task_id 发送输入 / 杀死进程树 / 等待完成。

        Args:
            bg: 实际执行命令的 BashFunc 实例
            task_id: 后台任务 ID
        """
        agent = getattr(self, 'agent', None)

        def _on_ready(process, mode, master_fd=None, stdin_writer=None):
            """子进程创建回调：把进程句柄写入任务记录（bash_opt 工具使用）。"""
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

        async def _publish_line(text: str, is_stderr: bool) -> None:
            """后台任务实时输出行 → 任务记录 read_buffer（bash_opt read 消费）。"""
            if agent is None or not hasattr(agent, '_background_tasks'):
                return
            rec = agent._background_tasks.get(task_id)
            if rec is None:
                return
            await _append_read_buffer(rec, text)

        try:
            result = await bg._run_interactive_async(_on_ready, publish_line_fn=_publish_line)
        except asyncio.CancelledError:
            result = self._error_result("(后台命令已被取消)")
        except Exception as e:
            logger.exception("后台命令执行异常: %s", self.command[:200])
            result = self._error_result(f"(后台命令执行出错: {e})")

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

    async def execute(self) -> str:
        """异步执行命令并返回三元 JSON 结果。

        返回给大模型的结构：{"stdout": ..., "stderr": ..., "returncode": N}——
        stdout/stderr 分离（前台 PIPE 模式），returncode 是**命令的退出码**
        （bash $? 语义：被信号杀死为 128+sig）；工具级错误（危险命令拒绝 /
        执行异常 / 中断取消）时 stdout 为空、stderr 放错误说明、
        returncode 为 -1。

        有 UI 副作用（危险命令时终端输出红色警告）。
        使用 asyncio.create_subprocess_shell 避免阻塞事件循环。
        """
        # ★ P0 安全防护：运行时检查危险命令（schema 侧 + 运行时侧双保险）
        danger = _has_dangerous_command(self.command)
        if danger:
            await print_to_terminal(f"{RED}$ {self.command}{RESET}\n{RED}(拒绝执行危险命令: {danger}){RESET}\n")
            return self._error_result(f"(拒绝执行危险命令: {danger})")
        # ── 后台模式：不等待命令完成，立即返回 task_id JSON ──
        if self.background:
            return await self._execute_background()
        return await self._run_async(show_command=False, show_output=False)

    async def _run_with_line_callback(self, on_line) -> str:
        """共享的 UI 执行框架：执行命令、异常处理（display 路径）。

        前台执行与 _run_async 一致：超过 _AUTO_BG_TIMEOUT 秒自动转后台
        （命令不终止，返回 task_id JSON）；publish_line_fn 持续接收实时输出行。

        Args:
            on_line: 异步回调 async (text: str, is_stderr: bool) -> None

        Returns:
            三元 JSON 字符串 {"stdout", "stderr", "returncode"}（已截断）；
            超时转后台时返回 task_id JSON 字符串
        """
        # ── 后台模式：display 路径同样不等待命令完成 ──
        if self.background:
            return await self._execute_background()

        return await self._run_async(
            show_command=False, show_output=False,
            publish_line_fn=on_line,
        )

    async def display(self):
        """异步执行命令并实时输出到终端"""
        async def _on_line(text: str, is_stderr: bool) -> None:
            # ★ 终端模拟：先剥 ANSI，再兑现 \r 覆盖语义（进度条等行内刷新
            #   输出在工具卡片呈现与真实终端一致的最终状态，而非字面 \r）。
            #   text 已由 _read_loop._handle_line 统一处理（ANSI 已剥、\r 已
            #   覆盖），此处为幂等防御（无 \r/ANSI 时走零开销快路径）。
            safe = _simulate_terminal(_strip_ansi(text))
            if not safe.endswith('\n'):
                safe += '\n'
            if is_stderr:
                # ★ BUG-79：行尾 \n 保持在 RESET 之后（_wrap_colored_line）——
                #   修复前 ``f"{RED}{safe}{RESET}"`` 使下游 rstrip(\n)/尾空
                #   segment 剔除失效，工具卡每个 stderr 行多一个空白行。
                await print_to_terminal(_wrap_colored_line(safe, RED))
            else:
                await print_to_terminal(safe)

        return await self._run_with_line_callback(_on_line)


__all__ = [
    "BashFunc",
    "READ_BUFFER_DROPPED_KEY",
    # 兼容面：由 _bash_support 下沉实现后保留的公开 API（bash_opt / base_agent 导入）
    "kill_process_tree",
]
