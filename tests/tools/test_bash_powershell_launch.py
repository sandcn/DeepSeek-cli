"""bash 工具按交互 shell 选择解释器（PowerShell 执行路径）测试。

覆盖：
  - ``_powershell_script`` 的 UTF-8 输出 / LASTEXITCODE 包装（默认 Continue 语义）；
  - ``-File`` 执行方式：临时脚本 UTF-8 BOM + LF、Windows 追加 ``-ExecutionPolicy
    Bypass``、临时文件在命令结束后删除、写不出时回退 ``-EncodedCommand``；
  - ``-EncodedCommand`` 的 UTF-16LE + base64 编码（回退路径）；
  - ``_resolve_shell_launch``：PowerShell（各平台）/ cmd / 找不到可执行文件；
  - ``_get_subprocess_env`` 注入真实交互 shell 标记；
  - ``_run_pipe`` 把解析结果（argv 列表 + shell=False）传给进程创建接缝；
  - Windows 上真实调用 PowerShell 执行脚本（含非零退出码）。
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import sys

import pytest

import src.tools.bash as bash_mod
from src.tools.bash import BashFunc


# ── PowerShell 脚本包装 ──────────────────────────────────

def test_powershell_script_sets_encoding_and_exit_code():
    script = bash_mod._powershell_script("Get-ChildItem")
    assert "$ProgressPreference = 'SilentlyContinue'" in script
    assert "[Console]::OutputEncoding" in script
    assert "$global:LASTEXITCODE = 0" in script
    assert "Get-ChildItem" in script
    assert "$ok = $?" in script
    assert "if ($null -ne $LASTEXITCODE -and $LASTEXITCODE -ne 0) { exit $LASTEXITCODE }" in script
    assert "if (-not $ok) { exit 1 }" in script


def test_encoding_line_follows_os_code_page():
    """Windows 跟随系统 OEM 代码页（原生 stderr 解码才与原生程序写出一致）。"""
    line = bash_mod._powershell_encoding_line()
    if os.name == "nt":
        assert "OEMCodePage" in line
        assert "GetEncoding" in line
        assert "UTF8" not in line
    else:
        assert "UTF8" in line


def test_encoding_line_windows_uses_oem_code_page(monkeypatch):
    monkeypatch.setattr(bash_mod.os, "name", "nt")
    line = bash_mod._powershell_encoding_line()
    assert "CultureInfo" in line and "OEMCodePage" in line


def test_encoding_line_posix_keeps_utf8(monkeypatch):
    monkeypatch.setattr(bash_mod.os, "name", "posix")
    assert "UTF8" in bash_mod._powershell_encoding_line()


def test_powershell_script_keeps_default_error_semantics():
    """用户命令不得被 ``Stop`` / ``try/catch`` 包裹（否则原生 stderr 会中断整条命令）。

    回归（2026-10-20，用户报障「stdout 偶发不完整，只有几行」）：Windows
    PowerShell 5.1 把「原生命令 stderr 被重定向」（``2>&1`` / ``2> file``）
    包成终止错误 ``NativeCommandError``；``$ErrorActionPreference='Stop'`` 下
    既会被 ``try/catch`` 立刻 ``exit 1``，也会让管道整体中止。修复后保持
    PowerShell 默认的 ``Continue`` 语义，输出与管道行为都按 shell 自身语义走。
    """
    script = bash_mod._powershell_script("cmd /c exit 0")
    assert "$ErrorActionPreference = 'Continue'" in script
    assert "$ErrorActionPreference = 'Stop'" not in script
    assert "trap {" not in script
    command_index = script.index("cmd /c exit 0")
    tail = script[command_index:]
    assert "try {" not in tail.split("$ok = $?")[0]
    assert "catch {" not in tail.split("$ok = $?")[0]


def test_powershell_script_keeps_multiline_command():
    script = bash_mod._powershell_script("$x = 1\nWrite-Output $x")
    assert "$x = 1\nWrite-Output $x" in script


def test_encode_powershell_command_utf16le_base64():
    script = "Write-Output 中文"
    encoded = bash_mod._encode_powershell_command(script)
    assert base64.b64decode(encoded).decode("utf-16-le") == script


# ── 脚本文件（-File 模式：无 CLIXML、无命令行长度上限） ─────

def test_script_file_written_as_utf8_bom_with_lf():
    """``.ps1`` 必须是 UTF-8 BOM + LF：PowerShell 5.1 只有见到 BOM 才按 UTF-8 解析。"""
    path = bash_mod._write_powershell_script_file("Write-Output '中文'")
    assert path is not None
    try:
        raw = open(path, "rb").read()
        assert raw.startswith(b"\xef\xbb\xbf")
        assert b"\r\n" not in raw
        assert "中文" in raw.decode("utf-8-sig")
    finally:
        bash_mod._remove_script_file(path)


def test_launch_argv_uses_file_and_cleans_up(monkeypatch):
    """``-File`` 模式：argv 带脚本路径，路径可由 ``_script_path_from_launch`` 取出。"""
    monkeypatch.setattr(bash_mod.os, "name", "posix")
    argv = bash_mod._powershell_launch_argv("/usr/bin/pwsh", "Write-Output hi")
    try:
        assert argv[:3] == ["/usr/bin/pwsh", "-NoLogo", "-NoProfile"]
        assert argv[3] == "-File"
        path = bash_mod._script_path_from_launch(argv)
        assert path == argv[4]
        assert os.path.isfile(path)
        assert "Write-Output hi" in open(path, encoding="utf-8-sig").read()
    finally:
        bash_mod._remove_script_file(bash_mod._script_path_from_launch(argv))
    assert not os.path.exists(path)


def test_launch_argv_adds_execution_policy_only_on_windows(monkeypatch):
    monkeypatch.setattr(bash_mod.os, "name", "nt")
    argv = bash_mod._powershell_launch_argv("powershell.exe", "Write-Output hi")
    try:
        assert argv[3:5] == ["-ExecutionPolicy", "Bypass"]
        assert argv[5] == "-File"
    finally:
        bash_mod._remove_script_file(bash_mod._script_path_from_launch(argv))


def test_launch_argv_falls_back_to_encoded_command(monkeypatch):
    """临时文件写不出来时回退 ``-EncodedCommand``（功能不丢）。"""
    monkeypatch.setattr(bash_mod, "_write_powershell_script_file", lambda script: None)
    argv = bash_mod._powershell_launch_argv("pwsh", "Write-Output hi")
    assert argv[:4] == ["pwsh", "-NoLogo", "-NoProfile", "-EncodedCommand"]
    assert "Write-Output hi" in base64.b64decode(argv[4]).decode("utf-16-le")
    assert bash_mod._script_path_from_launch(argv) is None


def test_remove_script_file_is_safe_for_missing_or_none():
    bash_mod._remove_script_file(None)
    bash_mod._remove_script_file("definitely-not-exists-dsh.ps1")  # 不抛异常
    assert bash_mod._script_path_from_launch("dir") is None
    assert bash_mod._script_path_from_launch(["-File"]) is None


# ── 解释器选择 ───────────────────────────────────────────

def test_resolve_shell_launch_powershell_any_platform(monkeypatch):
    """PowerShell 不只 Windows：POSIX 上的 pwsh 会话同样按 ``-File`` 执行。"""
    monkeypatch.setattr(bash_mod.os, "name", "posix")
    monkeypatch.setattr(bash_mod, "detect_shell", lambda: "powershell")
    monkeypatch.setattr(bash_mod, "_find_powershell_executable", lambda: "/usr/bin/pwsh")
    command, use_shell, shell_name = bash_mod._resolve_shell_launch("Get-ChildItem")
    assert use_shell is False
    assert shell_name == "powershell"
    assert command[0] == "/usr/bin/pwsh"
    assert "-ExecutionPolicy" not in command
    path = bash_mod._script_path_from_launch(command)
    assert path and os.path.isfile(path)
    try:
        assert "Get-ChildItem" in open(path, encoding="utf-8-sig").read()
    finally:
        bash_mod._remove_script_file(path)


def test_resolve_shell_launch_powershell(monkeypatch):
    monkeypatch.setattr(bash_mod.os, "name", "nt")
    monkeypatch.setattr(bash_mod, "detect_shell", lambda: "powershell")
    monkeypatch.setattr(
        bash_mod, "_find_powershell_executable", lambda: r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
    )
    command, use_shell, shell_name = bash_mod._resolve_shell_launch("Get-ChildItem")
    assert use_shell is False
    assert shell_name == "powershell"
    assert isinstance(command, list)
    assert command[0].endswith("powershell.exe")
    assert command[1:3] == ["-NoLogo", "-NoProfile"]
    assert command[3:5] == ["-ExecutionPolicy", "Bypass"]
    assert command[5] == "-File"
    path = bash_mod._script_path_from_launch(command)
    assert path and os.path.isfile(path)
    try:
        assert "Get-ChildItem" in open(path, encoding="utf-8-sig").read()
    finally:
        bash_mod._remove_script_file(path)


def test_resolve_shell_launch_cmd_passthrough(monkeypatch):
    monkeypatch.setattr(bash_mod.os, "name", "nt")
    monkeypatch.setattr(bash_mod, "detect_shell", lambda: "cmd")
    command, use_shell, shell_name = bash_mod._resolve_shell_launch("dir")
    assert command == "dir"
    assert use_shell is True
    assert shell_name == "cmd"


def test_resolve_shell_launch_without_powershell_falls_back(monkeypatch):
    """检测到 powershell 但找不到可执行文件时回退 cmd（不能直接失败）。"""
    monkeypatch.setattr(bash_mod.os, "name", "nt")
    monkeypatch.setattr(bash_mod, "detect_shell", lambda: "powershell")
    monkeypatch.setattr(bash_mod, "_find_powershell_executable", lambda: None)
    command, use_shell, _shell_name = bash_mod._resolve_shell_launch("dir")
    assert command == "dir"
    assert use_shell is True


def test_find_powershell_prefers_pwsh(monkeypatch):
    seen: list = []

    def _which(name):
        seen.append(name)
        return rf"C:\Program Files\PowerShell\7\{name}.exe" if name == "pwsh" else None

    monkeypatch.setattr(bash_mod.shutil, "which", _which)
    assert bash_mod._find_powershell_executable().endswith("pwsh.exe")
    assert seen[0] == "pwsh"


# ── 子进程环境标记 ───────────────────────────────────────

def test_get_subprocess_env_injects_shell_marker(monkeypatch):
    monkeypatch.setattr(bash_mod, "detect_shell", lambda: "powershell")
    env = BashFunc._get_subprocess_env()
    assert env[bash_mod.SHELL_ENV_MARKER] == "powershell"
    assert env["PYTHONUNBUFFERED"] == "1"
    assert env["GIT_PAGER"] == "cat"


def test_get_subprocess_env_without_shell_keeps_env_clean(monkeypatch):
    # 父进程（本工具链）可能已带同名标记，测试先清掉再验证「不再写入」。
    monkeypatch.delenv(bash_mod.SHELL_ENV_MARKER, raising=False)
    monkeypatch.setattr(bash_mod, "detect_shell", lambda: "")
    env = BashFunc._get_subprocess_env()
    assert bash_mod.SHELL_ENV_MARKER not in env


# ── _run_pipe 使用解析结果 ───────────────────────────────

@pytest.mark.asyncio
async def test_run_pipe_uses_resolved_launch(monkeypatch):
    from src.core.adapters import kernel_runtime

    captured: dict = {}
    real_spawn = kernel_runtime.spawn_process

    async def _fake_spawn(command, *, shell=True, **kwargs):
        captured["command"] = command
        captured["shell"] = shell
        return await real_spawn(command, shell=shell, **kwargs)

    monkeypatch.setattr(kernel_runtime, "spawn_process", _fake_spawn)
    launch = [sys.executable, "-c", "print('ok-from-launch')"]
    monkeypatch.setattr(
        bash_mod, "_resolve_shell_launch",
        lambda command: (launch, False, "powershell"),
    )

    result = await BashFunc("ignored")._run_pipe()
    assert result["returncode"] == 0
    assert result["stdout"].strip() == "ok-from-launch"
    assert captured["command"] == launch
    assert captured["shell"] is False


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.name != "nt" or bash_mod._find_powershell_executable() is None,
    reason="需要 Windows PowerShell / pwsh",
)
async def test_run_pipe_removes_script_file(monkeypatch):
    """``-File`` 模式的临时包装脚本在命令执行后删除（不残留临时文件）。"""
    monkeypatch.setattr(bash_mod, "detect_shell", lambda: "powershell")
    created: list = []
    real_writer = bash_mod._write_powershell_script_file

    def _record(script):
        path = real_writer(script)
        created.append(path)
        return path

    monkeypatch.setattr(bash_mod, "_write_powershell_script_file", _record)
    result = json.loads(await BashFunc("Write-Output 'cleanup-ok'").execute())
    assert result["stdout"] == "cleanup-ok"
    assert len(created) == 1 and created[0]
    assert not os.path.exists(created[0])


# ── 真实 PowerShell 执行（仅 Windows） ───────────────────

async def _run_powershell(command: str) -> tuple:
    executable = bash_mod._find_powershell_executable()
    if not executable:
        pytest.skip("未找到 pwsh/powershell 可执行文件")
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
    # 与生产链路同一套编码自适应解码（Windows 下 PowerShell 按本机代码页
    # 输出，直接按 UTF-8 解码会把中文 / 本地化错误消息变成 U+FFFD）
    from src.tools._process_output import decode_output

    return process.returncode, decode_output(stdout), decode_output(stderr)


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "nt", reason="仅 Windows 有 PowerShell")
async def test_real_powershell_success_output():
    code, stdout, _stderr = await _run_powershell("Write-Output 'powershell-ok'")
    assert code == 0
    assert "powershell-ok" in stdout


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "nt", reason="仅 Windows 有 PowerShell")
async def test_real_powershell_nonzero_exit_code():
    code, _stdout, _stderr = await _run_powershell("exit 7")
    assert code == 7


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "nt", reason="仅 Windows 有 PowerShell")
async def test_real_powershell_last_exit_code_propagates():
    """原生命令失败时 $LASTEXITCODE 必须成为工具返回码（bash $? 语义）。"""
    code, _stdout, _stderr = await _run_powershell("cmd /c exit 9")
    assert code == 9


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "nt", reason="仅 Windows 有 PowerShell")
async def test_real_powershell_ls_alias_available():
    """PowerShell 的 ls 别名（Get-ChildItem）可用——cmd 下会报「不是内部或外部命令」。"""
    code, stdout, stderr = await _run_powershell("ls")
    assert code == 0
    assert "不是内部或外部命令" not in (stdout + stderr)
    assert "CLIXML" not in stderr


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "nt", reason="仅 Windows 有 PowerShell")
async def test_real_powershell_cmdlet_failure_is_nonzero():
    """cmdlet 失败（$? 为 False）按 bash 语义返回非零，而不是永远 0。"""
    code, _stdout, stderr = await _run_powershell("Get-Item definitely-not-exist-xyz")
    assert code == 1
    assert "CLIXML" not in stderr
    # 错误消息的语言随系统 UI 语言变化（英文 / 本地化），只断言与命令相关的稳定信息
    assert "Get-Item" in stderr
    assert "definitely-not-exist-xyz" in stderr
    assert "\ufffd" not in stderr


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "nt", reason="仅 Windows 有 PowerShell")
async def test_execute_uses_powershell_when_detected(monkeypatch):
    """端到端：检测为 PowerShell 会话 → bash 工具整条链路按 PowerShell 执行。

    ``ls`` 在 PowerShell 里是 Get-ChildItem 的别名，cmd 下会是「不是内部或外部
    命令」——用它能区分实际使用的解释器。
    """
    monkeypatch.setattr(bash_mod, "detect_shell", lambda: "powershell")
    result = json.loads(await BashFunc("ls").execute())
    assert result["returncode"] == 0
    assert "不是内部或外部命令" not in (result["stdout"] + result["stderr"])
