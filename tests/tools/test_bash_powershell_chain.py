"""Windows PowerShell 5.1 的 ``&&`` / ``||`` 链式命令改写测试。

覆盖：
  - :func:`_split_chain_operators` 只切「顶层」运算符（引号 / here-string /
    注释 / ``2>&1`` 里的 ``&`` 都不算）；
  - :func:`_rewrite_chain_operators` 生成的条件语句块保持 bash 语义
    （``a && b || c`` 在 b 失败时仍执行 c）；
  - :func:`_is_windows_powershell_51` 只对 ``powershell.exe`` 生效；
  - :func:`_resolve_shell_launch` 对 powershell.exe 改写、对 pwsh 不改写；
  - 真实执行（仅 Windows PowerShell 5.1）：``&&`` / ``||`` 命令可直接跑通。
"""

from __future__ import annotations

import asyncio
import os

import pytest

import src.tools.bash as bash_mod
from src.tools._process_output import decode_output


# ── 顶层运算符切分 ───────────────────────────────────────

def test_split_simple_chain():
    assert bash_mod._split_chain_operators("echo A && echo B") == (
        "echo A", [("&&", "echo B")],
    )


def test_split_multiple_operators():
    first, rest = bash_mod._split_chain_operators("a && b || c")
    assert first == "a"
    assert rest == [("&&", "b"), ("||", "c")]


def test_split_ignores_double_quoted_operator():
    """引号内的 ``&&`` 是字符串正文，不是链式运算符。"""
    assert bash_mod._split_chain_operators('echo "a && b"') is None


def test_split_ignores_single_quoted_operator():
    assert bash_mod._split_chain_operators("echo 'a || b'") is None


def test_split_ignores_redirection_operators():
    """``2>&1`` 里的 ``&`` 不是 ``&&``（重定向不应触发改写）。"""
    assert bash_mod._split_chain_operators("cmd /c foo 2>&1") is None


def test_split_ignores_comment_operator():
    assert bash_mod._split_chain_operators("echo A # x && y") is None


def test_split_handles_here_string():
    command = "Write-Output @'\nA && B\n'@"
    assert bash_mod._split_chain_operators(command) is None


def test_split_rejects_incomplete_chain():
    """``a &&`` 这种残缺命令交给 PowerShell 自己报语法错误，不猜测改写。"""
    assert bash_mod._split_chain_operators("echo A &&") is None
    assert bash_mod._split_chain_operators("&& echo B") is None


def test_split_keeps_escaped_quote():
    command = 'echo "he said ""hi"" && bye" && echo B'
    first, rest = bash_mod._split_chain_operators(command)
    assert first == 'echo "he said ""hi"" && bye"'
    assert rest == [("&&", "echo B")]


def test_split_no_operator_returns_none():
    assert bash_mod._split_chain_operators("echo hello") is None
    assert bash_mod._split_chain_operators("") is None


# ── 改写结果 ─────────────────────────────────────────────

def test_rewrite_produces_state_machine():
    rewritten = bash_mod._rewrite_chain_operators("a && b || c")
    assert rewritten.splitlines()[0] == "a"
    assert "$__dsh_ok = $?" in rewritten
    assert "if ($__dsh_ok) {" in rewritten       # && 分支
    assert "if (-not $__dsh_ok) {" in rewritten  # || 分支
    # 每段执行后都回写状态（a && b || c 在 b 失败时仍执行 c）
    assert rewritten.count("$__dsh_ok = $?") == 3


def test_rewrite_keeps_plain_command_untouched():
    command = "Get-ChildItem -Force"
    assert bash_mod._rewrite_chain_operators(command) == command


def test_rewrite_does_not_touch_quoted_operator():
    command = 'Write-Output "a && b"'
    assert bash_mod._rewrite_chain_operators(command) == command


# ── 版本判定与启动路径 ───────────────────────────────────

def test_is_windows_powershell_51():
    assert bash_mod._is_windows_powershell_51(r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe")
    assert bash_mod._is_windows_powershell_51("powershell")
    assert not bash_mod._is_windows_powershell_51(r"C:\Program Files\PowerShell\7\pwsh.exe")
    assert not bash_mod._is_windows_powershell_51("")


def test_resolve_shell_launch_rewrites_for_powershell_51(monkeypatch):
    monkeypatch.setattr(bash_mod.os, "name", "nt")
    monkeypatch.setattr(bash_mod, "detect_shell", lambda: "powershell")
    monkeypatch.setattr(
        bash_mod, "_find_powershell_executable",
        lambda: r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
    )
    command, use_shell, _name = bash_mod._resolve_shell_launch("echo A && echo B")
    assert use_shell is False
    path = bash_mod._script_path_from_launch(command)
    try:
        script = open(path, encoding="utf-8-sig").read()
        assert "if ($__dsh_ok) {" in script
        assert "echo B" in script
    finally:
        bash_mod._remove_script_file(path)


def test_resolve_shell_launch_keeps_chain_for_pwsh(monkeypatch):
    """pwsh（6+）原生支持 ``&&``：不能改写（否则破坏原生语义）。"""
    monkeypatch.setattr(bash_mod.os, "name", "posix")
    monkeypatch.setattr(bash_mod, "detect_shell", lambda: "powershell")
    monkeypatch.setattr(bash_mod, "_find_powershell_executable", lambda: "/usr/bin/pwsh")
    command, _use_shell, _name = bash_mod._resolve_shell_launch("echo A && echo B")
    path = bash_mod._script_path_from_launch(command)
    try:
        script = open(path, encoding="utf-8-sig").read()
        assert "echo A && echo B" in script
        assert "$__dsh_ok" not in script
    finally:
        bash_mod._remove_script_file(path)


# ── 真实执行（Windows PowerShell 5.1） ───────────────────

async def _run_ps(command: str) -> tuple:
    """用与生产链路一致的包装执行一条命令（Windows PowerShell 5.1 会先改写）。"""
    executable = bash_mod._find_powershell_executable()
    if not executable:
        pytest.skip("未找到 pwsh/powershell 可执行文件")
    if bash_mod._is_windows_powershell_51(executable):
        command = bash_mod._rewrite_chain_operators(command)
    script = bash_mod._powershell_script(command)
    argv = bash_mod._powershell_launch_argv(executable, script)
    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
    finally:
        bash_mod._remove_script_file(bash_mod._script_path_from_launch(argv))
    return process.returncode, decode_output(stdout), decode_output(stderr)


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "nt", reason="仅 Windows 有 Windows PowerShell")
async def test_real_chain_and_operator():
    code, stdout, stderr = await _run_ps("Write-Output A && Write-Output B")
    assert code == 0, stderr
    assert "A" in stdout and "B" in stdout


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "nt", reason="仅 Windows 有 Windows PowerShell")
async def test_real_chain_stops_on_failure():
    """``&&`` 前一条失败时后一条不执行（与 bash 一致）。"""
    code, stdout, _stderr = await _run_ps("cmd /c exit 3 && Write-Output SHOULD-NOT-RUN")
    assert code == 3
    assert "SHOULD-NOT-RUN" not in stdout


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "nt", reason="仅 Windows 有 Windows PowerShell")
async def test_real_chain_or_operator():
    """``||`` 在前一条失败时执行后一条。"""
    code, stdout, _stderr = await _run_ps("cmd /c exit 4 || Write-Output FALLBACK")
    assert "FALLBACK" in stdout
    assert code == 0


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "nt", reason="仅 Windows 有 Windows PowerShell")
async def test_real_chain_mixed_semantics():
    """``a && b || c``：a 成功、b 失败时仍执行 c（bash 语义）。"""
    _code, stdout, _stderr = await _run_ps(
        "cmd /c exit 0 && cmd /c exit 5 || Write-Output RECOVERED")
    assert "RECOVERED" in stdout
