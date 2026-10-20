"""命令行环境信息（shell_info / env_info）单元测试。

覆盖：
  - 名称归一化与 shell 别名解析
  - /proc 父进程链解析与 psutil 回退
  - Windows 原生父进程链（Toolhelp32 快照，无 psutil 时识别 powershell）
  - Shell 检测（进程链 / SHELL / BASH_VERSION / ComSpec）
  - 非交互执行外壳跳过（cmd /c、powershell -Command、sh -c）与环境标记
  - 终端模拟器检测（环境变量 / 进程链 / TERM 回退）
  - GUI 宿主检测（Windows Terminal / VS Code / ConEmu / JetBrains，跳过 tmux）
  - 运行环境检测（Cygwin / MSYS2 / WSL / Termux / Windows / macOS / Linux）
  - detect_command_line 单行组装（命令行不再混入终端）
  - 版本号检测（Shell 版本环境变量 / PE 版本资源 / --version 探测 / 版本归一化）
  - build_environment_info 注入「- 命令行:」「- GUI 程序:」行（均带版本号）
  - build_environment_info 注入写死的「- 文本编码格式: utf-8」「- 行尾格式: LF」
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
    monkeypatch.setattr(si, "_proc_ancestor_details", lambda pid: [])
    monkeypatch.setattr(si, "_psutil_ancestor_details", lambda pid: [("bash", "bash -l")])
    monkeypatch.setattr(si, "_win_ancestor_details", lambda pid: [])
    assert si._ancestor_names(1) == ["bash"]
    assert si._ancestor_details(1) == [("bash", "bash -l")]


def test_ancestor_names_windows_native_fallback(monkeypatch):
    """无 /proc、无 psutil 时（Windows 默认状态）改用原生进程表。"""
    monkeypatch.setattr(si, "_proc_ancestor_details", lambda pid: [])
    monkeypatch.setattr(si, "_psutil_ancestor_details", lambda pid: [])
    monkeypatch.setattr(
        si, "_win_ancestor_details",
        lambda pid: [
            ("python.exe", ""),
            ("powershell.exe", ""),
            ("WindowsTerminal.exe", ""),
        ],
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


def test_win_ancestor_details_adds_shell_cmdline(monkeypatch):
    """只有 shell 候选进程才补充命令行（用于识别 cmd /c 执行外壳）。"""
    table = {
        10: (20, "python.exe"),
        20: (30, "cmd.exe"),
        30: (40, "powershell.exe"),
        40: (0, "WindowsTerminal.exe"),
    }
    monkeypatch.setattr(si, "_win_process_table", lambda: table)
    monkeypatch.setattr(
        si, "_win_process_cmdline",
        lambda pid: {20: "cmd.exe /c python t.py", 30: "powershell.exe"}.get(pid, ""),
    )
    assert si._win_ancestor_details(10) == [
        ("python.exe", ""),
        ("cmd.exe", "cmd.exe /c python t.py"),
        ("powershell.exe", "powershell.exe"),
        ("WindowsTerminal.exe", ""),
    ]


# ── Shell 检测 ───────────────────────────────────────────

def test_detect_shell_from_process_chain(monkeypatch):
    monkeypatch.setattr(
        si, "_ancestor_details",
        lambda pid: [
            ("/usr/bin/python3.9", ""),
            ("/usr/bin/bash", ""),
            ("/usr/bin/mintty", ""),
        ],
    )
    assert si.detect_shell(env={}, ppid=10) == "bash"


def test_detect_shell_powershell_from_native_chain(monkeypatch):
    """Windows 父链里最近的交互 shell 是 powershell 时不得回退成 cmd。"""
    monkeypatch.setattr(
        si, "_ancestor_details",
        lambda pid: [
            ("python.exe", ""),
            ("powershell.exe", ""),
            ("WindowsTerminal.exe", ""),
        ],
    )
    env = {"ComSpec": r"C:\WINDOWS\system32\cmd.exe"}
    assert si.detect_shell(env=env, ppid=10) == "powershell"


def test_detect_shell_pwsh_from_native_chain(monkeypatch):
    monkeypatch.setattr(
        si, "_ancestor_details",
        lambda pid: [("python.exe", ""), ("pwsh.exe", "")],
    )
    assert si.detect_shell(env={}, ppid=10) == "powershell"


def test_detect_shell_skips_cmd_exec_launcher(monkeypatch):
    """bash 工具用 cmd.exe /c 启动子进程：跳过外壳，识别真实交互 shell。"""
    monkeypatch.setattr(
        si, "_ancestor_details",
        lambda pid: [
            ("cmd.exe", r'cmd.exe /d /s /c "python -c ""print(1)"""'),
            ("python.exe", ""),
            ("powershell.exe", r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"),
            ("WindowsTerminal.exe", ""),
        ],
    )
    assert si.detect_shell(env={"ComSpec": r"C:\WINDOWS\system32\cmd.exe"}, ppid=10) == "powershell"


def test_detect_shell_keeps_interactive_cmd(monkeypatch):
    """cmd /k 是执行后保持交互的会话，不能被当成执行外壳跳过。"""
    monkeypatch.setattr(
        si, "_ancestor_details",
        lambda pid: [("cmd.exe", "cmd.exe /k"), ("WindowsTerminal.exe", "")],
    )
    assert si.detect_shell(env={}, ppid=10) == "cmd"


def test_detect_shell_skips_powershell_command_launcher(monkeypatch):
    """powershell -Command ... 是执行外壳；继续向上找交互 powershell。"""
    monkeypatch.setattr(
        si, "_ancestor_details",
        lambda pid: [
            ("powershell.exe", 'powershell.exe -NoProfile -EncodedCommand ZQBjAGgAbwA='),
            ("python.exe", ""),
            ("powershell.exe", r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"),
            ("WindowsTerminal.exe", ""),
        ],
    )
    assert si.detect_shell(env={}, ppid=10) == "powershell"


def test_detect_shell_keeps_powershell_noexit(monkeypatch):
    """powershell -NoExit 是交互会话，不跳过。"""
    monkeypatch.setattr(
        si, "_ancestor_details",
        lambda pid: [("powershell.exe", "powershell.exe -NoExit -Command Get-Date")],
    )
    assert si.detect_shell(env={}, ppid=10) == "powershell"


def test_detect_shell_skips_posix_command_launcher(monkeypatch):
    monkeypatch.setattr(
        si, "_ancestor_details",
        lambda pid: [
            ("sh", "sh -c python3 app.py"),
            ("python3", ""),
            ("bash", "-bash"),
        ],
    )
    assert si.detect_shell(env={}, ppid=10) == "bash"


def test_detect_shell_env_marker_wins(monkeypatch):
    """启动方注入的真实 shell 优先于父链上的执行外壳。"""
    monkeypatch.setattr(
        si, "_ancestor_details",
        lambda pid: [("cmd.exe", "cmd.exe /c python child.py"), ("python.exe", "")],
    )
    assert si.detect_shell(
        env={si.SHELL_ENV_MARKER: "powershell"}, ppid=10,
    ) == "powershell"


def test_is_launcher_shell_requires_cmdline():
    assert si._is_launcher_shell("cmd.exe", "") is False
    assert si._is_launcher_shell("cmd.exe", r"cmd.exe /c dir") is True
    assert si._is_launcher_shell("cmd.exe", r"cmd.exe /k") is False
    assert si._is_launcher_shell("powershell.exe", "powershell.exe -Command dir") is True
    assert si._is_launcher_shell("powershell.exe", "powershell.exe -NoExit") is False
    assert si._is_launcher_shell("bash", "bash -c 'ls'") is True
    assert si._is_launcher_shell("bash", "-bash") is False


def test_detect_command_line_powershell(monkeypatch):
    monkeypatch.setattr(
        si, "_ancestor_details",
        lambda pid: [
            ("python.exe", ""),
            ("powershell.exe", ""),
            ("WindowsTerminal.exe", ""),
        ],
    )
    out = si.detect_command_line(env={"WT_SESSION": "abc"}, ppid=10, sys_platform="win32", release="10")
    assert out == "powershell (Windows)"


def test_detect_shell_env_shell(monkeypatch):
    monkeypatch.setattr(si, "_ancestor_details", lambda pid: [])
    assert si.detect_shell(env={"SHELL": "/bin/zsh"}, ppid=10) == "zsh"


def test_detect_shell_env_version_marker(monkeypatch):
    monkeypatch.setattr(si, "_ancestor_details", lambda pid: [])
    assert si.detect_shell(env={"BASH_VERSION": "5.2.15"}, ppid=10) == "bash"
    assert si.detect_shell(env={"FISH_VERSION": "3.6.0"}, ppid=10) == "fish"


def test_detect_shell_comspec_fallback(monkeypatch):
    monkeypatch.setattr(si, "_ancestor_details", lambda pid: [])
    assert si.detect_shell(env={"ComSpec": r"C:\WINDOWS\system32\cmd.exe"}, ppid=10) == "cmd"


def test_detect_shell_unknown(monkeypatch):
    monkeypatch.setattr(si, "_ancestor_details", lambda pid: [])
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
    assert si._format_command_line("bash", "Cygwin") == "bash (Cygwin)"
    assert si._format_command_line("bash", "") == "bash"
    assert si._format_command_line("", "Cygwin") == "Cygwin"
    assert si._format_command_line("", "") == "unknown"


def test_detect_command_line_full(monkeypatch):
    monkeypatch.setattr(
        si, "_ancestor_details",
        lambda pid: [("/usr/bin/bash", ""), ("/usr/bin/mintty", "")],
    )
    out = si.detect_command_line(env={}, ppid=1, sys_platform="cygwin", release="CYGWIN_NT-10.0")
    assert out == "bash (Cygwin)"


def test_detect_command_line_runtime_only(monkeypatch):
    monkeypatch.setattr(si, "_ancestor_details", lambda pid: [])
    out = si.detect_command_line(env={}, ppid=1, sys_platform="cygwin", release="CYGWIN_NT-10.0")
    assert out == "Cygwin"


def test_detect_command_line_unknown(monkeypatch):
    monkeypatch.setattr(si, "_ancestor_details", lambda pid: [])
    out = si.detect_command_line(env={}, ppid=1, sys_platform="plan9", release="plan9")
    assert out == "unknown"


# ── 版本号检测 ───────────────────────────────────────────

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("5.2.21(1)-release", "5.2.21"),
        ("2.9.6.0", "2.9.6"),
        ("10.0.26100.8875 (WinBuild.160101.0800)", "10.0.26100.8875"),
        ("mintty 2.9.6 (x86_64-pc-cygwin)", "2.9.6"),
        ("3.44.0", "3.44.0"),
        ("没有版本号", ""),
        ("", ""),
    ],
)
def test_normalize_version(raw, expected):
    assert si._normalize_version(raw) == expected


def test_decode_konsole_version():
    assert si._decode_konsole_version("220400") == "22.04.00"
    assert si._decode_konsole_version("230801") == "23.08.01"
    assert si._decode_konsole_version("3.44.0") == "3.44.0"


def test_resolve_shell_version_from_env(monkeypatch):
    monkeypatch.setattr(si, "_ancestor_pids", lambda pid: [])
    assert si._resolve_shell_version("bash", {"BASH_VERSION": "5.2.21(1)-release"}, 1) == "5.2.21"
    assert si._resolve_shell_version("zsh", {"ZSH_VERSION": "5.9"}, 1) == "5.9"
    assert si._resolve_shell_version("fish", {"FISH_VERSION": "3.6.0"}, 1) == "3.6.0"
    # sh 常是 bash 的 POSIX 模式：BASH_VERSION 存在即取其版本
    assert si._resolve_shell_version("sh", {"BASH_VERSION": "5.2.21(1)-release"}, 1) == "5.2.21"


def test_resolve_shell_version_from_exe_resource(monkeypatch):
    """cmd / powershell 不导出 shell 版本变量 → 读可执行文件版本资源。"""
    monkeypatch.setattr(
        si, "_ancestor_pids",
        lambda pid: [(20, "cmd.exe"), (30, "WindowsTerminal.exe")],
    )
    monkeypatch.setattr(
        si, "_process_version_path",
        lambda pid: r"C:\Windows\System32\cmd.exe" if pid == 20 else "",
    )
    monkeypatch.setattr(
        si, "_exe_file_version",
        lambda path: "10.0.26100.8875" if path.endswith("cmd.exe") else "",
    )
    assert si._resolve_shell_version("cmd", {}, 10) == "10.0.26100.8875"


def test_resolve_gui_version_from_env(monkeypatch):
    monkeypatch.setattr(si, "_ancestor_pids", lambda pid: [])
    assert si._resolve_gui_version("VS Code", {"TERM_PROGRAM_VERSION": "1.85.0"}, 1) == "1.85.0"
    assert si._resolve_gui_version("Konsole", {"KONSOLE_VERSION": "220400"}, 1) == "22.04.00"
    assert si._resolve_gui_version("Termux", {"TERMUX_VERSION": "0.118"}, 1) == "0.118"


def test_resolve_gui_version_from_exe_resource(monkeypatch):
    """Windows / Cygwin：GUI 宿主进程的可执行文件版本资源（mintty 3.6.4 ...）。"""
    monkeypatch.setattr(si, "_ancestor_pids", lambda pid: [(20, "mintty"), (30, "explorer")])
    monkeypatch.setattr(
        si, "_process_version_path",
        lambda pid: r"E:\bin\mintty.exe" if pid == 20 else "",
    )
    monkeypatch.setattr(
        si, "_exe_file_version",
        lambda path: "2.9.6" if path.endswith("mintty.exe") else "",
    )
    monkeypatch.setattr(si, "_probe_program_version", lambda path, name: "")
    assert si._resolve_gui_version("mintty", {}, 10) == "2.9.6"


def test_resolve_gui_version_from_probe(monkeypatch):
    """没有版本资源（Linux）时询问终端自身 ``--version``。"""
    monkeypatch.setattr(si, "_ancestor_pids", lambda pid: [(20, "gnome-terminal-server")])
    monkeypatch.setattr(si, "_process_version_path", lambda pid: "")
    monkeypatch.setattr(si, "_exe_file_version", lambda path: "")
    monkeypatch.setattr(
        si, "_process_exe_path",
        lambda pid: "/usr/libexec/gnome-terminal-server",
    )
    seen: dict = {}

    def fake_probe(path, name):
        seen["call"] = (path, name)
        return "3.44.0"

    monkeypatch.setattr(si, "_probe_program_version", fake_probe)
    assert si._resolve_gui_version("GNOME Terminal", {}, 10) == "3.44.0"
    assert seen["call"] == ("/usr/libexec/gnome-terminal-server", "gnome-terminal-server")


def test_resolve_gui_version_unknown(monkeypatch):
    monkeypatch.setattr(si, "_ancestor_pids", lambda pid: [(20, "sshd")])
    monkeypatch.setattr(si, "_probe_program_version", lambda path, name: "")
    assert si._resolve_gui_version("", {}, 10) == ""
    assert si._resolve_gui_version("mintty", {}, 10) == ""


def test_probe_program_version_skips_unlisted_program(monkeypatch):
    """表外程序不探测——构建提示词时不得为取版本号拉起 GUI 窗口。"""
    monkeypatch.setattr(si, "os", SimpleNamespace(name="posix"))
    assert si._probe_program_version("/usr/bin/not-a-terminal", "not-a-terminal") == ""


def test_runtime_version(monkeypatch):
    assert si._runtime_version("Cygwin", "3.5.4-1.x86_64") == "3.5.4"
    assert si._runtime_version("Linux", "6.1.0-13-amd64") == "6.1.0"
    assert si._runtime_version("", "6.1.0") == ""
    monkeypatch.setattr(si._platform, "version", lambda: "10.0.26220")
    assert si._runtime_version("Windows", "10") == "10.0.26220"


def test_format_command_line_with_version():
    assert si._format_command_line("bash", "Cygwin", "5.2.21") == "bash 5.2.21 (Cygwin)"
    assert si._format_command_line("bash", "", "5.2.21") == "bash 5.2.21"
    # Shell 版本取不到时用运行环境版本兜底（「命令行」始终带版本号）
    assert si._format_command_line("bash", "Cygwin", "", "3.5.4") == "bash (Cygwin 3.5.4)"
    assert si._format_command_line("", "Cygwin", "", "3.5.4") == "Cygwin 3.5.4"
    assert si._format_command_line("", "Cygwin") == "Cygwin"


def test_format_gui_program():
    assert si._format_gui_program("mintty", "2.9.6") == "mintty 2.9.6"
    assert si._format_gui_program("mintty", "") == "mintty"
    assert si._format_gui_program("", "2.9.6") == ""


def test_detect_command_line_with_version(monkeypatch):
    monkeypatch.setattr(
        si, "_ancestor_details",
        lambda pid: [("/usr/bin/bash", ""), ("/usr/bin/mintty", "")],
    )
    monkeypatch.setattr(si, "_resolve_shell_version", lambda shell, env, pid: "5.2.21")
    out = si.detect_command_line(
        env={}, ppid=1, sys_platform="cygwin", release="CYGWIN_NT-10.0", with_version=True,
    )
    assert out == "bash 5.2.21 (Cygwin)"
    # 不带 with_version 时保持既有输出（调用方不受影响）
    assert si.detect_command_line(
        env={}, ppid=1, sys_platform="cygwin", release="CYGWIN_NT-10.0",
    ) == "bash (Cygwin)"


def test_detect_gui_program_with_version(monkeypatch):
    monkeypatch.setattr(si, "_ancestor_names", lambda pid: ["python", "mintty"])
    monkeypatch.setattr(si, "_resolve_gui_version", lambda label, env, pid: "2.9.6")
    assert si.detect_gui_program(env={}, ppid=1, with_version=True) == "mintty 2.9.6"
    assert si.detect_gui_program(env={}, ppid=1) == "mintty"


def test_detect_shell_and_gui_version_entrypoints(monkeypatch):
    monkeypatch.setattr(si, "_ancestor_details", lambda pid: [("/usr/bin/bash", "")])
    monkeypatch.setattr(si, "_ancestor_names", lambda pid: ["python", "mintty"])
    monkeypatch.setattr(si, "_resolve_shell_version", lambda shell, env, pid: "5.2.21")
    monkeypatch.setattr(si, "_resolve_gui_version", lambda label, env, pid: "2.9.6")
    assert si.detect_shell_version(env={}, ppid=1) == "5.2.21"
    assert si.detect_gui_program_version(env={}, ppid=1) == "2.9.6"


def test_exe_file_version_live_pe_resource():
    """真实 PE 文件（cmd.exe）能读到版本号；无 version.dll 的环境跳过。"""
    path = os.environ.get("COMSPEC") or r"C:\Windows\System32\cmd.exe"
    if si._version_dll() is None or not os.path.exists(path):
        pytest.skip("当前平台没有可用的 version.dll / PE 文件")
    assert si._normalize_version(si._exe_file_version(path)) != ""


# ── GUI 宿主检测 ─────────────────────────────────────────

def test_detect_gui_program_windows_terminal_env(monkeypatch):
    monkeypatch.setattr(si, "_ancestor_names", lambda pid: [])
    assert si.detect_gui_program(env={"WT_SESSION": "abc"}, ppid=1) == "Windows Terminal"


def test_detect_gui_program_term_program(monkeypatch):
    monkeypatch.setattr(si, "_ancestor_names", lambda pid: [])
    assert si.detect_gui_program(env={"TERM_PROGRAM": "vscode"}, ppid=1) == "VS Code"
    assert si.detect_gui_program(env={"TERM_PROGRAM": "Apple_Terminal"}, ppid=1) == "Apple Terminal"
    assert si.detect_gui_program(env={"TERMINAL_EMULATOR": "JetBrains-JediTerm"}, ppid=1) == "JetBrains"


def test_detect_gui_program_process_chain(monkeypatch):
    monkeypatch.setattr(
        si, "_ancestor_names",
        lambda pid: ["python.exe", "powershell.exe", "WindowsTerminal.exe"],
    )
    assert si.detect_gui_program(env={}, ppid=1) == "Windows Terminal"


def test_detect_gui_program_skips_multiplexer(monkeypatch):
    """tmux 只是复用器：GUI 宿主要继续向上找（gnome-terminal-server）。"""
    monkeypatch.setattr(
        si, "_ancestor_names",
        lambda pid: ["python", "bash", "tmux", "gnome-terminal-server"],
    )
    assert si.detect_gui_program(env={}, ppid=1) == "GNOME Terminal"


def test_detect_gui_program_jetbrains_process(monkeypatch):
    monkeypatch.setattr(si, "_ancestor_names", lambda pid: ["python", "pycharm64"])
    assert si.detect_gui_program(env={}, ppid=1) == "JetBrains"


def test_detect_gui_program_unknown(monkeypatch):
    monkeypatch.setattr(si, "_ancestor_names", lambda pid: ["python", "sshd"])
    assert si.detect_gui_program(env={}, ppid=1) == ""


def test_detect_terminal_expanded_aliases(monkeypatch):
    monkeypatch.setattr(si, "_ancestor_names", lambda pid: ["python", "conemu64"])
    assert si.detect_terminal(env={}, ppid=1) == "ConEmu"
    monkeypatch.setattr(si, "_ancestor_names", lambda pid: ["python", "fluentterminal"])
    assert si.detect_terminal(env={}, ppid=1) == "Fluent Terminal"


# ── build_environment_info 集成 ──────────────────────────

def test_build_environment_info_contains_command_line(monkeypatch):
    seen: dict = {}

    def fake_command_line(**kwargs):
        seen.update(kwargs)
        return "bash 5.2.21 (Cygwin)"

    monkeypatch.setattr(ei, "detect_command_line", fake_command_line)
    monkeypatch.setattr(ei, "detect_gui_program", lambda **kwargs: "mintty 2.9.6")
    text = ei.build_environment_info("/tmp/work")
    assert text.startswith("# 当前执行环境\n")
    assert "- 工作目录: /tmp/work" in text
    assert "- 命令行: bash 5.2.21 (Cygwin)" in text
    assert "- GUI 程序: mintty 2.9.6" in text
    assert text.endswith("\n")
    # 「命令行」「GUI 程序」都要带版本号 → 构建时显式要求带版本
    assert seen.get("with_version") is True


def test_build_environment_info_gui_unknown(monkeypatch):
    monkeypatch.setattr(ei, "detect_command_line", lambda **kwargs: "bash 5.2.21 (Linux)")
    monkeypatch.setattr(ei, "detect_gui_program", lambda **kwargs: "")
    text = ei.build_environment_info("/tmp/work")
    assert "- GUI 程序: unknown" in text


def test_build_environment_info_line_order(monkeypatch):
    monkeypatch.setattr(ei, "detect_command_line", lambda **kwargs: "bash 5.2.21 (Cygwin)")
    monkeypatch.setattr(ei, "detect_gui_program", lambda **kwargs: "mintty 2.9.6")
    lines = ei.build_environment_info("/tmp/work").splitlines()
    cwd_idx = next(i for i, line in enumerate(lines) if line.startswith("- 工作目录:"))
    cmd_idx = next(i for i, line in enumerate(lines) if line.startswith("- 命令行:"))
    gui_idx = next(i for i, line in enumerate(lines) if line.startswith("- GUI 程序:"))
    enc_idx = next(i for i, line in enumerate(lines) if line.startswith("- 文本编码格式:"))
    eol_idx = next(i for i, line in enumerate(lines) if line.startswith("- 行尾格式:"))
    assert cmd_idx == cwd_idx + 1
    assert gui_idx == cmd_idx + 1
    assert enc_idx == gui_idx + 1
    assert eol_idx == enc_idx + 1


def test_build_environment_info_text_conventions_hardcoded(monkeypatch):
    """文本编码 / 行尾是写死的常量：不随平台与 locale 检测变化。"""
    monkeypatch.setattr(ei, "detect_command_line", lambda **kwargs: "cmd 10.0.26100.8875 (Windows)")
    monkeypatch.setattr(ei, "detect_gui_program", lambda **kwargs: "Windows Terminal 1.21.2911.0")
    monkeypatch.setattr(ei.platform, "system", lambda: "Windows")
    monkeypatch.setattr(ei.os, "linesep", "\r\n")
    text = ei.build_environment_info("/tmp/work")
    assert ei.TEXT_ENCODING == "utf-8"
    assert ei.LINE_ENDING == "LF"
    assert "- 文本编码格式: utf-8" in text
    assert "- 行尾格式: LF" in text
    # 不得被平台默认值污染（Windows 的 GBK / CRLF）
    assert "gbk" not in text.lower()
    assert "crlf" not in text.lower()


def test_build_environment_info_excludes_python(monkeypatch):
    monkeypatch.setattr(ei, "detect_command_line", lambda **kwargs: "bash 5.2.21 (Cygwin)")
    monkeypatch.setattr(ei, "detect_gui_program", lambda **kwargs: "mintty 2.9.6")
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
