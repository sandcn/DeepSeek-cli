from __future__ import annotations

import asyncio
import base64
import errno as _errno
import inspect
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

from .base import Func, print_to_terminal
from . import _conpty
from ._bash_support import (
    _has_dangerous_command,
    is_shell_like_exe,
    _INTERRUPT_CHECK_INTERVAL,
    _READ_CHUNK_SIZE,
    _strip_ansi,
    _PtyEioAsEofProtocol,
    _simulate_terminal,
    _wrap_colored_line,
    _kill_process_tree,
    kill_process_tree,  # noqa: F401  # 公开 API：bash_opt 与测试经 bash 模块导入
    process_create_time,
    create_process_job,
    close_process_job,
    terminate_process_job,
    ProcessTreeTracker,
    PipeFdWriter,
    open_interactive_stdin,
)
from ._process_output import OutputLineDecoder

from ..core.constants import GREEN, RED, DIM, RESET
from ..api.interrupt_async import is_interrupted
from ..prompt_builder.shell_info import SHELL_ENV_MARKER, detect_shell

logger = logging.getLogger(__name__)

#: 能调用 Win32 进程 API 的平台（原生 Windows / Cygwin / MSYS2）。
#: Cygwin 的 ``os.name`` 是 ``posix``，但内核是 Windows：Job / Toolhelp32 /
#: TerminateProcess 都可用（经 ``_win_proc`` 的 ctypes.CDLL 绑定）。
_WINDOWS_LIKE = os.name == "nt" or (sys.platform or "").startswith(("cygwin", "msys"))
#: Cygwin / MSYS2：POSIX pid 与 Windows pid 是两套编号，原生程序 stdin
#: 不能用 asyncio 的 socketpair（须用 os.pipe 显式建管道）。
_CYGWIN_LIKE = (sys.platform or "").startswith(("cygwin", "msys"))

#: 命令行中显式出现的 ``.exe`` token（用于 Cygwin 下 Store 应用 / App
#: Execution Alias 的窗口归属兜底：真实进程可能不是任务根进程的后代，
#: 但 exe 名通常仍能对上）。取**最后一个**，覆盖
#: ``cmd /c notepad.exe``、``powershell -Command 'Start-Process notepad.exe'``。
_EXE_TOKEN_RE = re.compile(r"""[^\s"']+\.exe""", re.IGNORECASE)

#: 命令行里的「简单命令」分隔符（``&&`` / ``||`` / ``;`` / ``|`` / ``&`` / 换行）。
#: GUI 程序常出现在**后面的段**里（``cd /tmp && mintty ...``）：Cygwin 下
#: mintty 会 daemon 化（自劫持后父进程退出、POSIX PPID 变 1、Windows 父
#: PID 指向已退出的 fork 中间体），既不在进程树也不在 Job 里——按名兜底
#: 是唯一还能定位它的通道，只看首个 token 会完全漏掉它（实测 ``op=kill``
#: 报「无残留进程」而 mintty 窗口仍在）。
_SEGMENT_SEPARATORS = ";&|\n"

#: 透传型启动器：自身不是被测程序，真正的程序在其后的非选项 token
#: （``cygstart mintty ...`` / ``env FOO=1 mintty ...`` / ``nohup mintty``）。
_LAUNCHER_STEMS = frozenset({
    "cygstart", "start", "env", "nohup", "exec", "nice", "time",
    "command", "builtin", "xargs", "sudo",
})

#: shell 内建命令：不是可执行文件，且其参数只是数据（``cd /tmp`` 里的
#: ``/tmp`` 不是程序），不产出 hint。
_SHELL_BUILTINS = frozenset({
    ".", ":", "[", "alias", "bg", "break", "cd", "command", "continue",
    "echo", "eval", "exec", "exit", "export", "false", "fc", "fg", "getopts",
    "hash", "jobs", "kill", "local", "printf", "pwd", "read", "return", "set",
    "shift", "source", "test", "times", "trap", "true", "type", "ulimit",
    "umask", "unalias", "unset", "wait",
})

#: shell 复合语句关键字（出现在段首，不是程序位）
_SHELL_KEYWORDS = frozenset({
    "if", "then", "else", "elif", "fi", "do", "done", "for", "while",
    "until", "case", "esac", "function", "!", "{", "}",
})

#: 环境变量赋值 / 纯数字参数（都不是程序位）
_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_NUMERIC_RE = re.compile(r"^\d+$")

#: 解释器参数里的「词」（``python -c "Popen(['mintty'])"``）
_WORD_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_./\\-]*")

#: 解释器参数逐词做 PATH 查询的上限（防超长脚本 / heredoc 拖慢任务启动）
_HINT_MAX_WORDS = 200


def _hint_token(raw: str) -> str:
    """去掉包裹符号（引号 / 括号 / 逗号）后的 token 文本。"""
    return str(raw or "").strip().strip("\"'`()[]{},")


def _split_segments(text: str) -> list[str]:
    """按 shell 分隔符切分命令行（``&&`` / ``||`` / ``;`` / ``|`` / ``&`` / 换行）。

    **不切引号内部**：``python -c "import x; y(['mintty'])"`` 里的 ``;`` 属于
    代码片段，按字面切开会把双引号劈成两半，连带丢掉真正的程序名
    （实测会把 ``mintty`` 变成 ``Popen(['mintty`` 这种残缺 token）。
    """
    segments: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if quote:
            buf.append(char)
            if char == "\\" and quote == '"' and index + 1 < length:
                buf.append(text[index + 1])
                index += 2
                continue
            if char == quote:
                quote = None
            index += 1
            continue
        if char in ("'", '"'):
            quote = char
            buf.append(char)
            index += 1
            continue
        if char in _SEGMENT_SEPARATORS:
            segments.append("".join(buf))
            buf = []
            index += 1
            # 吃掉成对的 && / ||
            if index < length and text[index] == char and char in "&|":
                index += 1
            continue
        buf.append(char)
        index += 1
    segments.append("".join(buf))
    return segments


def _hint_name(name: str) -> str:
    """小写 basename（保留 ``.exe`` 后缀，与既有 hint 形态一致）。"""
    return _hint_token(name).replace("\\", "/").rsplit("/", 1)[-1].lower()


def _hint_stem(name: str) -> str:
    """小写 basename（去掉 ``.exe`` 后缀）——只用于比较，不改变 hint 形态。"""
    text = _hint_name(name)
    return text[:-4] if text.endswith(".exe") else text


def _program_token(tokens: list[str]) -> tuple[str | None, int]:
    """返回一段简单命令里的「程序位」token 及其下标（找不到返回 ``(None, -1)``）。

    跳过段首的环境变量赋值与复合语句关键字；遇到透传型启动器
    （``cygstart`` / ``env`` / ``nohup`` 等）继续往后找它启动的程序。
    """
    index = 0
    while index < len(tokens):
        token = _hint_token(tokens[index])
        if not token:
            index += 1
            continue
        if _ASSIGNMENT_RE.match(token) or token in _SHELL_KEYWORDS:
            index += 1
            continue
        break
    while index < len(tokens) and _hint_stem(tokens[index]) in _LAUNCHER_STEMS:
        index += 1
        while index < len(tokens):
            nxt = _hint_token(tokens[index])
            if (not nxt or nxt.startswith("-")
                    or _ASSIGNMENT_RE.match(nxt) or _NUMERIC_RE.match(nxt)):
                index += 1
                continue
            break
    if index >= len(tokens):
        return None, -1
    return tokens[index], index


def _resolvable_program(name: str) -> bool:
    """``name`` 是否是 PATH 里真实存在的可执行文件（嵌在脚本里的词用它过滤）。"""
    try:
        return bool(shutil.which(name))
    except Exception:  # pragma: no cover - 依赖 PATH 环境
        return False


def _script_hints(text: str) -> list[str]:
    """在解释器参数（``-c`` 的脚本正文）里找真正的程序名。

    只认能在 PATH 里解析到、且不是 shell 内建 / shell 解释器的词：
    ``bash -c 'mintty -t x'`` → ``mintty``；``python -c "Popen(['mintty'])"``
    → ``mintty``；而 ``python script.py`` 里的 ``script.py``（数据文件，
    PATH 里找不到）不算。
    """
    candidates: list[str] = []
    for segment in _split_segments(text):
        try:
            tokens = shlex.split(segment, comments=False, posix=True)
        except ValueError:
            tokens = segment.split()
        program, _index = _program_token(tokens)
        if program is not None and _hint_stem(program) in _SHELL_BUILTINS:
            # shell 内建命令（``echo`` / ``cd`` …）：参数只是数据
            program = None
        if program:
            if not is_shell_like_exe(program):
                candidates.append(program)
                if _resolvable_program(program):
                    # 明确的程序位：后面都是它的参数，不做逐词猜测
                    # （否则 ``mintty -t x -e sleep 5`` 会把 x / sleep 也
                    # 当成程序名）
                    continue
        # ``python -c`` / ``perl -e`` 的代码片段没有命令结构：退回按词扫描
        for word in _WORD_RE.findall(segment):
            if is_shell_like_exe(word) or _hint_stem(word) in _SHELL_BUILTINS:
                continue
            candidates.append(word)
    result: list[str] = []
    checked = 0
    for name in candidates:
        if name in result:
            continue
        if checked >= _HINT_MAX_WORDS:
            break
        checked += 1
        if _resolvable_program(name):
            result.append(name)
    return result


def _segment_hints(tokens: list[str]) -> list[str]:
    """一段简单命令里的程序名候选（shell / 解释器包装会继续解析其脚本参数）。"""
    program, index = _program_token(tokens)
    if program is None:
        return []
    if _hint_stem(program) in _SHELL_BUILTINS:
        return []
    if not is_shell_like_exe(program):
        return [program]
    # shell / 解释器包装：真正的程序在其后的脚本参数里
    # （``bash -c 'mintty ...'``、``python -c "Popen(['mintty'])"``）。
    hints: list[str] = []
    for raw in tokens[index + 1:]:
        token = _hint_token(raw)
        if not token or token.startswith("-"):
            continue
        hints.extend(_script_hints(token))
    return hints


def _command_exe_hints(command: str) -> list[str]:
    """从命令行提取全部可执行名提示（小写 basename，去重保序）。

    与 :func:`_command_exe_hint` 的区别是返回**所有**候选，供 ``op=kill``
    的按名兜底使用：Cygwin 下 mintty 等 GUI 程序启动后会 daemon 化，进程树
    与 Job 都够不着它，只有「命令里出现过的程序名 + 任务启动时间窗」这条
    通道还能定位到它。

    ★ shell / 解释器名不作为提示：Cygwin / MSYS2 下每个后台任务都有
    ``bash.exe``，用 ``bash`` 这类通用名做「同名进程」兜底会把其它任务的
    shell（及其 Windows 后代窗口）算进当前任务，实测导致 op=windows /
    screenshot / 输入注入串到别的任务窗口。这类名字被丢弃，窗口归属只走
    根 WINPID / Job / 进程树。
    """
    text = str(command or "")
    hints: list[str] = []
    # 1) 显式 ``.exe`` token 优先：覆盖 ``cmd /c notepad.exe``、
    #    ``powershell -Command 'Start-Process notepad.exe'``。
    for match in _EXE_TOKEN_RE.findall(text):
        base = _hint_token(match)
        if base and not is_shell_like_exe(base):
            hints.append(base)
    # 2) 按「简单命令」分段取程序位：``cd /tmp && mintty ...`` 里的 mintty
    for segment in _split_segments(text):
        try:
            tokens = shlex.split(segment, comments=False, posix=True)
        except ValueError:
            tokens = segment.split()
        hints.extend(_segment_hints(tokens))
    # 统一成小写 basename 并去重（保持首次出现的顺序）
    ordered: list[str] = []
    for hint in hints:
        name = _hint_name(hint)
        if not name or is_shell_like_exe(name) or name in ordered:
            continue
        ordered.append(name)
    return ordered


def _command_exe_hint(command: str) -> str | None:
    """从命令行提取**首选**可执行名提示（小写 basename；提取不到返回 None）。

    取 :func:`_command_exe_hints` 的首个候选：窗口归属（``op=windows`` /
    screenshot / 输入注入）与 ``op=kill`` 的按名兜底都用它。Cygwin 下 GUI
    程序常出现在命令后半段（``cd /tmp && mintty ...``），这里同样能取到。
    """
    hints = _command_exe_hints(command)
    return hints[0] if hints else None

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

#: Windows 伪终端（ConPTY）可用性：Windows 10 1809+ 提供 ``CreatePseudoConsole``。
#: ``pty=true`` 时 Windows 走 ConPTY（见 ``_conpty`` 模块），POSIX 走 ``pty.openpty``。
_HAS_CONPTY = _conpty.is_available()


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


def _is_windows_powershell_51(executable: str) -> bool:
    """该 PowerShell 可执行文件是否为 Windows PowerShell 5.1（``powershell.exe``）。

    ``pwsh``（PowerShell 6+）原生支持 ``&&`` / ``||`` 链式运算符；Windows
    自带的 ``powershell.exe``（5.1）不支持，需要改写（见
    :func:`_rewrite_chain_operators`）。
    """
    name = _shell_basename(executable or "")
    return name == "powershell"


def _line_start(text: str) -> bool:
    """``text`` 末尾是否位于行首（仅空白）——PowerShell here-string 的判定条件。"""
    return not text.rsplit("\n", 1)[-1].strip()


def _split_chain_operators(command: str):
    """按**顶层** ``&&`` / ``||`` 切分命令，返回 ``(首段, [(运算符, 段), ...])``。

    只认引号外（以及 here-string / 注释之外）的运算符：``"a && b"`` 里的
    ``&&`` 属于字符串正文，``2>&1`` 里的 ``&`` 是重定向。没有顶层运算符时
    返回 ``None``（命令保持原样）。

    扫描规则（与 PowerShell 词法一致的最小子集）：

      - 单引号字符串 ``'...'``（内部 ``''`` 为转义的单引号）；
      - 双引号字符串 ``"..."``（内部反引号转义下一个字符、``""`` 为转义）；
      - here-string ``@'...'@`` / ``@"..."@``（必须出现在行首）；
      - 行注释 ``#`` 到行尾、块注释 ``<# ... #>``；
      - 反引号 `` ` `` 转义下一个字符（引号外）。

    Returns:
        ``(first, [(op, segment), ...])`` 或 ``None``（无顶层运算符 / 残缺命令）。
    """
    if not command or ("&&" not in command and "||" not in command):
        return None
    segments: list[tuple[str | None, str]] = []
    current: list[str] = []
    pending: str | None = None
    quote: str | None = None
    index = 0
    length = len(command)
    while index < length:
        char = command[index]
        if quote is None:
            if char == "@" and index + 1 < length and command[index + 1] in "'\"":
                if _line_start("".join(current)):
                    quote = "@" + command[index + 1]
                    current.append(command[index:index + 2])
                    index += 2
                    continue
            if char in "'\"":
                quote = char
                current.append(char)
                index += 1
                continue
            if char == "#":
                end = command.find("\n", index)
                if end < 0:
                    current.append(command[index:])
                    break
                current.append(command[index:end])
                index = end
                continue
            if char == "<" and index + 1 < length and command[index + 1] == "#":
                end = command.find("#>", index + 2)
                if end < 0:
                    current.append(command[index:])
                    break
                current.append(command[index:end + 2])
                index = end + 2
                continue
            if command.startswith("&&", index) or command.startswith("||", index):
                # 当前段结束；分隔符是**下一段**的运算符（pending）
                segments.append((pending, "".join(current).strip()))
                pending = command[index:index + 2]
                current = []
                index += 2
                continue
            if char == "`" and index + 1 < length:
                current.append(command[index:index + 2])
                index += 2
                continue
            current.append(char)
            index += 1
            continue
        # ── 引号 / here-string 内部：只找结束标记 ──
        if quote in ("'", '"'):
            if char == quote:
                if quote == "'" and index + 1 < length and command[index + 1] == "'":
                    current.append("''")
                    index += 2
                    continue
                quote = None
                current.append(char)
                index += 1
                continue
            if quote == '"' and char == "`" and index + 1 < length:
                current.append(command[index:index + 2])
                index += 2
                continue
            current.append(char)
            index += 1
            continue
        if quote == "@'" and char == "'" and index + 1 < length and command[index + 1] == "@":
            current.append("'@")
            quote = None
            index += 2
            continue
        if quote == '@"' and char == '"' and index + 1 < length and command[index + 1] == "@":
            current.append('"@')
            quote = None
            index += 2
            continue
        current.append(char)
        index += 1
    segments.append((pending, "".join(current).strip()))
    if len(segments) < 2:
        return None
    first = segments[0][1]
    rest = [(op or "&&", segment) for op, segment in segments[1:]]
    if not first or any(not segment for _op, segment in rest):
        # 形如 `a &&` / `&& b` 的残缺命令：交给 PowerShell 自己报语法错误，
        # 改写只会掩盖真实错误
        return None
    return first, rest


def _rewrite_chain_operators(command: str) -> str:
    """把 Windows PowerShell 5.1 不支持的 ``&&`` / ``||`` 改写为等价语句块。

    语义（与 bash 一致）：``&&`` 前一条成功才执行后一条，``||`` 前一条失败
    才执行后一条，且**逐段传递**结果——``a && b || c`` 在 a 成功、b 失败时
    仍会执行 c（这一点用 ``if/else`` 表达会错，必须用中间状态变量）。

    改写结果形如::

        <a>
        $__dsh_ok = $?
        if ($__dsh_ok) {
        <b>
        $__dsh_ok = $?
        }
        if (-not $__dsh_ok) {
        <c>
        $__dsh_ok = $?
        }

    ``$?`` 是 PowerShell 自身的「上一条语句是否成功」——对 cmdlet 与原生命令
    都有效（原生命令另有 ``$LASTEXITCODE``，包装脚本末尾仍按它退出）。

    未命中顶层运算符时原样返回（不做任何改写）。
    """
    split = _split_chain_operators(command)
    if split is None:
        return command
    first, rest = split
    lines = [first, "$__dsh_ok = $?"]
    for op, segment in rest:
        condition = "$__dsh_ok" if op == "&&" else "-not $__dsh_ok"
        lines.append(f"if ({condition}) {{")
        lines.append(segment)
        lines.append("$__dsh_ok = $?")
        lines.append("}")
    # 末段成功时清除 $LASTEXITCODE：bash 语义下 `a || b` 的退出码取 b（成功即 0），
    # 而 PowerShell 的原生命令失败会留下 LASTEXITCODE（如 cmd /c exit 4），
    # 不清除会让包装脚本以 4 退出；末段失败则保留，交由包装脚本按非零退出。
    lines.append("if ($__dsh_ok) { $global:LASTEXITCODE = 0 }")
    return "\n".join(lines)


def _powershell_script(command: str) -> str:
    """把用户命令包装成 PowerShell 脚本。

    - 关闭进度流（``$ProgressPreference``）：stderr 被重定向时 PowerShell
      会把进度记录序列化成 CLIXML（``#< CLIXML <Objs ... progress ...``）
      写进 stderr，工具卡片里是一大段噪声；
    - 错误流保持 PowerShell **原生 ``Continue`` 语义**（★ 关键修复，2026-10-20）：
      修复前这里是 ``$ErrorActionPreference = 'Stop'`` + 外层 ``try/catch``
      （动机是「stderr 重定向时错误记录会被序列化成 CLIXML」）。但 ``Stop``
      会把**原生命令的 stderr** 也变成终止错误——``cmd /c ... 2>&1`` /
      ``python ... 2> err.txt`` 这类重定向（模型写命令时极常见）在 Windows
      PowerShell 5.1 里会产生 ``NativeCommandError`` 终止错误：``try/catch``
      立刻吞掉它并 ``exit 1`` → 用户命令**中途被打断**，后续语句不执行、
      stdout 只剩出错前那几行；换成 ``trap { continue }`` 虽能让脚本继续，
      但管道（``cmd ... 2>&1 | Select-Object ...``）仍会整体中止、上游
      native 命令的 stdout 全部丢失。用户侧现象就是「bash 执行命令 stdout
      有一定机率不完整，只有几行」。
      改回默认 ``Continue`` 后：原生命令 stderr 只是普通的**非终止**错误记录，
      脚本与管道都按 PowerShell 自身语义继续执行，输出完整；
    - CLIXML 噪声改由**执行方式**消除：脚本用 ``-File`` 执行（见
      :func:`_powershell_launch_argv`）——``-File`` 模式下 PowerShell 以主机
      文本格式把错误写 stderr（与真实控制台一致），不再序列化成 CLIXML
      （``-EncodedCommand`` 只有临时文件不可写时才会回退使用，届时可能出现
      CLIXML，属已知降级路径）；
    - 编码统一为 **UTF-8（无 BOM）**（:func:`_powershell_encoding_line`），并
      在包装脚本 finally 中恢复原编码：
        * ``[Console]::OutputEncoding`` 决定 PowerShell **解码原生命令**
          stdout/stderr（``2>&1`` / 管道）时用的编码；
        * ``[Console]::InputEncoding`` 决定它为子进程 stdin 建 StreamWriter
          时的编码——Windows PowerShell 5.1 在代码页 65001 下会先写 UTF-8
          BOM（.NET Framework ``Process`` + ``StreamWriter.AutoFlush`` 行为），
          设为无 BOM UTF-8 可消除 ``cmd | python`` 这类管道输入前的 BOM；
        * ``$OutputEncoding`` 决定管道正文编码；
        * ``PYTHONIOENCODING`` / ``PYTHONUTF8`` 让 Python 子进程也输出 UTF-8
          （否则它按本机代码页输出，经 PowerShell 管道会被 UTF-8 错误解码）。
      修复前跟随 OEM 代码页（cp936）：``git`` / ``node`` 等输出 UTF-8 的
      原生命令经管道时被错误解码，中文提交信息显示为「绯荤粺…」乱码。
    - 命令前重置 ``$LASTEXITCODE``，命令后按原生命令退出码退出——保持
      bash 工具「returncode = 命令退出码」的契约；命令是纯 cmdlet 时
      用 ``$?`` 兜底（PowerShell 的 cmdlet 错误默认不设置 ``$LASTEXITCODE``，
      如 ``Get-Item nope`` 在 bash 语义下应为非零）；
    - ``exit`` 由用户命令自己触发时不会走到末尾判断，语义不变。
    """
    return (
        "$ProgressPreference = 'SilentlyContinue'\n"
        "$ErrorActionPreference = 'Continue'\n"
        "$__dsh_input_encoding = [Console]::InputEncoding\n"
        "$__dsh_output_encoding = [Console]::OutputEncoding\n"
        "try {\n"
        f"{_powershell_encoding_line()}\n"
        "$global:LASTEXITCODE = 0\n"
        f"{command}\n"
        "$ok = $?\n"
        "if ($null -ne $LASTEXITCODE -and $LASTEXITCODE -ne 0) { exit $LASTEXITCODE }\n"
        "if (-not $ok) { exit 1 }\n"
        "} finally {\n"
        "try { [Console]::InputEncoding = $__dsh_input_encoding } catch { }\n"
        "try { [Console]::OutputEncoding = $__dsh_output_encoding } catch { }\n"
        "}\n"
    )


def _powershell_encoding_line() -> str:
    """返回包装脚本里的「UTF-8（无 BOM）编码设置」语句。

    三条编码各自的作用：

      1. ``[Console]::OutputEncoding``：PowerShell **自己**写 stdout/stderr
         的编码，以及把**原生命令**输出转成文本（``2>&1`` / 管道）时
         **解码**它们的编码。统一 UTF-8 后，``git`` / ``node`` 等按 UTF-8
         输出的原生命令不再被按 cp936 错误解码（用户侧「git 中文提交信息
         乱码」的根因）；
      2. ``[Console]::InputEncoding``：.NET Framework 的 ``Process`` 在
         重定向子进程 stdin 时用 ``Console.InputEncoding`` 建 StreamWriter，
         ``AutoFlush`` 会先写该编码的 BOM；Windows PowerShell 5.1 在代码页
         65001 下因此给 ``cmd | python`` 的 stdin 加上 ``EF BB BF``。设为
         **无 BOM** UTF-8 可消除该 BOM；
      3. ``$OutputEncoding``：`|` 管道正文的编码，同样用无 BOM UTF-8。

    同时设置 ``PYTHONIOENCODING`` / ``PYTHONUTF8``：Python 在 Windows 上
    默认按本机代码页（cp936）写 stdout，经 PowerShell 管道会被 UTF-8 解码
    成 U+FFFD；统一为 UTF-8 与上面的设置配套。

    设置失败（无控制台 / 平台不支持）时静默忽略，不影响命令执行；工具侧
    ``OutputLineDecoder`` 仍对原生命令输出做逐行自适应解码兜底。
    包装脚本在 ``finally`` 中恢复原来的 Input/OutputEncoding，避免改变
    调用方（或同一控制台上的后续命令）的代码页。

    非 Windows（pwsh）默认就是 UTF-8，同样显式设置以保持一致。
    """
    lines = [
        "$__dsh_utf8 = New-Object System.Text.UTF8Encoding($false)",
        "try { [Console]::InputEncoding = $__dsh_utf8 } catch { }",
        "try { [Console]::OutputEncoding = $__dsh_utf8 } catch { }",
        "$OutputEncoding = $__dsh_utf8",
        "$env:PYTHONIOENCODING = 'utf-8'",
        "$env:PYTHONUTF8 = '1'",
        # 读类 cmdlet 的默认编码（★ Windows 中文乱码修复，2026-10-20）：
        # Windows PowerShell 5.1 的 Get-Content / Select-String / Import-Csv
        # 默认按 **ANSI 代码页**（简体中文为 cp936/GBK）解码无 BOM 文件——
        # 本项目与绝大多数现代仓库是 UTF-8（无 BOM），于是
        # `Get-Content README.md` / `Select-String 关键词 文件` 的中文全部
        # 变成「涓枃缂栫爜」式乱码（实测复现）。统一默认 UTF-8 后与工具链
        # 其余部分（stdout/stderr 均按 UTF-8 收集）一致；确实要读 GBK 文件
        # 时用户可显式 `-Encoding gb2312` 覆盖。
        "$PSDefaultParameterValues['Get-Content:Encoding'] = 'utf8'",
        "$PSDefaultParameterValues['Select-String:Encoding'] = 'utf8'",
        "$PSDefaultParameterValues['Import-Csv:Encoding'] = 'utf8'",
    ]
    return "\n".join(lines)


def _encode_powershell_command(script: str) -> str:
    """PowerShell ``-EncodedCommand`` 参数（UTF-16LE + base64）。"""
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def _write_powershell_script_file(script: str) -> str | None:
    """把包装脚本写入临时 ``.ps1`` 文件，返回路径（失败返回 ``None``）。

    编码用 **UTF-8 with BOM**：Windows PowerShell 5.1 只有见到 BOM 才按 UTF-8
    解析脚本，否则按 ANSI 代码页解码——命令里的中文会乱码甚至语法错误。
    换行保持 LF（5.1 / 7.x 都接受）。
    """
    try:
        fd, path = tempfile.mkstemp(prefix="dsh-bash-", suffix=".ps1")
        with os.fdopen(fd, "w", encoding="utf-8-sig", newline="\n") as handle:
            handle.write(script)
        return path
    except OSError:  # pragma: no cover - 临时目录不可写（极端环境）
        logger.debug("写入 PowerShell 包装脚本失败，回退 -EncodedCommand", exc_info=True)
        return None


def _powershell_launch_argv(executable: str, script: str) -> list:
    """PowerShell 启动 argv：优先 ``-File`` 执行包装脚本。

    ``-File`` 与 ``-EncodedCommand`` 的差别（本函数选择前者的原因）：

      - ``-File`` 模式下错误记录按**主机文本格式**写 stderr（与真实控制台
        一致），不会出现 ``#< CLIXML`` 序列化块；
      - 脚本不受 Windows 命令行 32KB 上限约束（``-EncodedCommand`` 的
        base64 约为脚本长度的 2.7 倍，长命令会被直接拒绝执行）；
      - 临时文件写不出来时回退 ``-EncodedCommand``（功能不变，错误可能带
        CLIXML 序列化块）。

    ``-ExecutionPolicy Bypass`` 仅在 Windows 传入：Windows PowerShell 默认
    ``Restricted`` 会把刚写入的 ``.ps1`` 判为不可执行；该参数是**进程级**
    设置，不改动系统执行策略（pwsh 在非 Windows 上不接受该参数）。
    """
    path = _write_powershell_script_file(script)
    if path:
        argv = [executable, "-NoLogo", "-NoProfile"]
        if os.name == "nt":
            argv += ["-ExecutionPolicy", "Bypass"]
        argv += ["-File", path]
        return argv
    return [
        executable,
        "-NoLogo",
        "-NoProfile",
        "-EncodedCommand",
        _encode_powershell_command(script),
    ]


def _script_path_from_launch(launch_arg) -> str | None:
    """从启动 argv 中取出 ``-File`` 包装脚本的临时路径（无则 ``None``）。"""
    if not isinstance(launch_arg, (list, tuple)):
        return None
    args = list(launch_arg)
    try:
        index = args.index("-File")
    except ValueError:
        return None
    if index + 1 >= len(args):
        return None
    return args[index + 1]


def _remove_script_file(path: str | None) -> None:
    """删除 ``-File`` 模式的临时包装脚本（best-effort，绝不抛异常）。"""
    if not path:
        return
    try:
        os.remove(path)
    except OSError:  # pragma: no cover - 已被 PowerShell 删除 / 文件被占用
        logger.debug("删除 PowerShell 包装脚本失败: %s", path, exc_info=True)


def _resolve_shell_launch(command: str) -> tuple:
    """按「启动本程序的交互 shell」决定命令启动方式。

    检测到的交互 shell 就是系统提示词里声明的「命令行」，命令必须由同一个
    解释器执行（模型按该 shell 的语法写命令）：

    Returns:
        ``(command_arg, use_shell, shell_name)``：
          - ``powershell``：``([pwsh/powershell, -NoLogo, -NoProfile,
            (-ExecutionPolicy, Bypass,) -File, <临时脚本路径>], False,
            "powershell")``（临时文件不可写时回退 ``-EncodedCommand``，
            见 :func:`_powershell_launch_argv`）；
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
            # ★ Windows PowerShell 5.1（powershell.exe）不支持 `&&` / `||`
            #   （解析阶段直接报「不是此版本中的有效语句分隔符」）；改写成
            #   等价的条件语句块，使 bash 风格的链式命令能直接执行。
            #   pwsh（6+）原生支持，保持原样不动。
            if _is_windows_powershell_51(executable):
                command = _rewrite_chain_operators(command)
            script = _powershell_script(command)
            return _powershell_launch_argv(executable, script), False, shell
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


def _conpty_launch_argv(command: str) -> list:
    """Windows ConPTY 分支的命令 argv（``pty=true`` 且 ``os.name == 'nt'``）。

    与 :func:`_pty_launch_argv` 同构，但回退 shell 是 **cmd.exe**（``ComSpec``）
    而不是 POSIX ``sh``——Windows 上没有 ``/bin/sh``，沿用 POSIX 默认会让
    ``pty=true`` 在 cmd 会话里直接「找不到 sh」。
    """
    launch_arg, use_shell, _shell_name = _resolve_shell_launch(command)
    if not use_shell and isinstance(launch_arg, (list, tuple)):
        return list(launch_arg)
    comspec = (os.environ.get("ComSpec") or "").strip()
    if not comspec:
        system_root = os.environ.get("SystemRoot") or r"C:\Windows"
        comspec = os.path.join(system_root, "System32", "cmd.exe")
    return [comspec, "/c", command]


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


def _remember_pre_background_line(line_cb: dict, text: str) -> None:
    """记录「自动转后台之前」已产生的输出行（供 bash_opt read 可见）。

    背景：前台命令超过 ``_AUTO_BG_TIMEOUT`` 秒转后台时，转后台之前的输出
    只存在于 ``_run_pipe`` 的局部 ``lines`` 列表里，转后台后 read_buffer 为空
    ——``op=read`` 看不到启动阶段输出（实测 ``read`` 为空、``wait`` 才有
    ``start\\nend``），模型会误判命令卡住。

    这里在行回调代理里顺手把转后台前的行记进 ``line_cb["pre"]``，转后台时
    一次性写入 ``read_buffer``。同样受 :data:`_READ_BUFFER_MAX_CHARS` 限制，
    超限丢弃最旧内容（与 read_buffer 语义一致）。
    """
    pre = line_cb.get("pre")
    if pre is None:
        return
    pre.append(text)
    line_cb["pre_len"] = int(line_cb.get("pre_len", 0) or 0) + len(text)
    # 丢弃最旧的行，直到回到上限以内（至少保留最新一行）
    while line_cb["pre_len"] > _READ_BUFFER_MAX_CHARS and len(pre) > 1:
        line_cb["pre_len"] -= len(pre.pop(0))


def _take_pre_background_output(line_cb: dict | None) -> str:
    """取出并清空「转后台前输出」缓存（返回拼接后的文本）。"""
    if not line_cb:
        return ""
    pre = line_cb.get("pre") or []
    line_cb["pre"] = []
    line_cb["pre_len"] = 0
    return "".join(pre)


#: 输入回显过滤队列的最大长度（行）。只保留最近写入的行，避免无限增长。
_ECHO_FILTER_MAX_LINES = 200


def _consume_echo_line(echo_filter: dict | None, line: str) -> bool:
    """终端输入回显去重：命中「最近写入行」时返回 True（调用方丢弃该行）。

    PTY 的终端驱动（ConPTY 的 conhost；POSIX 关掉 ``ECHO`` 后由程序自己回显）
    会把经 ``bash_opt op=stdin`` 写入的行再输出一遍，模型看到的输出因此出现
    重复输入。写入端（bash_opt）把写入的每一行登记到
    ``echo_filter["lines"]``，读取端在行处理时按**行尾匹配**消费一条：

      - ``C:\\path>echo hi`` / ``>>> echo hi`` / ``$ echo hi`` 这类「提示符 +
        输入」拼在同一行的回显都能命中；
      - 只消费一次（真实输出再次出现同样文本时保留第二行）；
      - 空行 / 空白行不参与匹配（避免误删）。

    Args:
        echo_filter: ``{"lines": [str, ...]}``（可变对象，读取端与写入端共享）。
        line: 已解码、已剥 ANSI 的一行（可含行尾换行）。

    Returns:
        bool: True 表示该行是输入回显，调用方应丢弃。
    """
    if not echo_filter:
        return False
    pending = echo_filter.get("lines")
    if not pending:
        return False
    text = line.strip("\r\n")
    if not text.strip():
        return False
    for index, candidate in enumerate(pending):
        if candidate and text.endswith(candidate):
            del pending[index]
            return True
    return False


def _remember_echo_lines(echo_filter: dict | None, text: str) -> None:
    """登记写入终端的文本行（供读取端去重输入回显；见 :func:`_consume_echo_line`）。

    Args:
        echo_filter: ``{"lines": [str, ...]}``（与 ``_read_loop`` 共享的可变对象）。
        text: 写入 stdin 的文本（``op=stdin`` 的 text 参数，可含多行）。
    """
    if not echo_filter:
        return
    lines = echo_filter.setdefault("lines", [])
    for part in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        candidate = part.rstrip()
        if candidate:
            lines.append(candidate)
    overflow = len(lines) - _ECHO_FILTER_MAX_LINES
    if overflow > 0:
        del lines[:overflow]


#: Windows 下被 Ctrl+C 终止的退出码（``STATUS_CONTROL_C_EXIT``）
_WIN_CTRL_C_EXIT = 0xC000013A


def _is_ctrl_c_exit(returncode: "int | None") -> bool:
    """退出码是否表示「被 Ctrl+C 终止」（POSIX 130 / Windows 0xC000013A）。

    PTY 模式里 Ctrl+C 由终端驱动投递成信号（不经过 ``_read_loop`` 的中断
    检测），``_interrupted`` 标志不会置位；只能从退出码判断，否则模型拿到的
    是「stdout 空、stderr 空、returncode 130」，无法区分「命令无输出」与
    「被中断」。
    """
    return returncode == 130 or returncode == _WIN_CTRL_C_EXIT


def _notify_pty_ready(callback, master_fd, resize_fn) -> None:
    """调用 ``pty_ready_fn``，兼容只接受 ``master_fd`` 的旧回调。

    Args:
        callback: 调用方提供的回调。
        master_fd: PTY master / 输入写端 fd（可能为 None——ConPTY 转 fd 失败）。
        resize_fn: ``resize(cols, rows) -> bool``；回调可通过 ``resize=`` 关键字
            取得（只接受位置参数的回调不传该关键字）。
    """
    try:
        parameters = inspect.signature(callback).parameters
        accepts_resize = (
            "resize" in parameters
            or any(p.kind is inspect.Parameter.VAR_KEYWORD
                   for p in parameters.values())
        )
    except (TypeError, ValueError):  # pragma: no cover - 内置/不可内省回调
        accepts_resize = False
    try:
        if accepts_resize:
            callback(master_fd, resize=resize_fn)
        else:
            callback(master_fd)
    except Exception:
        logger.debug("pty_ready_fn 回调异常", exc_info=True)


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
                    "后台运行强制用 bash_opt 清理：background=true（含前台超时自动转后台）返回的 task_id "
                    "只能由 bash_opt 接管——任务结束时必须用 bash_opt wait 取走结果，不再需要时必须用 "
                    "bash_opt kill 终止整棵进程树；禁止放任后台任务遗留运行，禁止另起命令（其他 bash 调用 / "
                    "系统 kill / 任务管理器）清理后台任务。"
                    "禁止交互式命令（vim/top/less）；禁止 git push -f / reset --hard；"
                    "禁止 rm -rf / mkfs / dd / chmod 777 / sudo / chown。对 git 状态断言前先 git log/diff/status 验证。"
                    "★ Windows PowerShell 5.1 环境（当前常见）：`&&` / `||` 不是有效的语句分隔符，"
                    "请用 `;` 顺序执行（需要「前一条成功才执行」时用 `if ($?) { ... }`），"
                    "确实要在 cmd 内组合时写成 `cmd /c \"a && b\"`（引号内由 cmd 解释）。"
                    "命令输出统一按 UTF-8 收集（包装脚本会把 PowerShell / 原生程序 / Python 的"
                    " stdio 编码统一为 UTF-8，并消除 PowerShell 5.1 管道给子进程 stdin 加的 BOM）；"
                    "如需读取二进制或非 UTF-8 文件，请用 read / 专门工具，不要靠管道文本再用。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": (
                                "要执行的 shell 命令，由当前命令行对应的解释器执行（bash/zsh/fish/cmd/powershell 等），"
                                "语法按其支持情况书写（管道 |、重定向 > >> <、环境变量、$(...) 子 shell、通配符等）。"
                                "★ Windows PowerShell 5.1 不支持 `&&` / `||`：用 `;` 顺序执行，"
                                "或在 cmd 内写成 `cmd /c \"a && b\"`。"
                                "返回 JSON：stdout 与 stderr 分离，无输出时对应字段为空字符串；"
                                "returncode 为退出码；stdout/stderr 各自超 1000 行截断保留尾部。"
                            )
                        },
                        "background": {
                            "type": "boolean",
                            "description": (
                                "是否后台执行（默认 false）。true 立即返回 task_id JSON，后台完成自动注入对话；"
                                "false 前台执行，超 1 分钟自动转后台。"
                                "★ 后台运行强制由 bash_opt 清理：返回的 task_id 必须用 bash_opt wait 取走结果、"
                                "不再需要时必须用 bash_opt kill 终止进程树，不得放任后台任务遗留运行。"
                            )
                        },
                        "pty": {
                            "type": "boolean",
                            "description": (
                                "是否用伪终端（PTY）执行（默认 false）。"
                                "Windows 走 ConPTY、POSIX 走 pty.openpty。"
                                "启用后子进程拿到真 TTY：交互式程序（python REPL、ssh 等）可用，"
                                "彩色输出与进度条正常，stdin 按终端语义解析（回车是 \\r，不是 \\n）。"
                                "代价：PTY 只有一条输出流，stdout / stderr 物理合并（stderr 为空）——"
                                "需要分离双流时才用默认 PIPE 模式。"
                                "交互式后台任务（如 python 交互、ssh、REPL）建议 background=true 且 pty=true。"
                                "★ 全屏 TUI 程序（vim / top / less）会持续输出光标控制序列，"
                                "剥离 ANSI 后只剩互相覆盖的文本，对判断命令结果没有价值——"
                                "需要可读输出时请用默认 PIPE 模式。"
                                "★ 终端驱动的输入回显已尽量过滤（POSIX 关闭 ECHO；ConPTY 按最近写入行去重），"
                                "但子进程自己回显的内容不保证完全去除。"
                                "★ 被中断时（Esc / Ctrl+C）stderr 会返回「(命令已被中断)」提示，"
                                "其余情况 stderr 为空。"
                                "★ 平台限制：Windows ConPTY 下 ctrl+c（写入 \\x03）对部分程序可中断"
                                "（实测可中断 Python 的 time.sleep），但不保证所有程序都响应——"
                                "进程无响应时请用 bash_opt op=kill 终止任务。"
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

    def __init__(self, command, background=False, pty=False):
        super().__init__()
        self.command = command
        self.background = bool(background)
        #: 是否用伪终端执行（``pty=true``）：Windows 走 ConPTY、POSIX 走
        #: ``pty.openpty``。启用后子进程拿到真 TTY——交互式程序 / TUI 可用、
        #: 彩色与进度条正常、``Ctrl+C`` 会变成信号；代价是 stdout/stderr
        #: 物理合并（``stderr`` 恒为空），需要分离双流时用默认 PIPE 模式。
        self.pty = bool(pty)
        #: PTY 输入回显去重队列（``{"lines": [...]}``）：``bash_opt op=stdin``
        #: 写入的每一行登记到这里，``_read_loop`` 在输出里命中时丢弃一次
        #: （终端驱动的本地回显 / 程序自身回显都会把输入重复输出）。
        #: 用可变 dict 是为了「前台转后台」后仍与任务记录里的队列共享同一对象。
        self._echo_filter: dict = {"lines": []}
        # 无 cwd 参数：命令始终在当前工作目录执行。
        # 无 timeout 参数：前台执行超过 _AUTO_BG_TIMEOUT 秒自动转后台
        # （不终止进程，返回 task_id JSON）；后台任务无限运行。

    def _pty_supported(self) -> bool:
        """当前平台能否按 ``pty=true`` 执行（Windows → ConPTY，POSIX → openpty）。"""
        if os.name == "nt":
            return _HAS_CONPTY
        return _HAS_PTY

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

    #: PowerShell 5.1 把 ``&&`` / ``||`` 判为无效语句分隔符时的错误文本片段
    #: （中英文系统均覆盖）；命中后在 stderr 末尾追加等价的改写提示。
    _PS_STATEMENT_SEPARATOR_MARKERS: tuple[str, ...] = (
        "不是此版本中的有效语句分隔符",
        "not a valid statement separator",
    )

    #: PowerShell 5.1 拒绝「把其它流重定向到 stderr」（``1>&2`` / ``3>&2`` 这类）
    #: 时的错误文本片段（中英文系统均覆盖）；命中后追加可用写法。
    #: 5.1 只支持 ``2>&1`` / ``3>&1`` 等「合并到成功流」的方向，不像 bash
    #: 那样允许 ``1>&2``；此前该命令直接解析失败且只回一句「保留运算符」，
    #: 模型容易反复重试同一条写法。
    _PS_RESERVED_REDIRECT_MARKERS: tuple[str, ...] = (
        "运算符是为将来使用而保留的",
        "operator is reserved for future use",
    )

    @classmethod
    def _format_result(cls, res: dict) -> str:
        """把结构化执行结果 dict 组装为返回给大模型的三元 JSON 字符串。

        Args:
            res: {"stdout": str, "stderr": str, "returncode": int}

        Returns:
            JSON 字符串：stdout/stderr 分别截断（超 MAX_LINES 行保留尾部），
            returncode 原样输出；stderr 是 PowerShell 语法错误时额外附上
            当前环境可用的改写方式（见 :meth:`_append_shell_syntax_hint`）。
        """
        stdout = cls._truncate_output(res.get("stdout", "") or "")
        stderr = cls._truncate_output(res.get("stderr", "") or "")
        stderr = cls._append_shell_syntax_hint(stderr)
        return json.dumps({
            "stdout": stdout,
            "stderr": stderr,
            "returncode": res.get("returncode"),
        }, ensure_ascii=False)

    @classmethod
    def _append_shell_syntax_hint(cls, stderr: str) -> str:
        """PowerShell 5.1 拒绝 ``&&`` / ``||`` 时追加可执行的等价写法。

        命令在 PowerShell **解析阶段**就失败（stderr 已是语法错误），这里
        不改执行结果、也不猜测用户意图，只在错误文本后补一条「当前 shell
        的等价写法」，避免模型反复用 bash 语法重试；不在引号内的 cmd 组合
        命令（``cmd /c "a && b"``）本来就能执行，不会进入该分支。

        Args:
            stderr: 已截断的 stderr 文本。

        Returns:
            原文本，或原文本 + 环境提示。
        """
        if not stderr:
            return stderr
        if any(marker in stderr
               for marker in cls._PS_STATEMENT_SEPARATOR_MARKERS):
            return stderr + (
                "\n(环境提示：当前环境是 Windows PowerShell 5.1，`&&` / `||` 不是"
                "有效的语句分隔符。请改用 `;` 顺序执行；需要「前一条成功才执行」"
                "时用 `if ($?) { ... }`；确需在 cmd 内组合时写成 "
                "`cmd /c \"a && b\"`（引号内由 cmd 解释）)"
            )
        if any(marker in stderr
               for marker in cls._PS_RESERVED_REDIRECT_MARKERS):
            return stderr + (
                "\n(环境提示：当前环境是 Windows PowerShell 5.1，不支持把标准输出"
                "重定向到标准错误（`1>&2` / `3>&2` 这类写法；5.1 只支持 `2>&1` "
                "等合并到成功流的方向）。可改用由 cmd 解释该重定向："
                "`cmd /c \"... 1>&2\"`；或把输出写到 stderr："
                "`... | ForEach-Object { [Console]::Error.WriteLine($_) }`；"
                "只需保留错误日志时直接写文件：`... 2> err.txt`)"
            )
        return stderr

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
                         kill_fn=None, decoder=None, echo_filter=None):
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

        ★ Cygwin / MSYS2 乱码修复（2026-10-20）：这类环境里 Cygwin 程序按
        ``locale`` 输出 UTF-8、原生 Windows 程序（``ipconfig`` /
        ``driverquery`` 等）按 ANSI 代码页输出 GBK，同一条命令流可能混排。
        「UTF-8 与 GBK 双重合法」的行（GBK「系统」=``CF B5 CD B3`` 也是合法
        UTF-8）改由「误读特征 + 环境主编码」判定（``_process_output`` 的
        ``_ambiguous_local_decode`` / ``prefer_local_output``）：真 UTF-8 的
        希腊字母（``α``）、西里尔文（``Привет``）不再被改成「伪」「袩褉懈…」，
        GBK 误读行（``ʲô`` / ``ɸѡ``）仍能还原成中文。

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
            echo_filter: 可选的 ``{"lines": [str, ...]}``（PTY 输入回显去重，
                    与 ``bash_opt op=stdin`` 写入端共享的可变对象；见
                    :func:`_consume_echo_line`）。PIPE 流不传。

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
            # ★ 输入回显去重（PTY）：写入端（bash_opt op=stdin）登记过的行
            #   再次出现在输出里时丢弃一次——终端驱动的本地回显 / 程序自身
            #   回显都会把输入重复输出，返回给模型的内容不应包含这段重复。
            if _consume_echo_line(echo_filter, clean):
                return
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
                # ★ 读错误一律按「流结束」处理（2026-10-20 修复）：PTY 下
                #   子进程退出 → EIO；PIPE 下 Windows 子进程异常退出时可能是
                #   ERROR_BROKEN_PIPE / 连接重置（映射为 BrokenPipeError /
                #   ConnectionResetError，errno 并非 EIO）。这些都是「流已不可
                #   用」，已读到的数据仍然有效、尾部不会再有新数据。修复前只有
                #   EIO 当 EOF，其余向上抛给 ``_run_pipe`` 的
                #   ``asyncio.gather(return_exceptions=True)``——它只记一条
                #   warning 就返回**部分** stdout/stderr（模型无法区分「命令只
                #   输出这么多」与「读流出错丢了尾部」）。
                logger.debug("读取输出流结束（OSError）: %s", e)
                break
            except Exception:
                # 防御分支：StreamReader 上被 transport 设置了非 OSError 异常
                # （``reader.set_exception``）时 read() 会抛出它。同样按「流结束」
                # 处理，保证已读到的输出完整返回，而不是整段丢弃。
                logger.debug("读取输出流异常，按结束处理", exc_info=True)
                break
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

        # Cygwin / MSYS2：asyncio 的 stdin=PIPE 用 socketpair 实现，原生
        # Windows 控制台程序读不了；交互模式改用 os.pipe() 建的真管道。
        stdin_target = asyncio.subprocess.PIPE if interactive else asyncio.subprocess.DEVNULL
        stdin_read_fd = None
        stdin_writer = None
        if interactive and _CYGWIN_LIKE:
            try:
                stdin_read_fd, stdin_writer = open_interactive_stdin()
                stdin_target = stdin_read_fd
            except OSError:
                logger.debug("创建 stdin 管道失败，回退 asyncio PIPE", exc_info=True)
                stdin_read_fd = None
                stdin_writer = None

        try:
            process = await spawn_process(
                launch_arg,
                shell=use_shell,
                stdin=stdin_target,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                close_fds=True,
                env=self._get_subprocess_env(),
                **popen_kwargs,
            )
        except BaseException:
            # ★ PowerShell ``-File`` 模式的临时脚本：进程没能创建也要清掉
            _remove_script_file(_script_path_from_launch(launch_arg))
            if stdin_read_fd is not None:
                try:
                    os.close(stdin_read_fd)
                except OSError:
                    pass
            if stdin_writer is not None:
                stdin_writer.close()
            raise
        finally:
            # 子进程已拿到读端副本；父进程不再需要 read_fd
            if stdin_read_fd is not None:
                try:
                    os.close(stdin_read_fd)
                except OSError:
                    pass

        # ★ Windows：创建后立即把进程加入 Job。父进程退出后 Job 成员关系
        #   仍保留，kill 时 TerminateJobObject 能杀掉 Toolhelp32 PPID 链已
        #   断裂的孤儿后代（后台 shell → 已退出的中间层 → 常驻孙进程）。
        #   句柄归属：后台任务 / 自动转后台 → 随任务记录保存（记录移除时
        #   关闭，见 _promote_to_background）；普通前台命令 → _run_exec 的
        #   finally 关闭。
        win_job = create_process_job(process.pid) if _WINDOWS_LIKE else None

        # ★ interactive_ready_fn 回调：提供 stdin 写端供 bash_opt 工具写入。
        if interactive_ready_fn is not None:
            try:
                interactive_ready_fn(
                    process, "pipe",
                    stdin_writer=stdin_writer or process.stdin,
                    job_handle=win_job,
                )
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
                terminate_process_job(win_job)  # 覆盖 PPID 链断裂的孤儿后代
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
            # 关闭自定义 stdin 写端（原生 Windows 程序的 os.pipe）：进程已
            # 退出，继续持有 fd 只会泄漏；后续 stdin 写入应得到「进程已结束」。
            if isinstance(stdin_writer, PipeFdWriter):
                stdin_writer.close()
            # ★ PowerShell ``-File`` 模式的临时包装脚本：进程结束后删除
            _remove_script_file(_script_path_from_launch(launch_arg))

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
                          ``pty_ready_fn(master_fd, resize=_resize)``：外部可获得
                          PTY master fd 与 resize 函数（``_resize(cols, rows)``），
                          用于终端 resize 时同步更新 PTY winsize。只接受一个
                          位置参数的回调也兼容（不会传 ``resize``）。
            interactive: 是否启用交互式输入（**仅为签名兼容保留**）。PTY 模式
                          下子进程 stdin / stdout / stderr 恒连接到 PTY slave——
                          否则子进程会看到「stdout 是终端、stdin 不是」，
                          ``isatty(stdin)`` 为假，与真实终端语义不一致。
            interactive_ready_fn: 可选的回调，子进程创建后调用
                          interactive_ready_fn(process, "pty", master_fd=master_fd)
                          供后台任务记录进程句柄（bash_opt 工具写入输入用）。

        Returns:
            结构化结果 dict：{"stdout": str, "stderr": str, "returncode": int}
            （PTY 模式 stdout/stderr 物理合并到同一 master fd，无法分离，
            全部输出归入 stdout；stderr 恒为空——**唯一例外是被中断时**，
            此时 stderr 放中断提示，便于模型区分「无输出正常退出」与
            「被 Ctrl+C / 中断取消」。returncode 为子进程退出码。）
        """
        if os.name == "nt":
            # Windows：PTY 走 ConPTY（见 _conpty 模块）。POSIX 的 pty/fcntl
            # 在原生 Windows 上不存在，不能走下面的 openpty 路径。
            return await self._run_conpty(
                show_command=show_command, show_output=show_output,
                publish_line_fn=publish_line_fn,
                pty_ready_fn=pty_ready_fn,
                interactive=interactive,
                interactive_ready_fn=interactive_ready_fn,
            )
        import fcntl
        import pty
        import struct
        import termios

        if show_command:
            await print_to_terminal(f"\n{GREEN}$ {self.command}{RESET}\n")

        master_fd, slave_fd = pty.openpty()

        # 初始尺寸：跟随当前终端（拿不到时回退 120x24，避免 COLUMNS=0）
        size = shutil.get_terminal_size(fallback=(120, 24))
        cols = max(int(getattr(size, "columns", 120) or 120), 1)
        rows = max(int(getattr(size, "lines", 24) or 24), 1)

        def _resize(new_cols, new_rows) -> bool:
            """同步 PTY 窗口大小（外部终端 resize 时调用）。"""
            try:
                fcntl.ioctl(master_fd, termios.TIOCSWINSZ,
                            struct.pack('HHHH', max(int(new_rows), 1),
                                        max(int(new_cols), 1), 0, 0))
            except (OSError, ValueError):
                logger.debug("PTY TIOCSWINSZ 设置失败", exc_info=True)
                return False
            return True

        _resize(cols, rows)

        # ★ 关闭终端驱动的本地回显：写入 stdin 的内容不应再被回显到输出流。
        #   修复前 PTY 默认 ECHO 开启，`bash_opt op=stdin` 写入的每一行都会
        #   在输出里重复一遍（实测 `cat` 返回 "hello\nhello"：第一行是驱动
        #   回显、第二行才是程序输出），模型会把重复输入当成命令结果。
        try:
            attrs = termios.tcgetattr(slave_fd)
            attrs[3] &= ~(termios.ECHO | getattr(termios, "ECHONL", 0))
            termios.tcsetattr(slave_fd, termios.TCSANOW, attrs)
        except (OSError, termios.error):
            logger.debug("PTY 关闭 ECHO 失败", exc_info=True)

        # ★ pty_ready_fn 回调：通知外层 PTY master fd + resize 函数
        #   （终端 resize 时同步更新 PTY winsize）。
        if pty_ready_fn is not None:
            _notify_pty_ready(pty_ready_fn, master_fd, _resize)

        env = self._get_subprocess_env()
        env['TERM'] = 'xterm-256color'  # 告诉子进程这是终端

        # ★ 与 PIPE 分支同一套「交互 shell」解析结果（bash / zsh / fish /
        #   powershell ...）；回退平台默认 POSIX shell 的逻辑在 _pty_launch_argv。
        launch_argv = _pty_launch_argv(self.command)

        def _attach_controlling_terminal() -> None:
            """子进程 exec 前把 PTY slave 设为控制终端（preexec_fn 内执行）。

            会话由 ``start_new_session=True`` 在 subprocess 的 C 层建立
            （``setsid()``，子进程成为新会话首进程 / 前台进程组），这里只做
            ``TIOCSCTTY``：把 fd 1（subprocess 已 dup 成 PTY slave）设为该
            会话的控制终端。

            为什么必须设置：没有控制终端时 line discipline 的 ``ISIG`` 没有
            可投递信号的前台进程组，向 master 写 ``\\x03``（ctrl+c）只会作为
            普通输入字节传给程序，不会产生 SIGINT——``bash_opt op=keys
            key='ctrl+c'`` 会「发送成功但无法中断进程」。

            ★ 为什么不自己 ``setsid()/setpgid()``：preexec_fn 在 fork 之后
            执行 Python 代码，多线程进程里有死锁风险（CPython 文档明示）；
            把 setsid 交给 C 层能缩小风险窗口，这里只剩一次 ioctl。失败一律
            静默：最坏退回「ctrl+c 不生效」，不应阻止命令执行。

            ★ 不使用 ``pass_fds`` 保留 slave fd：subprocess 的 dup2 在
            preexec_fn 之前完成，fd 1 已是 slave；而 pass_fds 会让子进程及其
            全部后代多持有一份 slave 副本（实测孙进程出现 ``/dev/ptyN`` fd），
            既泄漏 fd，也破坏「输出全部关闭 → master 收 EOF」的语义。
            """
            for fd in (1, 0, 2):
                try:
                    fcntl.ioctl(fd, termios.TIOCSCTTY, 0)
                    return
                except OSError:
                    continue

        try:
            from ..core.adapters.kernel_runtime import spawn_process

            process = await spawn_process(
                launch_argv,
                shell=False,
                # PTY 下 stdin 必须是 slave（见 interactive 参数说明）
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                env=env,
                start_new_session=True,
                preexec_fn=_attach_controlling_terminal,
            )
        except BaseException:
            os.close(master_fd)
            os.close(slave_fd)
            _remove_script_file(_script_path_from_launch(launch_argv))
            raise

        # 关闭父进程中的 slave 端，确保子进程退出后
        # master 端能收到 EOF
        os.close(slave_fd)

        # ★ Cygwin / MSYS2（POSIX PTY + Windows 内核）：为 PTY 子进程建立
        #   Windows Job。Cygwin 的 ``bash -c`` 在 exec 成目标程序时会**重建
        #   Windows 进程**（``/proc/<pid>/winpid`` 与 ``GetProcessTimes`` 的
        #   创建时间都会变，实测 ready 时 22512 → exec 后 8060）。而任务记录
        #   的 ``pid_create_time`` 是在子进程创建后立刻采样的（exec 前），
        #   于是 ``op=kill`` 的 pid 复用防护会把这 15ms 的创建时间变化误判成
        #   「PID 已被系统复用」而拒绝杀树——实测 PTY 后台任务（没有 Job，
        #   ``job_was_active`` 为 0）100% 复现。Job 成员关系由内核维护，
        #   exec 后仍然保留（实测 assign 后 ``QueryInformationJobObject``
        #   活动进程数恒为 1），既让 kill 跳过复用判定，也让整树终止覆盖
        #   exec 后的进程与后代。
        #   原生 Windows 走 ``_run_conpty``（其分支内已创建 Job）。
        win_job = None
        if _WINDOWS_LIKE and interactive_ready_fn is not None:
            try:
                win_job = create_process_job(process.pid)
            except Exception:  # pragma: no cover - 依赖系统调用
                logger.debug("创建 PTY 进程 Job 失败", exc_info=True)

        # ★ interactive_ready_fn 回调：通知外层子进程已创建，提供交互句柄。
        #   在 master 包装为 asyncio 读管道前调用，保证 bash_opt 工具
        #   能尽早向 PTY master 写入输入。
        if interactive_ready_fn is not None:
            try:
                interactive_ready_fn(
                    process, "pty", master_fd=master_fd, job_handle=win_job,
                )
            except Exception:
                logger.debug("interactive_ready_fn 回调异常")

        # 将 master FD 包装为 asyncio StreamReader
        # ★ 使用 _PtyEioAsEofProtocol：PTY 子进程退出关闭 slave 端后 master
        #   read 返回 EIO，但缓冲中可能还有未消费的数据（一次性到达的多行
        #   输出）。该 protocol 把 EIO 归一化为 EOF，先消费完缓冲再结束，
        #   避免「多行输出只返回第一行」。
        loop = asyncio.get_running_loop()
        reader = asyncio.StreamReader()
        protocol = _PtyEioAsEofProtocol(reader)
        # ★ fd 所有权只交给 pipe 对象一次：连接失败时关闭 pipe（它会关闭 fd），
        #   不再额外 os.close(master_fd)——旧实现两者都关，FileIO 析构时会
        #   二次关闭同一个 fd 号（期间可能已被别的代码复用 → 误关无关 fd）。
        pipe = os.fdopen(master_fd, 'rb', buffering=0)
        try:
            transport, _ = await loop.connect_read_pipe(lambda: protocol, pipe)
        except BaseException:
            pipe.close()
            raise

        lines = []

        _kill_once = False

        def _kill_tree_once() -> None:
            """杀整棵进程树（含 Windows Job；与 PIPE 分支一致）。"""
            nonlocal _kill_once
            if _kill_once:
                return
            _kill_once = True
            # Job 先终止：Cygwin 的 bash 在 exec 时会重建 Windows 进程，
            # PPID 链 / pid 复用防护都不可靠，Job 成员关系才是可靠的整树
            # 终止手段（修复前这里只扫进程树，中断时孤儿后代残留）。
            terminate_process_job(win_job)
            _kill_process_tree(process.pid)

        try:
            _interrupted = await BashFunc._read_loop(
                reader, process, lines, publish_line_fn,
                show_output=show_output, is_stderr=False,
                kill_fn=_kill_tree_once,
                # ★ POSIX 分支不启用输入回显过滤：终端驱动的 ECHO 已在
                #   openpty 后关闭，剩下的输出都是程序真实输出。再按「写入行」
                #   过滤会误删真实输出（实测 `cat` 收到 hello 后输出的 "hello"
                #   被当成回显丢弃，read 变成空）。ConPTY 无法关闭终端回显，
                #   才在 _run_conpty 里启用该过滤。
                echo_filter=None,
            )
        except OSError as e:
            if e.errno == _errno.EIO:
                pass  # treat as EOF, fall through to finally
            else:
                raise
        except asyncio.CancelledError:
            _kill_tree_once()
            raise
        finally:
            try:
                transport.close()
            except OSError:
                pass
            await process.wait()
            _remove_script_file(_script_path_from_launch(launch_argv))

        returncode = self._bash_rc(process.returncode)
        stdout = ''.join(lines).strip()
        if _interrupted or _is_ctrl_c_exit(returncode):
            # ★ 中断提示（2026-10-20 修复）：Ctrl+C / Esc 中断时旧实现返回
            #   {"stdout": "", "stderr": "", "returncode": 130}——既没有输出也
            #   没有提示，模型只能靠退出码猜。这里保留已读到的输出，并像
            #   PIPE 分支一样在 stderr 给出「(命令已被中断)」。
            return {"stdout": stdout, "stderr": "(命令已被中断)",
                    "returncode": returncode}
        return {
            "stdout": stdout,
            # PTY 模式 stdout/stderr 合并到同一 master fd，无法分离
            "stderr": "",
            "returncode": returncode,
        }

    async def _run_conpty(self, show_command=False, show_output=False,
                          publish_line_fn=None, pty_ready_fn=None,
                          interactive=False,
                          interactive_ready_fn=None):
        """用 Windows ConPTY 执行命令（``pty=true`` 且 ``os.name == 'nt'``）。

        与 POSIX :meth:`_run_pty` 的差异只在「怎么建伪终端」：

          - POSIX：``pty.openpty`` + ``preexec_fn``（TIOCSCTTY）+ ``start_new_session``；
          - Windows：``_conpty.spawn``（CreatePseudoConsole + STARTUPINFOEX）。

        两者的输出处理完全复用 :meth:`_read_loop`（同一个 ``StreamReader``
        接口），因此 UTF-8/本地代码页自适应解码、ANSI 剥离、``\\r`` 覆盖模拟、
        输入回显去重、行回调（display / read_buffer）行为一致。

        Args:
            show_command: 是否打印命令行到终端。
            show_output: 是否实时打印输出到终端。
            publish_line_fn: 可选的行回调 ``async (text, is_stderr) -> None``。
            pty_ready_fn: 可选回调 ``pty_ready_fn(write_fd, resize=_resize)``：
                外部可获得输入写端 fd 与 resize 函数（调用
                ``ResizePseudoConsole``）。只接受一个位置参数的回调也兼容。
            interactive: 是否启用交互式输入（**仅为签名兼容保留**）：
                ConPTY 的输入写端始终连接伪终端，不存在「非交互」形态。
            interactive_ready_fn: 子进程创建后的回调
                ``(process, "pty", master_fd=<写端 fd>, job_handle=<Job>)``，
                供后台任务记录 stdin 写端与进程句柄。

        Returns:
            三元结果 dict（PTY 输出合并，``stderr`` 通常为空；被中断时
            ``stderr`` 放中断提示，便于模型区分「无输出」与「被中断」）。
        """
        if show_command:
            await print_to_terminal(f"\n{GREEN}$ {self.command}{RESET}\n")

        env = self._get_subprocess_env()
        env['TERM'] = 'xterm-256color'
        # 交互式程序（REPL / TUI）在 PTY 下会读取行列数；跟随当前终端尺寸
        # （拿不到时回退 120x30），避免 0 列导致折行异常。
        size = shutil.get_terminal_size(fallback=(120, 30))
        cols = max(int(getattr(size, "columns", 120) or 120), 1)
        rows = max(int(getattr(size, "lines", 30) or 30), 1)
        launch_argv = _conpty_launch_argv(self.command)

        try:
            process = _conpty.spawn(launch_argv, env=env, cols=cols, rows=rows)
        except BaseException:
            _remove_script_file(_script_path_from_launch(launch_argv))
            raise

        # ★ pty_ready_fn：提供写端 fd + resize 回调（ConPTY 的
        #   ResizePseudoConsole）。修复前 _run_conpty 完全忽略该参数、
        #   且 ResizePseudoConsole 从未被调用——终端 resize 不会同步，
        #   TUI 尺寸永远停在启动时的初始值。
        if pty_ready_fn is not None:
            _notify_pty_ready(pty_ready_fn, process.write_fd, process.resize)

        # Windows Job：kill / 中断时 TerminateJobObject 覆盖 PPID 链断裂的
        # 孤儿后代（与 PIPE 分支同一套机制）
        win_job = create_process_job(process.pid)

        if interactive_ready_fn is not None:
            try:
                interactive_ready_fn(
                    process, "pty",
                    master_fd=process.write_fd, job_handle=win_job,
                )
            except Exception:
                logger.debug("interactive_ready_fn 回调异常")

        lines: list[str] = []
        _kill_once = False

        def _kill_tree_once() -> None:
            nonlocal _kill_once
            if _kill_once:
                return
            _kill_once = True
            terminate_process_job(win_job)
            _kill_process_tree(process.pid)

        interrupted = False
        try:
            interrupted = await BashFunc._read_loop(
                process.reader, process, lines, publish_line_fn,
                show_output=show_output, is_stderr=False,
                kill_fn=_kill_tree_once,
                echo_filter=self._echo_filter,
            )
        except asyncio.CancelledError:
            _kill_tree_once()
            raise
        finally:
            try:
                await process.wait()
            finally:
                process.close()

        _remove_script_file(_script_path_from_launch(launch_argv))

        returncode = self._bash_rc(process.returncode)
        stdout = ''.join(lines).strip()
        if interrupted or _is_ctrl_c_exit(returncode):
            return {"stdout": stdout, "stderr": "(命令已被中断)",
                    "returncode": returncode}
        return {
            "stdout": stdout,
            # ConPTY 只有一条输出流，stdout/stderr 物理合并
            "stderr": "",
            "returncode": returncode,
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

        def _on_ready(process, mode, master_fd=None, stdin_writer=None,
                      job_handle=None):
            holder["process"] = process
            holder["mode"] = mode
            holder["master_fd"] = master_fd
            holder["stdin_writer"] = stdin_writer
            holder["job_handle"] = job_handle
            # PTY 输入回显去重队列：与 self._echo_filter 共享，转后台后
            # 由 _promote_to_background 换成任务记录里的队列（同一 list）。
            holder["echo_filter"] = self._echo_filter["lines"]
            if _WINDOWS_LIKE:
                try:
                    from . import _win_proc
                    from ._task_winpid import now_filetime
                    holder["winpid"] = _win_proc.to_winpid(process.pid, retries=5)
                    holder["win_start_time"] = now_filetime()
                except Exception:  # pragma: no cover - 依赖系统调用
                    holder["winpid"] = None
                    holder["win_start_time"] = None
                holder["win_hint"] = _command_exe_hint(self.command)
                # 全部候选（``cd /tmp && mintty ...`` 里的 mintty 也要能被
                # op=kill 的按名兜底找到——它在启动后 daemon 化，进程树 /
                # Job 都可能够不着）。
                holder["win_hints"] = _command_exe_hints(self.command)

        # ★ 行回调代理（可变引用）：自动转后台后把 fn 置 None，断开实时输出，
        #   避免转后台后命令输出继续污染已闭合的工具卡片（工具已返回
        #   task_id JSON，后续输出由 bash_opt wait 获取完整结果）。
        #   fn 置 None 后，后续输出行写入任务记录 read_buffer（bash_opt
        #   read 操作实时消费，见 _promote_to_background 填充 line_cb）。
        line_cb: dict = {"fn": publish_line_fn, "pre": [], "pre_len": 0}

        async def _line_proxy(text: str, is_stderr: bool) -> None:
            # ★ 转后台后（rec 已设置）：输出写入任务记录 read_buffer
            #   （bash_opt read 实时消费），不再发布到 UI/回调。
            rec = line_cb.get("rec")
            if rec is not None:
                await _append_read_buffer(rec, text)
                return
            # 转后台前也留一份给 read：否则 read 只能看到转后台之后的输出
            # （启动阶段的日志会「消失」，wait 才拿得到）。
            _remember_pre_background_line(line_cb, text)
            fn = line_cb["fn"]
            if fn is not None:
                await fn(text, is_stderr)

        async def _run_exec():
            # ★ 前台统一 PIPE 分离模式（见 _run_async docstring）：
            #   stdout/stderr 独立收集 → 三元 JSON 真实分离。
            try:
                if self.pty and self._pty_supported():
                    # pty=true：走伪终端（Windows ConPTY / POSIX openpty）。
                    # PTY 只有一条输出流，stdout/stderr 合并（见 _run_conpty）。
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
                return self._format_result(result)
            finally:
                # 未转后台（命令在本函数内正常结束 / 被取消）：关闭 Job 句柄；
                # 已转后台时句柄归任务记录（op=kill 杀孤儿后代用，记录移除
                # 时关闭），不能在此关闭——否则后续 kill 会用到已释放句柄。
                if not holder.get("promoted"):
                    close_process_job(holder.get("job_handle"))
                    holder["job_handle"] = None

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
                    # Windows Job 先终止（覆盖 PPID 链断裂的孤儿后代）
                    terminate_process_job(holder.get("job_handle"))
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
            # ★ 无 Agent 上下文时不能「丢下任务不管」：旧实现直接返回错误，
            #   但 exec_task 与子进程仍在运行——命令永远无法被管理（读 stdin
            #   的 cat / ssh 会永久挂起，且占用子进程与 fd）。这里取消执行并
            #   终止进程树（PTY 分支的 CancelledError 处理也会杀一次）。
            process = holder.get("process")
            exec_task.cancel()
            try:
                await asyncio.wait({exec_task}, timeout=2.0)
            except Exception:  # pragma: no cover - 取消过程异常不影响结果
                logger.debug("取消超时前台任务失败", exc_info=True)
            if process is not None:
                try:
                    terminate_process_job(holder.get("job_handle"))
                    _kill_process_tree(process.pid)
                except Exception:  # pragma: no cover - 依赖系统调用
                    logger.debug("清理超时前台任务进程失败", exc_info=True)
            close_process_job(holder.get("job_handle"))
            holder["job_handle"] = None
            return self._error_result(
                f"(命令执行超过 {self._AUTO_BG_TIMEOUT} 秒，但当前未关联 "
                f"Agent 上下文，无法自动转后台管理；已终止该命令)")

        task_id = f"bg-{uuid.uuid4().hex[:12]}"
        process = holder.get("process")
        pid = process.pid if process is not None else None

        rec = {
            "task": exec_task,
            "created_at": time.time(),
            "done": False,
            "result": "",
            "status": "running",
            # ── 交互控制字段（bash_opt 工具按 task_id 操作） ──
            "process": process,
            "pid": pid,
            # 根进程创建时间（Windows；pid 复用防护）
            "pid_create_time": process_create_time(pid) if pid else None,
            # 根进程的 Windows PID（Cygwin 下 POSIX pid 与 Windows pid 不同；
            # 窗口枚举 / Job / TerminateProcess 需要它）
            "winpid": holder.get("winpid"),
            # 任务启动时间 / 可执行名提示（Store 应用窗口归属兜底）
            "win_start_time": holder.get("win_start_time"),
            "win_hint": holder.get("win_hint"),
            # 命令行里出现的全部可执行名候选（op=kill 按名兜底：daemon 化的
            # GUI 如 mintty 不在进程树 / Job 里，只能按名 + 启动时窗定位）
            "win_hints": holder.get("win_hints") or [],
            # Windows Job 句柄（杀整棵进程树；记录移除时关闭）
            "job_handle": holder.get("job_handle"),
            # 运行期采样累积的后代（见 BashFunc._sample_process_tree）
            "descendant_pids": set(),
            "descendant_create_times": {},
            "descendant_win_pids": set(),
            "descendant_win_create_times": {},
            "job_assigned": set(),
            "mode": holder.get("mode"),
            "master_fd": holder.get("master_fd"),
            "stdin_writer": holder.get("stdin_writer"),
            # PTY 输入回显去重队列（bash_opt op=stdin 登记写入行，
            # _read_loop 消费；与 self._echo_filter["lines"] 共享同一 list）
            "echo_filter": {"lines": holder.get("echo_filter") or []},
            "io_lock": asyncio.Lock(),
            # ── 实时输出缓冲（bash_opt read 读取后清空） ──
            "read_buffer": "",
            "read_buffer_dropped": 0,  # 因缓冲上限被丢弃的字符数（read 回报）
        }
        # ★ 转后台前的输出也并入 read_buffer：否则 op=read 看不到启动阶段
        #   输出（wait 才有完整 stdout），模型会误判「命令没输出 / 卡住」。
        pre_output = _take_pre_background_output(line_cb)
        if pre_output:
            overflow = max(0, len(pre_output) - _READ_BUFFER_MAX_CHARS)
            rec["read_buffer"] = pre_output[-_READ_BUFFER_MAX_CHARS:]
            if overflow:
                rec["read_buffer_dropped"] = overflow
        agent._register_background_task(task_id, rec)
        # Job 句柄归属转给任务记录：_run_exec 的 finally 不再关闭它
        holder["promoted"] = True
        # ★ 回显去重队列换成任务记录里的 list（**同一对象**）：bash_opt 写入时
        #   登记到 rec["echo_filter"]["lines"]，_read_loop 经 self._echo_filter
        #   读取——共享同一 list 才能让转后台后的输入回显也被过滤。
        self._echo_filter["lines"] = rec["echo_filter"]["lines"]
        if _WINDOWS_LIKE and pid:
            try:
                from ._task_winpid import register
                register(pid, rec.get("winpid"),
                         job=rec.get("job_handle"),
                         hint=rec.get("win_hint"),
                         start_time=rec.get("win_start_time"))
            except Exception:  # pragma: no cover - 依赖导入链
                logger.debug("登记任务 Windows PID 失败", exc_info=True)

        # ★ Windows：转后台后启动进程树采样（与 background=True 路径一致），
        #   供 op=kill 覆盖 PPID 链断裂的孤儿后代。任务完成（done）后采样
        #   协程自行退出，记录移除时 Job 句柄关闭。
        if _WINDOWS_LIKE:
            asyncio.ensure_future(self._sample_process_tree(agent, task_id))

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
            if self.pty and self._pty_supported():
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
        bg = BashFunc(command=self.command, pty=self.pty)

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
            # 根进程创建时间（Windows FILETIME；pid 复用防护用，见
            # _bash_support.kill_process_tree 的 create_time 参数）
            "pid_create_time": None,
            # 根进程的 Windows PID / 启动时间 / exe 名提示（Cygwin 下窗口
            # 归属与原生子进程杀灭用；原生 Windows 也写入同一字段）
            "winpid": None,
            "win_start_time": None,
            "win_hint": _command_exe_hint(self.command),
            # 命令行里出现的全部 exe 名候选：op=kill 的按名兜底要用
            # （Cygwin 下 mintty 等 GUI 启动后 daemon 化，进程树 / Job 都
            # 够不着，只能按名 + 启动时窗定位）
            "win_hints": _command_exe_hints(self.command),
            # Windows Job 句柄（杀整棵进程树用；任务记录移除时关闭）
            "job_handle": None,
            # 运行期采样累积的后代（Python 断链 / Store 应用不继承 Job 时
            # 仍能定位孤儿；见 BashFunc._sample_process_tree）
            "descendant_pids": set(),
            "descendant_create_times": {},
            "descendant_win_pids": set(),
            "descendant_win_create_times": {},
            "job_assigned": set(),
            "mode": None,           # "pty" / "pipe"（写入方式）
            "master_fd": None,      # PTY master fd（mode="pty" 时，os.write 写入）
            "stdin_writer": None,   # PIPE stdin StreamWriter（mode="pipe" 时）
            # PTY 输入回显去重：op=stdin 写入行登记于此，_read_loop 命中时丢弃
            "echo_filter": {"lines": []},
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

        def _on_ready(process, mode, master_fd=None, stdin_writer=None,
                      job_handle=None):
            """子进程创建回调：把进程句柄写入任务记录（bash_opt 工具使用）。"""
            if agent is None or not hasattr(agent, '_background_tasks'):
                return
            rec = agent._background_tasks.get(task_id)
            if rec is None:
                return
            rec["process"] = process
            rec["pid"] = process.pid
            # pid 复用防护：记录根进程创建时间（仅 Windows 可读，其它平台为
            # None）。kill 时比对，PID 被系统复用时拒绝杀，避免误杀新进程树。
            rec["pid_create_time"] = process_create_time(process.pid)
            # Windows Job 句柄：kill 时 TerminateJobObject，覆盖 Toolhelp32
            # PPID 链断裂后收集不到的孤儿后代（记录移除时关闭）
            rec["job_handle"] = job_handle
            rec["mode"] = mode
            rec["master_fd"] = master_fd
            rec["stdin_writer"] = stdin_writer
            # ★ PTY 输入回显去重队列：后台执行体（bg）的 _read_loop 读取
            #   bg._echo_filter，这里把它指向任务记录的 list（同一对象），
            #   bash_opt op=stdin 写入时登记到 rec["echo_filter"]["lines"]。
            echo_filter = rec.get("echo_filter")
            if isinstance(echo_filter, dict):
                bg._echo_filter["lines"] = echo_filter.setdefault("lines", [])
            if _WINDOWS_LIKE:
                try:
                    from . import _win_proc
                    from ._task_winpid import now_filetime, register
                    rec["winpid"] = _win_proc.to_winpid(process.pid, retries=5)
                    rec["win_start_time"] = now_filetime()
                    rec.setdefault("win_hint", _command_exe_hint(self.command))
                    rec.setdefault(
                        "win_hints", _command_exe_hints(self.command))
                    register(process.pid, rec.get("winpid"),
                             job=job_handle,
                             hint=rec.get("win_hint"),
                             start_time=rec.get("win_start_time"))
                except Exception:  # pragma: no cover - 依赖系统调用
                    logger.debug("记录任务 Windows PID 失败", exc_info=True)

        async def _publish_line(text: str, is_stderr: bool) -> None:
            """后台任务实时输出行 → 任务记录 read_buffer（bash_opt read 消费）。"""
            if agent is None or not hasattr(agent, '_background_tasks'):
                return
            rec = agent._background_tasks.get(task_id)
            if rec is None:
                return
            await _append_read_buffer(rec, text)

        # ★ Windows 进程树采样：后台任务运行期间持续记录后代关系（30ms 一次）。
        #   Python 中间层退出后 PPID 链断裂、Store 应用不继承 Job，单次快照
        #   都会漏掉孤儿后代；采样把历史关系 / 观察到的存活 PID 存入任务记录，
        #   op=kill 时据此补杀（见 _bash_support.ProcessTreeTracker）。
        sample_task = None
        if _WINDOWS_LIKE:
            sample_task = asyncio.ensure_future(
                self._sample_process_tree(agent, task_id))

        try:
            result = await bg._run_interactive_async(_on_ready, publish_line_fn=_publish_line)
        except asyncio.CancelledError:
            result = self._error_result("(后台命令已被取消)")
        except Exception as e:
            logger.exception("后台命令执行异常: %s", self.command[:200])
            result = self._error_result(f"(后台命令执行出错: {e})")
        finally:
            if sample_task is not None:
                sample_task.cancel()
                try:
                    await sample_task
                except asyncio.CancelledError:
                    pass
                except Exception:  # pragma: no cover - 采样协程内部异常
                    logger.debug("后台任务进程树采样协程异常", exc_info=True)

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

    #: 后台任务进程树采样间隔（秒）：30ms 兼顾覆盖率与 CPU 开销
    #: （Toolhelp32 全表快照约 2ms）。间隔太长会错过存活时间很短的中间层
    #: 进程——它退出后孙进程的 PPID 链断裂，就再也收集不到。
    _PROCESS_TREE_SAMPLE_INTERVAL = 0.03
    #: 启动 5 秒后的空闲采样间隔（秒）：后代关系已建立，长时任务降频采样
    _PROCESS_TREE_SAMPLE_IDLE_INTERVAL = 0.2

    async def _sample_process_tree(self, agent, task_id: str) -> None:
        """后台任务运行期持续采样进程树（Windows），把后代写入任务记录。

        为什么需要：Windows 的 PPID 是创建时的父 PID，中间层退出后链断
        （``shell → 已退出的 python → 存活的孙进程``）；Store 应用
        （App Execution Alias）也不保证继承 Job。采样器保留历史父子关系：
        只要在中间层存活期间看到过它，之后孙进程（PPID 指向它）仍可判定为
        后代。

        写入：
          - ``rec["descendant_pids"]``：采样中**观察到存活**的后代（kill
            目标；只出现在历史关系里的已退出 PID 不作为目标，避免误杀）；
          - ``rec["descendant_create_times"]``：对应创建时间（pid 复用防护）；
          - 同时把新后代加入任务 Job（Job 继承失败时的兜底）。
        """
        from . import _win_proc

        tracker = ProcessTreeTracker()
        # 动态采样间隔：任务启动后的前几秒是后代关系建立的高峰期（shell 解析
        # 命令、启动子进程、中间层可能很快退出），用 30ms 高频采样保证跨断链
        # 关联；之后降为 200ms（长时任务的后代 fork 稀疏），避免持续全表扫描
        # 占用 CPU。
        fast_interval = self._PROCESS_TREE_SAMPLE_INTERVAL
        idle_interval = self._PROCESS_TREE_SAMPLE_IDLE_INTERVAL
        started = time.monotonic()
        try:
            while True:
                rec = None
                if agent is not None and hasattr(agent, "_background_tasks"):
                    rec = agent._background_tasks.get(task_id)
                if rec is None:
                    return
                if rec.get("done"):
                    # 任务已结束：停止采样（finally 保留最后一次结果）
                    return
                pid = rec.get("winpid") or rec.get("pid")
                if pid:
                    tracker.root_pid = int(pid)
                    try:
                        tracker.observe()
                    except Exception:  # pragma: no cover - 依赖系统调用
                        logger.debug("后台任务进程树采样失败", exc_info=True)
                    if tracker.observed:
                        if _CYGWIN_LIKE:
                            # Cygwin：Toolhelp32 得到的是 Windows PID，不能
                            # 混进 POSIX 的 descendant_pids（编码 / kill 两套
                            # 编号）。单独存 Windows 后代。
                            rec["descendant_win_pids"] = set(tracker.observed)
                            rec["descendant_win_create_times"] = dict(tracker.observed)
                        else:
                            rec["descendant_pids"] = set(tracker.observed)
                            rec["descendant_create_times"] = dict(tracker.observed)
                    job = rec.get("job_handle")
                    if job:
                        assigned = rec.get("job_assigned")
                        if not isinstance(assigned, set):
                            assigned = set()
                            rec["job_assigned"] = assigned
                        for child in tracker.observed:
                            if child in assigned:
                                continue
                            assigned.add(child)
                            try:
                                _win_proc.assign_process_to_job(job, child)
                            except Exception:  # pragma: no cover
                                logger.debug(
                                    "将后代加入 Job 失败: %s", child, exc_info=True)
                interval = (fast_interval
                            if time.monotonic() - started < 5.0
                            else idle_interval)
                await asyncio.sleep(interval)
        finally:
            # 任务结束 / 取消：保留最后一次采样结果，供 op=kill 补杀孤儿
            rec = None
            if agent is not None and hasattr(agent, "_background_tasks"):
                rec = agent._background_tasks.get(task_id)
            if rec is not None and tracker.observed:
                if _CYGWIN_LIKE:
                    rec["descendant_win_pids"] = set(tracker.observed)
                    rec["descendant_win_create_times"] = dict(tracker.observed)
                else:
                    rec["descendant_pids"] = set(tracker.observed)
                    rec["descendant_create_times"] = dict(tracker.observed)

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
