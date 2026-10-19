"""bash 工具按「启动本程序的交互 shell」选择解释器测试（通用 shell）。

背景：修复前只有 Windows 会话按交互 shell 选解释器（PowerShell），非 Windows
一律走 ``shell=True`` → ``/bin/sh``——用户在 bash / zsh / fish 里，bash 工具却用
POSIX sh 执行，``[[ ... ]]`` / ``pipefail`` / fish 语法等被判语法错误；Windows
上 git-bash 会话同理被 ComSpec（cmd.exe）接管。现在统一按启动本程序的交互
shell 选解释器：PowerShell / bash / zsh / fish / nushell 等用各自解释器执行，
cmd 与检测失败回退平台默认 shell。

覆盖：
  - POSIX 系 shell（bash / zsh / fish / sh / nushell）→ ``[<shell>, "-c", command]``；
  - Windows 上的 POSIX shell（git-bash）→ 同样用其可执行文件执行；
  - ``$SHELL`` 绝对路径优先（与检测结果一致时）、不匹配时忽略；
  - cmd / 检测失败 / 找不到可执行文件 → 平台默认 ``shell=True`` 回退；
  - ``_shell_basename`` 归一化；
  - ``_pty_launch_argv``：PTY 分支复用同一套解析结果；
  - 真实执行：bash 会话下 bash 专有语法可用、cmd 会话下 cmd 内建命令可用；
  - 工具 schema 声明「命令由当前命令行对应的解释器执行」。
"""

from __future__ import annotations

import json
import os

import pytest

import src.tools.bash as bash_mod
from src.tools.bash import BashFunc


def _fake_which(mapping: dict):
    """按名返回预置路径的 ``shutil.which`` 替身。"""
    return lambda name: mapping.get(name)


def _loads(text: str) -> dict:
    return json.loads(text)


# ── 可执行文件定位 ───────────────────────────────────────

def test_shell_basename_normalizes_paths():
    assert bash_mod._shell_basename(r"C:\Program Files\Git\bin\BASH.EXE") == "bash"
    assert bash_mod._shell_basename("/usr/local/bin/zsh") == "zsh"
    assert bash_mod._shell_basename('"C:\\Windows\\System32\\cmd.exe"') == "cmd"
    assert bash_mod._shell_basename("") == ""


def test_find_shell_executable_prefers_login_shell_path(monkeypatch):
    """``$SHELL`` 与检测结果一致时用其绝对路径（PATH 里可能有同名不同版本）。"""
    monkeypatch.setattr(bash_mod.os, "name", "posix")
    monkeypatch.setenv("SHELL", "/usr/local/bin/zsh")
    monkeypatch.setattr(bash_mod.os.path, "isfile", lambda path: path == "/usr/local/bin/zsh")
    monkeypatch.setattr(bash_mod.shutil, "which", _fake_which({"zsh": "/usr/bin/zsh"}))
    assert bash_mod._find_shell_executable("zsh") == "/usr/local/bin/zsh"


def test_find_shell_executable_ignores_mismatched_login_shell(monkeypatch):
    """``$SHELL`` 是别的 shell（如登录 bash、当前 zsh）时不能拿来执行。"""
    monkeypatch.setattr(bash_mod.os, "name", "posix")
    monkeypatch.setenv("SHELL", "/bin/bash")
    monkeypatch.setattr(bash_mod.os.path, "isfile", lambda path: True)
    monkeypatch.setattr(bash_mod.shutil, "which", _fake_which({"zsh": "/usr/bin/zsh"}))
    assert bash_mod._find_shell_executable("zsh") == "/usr/bin/zsh"


def test_find_shell_executable_nushell_uses_nu(monkeypatch):
    """``nu`` 归一化为 ``nushell``，定位可执行文件仍按 ``nu``。"""
    seen: list = []

    def _which(name):
        seen.append(name)
        return r"C:\tools\nu.exe" if name == "nu" else None

    monkeypatch.setattr(bash_mod.os, "name", "nt")
    monkeypatch.setattr(bash_mod.shutil, "which", _which)
    assert bash_mod._find_shell_executable("nushell") == r"C:\tools\nu.exe"
    assert seen == ["nu"]


def test_find_shell_executable_returns_none_for_unknown(monkeypatch):
    monkeypatch.setattr(bash_mod.os, "name", "nt")
    monkeypatch.setattr(bash_mod.shutil, "which", lambda name: None)
    assert bash_mod._find_shell_executable("nosuchshell") is None
    assert bash_mod._find_shell_executable("") is None


def test_is_wsl_bash_stub_detects_launcher_paths():
    assert bash_mod._is_wsl_bash_stub(r"C:\Windows\System32\bash.exe")
    assert bash_mod._is_wsl_bash_stub(
        r"C:\Users\me\AppData\Local\Microsoft\WindowsApps\bash.exe")
    assert not bash_mod._is_wsl_bash_stub(r"C:\Program Files\Git\usr\bin\bash.exe")
    assert not bash_mod._is_wsl_bash_stub("/bin/bash")


def test_find_shell_executable_skips_wsl_bash_stub(monkeypatch):
    """Windows 上只有 WSL 启动器时不能拿它当本机 bash（文件系统不在一棵树）。"""
    monkeypatch.setattr(bash_mod.os, "name", "nt")
    monkeypatch.setattr(bash_mod.shutil, "which", _fake_which({
        "bash": r"C:\Windows\System32\bash.exe",
    }))
    assert bash_mod._find_shell_executable("bash") is None


# ── 启动方式解析 ─────────────────────────────────────────

@pytest.mark.parametrize("shell,path", [
    ("bash", "/bin/bash"),
    ("zsh", "/usr/bin/zsh"),
    ("fish", "/usr/bin/fish"),
    ("sh", "/bin/sh"),
    ("dash", "/bin/dash"),
    ("xonsh", "/usr/local/bin/xonsh"),
])
def test_resolve_shell_launch_posix_shells(monkeypatch, shell, path):
    """POSIX 系 shell 用 ``<shell> -c <command>`` 执行（argv，无二次转义）。"""
    monkeypatch.setattr(bash_mod.os, "name", "posix")
    monkeypatch.setattr(bash_mod, "detect_shell", lambda: shell)
    monkeypatch.delenv("SHELL", raising=False)
    monkeypatch.setattr(bash_mod.shutil, "which", _fake_which({shell: path}))
    command, use_shell, shell_name = bash_mod._resolve_shell_launch("echo hi")
    assert command == [path, "-c", "echo hi"]
    assert use_shell is False
    assert shell_name == shell


def test_resolve_shell_launch_windows_posix_shell(monkeypatch):
    """Windows 上的 git-bash 会话也用 bash 执行（不再被 ComSpec 接管）。"""
    monkeypatch.setattr(bash_mod.os, "name", "nt")
    monkeypatch.setattr(bash_mod, "detect_shell", lambda: "bash")
    git_bash = r"C:\Program Files\Git\usr\bin\bash.exe"
    monkeypatch.setattr(bash_mod.shutil, "which", _fake_which({"bash": git_bash}))
    command, use_shell, shell_name = bash_mod._resolve_shell_launch("ls")
    assert command == [git_bash, "-c", "ls"]
    assert use_shell is False
    assert shell_name == "bash"


def test_resolve_shell_launch_unknown_shell_passthrough(monkeypatch):
    """检测失败 → 平台默认 shell=True（POSIX /bin/sh、Windows cmd.exe）。"""
    monkeypatch.setattr(bash_mod, "detect_shell", lambda: "")
    assert bash_mod._resolve_shell_launch("echo hi") == ("echo hi", True, "")


def test_resolve_shell_launch_missing_executable_falls_back(monkeypatch):
    """检测到 shell 但找不到可执行文件 → 回退平台默认 shell，不让命令直接失败。"""
    monkeypatch.setattr(bash_mod.os, "name", "posix")
    monkeypatch.setattr(bash_mod, "detect_shell", lambda: "fish")
    monkeypatch.delenv("SHELL", raising=False)
    monkeypatch.setattr(bash_mod.shutil, "which", lambda name: None)
    assert bash_mod._resolve_shell_launch("echo hi") == ("echo hi", True, "fish")


# ── PTY 分支 ─────────────────────────────────────────────

def test_pty_launch_argv_uses_detected_shell(monkeypatch):
    """PTY 分支与 PIPE 分支同一套解析结果。"""
    monkeypatch.setattr(bash_mod.os, "name", "posix")
    monkeypatch.setattr(bash_mod, "detect_shell", lambda: "zsh")
    monkeypatch.delenv("SHELL", raising=False)
    monkeypatch.setattr(bash_mod.shutil, "which", _fake_which({"zsh": "/usr/bin/zsh"}))
    assert bash_mod._pty_launch_argv("echo hi") == ["/usr/bin/zsh", "-c", "echo hi"]


def test_pty_launch_argv_falls_back_to_default_posix_shell(monkeypatch):
    monkeypatch.setattr(bash_mod, "detect_shell", lambda: "")
    argv = bash_mod._pty_launch_argv("echo hi")
    assert argv[0] == bash_mod._default_posix_shell()
    assert argv[1:] == ["-c", "echo hi"]


# ── 真实执行（端到端） ───────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.skipif(os.name == "nt", reason="需要真实 POSIX shell")
async def test_execute_uses_detected_bash_syntax(monkeypatch):
    """检测为 bash → ``[[ ... ]]`` 等 bash 专有语法可用（sh/dash 下会报错）。"""
    if not bash_mod._find_shell_executable("bash"):
        pytest.skip("未找到 bash")
    monkeypatch.setattr(bash_mod, "detect_shell", lambda: "bash")
    result = _loads(await BashFunc("[[ -n x ]] && echo bash-ok").execute())
    assert result["returncode"] == 0
    assert "bash-ok" in result["stdout"]


@pytest.mark.asyncio
@pytest.mark.skipif(not BashFunc._is_pty_available(), reason="PTY 不可用（如原生 Windows）")
async def test_run_pty_executes_with_detected_shell(monkeypatch):
    monkeypatch.setattr(bash_mod, "detect_shell", lambda: "bash")
    if not bash_mod._find_shell_executable("bash"):
        pytest.skip("未找到 bash")
    result = await BashFunc("echo pty-ok")._run_pty()
    assert result["returncode"] == 0
    assert "pty-ok" in result["stdout"]


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "nt", reason="仅 Windows 验证 git-bash 会话")
async def test_execute_uses_git_bash_when_detected(monkeypatch):
    if not bash_mod._find_shell_executable("bash"):
        pytest.skip("未安装本机 bash（git-bash / MSYS2）")
    monkeypatch.setattr(bash_mod, "detect_shell", lambda: "bash")
    result = _loads(await BashFunc("echo bash-ok").execute())
    assert result["returncode"] == 0
    assert "bash-ok" in result["stdout"]


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "nt", reason="仅 Windows 有 cmd")
async def test_execute_uses_cmd_when_detected(monkeypatch):
    """cmd 会话（shell=True → ComSpec）下 cmd 内建命令可用。"""
    monkeypatch.setattr(bash_mod, "detect_shell", lambda: "cmd")
    result = _loads(await BashFunc("echo cmd-ok").execute())
    assert result["returncode"] == 0
    assert "cmd-ok" in result["stdout"]


# ── schema 声明 ──────────────────────────────────────────

def test_schema_declares_shell_specific_interpreter():
    """工具描述向模型声明「命令由当前命令行对应的解释器执行」。"""
    schema = BashFunc.to_tool_schema()["function"]
    assert "对应的 shell 解释器执行" in schema["description"]
    params = schema["parameters"]["properties"]["command"]["description"]
    assert "解释器执行" in params
