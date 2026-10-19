"""命令行环境信息（shell_info / env_info）单元测试。

覆盖：
  - 名称归一化与 shell 别名解析
  - /proc 父进程链解析与 psutil 回退
  - Windows 原生父进程链（Toolhelp32 快照，无 psutil 时识别 powershell）
  - Shell 检测（进程链 / SHELL / BASH_VERSION / ComSpec）
  - 终端模拟器检测（环境变量 / 进程链 / TERM 回退）
  - 运行环境检测（Cygwin / MSYS2 / WSL / Termux / Windows / macOS / Linux）
  - detect_command_line 单行组装
  - build_environment_info 注入「- 命令行:」行
  - build_environment_info 不包含 Python 版本信息
  - build_work_md 的 cwd 解析（省略时回退进程工作目录）与缺失文件兜底
"""

from __future__ import annotations

import os
from types import SimpleNamespace

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


def test_proc_path_uses_posix_separator(monkeypatch):
    """Windows 主机上 os.path.join 会拼出 \\proc\\...，/proc 路径必须始终用正斜杠。"""
    seen: list = []
    monkeypatch.setattr(si, "_read_text", lambda path: seen.append(path) or "")
    si._proc_name(123)
    si._proc_ppid(123)
    assert "/proc/123/exename" in seen
    assert "/proc/123/ppid" in seen
    assert all(path.startswith("/proc/") for path in seen)


def test_ancestor_names_psutil_fallback(monkeypatch):
    monkeypatch.setattr(si, "_proc_ancestor_names", lambda pid: [])
    monkeypatch.setattr(si, "_psutil_ancestor_names", lambda pid: ["bash"])
    assert si._ancestor_names(1) == ["bash"]


def test_ancestor_names_windows_native_fallback(monkeypatch):
    """无 /proc、无 psutil 时（Windows 默认状态）改用原生进程表。"""
    monkeypatch.setattr(si, "_proc_ancestor_names", lambda pid: [])
    monkeypatch.setattr(si, "_psutil_ancestor_names", lambda pid: [])
    monkeypatch.setattr(
        si, "_win_ancestor_names",
        lambda pid: ["python.exe", "powershell.exe", "WindowsTerminal.exe"],
    )
    assert si._ancestor_names(10) == ["python.exe", "powershell.exe", "WindowsTerminal.exe"]


# ── Windows 原生父进程链 ─────────────────────────────────

def test_win_ancestor_names_builds_chain(monkeypatch):
    table = {
        10: (20, "python.exe"),
        20: (30, "powershell.exe"),
        30: (0, "WindowsTerminal.exe"),
    }
    monkeypatch.setattr(si, "_win_process_table", lambda: table)
    assert si._win_ancestor_names(10) == [
        "python.exe",
        "powershell.exe",
        "WindowsTerminal.exe",
    ]


def test_win_ancestor_names_breaks_on_missing_parent(monkeypatch):
    monkeypatch.setattr(si, "_win_process_table", lambda: {10: (99, "python.exe")})
    assert si._win_ancestor_names(10) == ["python.exe"]


def test_win_ancestor_names_stops_on_cycle(monkeypatch):
    table = {10: (11, "python.exe"), 11: (10, "pwsh.exe")}
    monkeypatch.setattr(si, "_win_process_table", lambda: table)
    assert si._win_ancestor_names(10) == ["python.exe", "pwsh.exe"]


def test_win_ancestor_names_empty_table(monkeypatch):
    monkeypatch.setattr(si, "_win_process_table", lambda: {})
    assert si._win_ancestor_names(10) == []
    assert si._win_ancestor_names(0) == []


def test_win_ancestor_names_depth_limit(monkeypatch):
    table = {pid: (pid + 1, f"p{pid}.exe") for pid in range(1, 50)}
    monkeypatch.setattr(si, "_win_process_table", lambda: table)
    assert len(si._win_ancestor_names(1)) == si._MAX_ANCESTOR_DEPTH


def test_win_process_table_non_windows(monkeypatch):
    monkeypatch.setattr(si, "os", SimpleNamespace(name="posix"))
    assert si._win_process_table() == {}


@pytest.mark.skipif(os.name != "nt", reason="仅 Windows 有 Toolhelp32 快照")
def test_win_process_table_live_windows():
    table = si._win_process_table()
    assert table
    assert os.getpid() in table
    parent_pid, _name = table[os.getpid()]
    assert parent_pid > 0


# ── Shell 检测 ───────────────────────────────────────────

def test_detect_shell_from_process_chain(monkeypatch):
    monkeypatch.setattr(si, "_ancestor_names", lambda pid: ["/usr/bin/python3.9", "/usr/bin/bash", "/usr/bin/mintty"])
    assert si.detect_shell(env={}, ppid=10) == "bash"


def test_detect_shell_powershell_from_native_chain(monkeypatch):
    """Windows 父链里最近的交互 shell 是 powershell 时不得回退成 cmd。"""
    monkeypatch.setattr(
        si, "_ancestor_names",
        lambda pid: ["python.exe", "powershell.exe", "WindowsTerminal.exe"],
    )
    env = {"ComSpec": r"C:\WINDOWS\system32\cmd.exe"}
    assert si.detect_shell(env=env, ppid=10) == "powershell"


def test_detect_shell_pwsh_from_native_chain(monkeypatch):
    monkeypatch.setattr(si, "_ancestor_names", lambda pid: ["python.exe", "pwsh.exe"])
    assert si.detect_shell(env={}, ppid=10) == "powershell"


def test_detect_command_line_powershell(monkeypatch):
    monkeypatch.setattr(
        si, "_ancestor_names",
        lambda pid: ["python.exe", "powershell.exe", "WindowsTerminal.exe"],
    )
    out = si.detect_command_line(env={"WT_SESSION": "abc"}, ppid=10, sys_platform="win32", release="10")
    assert out == "powershell (Windows Terminal, Windows)"


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


def test_detect_runtime_explicit_platform_overrides_host():
    """显式传入 sys_platform 时以参数为准（Windows 主机上也要能判定非 Windows）。"""
    assert si.detect_runtime(env={}, sys_platform="darwin", release="23.0.0") == "macOS"
    assert si.detect_runtime(env={}, sys_platform="plan9", release="plan9") == ""


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


def test_build_environment_info_excludes_python(monkeypatch):
    monkeypatch.setattr(ei, "detect_command_line", lambda: "bash (mintty, Cygwin)")
    text = ei.build_environment_info("/tmp/work")
    assert "- Python:" not in text
    assert all(not line.startswith("- Python:") for line in text.splitlines())


# ── build_work_md（global.md / agent.md） ────────────────

def test_build_work_md_default_cwd_uses_process_cwd(tmp_path, monkeypatch):
    """cwd 省略时回退进程工作目录（此前 os.path.join(None, name) 直接 TypeError）。"""
    (tmp_path / "global.md").write_text("# 全局规则\n\n- 规则A\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    text = ei.build_work_md()
    assert "规则A" in text


def test_build_work_md_reads_from_given_cwd(tmp_path):
    (tmp_path / "agent.md").write_text("# 子代理规则\n\n- 只读分析\n", encoding="utf-8")
    text = ei.build_work_md("agent.md", cwd=str(tmp_path))
    assert "只读分析" in text


def test_build_work_md_missing_file_returns_empty(tmp_path):
    assert ei.build_work_md("nope.md", cwd=str(tmp_path)) == ""


def test_build_work_md_full_mode_keeps_raw_text(tmp_path):
    (tmp_path / "global.md").write_text("# 标题\n\n正文内容\n", encoding="utf-8")
    text = ei.build_work_md("global.md", summary_mode="full", cwd=str(tmp_path))
    assert "# 标题" in text
    assert "正文内容" in text
