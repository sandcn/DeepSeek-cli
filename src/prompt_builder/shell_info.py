"""命令行环境信息 — 检测当前 Shell 解释器、终端模拟器与运行环境。

用于系统提示词「当前执行环境」章节，向模型声明当前命令行类型，使其能按
对应语法执行命令（bash / zsh / fish / powershell / cmd 等）。

检测策略：
  - Shell 解释器：优先遍历父进程链（/proc，回退 psutil）识别交互 shell，
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
    except (OSError, IOError):
        return ""


def _proc_name(pid: int) -> str:
    for filename in ("exename", "comm", "cmdline"):
        text = _read_text(os.path.join(_PROC_ROOT, str(pid), filename))
        if not text:
            continue
        if filename == "cmdline":
            text = text.split("\x00", 1)[0]
        text = text.strip()
        if text:
            return text
    return ""


def _proc_ppid(pid: int) -> int:
    text = _read_text(os.path.join(_PROC_ROOT, str(pid), "ppid")).strip()
    if text.lstrip("-").isdigit():
        return int(text)
    for line in _read_text(os.path.join(_PROC_ROOT, str(pid), "status")).splitlines():
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


def _ancestor_names(pid: int) -> list:
    names = _proc_ancestor_names(pid)
    if names:
        return names
    return _psutil_ancestor_names(pid)


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
    if base in _SHELL_ALIASES:
        return _SHELL_ALIASES[base]
    if base.startswith("git-") and base[4:] in _SHELL_ALIASES:
        return _SHELL_ALIASES[base[4:]]
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
    return _TERM_PROGRAM_MAP.get(text.lower(), text)


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
    for var, label in _TERMINAL_ENV_RULES:
        value = env.get(var)
        if not value:
            continue
        if var == "TERM_PROGRAM":
            return _map_term_program(value)
        if var == "TERMINAL_EMULATOR":
            return _map_terminal_emulator(value)
        return label
    for name in ancestors:
        label = _TERMINAL_PROCESS_ALIASES.get(_basename_lower(name))
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
    if plat.startswith("win") or os.name == "nt":
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
