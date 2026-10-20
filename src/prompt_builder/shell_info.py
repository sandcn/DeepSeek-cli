"""命令行环境信息 — 检测当前 Shell 解释器、终端模拟器、GUI 宿主与运行环境。

用于系统提示词「当前执行环境」章节，向模型声明当前命令行类型，使其能按
对应语法执行命令（bash / zsh / fish / powershell / cmd 等），并知道承载
这个命令行的 GUI 程序（Windows Terminal / VS Code / ConEmu ...）。

检测策略：
  - Shell 解释器：优先遍历父进程链识别交互 shell——Linux 走 /proc，
    psutil 可用时走 psutil，Windows 无 psutil 时回退 kernel32 Toolhelp32
    快照（零第三方依赖，避免把 powershell 会话误判为 cmd）；
    父链里的**非交互执行外壳**（``cmd.exe /c ...``、``powershell -Command ...``、
    ``bash -c ...``）会被跳过，继续向上找真实交互 shell（工具子进程场景：
    bash 工具用 cmd /c 启动命令，父链上最近的是执行外壳而不是用户所在
    的 PowerShell）；再回退环境变量（SHELL / BASH_VERSION / ... / ComSpec）。
  - 终端模拟器：优先环境变量特征（WT_SESSION / TERM_PROGRAM / ...），
    回退父进程链（mintty / xterm / konsole ...），最后回退 TERM。
  - GUI 宿主：承载当前命令行的 GUI 程序（Windows Terminal / VS Code /
    ConEmu / JetBrains / GNOME Terminal ...）——环境变量特征优先，
    再沿父链找 GUI 宿主进程；tmux / screen / VTE 这类复用器或组件不算
    GUI 宿主，会继续向上找承载它的图形程序。
  - 运行环境：sys.platform + 环境变量识别 Cygwin / MSYS2 / WSL / Termux /
    Windows / macOS / Linux 等。
"""

from __future__ import annotations

import logging
import os
import platform as _platform
import re
import sys
from typing import Mapping

__all__ = [
    "SHELL_ENV_MARKER",
    "detect_command_line",
    "detect_gui_program",
    "detect_shell",
    "detect_terminal",
    "detect_runtime",
]

_PROC_ROOT = "/proc"
_MAX_ANCESTOR_DEPTH = 8

logger = logging.getLogger(__name__)

#: bash 工具启动子进程时注入的真实交互 shell 名（如 ``powershell``）。
#: 子进程的父链首项是工具自己的执行外壳（Windows 上是 ``cmd.exe /c``），
#: 单靠进程名无法与用户真正所在的 cmd 会话区分；启动方把已知的真实 shell
#: 写进环境变量，子进程内检测即可直接命中，不再按外壳误判。
SHELL_ENV_MARKER = "DEEPSEEK_CLI_SHELL"


def _detect_table(name: str, default):
    """取检测表（表现层数据注册表优先，缺席/非法时回退内置默认）。

    「一切皆插件」：Shell / 终端 / GUI 宿主检测表（shell_aliases /
    terminal_env_rules / term_program_map / terminal_process_aliases /
    gui_terminal_env_rules / gui_terminal_process_aliases）登记在表现层
    数据注册表（``presentation_data`` → ``shell_detect`` 表），可按
    Patch/Overlay 覆盖或禁用。
    """
    try:
        from ..presentation_data import shell_detect_table

        value = shell_detect_table(name, None)
    except Exception:
        value = None
    return value if value else default


_SHELL_ALIASES = {
    "bash": "bash",
    "sh": "sh",
    "zsh": "zsh",
    "fish": "fish",
    "ksh": "ksh",
    "mksh": "ksh",
    "dash": "dash",
    "ash": "ash",
    "csh": "csh",
    "tcsh": "tcsh",
    "nu": "nushell",
    "nushell": "nushell",
    "xonsh": "xonsh",
    "elvish": "elvish",
    "pwsh": "powershell",
    "powershell": "powershell",
    "powershell_ise": "powershell",
    "cmd": "cmd",
    "busybox": "sh",
}

_TERMINAL_ENV_RULES = (
    ("WT_SESSION", "Windows Terminal"),
    ("TERM_PROGRAM", None),
    ("TERMINAL_EMULATOR", None),
    ("WEZTERM_PANE", "WezTerm"),
    ("KITTY_WINDOW_ID", "kitty"),
    ("ALACRITTY_WINDOW_ID", "Alacritty"),
    ("ALACRITTY_SOCKET", "Alacritty"),
    ("KONSOLE_VERSION", "Konsole"),
    ("VTE_VERSION", "VTE"),
    ("TMUX", "tmux"),
    ("STY", "screen"),
    ("TERMUX_VERSION", "Termux"),
)

_TERM_PROGRAM_MAP = {
    "apple_terminal": "Apple Terminal",
    "iterm.app": "iTerm2",
    "vscode": "VS Code",
    "mintty": "mintty",
    "wezterm": "WezTerm",
    "hyper": "Hyper",
    "ghostty": "Ghostty",
    "tabby": "Tabby",
    "warp": "Warp",
    "rio": "Rio",
    "terminus": "Terminus",
    "extraterm": "Extraterm",
    "contour": "Contour",
    "windows_terminal": "Windows Terminal",
    "wt": "Windows Terminal",
}

_TERMINAL_PROCESS_ALIASES = {
    "mintty": "mintty",
    "xterm": "xterm",
    "konsole": "Konsole",
    "gnome-terminal": "GNOME Terminal",
    "gnome-terminal-server": "GNOME Terminal",
    "kgx": "GNOME Console",
    "kitty": "kitty",
    "alacritty": "Alacritty",
    "wezterm": "WezTerm",
    "wezterm-gui": "WezTerm",
    "windowsterminal": "Windows Terminal",
    "openconsole": "Windows Terminal",
    "wt": "Windows Terminal",
    "conemu": "ConEmu",
    "conemu64": "ConEmu",
    "conemuc": "ConEmu",
    "conemuc64": "ConEmu",
    "fluentterminal": "Fluent Terminal",
    "code": "VS Code",
    "code-insiders": "VS Code",
    "tmux": "tmux",
    "screen": "screen",
    "iterm2": "iTerm2",
    "qterminal": "QTerminal",
    "terminator": "Terminator",
    "tilix": "Tilix",
    "xfce4-terminal": "Xfce Terminal",
    "lxterminal": "LXTerminal",
    "mate-terminal": "MATE Terminal",
    "urxvt": "urxvt",
    "rxvt": "rxvt",
    "terminology": "Terminology",
    "warp": "Warp",
    "rio": "Rio",
    "terminus": "Terminus",
    "extraterm": "Extraterm",
    "contour": "Contour",
    "tabby": "Tabby",
}

#: GUI 宿主的环境变量特征。**不含 tmux / screen / VTE**——三者是终端复用器
#: 或终端组件，不是承载命令行的 GUI 程序，GUI 检测要继续沿父链找真正的
#: 图形宿主（如 tmux 之外的 Windows Terminal / GNOME Terminal）。
_GUI_TERMINAL_ENV_RULES = (
    ("WT_SESSION", "Windows Terminal"),
    ("TERM_PROGRAM", None),
    ("TERMINAL_EMULATOR", None),
    ("WEZTERM_PANE", "WezTerm"),
    ("KITTY_WINDOW_ID", "kitty"),
    ("ALACRITTY_WINDOW_ID", "Alacritty"),
    ("ALACRITTY_SOCKET", "Alacritty"),
    ("KONSOLE_VERSION", "Konsole"),
    ("TERMUX_VERSION", "Termux"),
)

#: GUI 宿主进程名 → 展示名。只收「承载命令行的图形程序」；xterm / urxvt
#: 只是终端模拟器进程，也可视为 GUI 宿主（它们本身有窗口），因此保留；
#: tmux / screen 是复用器，不收。
_GUI_TERMINAL_PROCESS_ALIASES = {
    "windowsterminal": "Windows Terminal",
    "openconsole": "Windows Terminal",
    "wt": "Windows Terminal",
    "conemu": "ConEmu",
    "conemu64": "ConEmu",
    "conemuc": "ConEmu",
    "conemuc64": "ConEmu",
    "fluentterminal": "Fluent Terminal",
    "code": "VS Code",
    "code-insiders": "VS Code",
    "mintty": "mintty",
    "wezterm-gui": "WezTerm",
    "alacritty": "Alacritty",
    "kitty": "kitty",
    "konsole": "Konsole",
    "gnome-terminal": "GNOME Terminal",
    "gnome-terminal-server": "GNOME Terminal",
    "kgx": "GNOME Console",
    "qterminal": "QTerminal",
    "terminator": "Terminator",
    "tilix": "Tilix",
    "xfce4-terminal": "Xfce Terminal",
    "lxterminal": "LXTerminal",
    "mate-terminal": "MATE Terminal",
    "urxvt": "urxvt",
    "rxvt": "rxvt",
    "terminology": "Terminology",
    "iterm2": "iTerm2",
    "hyper": "Hyper",
    "tabby": "Tabby",
    "ghostty": "Ghostty",
    "warp": "Warp",
    "rio": "Rio",
    "terminus": "Terminus",
    "extraterm": "Extraterm",
    "contour": "Contour",
    "guake": "Guake",
    "yakuake": "Yakuake",
    "tilda": "Tilda",
    "xterm": "xterm",
    "uxterm": "xterm",
}

#: JetBrains 系 IDE 进程名（Linux 下为 idea / pycharm64 等，Windows 下带 64）。
_JETBRAINS_PROCESS_NAMES = {
    "idea", "idea64", "idea.sh", "pycharm", "pycharm64",
    "webstorm", "webstorm64", "goland", "goland64", "clion", "clion64",
    "rider", "rider64", "datagrip", "datagrip64", "rubymine", "rubymine64",
    "phpstorm", "phpstorm64", "studio", "studio64", "aqua", "aqua64",
}



def _read_text(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        return ""


def _proc_path(pid: int, filename: str) -> str:
    """拼 /proc 下的文件路径（如 ``/proc/123/exename``）。

    不能用 ``os.path.join``：Windows 上会拼成 ``\\proc\\...``，既保证不了 POSIX
    语义，也让 /proc 解析逻辑在 Windows 上完全无法测试。分隔符固定为正斜杠。
    """
    return f"{_PROC_ROOT}/{pid}/{filename}"


def _proc_name(pid: int) -> str:
    for filename in ("exename", "comm", "cmdline"):
        text = _read_text(_proc_path(pid, filename))
        if not text:
            continue
        if filename == "cmdline":
            text = text.split("\x00", 1)[0]
        text = text.strip()
        if text:
            return text
    return ""


def _proc_ppid(pid: int) -> int:
    text = _read_text(_proc_path(pid, "ppid")).strip()
    if text.lstrip("-").isdigit():
        return int(text)
    for line in _read_text(_proc_path(pid, "status")).splitlines():
        if line.lower().startswith("ppid:"):
            value = line.split(":", 1)[1].strip()
            if value.lstrip("-").isdigit():
                return int(value)
    return -1


def _proc_ancestor_names(pid: int) -> list:
    names: list = []
    seen: set = set()
    for _ in range(_MAX_ANCESTOR_DEPTH):
        if not isinstance(pid, int) or pid <= 1 or pid in seen:
            break
        seen.add(pid)
        name = _proc_name(pid)
        if not name:
            break
        names.append(name)
        pid = _proc_ppid(pid)
    return names


def _proc_ancestor_details(pid: int) -> list:
    """``/proc`` 父进程链 → ``[(进程名, 命令行)]``（含起点进程本身）。

    只有 shell 候选进程才读取 ``cmdline``（用于判断「非交互执行外壳」），
    其余进程留空字符串，避免无谓的 /proc 读取。
    """
    details: list = []
    seen: set = set()
    for _ in range(_MAX_ANCESTOR_DEPTH):
        if not isinstance(pid, int) or pid <= 1 or pid in seen:
            break
        seen.add(pid)
        name = _proc_name(pid)
        if not name:
            break
        cmdline = ""
        if _shell_from_name(name):
            cmdline = _read_text(_proc_path(pid, "cmdline")).replace("\x00", " ").strip()
        details.append((name, cmdline))
        pid = _proc_ppid(pid)
    return details


def _psutil_ancestor_details(pid: int) -> list:
    """psutil 父进程链 → ``[(进程名, 命令行)]``（取不到命令行的平台留空）。"""
    try:
        import psutil
    except Exception:
        return []
    details: list = []
    try:
        proc = psutil.Process(pid)
        for _ in range(_MAX_ANCESTOR_DEPTH):
            try:
                name = proc.name()
            except Exception:
                break
            cmdline = ""
            if _shell_from_name(name):
                try:
                    cmdline = " ".join(proc.cmdline())
                except Exception:
                    cmdline = ""
            details.append((name, cmdline))
            parent = proc.parent()
            if parent is None:
                break
            proc = parent
    except Exception:
        return []
    return details


def _win_process_cmdline(pid: int) -> str:
    """取 Windows 进程命令行（psutil 优先，缺失时直接读 PEB）。

    Toolhelp32 快照只给进程名，无法区分 ``cmd.exe`` 是交互会话还是工具
    的 ``cmd /c`` 执行外壳；命令行必须读目标进程内存。psutil 未安装时
    （精简环境）走 kernel32/ntdll 的原生实现：``NtQueryInformationProcess``
    取 PEB 地址 → 读 ``ProcessParameters`` → 读 ``CommandLine``（UNICODE_STRING）。

    目标进程是其他用户 / 系统进程、32 位 Python 读 64 位进程（地址空间
    不兼容）或调用失败时返回空字符串（调用方按「无法判断」处理）。
    """
    try:
        import psutil

        return " ".join(psutil.Process(pid).cmdline())
    except Exception:  # pragma: no cover - psutil 探测失败（权限 / 进程已退出）
        logger.debug("psutil 读取进程命令行失败: pid=%s", pid, exc_info=True)
    if os.name != "nt":
        return ""
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        ntdll = ctypes.WinDLL("ntdll")
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.ReadProcessMemory.argtypes = (
            wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
            ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t),
        )
        kernel32.ReadProcessMemory.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        ntdll.NtQueryInformationProcess.argtypes = (
            wintypes.HANDLE, ctypes.c_uint, ctypes.c_void_p,
            ctypes.c_uint, ctypes.POINTER(ctypes.c_uint),
        )

        class _ProcessBasicInformation(ctypes.Structure):
            _fields_ = [
                ("Reserved1", ctypes.c_void_p),
                ("PebBaseAddress", ctypes.c_void_p),
                ("Reserved2", ctypes.c_void_p * 2),
                ("UniqueProcessId", ctypes.c_void_p),
                ("Reserved3", ctypes.c_void_p),
            ]

        #: PROCESS_QUERY_INFORMATION | PROCESS_VM_READ
        handle = kernel32.OpenProcess(0x0400 | 0x0010, False, int(pid))
        if not handle:
            return ""
        try:
            pbi = _ProcessBasicInformation()
            ret_len = ctypes.c_uint(0)
            status = ntdll.NtQueryInformationProcess(
                handle, 0, ctypes.byref(pbi), ctypes.sizeof(pbi), ctypes.byref(ret_len),
            )
            if status != 0 or not pbi.PebBaseAddress:
                return ""
            pointer_size = ctypes.sizeof(ctypes.c_void_p)
            #: PEB.ProcessParameters 偏移（x64/ARM64 = 0x20，x86 = 0x10）
            params_offset = 0x20 if pointer_size == 8 else 0x10
            #: RTL_USER_PROCESS_PARAMETERS.CommandLine 偏移（x64 = 0x70，x86 = 0x40）
            command_offset = 0x70 if pointer_size == 8 else 0x40

            params_ptr = ctypes.c_void_p()
            if not kernel32.ReadProcessMemory(
                handle, ctypes.c_void_p(pbi.PebBaseAddress + params_offset),
                ctypes.byref(params_ptr), pointer_size, None,
            ):
                return ""
            if not params_ptr.value:
                return ""
            raw = (ctypes.c_char * 16)()
            if not kernel32.ReadProcessMemory(
                handle, ctypes.c_void_p(params_ptr.value + command_offset),
                raw, len(raw), None,
            ):
                return ""
            length = ctypes.c_ushort.from_buffer(raw, 0).value
            buffer_offset = 8 if pointer_size == 8 else 4
            buffer_ptr = ctypes.c_void_p.from_buffer(raw, buffer_offset).value
            if not length or not buffer_ptr:
                return ""
            text = (ctypes.c_wchar * (length // 2 + 1))()
            if not kernel32.ReadProcessMemory(
                handle, ctypes.c_void_p(buffer_ptr), text, length, None,
            ):
                return ""
            return ctypes.wstring_at(text, length // 2).strip()
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return ""


def _psutil_ancestor_names(pid: int) -> list:
    try:
        import psutil
    except Exception:
        return []
    names: list = []
    try:
        proc = psutil.Process(pid)
        for _ in range(_MAX_ANCESTOR_DEPTH):
            names.append(proc.name())
            parent = proc.parent()
            if parent is None:
                break
            proc = parent
    except Exception:
        return []
    return names


_TH32CS_SNAPPROCESS = 0x00000002
_MAX_EXE_NAME = 260  # MAX_PATH


def _win_process_table() -> dict:
    """枚举 Windows 进程表：``pid → (父 pid, exe 名)``。

    Windows 没有 /proc，psutil 也不在本项目运行依赖里；此处直接调用
    kernel32 的 Toolhelp32 快照 API 拿父进程链。缺了这一步，在 PowerShell
    中启动的会话会因拿不到父进程链而回退到 ``ComSpec``（恒为 cmd.exe），
    被误判成 cmd。

    非 Windows 平台、API 不可用或调用失败时返回空表（由调用方继续回退）。
    """
    if os.name != "nt":
        return {}
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:
        return {}

    class _ProcessEntry32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_void_p),  # ULONG_PTR
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * _MAX_EXE_NAME),
        ]

    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
        kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        kernel32.Process32FirstW.argtypes = (wintypes.HANDLE, ctypes.POINTER(_ProcessEntry32W))
        kernel32.Process32FirstW.restype = wintypes.BOOL
        kernel32.Process32NextW.argtypes = (wintypes.HANDLE, ctypes.POINTER(_ProcessEntry32W))
        kernel32.Process32NextW.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL

        snapshot = kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
        invalid_handle = ctypes.c_void_p(-1).value
        if snapshot is None or snapshot == invalid_handle:
            return {}
        table: dict = {}
        try:
            entry = _ProcessEntry32W()
            entry.dwSize = ctypes.sizeof(_ProcessEntry32W)
            ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
            while ok:
                table[int(entry.th32ProcessID)] = (
                    int(entry.th32ParentProcessID),
                    entry.szExeFile,
                )
                ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
        finally:
            kernel32.CloseHandle(snapshot)
        return table
    except Exception:
        return {}


def _win_ancestor_names(pid: int) -> list:
    """沿 Windows 父进程链向上收集进程名（含起点进程本身）。

    与 ``_proc_ancestor_names`` 语义一致：链断掉（pid 不在表里 / 父 pid 为 0）
    或达到深度上限即停止，遇到环（pid 重复）也立即停止。
    """
    table = _win_process_table()
    if not table:
        return []
    names: list = []
    seen: set = set()
    for _ in range(_MAX_ANCESTOR_DEPTH):
        if not isinstance(pid, int) or pid <= 0 or pid in seen:
            break
        seen.add(pid)
        entry = table.get(pid)
        if entry is None:
            break
        parent, name = entry
        if name:
            names.append(name)
        pid = parent
    return names


def _win_ancestor_details(pid: int) -> list:
    """沿 Windows 父进程链收集 ``[(进程名, 命令行)]``（含起点进程本身）。

    进程名来自 Toolhelp32 快照（零依赖）；命令行只对 shell 候选进程经
    psutil 补充（psutil 缺失时为空，不影响父链识别）。
    """
    table = _win_process_table()
    if not table:
        return []
    details: list = []
    seen: set = set()
    for _ in range(_MAX_ANCESTOR_DEPTH):
        if not isinstance(pid, int) or pid <= 0 or pid in seen:
            break
        seen.add(pid)
        entry = table.get(pid)
        if entry is None:
            break
        parent, name = entry
        cmdline = _win_process_cmdline(pid) if name and _shell_from_name(name) else ""
        if name:
            details.append((name, cmdline))
        pid = parent
    return details


def _ancestor_details(pid: int) -> list:
    """当前平台的父进程链（``[(进程名, 命令行)]``，含起点进程本身）。"""
    # Windows 上没有 /proc，跳过（否则每次检测都要白白失败 3×N 次文件读取）。
    if os.name != "nt":
        details = _proc_ancestor_details(pid)
        if details:
            return details
    details = _psutil_ancestor_details(pid)
    if details:
        return details
    return _win_ancestor_details(pid)


def _ancestor_names(pid: int) -> list:
    return [name for name, _cmdline in _ancestor_details(pid)]


def _basename_lower(value: str) -> str:
    name = (value or "").strip().strip('"').strip("'")
    name = name.replace("\\", "/").rstrip("/")
    name = name.rsplit("/", 1)[-1]
    if name.lower().endswith(".exe"):
        name = name[:-4]
    return name.lower()


def _shell_from_name(value: str) -> str:
    base = _basename_lower(value)
    if not base:
        return ""
    aliases = _detect_table("shell_aliases", _SHELL_ALIASES)
    if base in aliases:
        return aliases[base]
    if base.startswith("git-") and base[4:] in aliases:
        return aliases[base[4:]]
    return ""


def _shell_env_candidates(env: Mapping) -> list:
    candidates: list = []
    shell = env.get("SHELL")
    if shell:
        candidates.append(shell)
    for var, name in (
        ("BASH_VERSION", "bash"),
        ("ZSH_VERSION", "zsh"),
        ("FISH_VERSION", "fish"),
        ("KSH_VERSION", "ksh"),
    ):
        if env.get(var):
            candidates.append(name)
    return candidates


#: 「非交互执行外壳」的命令行特征：工具（bash 工具 / CI / 编辑器任务）用它们
#: 包一层执行命令，父链上最近的那个 shell 不是用户所在的交互 shell。
_CMD_EXEC_RE = re.compile(r"(?:^|\s)/c(?=[\s\"]|$)", re.IGNORECASE)
_CMD_KEEP_RE = re.compile(r"(?:^|\s)/k(?=[\s\"]|$)", re.IGNORECASE)
_PS_COMMAND_RE = re.compile(
    r"(?:^|\s)-(?:command|encodedcommand|c)(?=[\s:=]|$)", re.IGNORECASE
)
_PS_NOEXIT_RE = re.compile(r"(?:^|\s)-noexit(?=[\s]|$)", re.IGNORECASE)
_POSIX_SHELL_NAMES = {
    "bash", "sh", "zsh", "fish", "dash", "ksh", "mksh", "csh", "tcsh", "ash",
}
_POSIX_COMMAND_RE = re.compile(r"(?:^|\s)-c(?=[\s\"']|$)")


def _is_launcher_shell(name: str, cmdline: str) -> bool:
    """判断一个 shell 进程是否是「非交互执行外壳」而不是用户的交互会话。

    依据命令行参数（不是进程名）：
      - ``cmd.exe /c ...``（无 ``/k``）——工具执行命令的默认外壳；
      - ``powershell.exe -Command ...`` / ``-EncodedCommand ...``（无
        ``-NoExit``）——按 PowerShell 执行命令时的外壳；
      - ``bash/sh/zsh -c ...``——POSIX 工具执行命令的外壳。

    命令行缺失时返回 False（无法判断就按交互 shell 处理，避免误跳过）。
    ``cmd /k``、``powershell -NoExit`` 是「执行后保持交互」的会话，不跳过。
    """
    base = _basename_lower(name)
    text = (cmdline or "").strip()
    if not base or not text:
        return False
    if base == "cmd":
        if _CMD_KEEP_RE.search(text):
            return False
        return bool(_CMD_EXEC_RE.search(text))
    if base in ("powershell", "pwsh"):
        if _PS_NOEXIT_RE.search(text):
            return False
        return bool(_PS_COMMAND_RE.search(text))
    if base in _POSIX_SHELL_NAMES:
        return bool(_POSIX_COMMAND_RE.search(text))
    return False


def _ancestor_entries(details) -> list:
    """父链条目归一化为 ``(name, cmdline)``（兼容旧格式的名字字符串）。"""
    entries: list = []
    for entry in details or ():
        if isinstance(entry, (tuple, list)):
            name = entry[0] if entry else ""
            cmdline = entry[1] if len(entry) > 1 else ""
        else:
            name, cmdline = entry, ""
        entries.append((name or "", cmdline or ""))
    return entries


def _detect_shell(env: Mapping, details) -> str:
    # ★ 启动方注入的真实 shell 优先：bash 工具用 cmd /c 启动子进程时，子进程
    #   父链最近的是执行外壳；环境变量里带着用户真实所在的 shell（如
    #   powershell），比进程名启发式更可靠。
    marker = env.get(SHELL_ENV_MARKER)
    if marker:
        shell = _shell_from_name(marker) or str(marker).strip()
        if shell:
            return shell
    for name, cmdline in _ancestor_entries(details):
        shell = _shell_from_name(name)
        if not shell:
            continue
        # 跳过工具自己的执行外壳，继续向上找用户所在的交互 shell。
        if _is_launcher_shell(name, cmdline):
            continue
        return shell
    for candidate in _shell_env_candidates(env):
        shell = _shell_from_name(candidate)
        if shell:
            return shell
    comspec = env.get("ComSpec") or env.get("COMSPEC")
    if comspec:
        return _shell_from_name(comspec) or "cmd"
    return ""


def _map_term_program(value: str) -> str:
    text = (value or "").strip()
    return _detect_table("term_program_map", _TERM_PROGRAM_MAP).get(text.lower(), text)


def _map_terminal_emulator(value: str) -> str:
    text = (value or "").strip()
    low = text.lower()
    if "jediterm" in low or "jetbrains" in low:
        return "JetBrains"
    return text


def _normalize_term(value: str) -> str:
    text = (value or "").strip()
    low = text.lower()
    for prefix, label in (("xterm", "xterm"), ("screen", "screen"), ("tmux", "tmux"), ("rxvt", "rxvt")):
        if low.startswith(prefix):
            return label
    return text


def _detect_terminal(env: Mapping, ancestors: list) -> str:
    for rule in _detect_table("terminal_env_rules", _TERMINAL_ENV_RULES):
        var, label = rule[0], rule[1]
        value = env.get(var)
        if not value:
            continue
        if var == "TERM_PROGRAM":
            return _map_term_program(value)
        if var == "TERMINAL_EMULATOR":
            return _map_terminal_emulator(value)
        return label
    aliases = _detect_table("terminal_process_aliases", _TERMINAL_PROCESS_ALIASES)
    for name in ancestors:
        label = aliases.get(_basename_lower(name))
        if label:
            return label
    term = env.get("TERM")
    if term and term.strip().lower() != "dumb":
        return _normalize_term(term)
    return ""


def _detect_gui_program(env: Mapping, ancestors: list) -> str:
    """检测承载当前命令行的 GUI 程序（Windows Terminal / VS Code / ...）。

    与 :func:`_detect_terminal` 的区别：GUI 检测只认**图形宿主**——
    tmux / screen / VTE 只是复用器或终端组件，跳过它们继续沿父链找真正
    承载窗口的程序；找不到图形宿主（SSH、纯控制台）时返回空字符串。
    """
    for rule in _detect_table("gui_terminal_env_rules", _GUI_TERMINAL_ENV_RULES):
        var, label = rule[0], rule[1]
        value = env.get(var)
        if not value:
            continue
        if var == "TERM_PROGRAM":
            return _map_term_program(value)
        if var == "TERMINAL_EMULATOR":
            return _map_terminal_emulator(value)
        return label
    aliases = _detect_table("gui_terminal_process_aliases", _GUI_TERMINAL_PROCESS_ALIASES)
    for name in ancestors:
        base = _basename_lower(name)
        label = aliases.get(base)
        if label:
            return label
        if base in _JETBRAINS_PROCESS_NAMES:
            return "JetBrains"
    return ""


def detect_shell(env: Mapping | None = None, ppid: int | None = None) -> str:
    """检测当前 Shell 解释器（如 bash / zsh / fish / powershell / cmd）。"""
    env = os.environ if env is None else env
    pid = os.getppid() if ppid is None else ppid
    return _detect_shell(env, _ancestor_details(pid))


def detect_terminal(env: Mapping | None = None, ppid: int | None = None) -> str:
    """检测当前终端模拟器（如 mintty / Windows Terminal / tmux / VS Code）。"""
    env = os.environ if env is None else env
    pid = os.getppid() if ppid is None else ppid
    return _detect_terminal(env, _ancestor_names(pid))


def detect_gui_program(env: Mapping | None = None, ppid: int | None = None) -> str:
    """检测承载当前命令行的 GUI 程序（如 Windows Terminal / VS Code / ConEmu）。

    只识别图形宿主：tmux / screen / VTE 等复用器或组件不算，会继续向上
    查找；SSH / 纯控制台等没有图形宿主时返回空字符串。
    """
    env = os.environ if env is None else env
    pid = os.getppid() if ppid is None else ppid
    return _detect_gui_program(env, _ancestor_names(pid))


def detect_runtime(
    env: Mapping | None = None,
    sys_platform: str | None = None,
    release: str | None = None,
) -> str:
    """检测当前运行环境（Cygwin / MSYS2 / WSL / Termux / Windows / macOS / Linux）。"""
    env = os.environ if env is None else env
    plat = (sys_platform if sys_platform is not None else sys.platform or "").lower()
    rel = release if release is not None else _platform.release()
    if plat.startswith("cygwin"):
        return "Cygwin"
    if plat.startswith("msys") or env.get("MSYSTEM"):
        return "MSYS2"
    if env.get("WSL_DISTRO_NAME") or env.get("WSL_INTEROP") or "microsoft" in (rel or "").lower():
        return "WSL"
    if env.get("TERMUX_VERSION") or "com.termux" in (env.get("PREFIX") or ""):
        return "Termux"
    if plat.startswith("win"):
        return "Windows"
    if plat.startswith("darwin"):
        return "macOS"
    if plat.startswith("linux"):
        return "Linux"
    if plat.startswith("freebsd"):
        return "FreeBSD"
    if plat.startswith("openbsd"):
        return "OpenBSD"
    if plat.startswith("netbsd"):
        return "NetBSD"
    if plat.startswith("dragonfly"):
        return "DragonFly BSD"
    if plat.startswith("sunos"):
        return "Solaris"
    if plat.startswith("aix"):
        return "AIX"
    return ""


def _format_command_line(shell: str, runtime: str) -> str:
    """命令行展示文本：Shell 解释器 +（运行环境）括号补充。

    GUI 宿主由 :func:`detect_gui_program` 单独返回，不再混入「命令行」，
    避免「cmd (Windows Terminal, Windows)」这类把终端当命令行类型的误导。
    """
    if shell:
        return f"{shell} ({runtime})" if runtime else shell
    return runtime or "unknown"


def detect_command_line(
    env: Mapping | None = None,
    ppid: int | None = None,
    sys_platform: str | None = None,
    release: str | None = None,
) -> str:
    """综合检测当前命令行类型，返回单行展示文本（Shell + 运行环境）。

    示例：``bash (Cygwin)`` / ``powershell (Windows)`` / ``zsh (Linux)``。
    承载命令行的 GUI 程序（Windows Terminal / VS Code ...）由
    :func:`detect_gui_program` 单独提供，不混入本函数的返回值。
    全部维度均无法识别时返回 ``unknown``。
    """
    env = os.environ if env is None else env
    pid = os.getppid() if ppid is None else ppid
    details = _ancestor_details(pid)
    shell = _detect_shell(env, details)
    runtime = detect_runtime(env, sys_platform, release)
    return _format_command_line(shell, runtime)
