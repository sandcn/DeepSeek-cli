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
  - 版本号：Shell 解释器与 GUI 宿主都尽量带版本号——优先环境变量
    （BASH_VERSION / TERM_PROGRAM_VERSION ...），Windows / Cygwin 读可执行
    文件的版本资源（version.dll），其余平台对已知终端程序探测 ``--version``。
    系统提示词「当前执行环境」的「命令行」「GUI 程序」两行即用带版本号的
    展示文本（``bash 5.2.21 (Cygwin)`` / ``mintty 2.9.6``）。
"""

from __future__ import annotations

import ctypes
import logging
import os
import platform as _platform
import re
import struct
import subprocess
import sys
from typing import Mapping

__all__ = [
    "SHELL_ENV_MARKER",
    "detect_command_line",
    "detect_gui_program",
    "detect_gui_program_version",
    "detect_shell",
    "detect_shell_version",
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

#: Shell 版本号来自哪些环境变量（交互 shell 自行导出；cmd / powershell 不
#: 导出，改读可执行文件的版本资源）。数据注册表
#: ``shell_detect → shell_version_env`` 可覆盖。
_SHELL_VERSION_ENV = {
    "bash": ("BASH_VERSION",),
    "zsh": ("ZSH_VERSION",),
    "fish": ("FISH_VERSION",),
    "ksh": ("KSH_VERSION",),
    # sh 常是 bash / zsh / ksh 的 POSIX 模式：有对应变量就取它的版本
    "sh": ("BASH_VERSION", "ZSH_VERSION", "KSH_VERSION"),
}

#: GUI 宿主版本环境变量 ``(变量名, 取值方式)``：``text`` 直接解析版本号，
#: ``konsole`` 解码 KDE 的 ``220400`` → ``22.04.00``。
_GUI_VERSION_ENV_RULES = (
    ("TERM_PROGRAM_VERSION", "text"),
    ("KONSOLE_VERSION", "konsole"),
    ("TERMUX_VERSION", "text"),
)

#: 没有版本资源时「询问」终端自身版本的参数。只收**已知支持该参数、不会因
#: 探测而弹出窗口**的程序；表外的 GUI 程序不做探测（宁可没有版本号，也不在
#: 构建提示词时拉起 GUI）。数据注册表 ``shell_detect → gui_version_args`` 可覆盖。
_GUI_VERSION_ARGS = {
    "mintty": ("--version",),
    "gnome-terminal": ("--version",),
    "gnome-terminal-server": ("--version",),
    "kgx": ("--version",),
    "konsole": ("--version",),
    "kitty": ("--version",),
    "alacritty": ("--version",),
    "xfce4-terminal": ("--version",),
    "lxterminal": ("--version",),
    "mate-terminal": ("--version",),
    "qterminal": ("--version",),
    "terminator": ("--version",),
    "tilix": ("--version",),
    "terminology": ("--version",),
    "rio": ("--version",),
    "contour": ("--version",),
    "ghostty": ("--version",),
    "wezterm-gui": ("--version",),
    "xterm": ("-version",),
    "uxterm": ("-version",),
}

#: 版本号文本：至少一个点，避免把年份 / 构建序号之类当成版本号。
_VERSION_TEXT_RE = re.compile(r"\d+(?:\.\d+)+")
#: ``VS_FIXEDFILEINFO`` 结构长度（13 × DWORD）；不足即视为无效资源。
_FIXED_FILE_INFO_SIZE = 52
#: ``--version`` 探测超时（秒）：只是取版本号，不能拖慢提示词构建。
_PROBE_TIMEOUT = 2.0

#: 可执行文件版本号缓存（``路径 → 版本号``）：一次会话只读一次。
_EXE_VERSION_CACHE: dict = {}
#: ``--version`` 探测缓存（``(路径, 参数) → 版本号``）。
_PROBE_VERSION_CACHE: dict = {}
#: ``version.dll`` 句柄与「是否已尝试加载」标志（非 Windows / 加载失败时为空）。
_VERSION_DLL = None
_VERSION_DLL_LOADED = False



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


def _proc_ancestor_pids(pid: int) -> list:
    """``/proc`` 父进程链 → ``[(pid, 进程名)]``（含起点进程本身）。"""
    entries: list = []
    seen: set = set()
    for _ in range(_MAX_ANCESTOR_DEPTH):
        if not isinstance(pid, int) or pid <= 1 or pid in seen:
            break
        seen.add(pid)
        name = _proc_name(pid)
        if not name:
            break
        entries.append((pid, name))
        pid = _proc_ppid(pid)
    return entries


def _psutil_ancestor_pids(pid: int) -> list:
    """psutil 父进程链 → ``[(pid, 进程名)]``（取不到的进程跳过）。"""
    try:
        import psutil
    except Exception:
        return []
    entries: list = []
    try:
        proc = psutil.Process(pid)
        for _ in range(_MAX_ANCESTOR_DEPTH):
            entries.append((proc.pid, proc.name()))
            parent = proc.parent()
            if parent is None:
                break
            proc = parent
    except Exception:
        return []
    return entries


def _win_ancestor_pids(pid: int) -> list:
    """Windows 父进程链 → ``[(pid, 进程名)]``（Toolhelp32 快照）。"""
    table = _win_process_table()
    if not table:
        return []
    entries: list = []
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
            entries.append((pid, name))
        pid = parent
    return entries


def _ancestor_pids(pid: int) -> list:
    """当前平台父进程链 ``[(pid, 进程名)]``（含起点进程本身）。

    版本号需要定位到**具体进程**（读它的可执行文件版本资源 / 询问版本），
    因此与只取名字的 :func:`_ancestor_names` 分开：/proc → psutil → 原生
    进程表，语义与 ``_ancestor_details`` 保持一致。
    """
    if os.name != "nt":
        entries = _proc_ancestor_pids(pid)
        if entries:
            return entries
    entries = _psutil_ancestor_pids(pid)
    if entries:
        return entries
    return _win_ancestor_pids(pid)


def _win_process_image_path(pid: int) -> str:
    """Windows 进程可执行文件完整路径（``QueryFullProcessImageNameW``）。

    Toolhelp32 快照只给进程名，读版本资源需要完整路径；取不到（权限不足 /
    进程已退出 / 32 位 Python 读 64 位进程）返回空字符串。
    """
    if os.name != "nt":
        return ""
    try:
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.QueryFullProcessImageNameW.argtypes = (
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        )
        kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        #: PROCESS_QUERY_LIMITED_INFORMATION
        handle = kernel32.OpenProcess(0x1000, False, int(pid))
        if not handle:
            return ""
        try:
            buffer = ctypes.create_unicode_buffer(32768)
            size = wintypes.DWORD(len(buffer))
            if not kernel32.QueryFullProcessImageNameW(
                handle, 0, buffer, ctypes.byref(size),
            ):
                return ""
            return buffer.value
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        logger.debug("读取进程可执行文件路径失败: pid=%s", pid, exc_info=True)
        return ""


def _process_version_path(pid: int) -> str:
    """进程可执行文件的 Windows 路径（读 PE 版本资源用；取不到返回空串）。

    Cygwin / MSYS2 的 ``/proc/<pid>/winexename`` 直接给 Windows 路径（原生
    进程与 Cygwin 程序都有）；Windows 上用 ``QueryFullProcessImageNameW``。
    """
    win_path = _read_text(_proc_path(pid, "winexename")).strip()
    if win_path:
        return win_path
    if os.name == "nt":
        return _win_process_image_path(pid)
    return ""


def _process_exe_path(pid: int) -> str:
    """进程可执行文件路径（启动 ``--version`` 探测用；取不到返回空串）。"""
    try:
        link = os.readlink(_proc_path(pid, "exe"))
    except OSError:
        link = ""
    if link:
        return link
    name = _read_text(_proc_path(pid, "exename")).strip()
    if name:
        return name
    if os.name == "nt":
        return _win_process_image_path(pid)
    return ""


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


def _gui_alias_for(name: str) -> str:
    """进程名 → GUI 宿主展示名（tmux / screen 等复用器不在表内，返回空串）。"""
    base = _basename_lower(name)
    aliases = _detect_table("gui_terminal_process_aliases", _GUI_TERMINAL_PROCESS_ALIASES)
    label = aliases.get(base)
    if label:
        return label
    if base in _JETBRAINS_PROCESS_NAMES:
        return "JetBrains"
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
    for name in ancestors:
        label = _gui_alias_for(name)
        if label:
            return label
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


def detect_gui_program(
    env: Mapping | None = None,
    ppid: int | None = None,
    with_version: bool = False,
) -> str:
    """检测承载当前命令行的 GUI 程序（如 Windows Terminal / VS Code / ConEmu）。

    只识别图形宿主：tmux / screen / VTE 等复用器或组件不算，会继续向上
    查找；SSH / 纯控制台等没有图形宿主时返回空字符串。

    Args:
        with_version: 是否附带版本号（``mintty 2.9.6``）；默认只给程序名，
            保持既有调用方输出不变。版本号取不到时只返回程序名。
    """
    env = os.environ if env is None else env
    pid = os.getppid() if ppid is None else ppid
    label = _detect_gui_program(env, _ancestor_names(pid))
    if not with_version:
        return label
    return _format_gui_program(label, _resolve_gui_version(label, env, pid))


def detect_gui_program_version(
    env: Mapping | None = None,
    ppid: int | None = None,
) -> str:
    """检测承载当前命令行的 GUI 程序版本号（取不到返回空字符串）。"""
    env = os.environ if env is None else env
    pid = os.getppid() if ppid is None else ppid
    label = _detect_gui_program(env, _ancestor_names(pid))
    return _resolve_gui_version(label, env, pid)


def detect_shell_version(
    env: Mapping | None = None,
    ppid: int | None = None,
) -> str:
    """检测当前 Shell 解释器的版本号（取不到返回空字符串）。"""
    env = os.environ if env is None else env
    pid = os.getppid() if ppid is None else ppid
    return _resolve_shell_version(_detect_shell(env, _ancestor_details(pid)), env, pid)


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


def _trim_version(text: str) -> str:
    """去掉 PE 版本号尾部只有 0 的第四段（``2.9.6.0`` → ``2.9.6``）。

    只针对 4 段的版本号：``3.6.0`` / ``6.1.0`` 的末段 0 是真实版本位，保留。
    """
    parts = text.split(".")
    if len(parts) == 4 and parts[-1] == "0":
        parts.pop()
    return ".".join(parts)


def _normalize_version(text: str) -> str:
    """从版本文本里提取 ``1.2.3`` 形式的版本号（没有版本号返回空串）。

    例：``5.2.21(1)-release`` → ``5.2.21``；
    ``10.0.26100.8875 (WinBuild.160101.0800)`` → ``10.0.26100.8875``。
    """
    match = _VERSION_TEXT_RE.search(text or "")
    if not match:
        return ""
    return _trim_version(match.group(0))


def _decode_konsole_version(value: str) -> str:
    """解码 ``KONSOLE_VERSION``（KDE 的整数编码，``220400`` → ``22.04.00``）。"""
    digits = re.sub(r"\D", "", value or "")
    if len(digits) < 5:
        return _normalize_version(value)
    number = int(digits)
    return f"{number // 10000}.{number // 100 % 100:02d}.{number % 100:02d}"


def _version_dll():
    """加载 ``version.dll``（Windows / Cygwin）；不可用时返回 ``None``。

    Cygwin 的 ctypes 没有 ``WinDLL``，且 ``wintypes`` 的 ``DWORD`` 是 C
    ``long``（Cygwin 为 8 字节），所以这里显式声明 32 位类型——否则
    ``GetFileVersionInfoSizeW`` 的返回值与 ``VS_FIXEDFILEINFO`` 布局全部错位。
    """
    global _VERSION_DLL, _VERSION_DLL_LOADED
    if _VERSION_DLL_LOADED:
        return _VERSION_DLL
    _VERSION_DLL_LOADED = True
    loader = getattr(ctypes, "WinDLL", None) or getattr(ctypes, "CDLL", None)
    if loader is None:  # pragma: no cover - 所有平台都有 CDLL
        return None
    try:
        lib = loader("version.dll")
        dword = ctypes.c_uint32
        lib.GetFileVersionInfoSizeW.argtypes = (ctypes.c_wchar_p, ctypes.POINTER(dword))
        lib.GetFileVersionInfoSizeW.restype = dword
        lib.GetFileVersionInfoW.argtypes = (ctypes.c_wchar_p, dword, dword, ctypes.c_void_p)
        lib.GetFileVersionInfoW.restype = ctypes.c_int
        lib.VerQueryValueW.argtypes = (
            ctypes.c_void_p, ctypes.c_wchar_p,
            ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_uint),
        )
        lib.VerQueryValueW.restype = ctypes.c_int
    except (OSError, AttributeError):
        return None
    _VERSION_DLL = lib
    return lib


def _ver_query(lib, block, sub: str):
    """``VerQueryValueW`` → ``(指针地址, 长度)``；查询失败返回 ``None``。"""
    pointer = ctypes.c_void_p()
    length = ctypes.c_uint()
    if not lib.VerQueryValueW(block, sub, ctypes.byref(pointer), ctypes.byref(length)):
        return None
    return pointer.value, length.value


def _query_string_version(lib, block) -> str:
    """版本资源里的 ``FileVersion`` / ``ProductVersion`` 字符串（优先英文翻译）。"""
    translation = _ver_query(lib, block, "\\VarFileInfo\\Translation")
    if not translation:
        return ""
    pointer, length = translation
    raw = ctypes.string_at(pointer, length)
    pairs = [
        struct.unpack("<HH", raw[offset:offset + 4])
        for offset in range(0, length - 3, 4)
    ]
    # 英文 / 中性翻译优先（其余保持资源里的顺序）
    pairs.sort(key=lambda item: item[0] not in (0x0409, 0x0000))
    for lang, codepage in pairs:
        for key in ("FileVersion", "ProductVersion"):
            found = _ver_query(lib, block, f"\\StringFileInfo\\{lang:04x}{codepage:04x}\\{key}")
            if not found:
                continue
            value_pointer, value_length = found
            version = _normalize_version(ctypes.wstring_at(value_pointer, value_length))
            if version:
                return version
    return ""


def _query_fixed_version(lib, block) -> str:
    """``VS_FIXEDFILEINFO`` 里的文件版本（字符串资源缺失时的兜底）。"""
    found = _ver_query(lib, block, "\\")
    if not found:
        return ""
    pointer, length = found
    if length < _FIXED_FILE_INFO_SIZE:
        return ""
    raw = ctypes.string_at(pointer, _FIXED_FILE_INFO_SIZE)
    file_version_ms, file_version_ls = struct.unpack_from("<II", raw, 16)
    parts = (
        file_version_ms >> 16, file_version_ms & 0xFFFF,
        file_version_ls >> 16, file_version_ls & 0xFFFF,
    )
    return _trim_version(".".join(str(part) for part in parts))


def _read_exe_file_version(path: str) -> str:
    """读 PE 可执行文件的版本资源（非 Windows / 读取失败返回空串）。"""
    lib = _version_dll()
    if lib is None or not path:
        return ""
    try:
        size = lib.GetFileVersionInfoSizeW(path, None)
        if not size:
            return ""
        buffer = ctypes.create_string_buffer(size)
        block = ctypes.cast(buffer, ctypes.c_void_p)
        if not lib.GetFileVersionInfoW(path, 0, size, block):
            return ""
        return _query_string_version(lib, block) or _query_fixed_version(lib, block)
    except Exception:
        logger.debug("读取可执行文件版本资源失败: %s", path, exc_info=True)
        return ""


def _exe_file_version(path: str) -> str:
    """可执行文件版本号（按路径缓存：一次会话只读一次）。"""
    if not path:
        return ""
    if path not in _EXE_VERSION_CACHE:
        _EXE_VERSION_CACHE[path] = _read_exe_file_version(path)
    return _EXE_VERSION_CACHE[path]


def _probe_program_version(exe_path: str, name: str) -> str:
    """询问程序自身版本（``<exe> --version``），结果按 ``(路径, 参数)`` 缓存。

    只在非 Windows 平台探测：Windows / Cygwin 已有版本资源可读；且只探测
    ``gui_version_args`` 表内**已知支持该参数**的终端程序——表外程序一律不
    探测，避免在构建提示词时拉起 GUI 窗口。
    """
    if not exe_path or os.name == "nt":
        return ""
    args = _detect_table("gui_version_args", _GUI_VERSION_ARGS).get(name)
    if not args:
        return ""
    key = (exe_path, tuple(args))
    if key in _PROBE_VERSION_CACHE:
        return _PROBE_VERSION_CACHE[key]
    version = ""
    try:
        completed = subprocess.run(
            [exe_path, *args],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=_PROBE_TIMEOUT,
            check=False,
        )
        version = _normalize_version((completed.stdout or b"").decode("utf-8", "replace"))
    except (OSError, subprocess.SubprocessError):
        version = ""
    _PROBE_VERSION_CACHE[key] = version
    return version


def _resolve_shell_version(shell: str, env: Mapping, pid: int) -> str:
    """Shell 解释器版本号：版本环境变量 → 可执行文件版本资源。"""
    if not shell:
        return ""
    variables = _detect_table("shell_version_env", _SHELL_VERSION_ENV).get(shell) or ()
    if isinstance(variables, str):
        variables = (variables,)
    for var in variables:
        version = _normalize_version(str(env.get(var) or ""))
        if version:
            return version
    # cmd / powershell 不导出 shell 自身的版本变量：读可执行文件版本资源。
    for entry_pid, name in _ancestor_pids(pid):
        if _shell_from_name(name) != shell:
            continue
        version = _exe_file_version(_process_version_path(entry_pid))
        if version:
            return version
    return ""


def _resolve_gui_version(label: str, env: Mapping, pid: int) -> str:
    """GUI 宿主版本号：环境变量 → 可执行文件版本资源 → ``--version`` 探测。"""
    if not label:
        return ""
    for var, kind in _detect_table("gui_version_env_rules", _GUI_VERSION_ENV_RULES):
        value = str(env.get(var) or "").strip()
        if not value:
            continue
        if kind == "konsole":
            version = _decode_konsole_version(value)
        else:
            version = _normalize_version(value)
        if version:
            return version
    for entry_pid, name in _ancestor_pids(pid):
        if _gui_alias_for(name) != label:
            continue
        version = _exe_file_version(_process_version_path(entry_pid))
        if version:
            return version
        version = _probe_program_version(_process_exe_path(entry_pid), _basename_lower(name))
        if version:
            return version
    return ""


def _format_gui_program(label: str, version: str) -> str:
    """GUI 宿主展示文本：``mintty 2.9.6``（版本号取不到时只有程序名）。"""
    if not label:
        return ""
    return f"{label} {version}" if version else label


def _runtime_version(runtime: str, release: str | None = None) -> str:
    """运行环境版本号（Windows 构建号 / macOS 产品版本 / 其余取内核发布号）。

    仅用于「Shell 版本取不到」时给「命令行」行补一个版本号，避免输出
    ``bash (Cygwin)`` 这种没有版本号的行。
    """
    if not runtime:
        return ""
    if runtime == "Windows":
        return _normalize_version(_platform.version())
    if runtime == "macOS":
        version = _normalize_version(_platform.mac_ver()[0])
        if version:
            return version
    return _normalize_version(release if release is not None else _platform.release())


def _format_command_line(
    shell: str,
    runtime: str,
    version: str = "",
    runtime_version: str = "",
) -> str:
    """命令行展示文本：Shell 解释器 + 版本号 +（运行环境）括号补充。

    「命令行」始终带版本号：优先 Shell 版本（``bash 5.2.21 (Cygwin)``）；
    Shell 版本取不到时用运行环境版本（``bash (Cygwin 3.5.4)``），保证模型
    能区分同名 Shell 的不同版本。

    GUI 宿主由 :func:`detect_gui_program` 单独返回，不混入「命令行」，
    避免「cmd (Windows Terminal, Windows)」这类把终端当命令行类型的误导。
    """
    if shell:
        name = f"{shell} {version}" if version else shell
        if not runtime:
            return name
        if version:
            return f"{name} ({runtime})"
        tag = f"{runtime} {runtime_version}" if runtime_version else runtime
        return f"{name} ({tag})"
    if runtime:
        return f"{runtime} {runtime_version}" if runtime_version else runtime
    return "unknown"


def detect_command_line(
    env: Mapping | None = None,
    ppid: int | None = None,
    sys_platform: str | None = None,
    release: str | None = None,
    with_version: bool = False,
) -> str:
    """综合检测当前命令行类型，返回单行展示文本（Shell + 版本 + 运行环境）。

    示例：``bash 5.2.21 (Cygwin)`` / ``powershell 10.0.19041.1 (Windows)`` /
    ``zsh (Linux)``。承载命令行的 GUI 程序（Windows Terminal / VS Code ...）
    由 :func:`detect_gui_program` 单独提供，不混入本函数的返回值。
    全部维度均无法识别时返回 ``unknown``。

    Args:
        with_version: 是否附带版本号；默认不带（``bash (Cygwin)``），保持既有
            调用方与测试的输出不变。系统提示词「当前执行环境」用
            ``with_version=True``。
    """
    env = os.environ if env is None else env
    pid = os.getppid() if ppid is None else ppid
    details = _ancestor_details(pid)
    shell = _detect_shell(env, details)
    runtime = detect_runtime(env, sys_platform, release)
    if not with_version:
        return _format_command_line(shell, runtime)
    shell_version = _resolve_shell_version(shell, env, pid)
    runtime_version = "" if shell_version else _runtime_version(runtime, release)
    return _format_command_line(shell, runtime, shell_version, runtime_version)
