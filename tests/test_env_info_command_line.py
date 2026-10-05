"""命令行环境信息（shell_info / env_info）单元测试。

覆盖：
  - 名称归一化与 shell 别名解析
  - /proc 父进程链解析与 psutil 回退
  - Shell 检测（进程链 / SHELL / BASH_VERSION / ComSpec）
  - 终端模拟器检测（环境变量 / 进程链 / TERM 回退）
  - 运行环境检测（Cygwin / MSYS2 / WSL / Termux / Windows / macOS / Linux）
  - detect_command_line 单行组装
  - build_environment_info 注入「- 命令行:」行
"""

from __future__ import annotations

import pytest

import src.prompt_builder.env_info as ei
import src.prompt_builder.shell_info as si


# ── 名称归一化 ────────────────────────────────────────────

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("/usr/bin/bash", "bash"),
        ("/bin/zsh", "zsh"),
        (r"C:\WINDOWS\system32\cmd.exe", "cmd"),
        ("/usr/bin/mintty", "mintty"),
        ('"C:\\Program Files\\PowerShell\\7\\pwsh.exe"', "pwsh"),
        ("/usr/bin/", "bin"),
        ("", ""),
    ],
)
def test_basename_lower(raw, expected):
    assert si._basename_lower(raw) == expected


def test_shell_from_name_variants():
    assert si._shell_from_name("/usr/bin/bash") == "bash"
    assert si._shell_from_name("/usr/bin/git-bash") == "bash"
    assert si._shell_from_name(r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe") == "powershell"
    assert si._shell_from_name("/bin/nu") == "nushell"
    assert si._shell_from_name("/usr/bin/notashell") == ""


# ── 父进程链 ─────────────────────────────────────────────

def test_proc_ancestor_names_reads_chain(monkeypatch):
    tree = {
        "/proc/10/exename": "/usr/bin/python3.9",
        "/proc/10/ppid": "20",
        "/proc/20/exename": "/usr/bin/bash",
        "/proc/20/ppid": "30",
        "/proc/30/exename": "/usr/bin/mintty",
        "/proc/30/ppid": "1",
    }
    monkeypatch.setattr(si, "_read_text", lambda path: tree.get(path, ""))
    assert si._proc_ancestor_names(10) == [
        "/usr/bin/python3.9",
        "/usr/bin/bash",
        "/usr/bin/mintty",
    ]


def test_proc_ancestor_names_linux_comm_fallback(monkeypatch):
    tree = {
        "/proc/50/comm": "bash\n",
        "/proc/50/status": "Name:\tbash\nPPid:\t1\n",
    }
    monkeypatch.setattr(si, "_read_text", lambda path: tree.get(path, ""))
    assert si._proc_ancestor_names(50) == ["bash"]


def test_ancestor_names_psutil_fallback(monkeypatch):
    monkeypatch.setattr(si, "_proc_ancestor_names", lambda pid: [])
    monkeypatch.setattr(si, "_psutil_ancestor_names", lambda pid: ["bash"])
    assert si._ancestor_names(1) == ["bash"]


# ── Shell 检测 ───────────────────────────────────────────

def test_detect_shell_from_process_chain(monkeypatch):
    monkeypatch.setattr(si, "_ancestor_names", lambda pid: ["/usr/bin/python3.9", "/usr/bin/bash", "/usr/bin/mintty"])
    assert si.detect_shell(env={}, ppid=10) == "bash"


def test_detect_shell_env_shell(monkeypatch):
    monkeypatch.setattr(si, "_ancestor_names", lambda pid: [])
    assert si.detect_shell(env={"SHELL": "/bin/zsh"}, ppid=10) == "zsh"


def test_detect_shell_env_version_marker(monkeypatch):
    monkeypatch.setattr(si, "_ancestor_names", lambda pid: [])
    assert si.detect_shell(env={"BASH_VERSION": "5.2.15"}, ppid=10) == "bash"
    assert si.detect_shell(env={"FISH_VERSION": "3.6.0"}, ppid=10) == "fish"


def test_detect_shell_comspec_fallback(monkeypatch):
    monkeypatch.setattr(si, "_ancestor_names", lambda pid: [])
    assert si.detect_shell(env={"ComSpec": r"C:\WINDOWS\system32\cmd.exe"}, ppid=10) == "cmd"


def test_detect_shell_unknown(monkeypatch):
    monkeypatch.setattr(si, "_ancestor_names", lambda pid: [])
    assert si.detect_shell(env={}, ppid=10) == ""


# ── 终端检测 ─────────────────────────────────────────────

def test_detect_terminal_windows_terminal(monkeypatch):
    monkeypatch.setattr(si, "_ancestor_names", lambda pid: [])
    assert si.detect_terminal(env={"WT_SESSION": "abc"}, ppid=1) == "Windows Terminal"


def test_detect_terminal_term_program_map(monkeypatch):
    monkeypatch.setattr(si, "_ancestor_names", lambda pid: [])
    assert si.detect_terminal(env={"TERM_PROGRAM": "vscode"}, ppid=1) == "VS Code"
    assert si.detect_terminal(env={"TERM_PROGRAM": "iTerm.app"}, ppid=1) == "iTerm2"
    assert si.detect_terminal(env={"TERM_PROGRAM": "unknownterm"}, ppid=1) == "unknownterm"


def test_detect_terminal_jetbrains(monkeypatch):
    monkeypatch.setattr(si, "_ancestor_names", lambda pid: [])
    assert si.detect_terminal(env={"TERMINAL_EMULATOR": "JetBrains-JediTerm"}, ppid=1) == "JetBrains"


def test_detect_terminal_multiplexer(monkeypatch):
    monkeypatch.setattr(si, "_ancestor_names", lambda pid: [])
    assert si.detect_terminal(env={"TMUX": "/tmp/tmux-1000/default,1,0"}, ppid=1) == "tmux"
    assert si.detect_terminal(env={"STY": "1234.pts-0.host"}, ppid=1) == "screen"


def test_detect_terminal_process_chain(monkeypatch):
    monkeypatch.setattr(si, "_ancestor_names", lambda pid: ["/usr/bin/bash", "/usr/bin/mintty"])
    assert si.detect_terminal(env={}, ppid=1) == "mintty"


def test_detect_terminal_term_fallback(monkeypatch):
    monkeypatch.setattr(si, "_ancestor_names", lambda pid: [])
    assert si.detect_terminal(env={"TERM": "xterm-256color"}, ppid=1) == "xterm"
    assert si.detect_terminal(env={"TERM": "dumb"}, ppid=1) == ""
    assert si.detect_terminal(env={}, ppid=1) == ""


# ── 运行环境检测 ─────────────────────────────────────────

@pytest.mark.parametrize(
    "sys_platform,release,env,expected",
    [
        ("cygwin", "CYGWIN_NT-10.0", {}, "Cygwin"),
        ("win32", "10", {"MSYSTEM": "MINGW64"}, "MSYS2"),
        ("msys", "3.5", {}, "MSYS2"),
        ("linux", "5.15.0", {"WSL_DISTRO_NAME": "Ubuntu"}, "WSL"),
        ("linux", "5.15.0-microsoft-standard-WSL2", {}, "WSL"),
        ("linux", "5.15.0", {"TERMUX_VERSION": "0.118"}, "Termux"),
        ("linux", "5.15.0", {"PREFIX": "/data/data/com.termux/files/usr"}, "Termux"),
        ("win32", "10", {}, "Windows"),
        ("darwin", "23.0.0", {}, "macOS"),
        ("linux", "6.1.0", {}, "Linux"),
        ("freebsd13", "13.2", {}, "FreeBSD"),
        ("netbsd9", "9.3", {}, "NetBSD"),
        ("plan9", "plan9", {}, ""),
    ],
)
def test_detect_runtime(sys_platform, release, env, expected):
    assert si.detect_runtime(env=env, sys_platform=sys_platform, release=release) == expected


# ── detect_command_line 组装 ─────────────────────────────

def test_format_command_line():
    assert si._format_command_line("bash", "mintty", "Cygwin") == "bash (mintty, Cygwin)"
    assert si._format_command_line("bash", "", "Cygwin") == "bash (Cygwin)"
    assert si._format_command_line("bash", "", "") == "bash"
    assert si._format_command_line("", "mintty", "") == "mintty"
    assert si._format_command_line("", "", "") == "unknown"


def test_detect_command_line_full(monkeypatch):
    monkeypatch.setattr(si, "_ancestor_names", lambda pid: ["/usr/bin/bash", "/usr/bin/mintty"])
    out = si.detect_command_line(env={}, ppid=1, sys_platform="cygwin", release="CYGWIN_NT-10.0")
    assert out == "bash (mintty, Cygwin)"


def test_detect_command_line_runtime_only(monkeypatch):
    monkeypatch.setattr(si, "_ancestor_names", lambda pid: [])
    out = si.detect_command_line(env={}, ppid=1, sys_platform="cygwin", release="CYGWIN_NT-10.0")
    assert out == "Cygwin"


def test_detect_command_line_unknown(monkeypatch):
    monkeypatch.setattr(si, "_ancestor_names", lambda pid: [])
    out = si.detect_command_line(env={}, ppid=1, sys_platform="plan9", release="plan9")
    assert out == "unknown"


# ── build_environment_info 集成 ──────────────────────────

def test_build_environment_info_contains_command_line(monkeypatch):
    monkeypatch.setattr(ei, "detect_command_line", lambda: "bash (mintty, Cygwin)")
    text = ei.build_environment_info("/tmp/work")
    assert text.startswith("# 当前执行环境\n")
    assert "- 工作目录: /tmp/work" in text
    assert "- 命令行: bash (mintty, Cygwin)" in text
    assert text.endswith("\n")


def test_build_environment_info_line_order(monkeypatch):
    monkeypatch.setattr(ei, "detect_command_line", lambda: "bash (mintty, Cygwin)")
    lines = ei.build_environment_info("/tmp/work").splitlines()
    cwd_idx = next(i for i, line in enumerate(lines) if line.startswith("- 工作目录:"))
    cmd_idx = next(i for i, line in enumerate(lines) if line.startswith("- 命令行:"))
    assert cmd_idx == cwd_idx + 1
