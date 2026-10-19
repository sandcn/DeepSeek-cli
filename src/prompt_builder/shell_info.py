"""命令行环境信息 — 检测当前 Shell 解释器、终端模拟器与运行环境。

用于系统提示词「当前执行环境」章节，向模型声明当前命令行类型，使其能按
对应语法执行命令（bash / zsh / fish / powershell / cmd 等）。

检测策略：
  - Shell 解释器：优先遍历父进程链识别交互 shell——Linux 走 /proc，
    psutil 可用时走 psutil，Windows 无 psutil 时回退 kernel32 Toolhelp32
    快照（零第三方依赖，避免把 powershell 会话误判为 cmd）；
    再回退环境变量（SHELL / BASH_VERSION / ... / ComSpec）。
  - 终端模拟器：优先环境变量特征（WT_SESSION / TERM_PROGRAM / ...），
    回退父进程链（mintty / xterm / konsole ...），最后回退 TERM。
  - 运行环境：sys.platform + 环境变量识别 Cygwin / MSYS2 / WSL / Termux /
    Windows / macOS / Linux 等。
"""

from __future__ import annotations

import os
import platform as _platform
import sys
from typing import Mapping

__all__ = [
    "detect_command_line",
    "detect_shell",
    "detect_terminal",
    "detect_runtime",
]

_PROC_ROOT = "/proc"
_MAX_ANCESTOR_DEPTH = 8


def _detect_table(name: str, default):
    """取检测表（表现层数据注册表优先，缺席/非法时回退内置默认）。

    「一切皆插件」：Shell/终端检测表（shell_aliases / terminal_env_rules /
    term_program_map / terminal_process_aliases）登记在表现层数据注册表
    （``presentation_data`` → ``shell_detect`` 表），可按 Patch/Overlay
    覆盖或禁用。
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
    "wt": "Windows Terminal",
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


def _ancestor_names(pid: int) -> list:
    # Windows 上没有 /proc，跳过（否则每次检测都要白白失败 3×N 次文件读取）。
    if os.name != "nt":
        names = _proc_ancestor_names(pid)
        if names:
            return names
    names = _psutil_ancestor_names(pid)
    if names:
        return names
    return _win_ancestor_names(pid)


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


def _detect_shell(env: Mapping, ancestors: list) -> str:
    for name in ancestors:
        shell = _shell_from_name(name)
        if shell:
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


def detect_shell(env: Mapping | None = None, ppid: int | None = None) -> str:
    """检测当前 Shell 解释器（如 bash / zsh / fish / powershell / cmd）。"""
    env = os.environ if env is None else env
    pid = os.getppid() if ppid is None else ppid
    return _detect_shell(env, _ancestor_names(pid))


def detect_terminal(env: Mapping | None = None, ppid: int | None = None) -> str:
    """检测当前终端模拟器（如 mintty / Windows Terminal / tmux / VS Code）。"""
    env = os.environ if env is None else env
    pid = os.getppid() if ppid is None else ppid
    return _detect_terminal(env, _ancestor_names(pid))


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


def _format_command_line(shell: str, terminal: str, runtime: str) -> str:
    extras = [value for value in (terminal, runtime) if value]
    if shell:
        return f"{shell} ({', '.join(extras)})" if extras else shell
    return ", ".join(extras) if extras else "unknown"


def detect_command_line(
    env: Mapping | None = None,
    ppid: int | None = None,
    sys_platform: str | None = None,
    release: str | None = None,
) -> str:
    """综合检测当前命令行类型，返回单行展示文本。

    示例：``bash (mintty, Cygwin)`` / ``powershell (Windows)`` / ``zsh (tmux, Linux)``。
    全部维度均无法识别时返回 ``unknown``。
    """
    env = os.environ if env is None else env
    pid = os.getppid() if ppid is None else ppid
    ancestors = _ancestor_names(pid)
    shell = _detect_shell(env, ancestors)
    terminal = _detect_terminal(env, ancestors)
    runtime = detect_runtime(env, sys_platform, release)
    return _format_command_line(shell, terminal, runtime)
