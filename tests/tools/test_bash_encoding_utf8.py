"""bash 工具编码统一（UTF-8 无 BOM）回归测试（2026-10-20）。

背景（本机实测）：Windows PowerShell 5.1 下

  1. 包装脚本把 ``[Console]::OutputEncoding`` 设为 OEM 代码页（cp936），
     而 ``git`` / ``node`` 等原生命令输出 UTF-8——经 PowerShell 管道
     （``| Out-String`` / ``2>&1``）时被按 cp936 解码，中文提交信息显示为
     「绯荤粺…」乱码；
  2. PowerShell 5.1 在 ``[Console]::InputEncoding`` 为 65001 时，通过
     .NET Framework ``Process`` 为重定向 stdin 建 StreamWriter 会先写
     UTF-8 BOM——``'abc' | python`` 的 stdin 变成 ``EF BB BF 61 62 63``；
  3. Python 在 Windows 上按本机代码页（cp936）写 stdout，经 PowerShell
     管道会被 UTF-8 解码成 U+FFFD。

修复：包装脚本统一把 Input/OutputEncoding、``$OutputEncoding`` 设为
**无 BOM UTF-8**，并设置 ``PYTHONIOENCODING`` / ``PYTHONUTF8``；命令结束后
在 finally 中恢复原编码。

覆盖：
  - 包装脚本结构（UTF-8 无 BOM / Python UTF-8 / finally 恢复）；
  - ``_format_result`` 对 PowerShell 5.1 ``&&`` / ``||`` 语法错误追加
    可执行的改写提示（且不影响普通 stderr）；
  - Windows 端到端：管道输入无 BOM、中文管道正确、cmd 中文经管道正确、
    Python stdout 为 UTF-8、git 输出直连与经管道一致。
"""

from __future__ import annotations

import json
import os

import pytest

import src.tools.bash as bash_mod
from src.tools.bash import BashFunc


def _requires_powershell() -> bool:
    return os.name == "nt" and bash_mod._find_powershell_executable() is not None


requires_powershell = pytest.mark.skipif(
    not _requires_powershell(), reason="需要 Windows + PowerShell",
)


async def _run(monkeypatch, command: str) -> dict:
    """按 PowerShell 解释器执行命令，返回解析后的三元 JSON。"""
    monkeypatch.setattr(bash_mod, "detect_shell", lambda: "powershell")
    return json.loads(await BashFunc(command).execute())


# ── 1. 包装脚本结构（纯函数，任何平台可跑） ───────────────

def test_wrapper_sets_utf8_without_bom_and_restores():
    script = bash_mod._powershell_script("Write-Output 'x'")
    assert "New-Object System.Text.UTF8Encoding($false)" in script
    assert "[Console]::InputEncoding = $__dsh_utf8" in script
    assert "[Console]::OutputEncoding = $__dsh_utf8" in script
    assert "$OutputEncoding = $__dsh_utf8" in script
    assert "$env:PYTHONIOENCODING = 'utf-8'" in script
    assert "$env:PYTHONUTF8 = '1'" in script
    # 原编码在 finally 中恢复（不改变调用方的代码页）
    assert "[Console]::InputEncoding = $__dsh_input_encoding" in script
    assert "[Console]::OutputEncoding = $__dsh_output_encoding" in script


def test_format_result_appends_powershell_separator_hint():
    """``&&`` / ``||`` 的 PowerShell 解析错误后附上等价写法提示。"""
    stderr = (
        "所在位置 C:\\tmp\\dsh-bash-x.ps1:3 字符: 22\n"
        "+ Write-Output \"first\" && Write-Output \"second\"\n"
        "+                      ~~\n"
        "标记“&&”不是此版本中的有效语句分隔符。"
    )
    payload = json.loads(BashFunc._format_result(
        {"stdout": "", "stderr": stderr, "returncode": 1}))
    assert "环境提示" in payload["stderr"]
    assert "PowerShell 5.1" in payload["stderr"]
    assert "`;`" in payload["stderr"]
    assert "cmd /c" in payload["stderr"]
    assert "不是此版本中的有效语句分隔符" in payload["stderr"]


def test_format_result_appends_hint_to_stdout_when_merged():
    """PTY 合并输出：语法错误文本落在 stdout 时，提示也附加到 stdout。"""
    error_text = "标记“&&”不是此版本中的有效语句分隔符。"
    payload = json.loads(BashFunc._format_result(
        {"stdout": error_text, "stderr": "", "returncode": 1}))
    assert "环境提示" in payload["stdout"]
    assert "PowerShell 5.1" in payload["stdout"]
    assert payload["stderr"] == ""


def test_format_result_keeps_normal_stderr_untouched():
    """普通错误（不含分隔符错误）不追加提示，避免误导。"""
    payload = json.loads(BashFunc._format_result(
        {"stdout": "", "stderr": "fatal: not a git repository", "returncode": 128}))
    assert payload["stderr"] == "fatal: not a git repository"


def test_format_result_handles_empty_stderr():
    payload = json.loads(BashFunc._format_result(
        {"stdout": "ok", "stderr": "", "returncode": 0}))
    assert payload["stderr"] == ""


def test_format_result_detects_english_separator_error():
    """英文系统下的同一错误同样给出提示。"""
    payload = json.loads(BashFunc._format_result(
        {"stdout": "", "stderr": "The token '&&' is not a valid statement separator "
                                 "in this version.", "returncode": 1}))
    assert "环境提示" in payload["stderr"]


def test_format_result_appends_reserved_redirect_hint():
    """PowerShell 5.1 拒绝 ``1>&2`` 时附上可用写法。

    回归场景：`... 1>&2` 在 5.1 里是解析错误（「1>&2 运算符是为将来使用
    而保留的」），整条命令不执行；此前 stderr 只有这句原始错误，模型容易
    反复重试同一条写法。
    """
    stderr = (
        "所在位置 C:\\tmp\\dsh-bash-x.ps1:16 字符: 96\n"
        "+ ... onds 1; Write-Output \"line2\"; Write-Output (\"err\" ) 1>&2; Write- ...\n"
        "+                                                              ~~~~\n"
        "“1>&2”运算符是为将来使用而保留的。\n"
        "    + CategoryInfo          : ParserError: (:) [], "
        "ParentContainsErrorRecordException\n"
        "    + FullyQualifiedErrorId : RedirectionNotSupported"
    )
    payload = json.loads(BashFunc._format_result(
        {"stdout": "", "stderr": stderr, "returncode": 1}))
    assert "环境提示" in payload["stderr"]
    assert "Windows PowerShell 5.1" in payload["stderr"]
    assert "cmd /c" in payload["stderr"]
    assert "2> err.txt" in payload["stderr"]
    # 不应把 && / || 的提示串到重定向错误上
    assert "语句分隔符" not in payload["stderr"]


def test_format_result_detects_english_reserved_redirect():
    """英文系统下的 ``1>&2`` 解析错误同样给出提示。"""
    payload = json.loads(BashFunc._format_result(
        {"stdout": "",
         "stderr": "The '1>&2' operator is reserved for future use.",
         "returncode": 1}))
    assert "环境提示" in payload["stderr"]
    assert "cmd /c" in payload["stderr"]


# ── 2. 端到端：管道输入无 BOM、编码统一 ─────────────────

@requires_powershell
async def test_piped_stdin_has_no_utf8_bom(monkeypatch):
    """``'abc' | python`` 的 stdin 不再带 UTF-8 BOM（EF BB BF）。"""
    result = await _run(
        monkeypatch,
        "'abc' | python -c \"import sys; print(sys.stdin.buffer.read().hex())\"",
    )
    stdout = result["stdout"].strip()
    assert result["returncode"] == 0
    assert stdout.startswith("616263")
    assert "efbbbf" not in stdout


@requires_powershell
async def test_piped_chinese_is_utf8(monkeypatch):
    """PowerShell 管道写出的中文按 UTF-8 到达子进程。"""
    result = await _run(
        monkeypatch,
        "'中文' | python -c \"import sys; print(sys.stdin.buffer.read().decode('utf-8'))\"",
    )
    assert result["returncode"] == 0
    assert result["stdout"].strip() == "中文"


@requires_powershell
async def test_python_stdout_is_utf8(monkeypatch):
    """Python 子进程按 UTF-8 输出（PYTHONIOENCODING / PYTHONUTF8）。"""
    result = await _run(
        monkeypatch, "python -c \"import sys; print(sys.stdout.encoding)\"")
    assert result["stdout"].strip().lower() == "utf-8"


@requires_powershell
async def test_cmd_chinese_through_powershell_pipe(monkeypatch):
    """cmd 内建中文经 PowerShell 管道后仍正确（控制台输出代码页已统一）。"""
    result = await _run(monkeypatch, 'cmd /c "echo 中文 cmd" | Out-String')
    assert result["returncode"] == 0
    assert "中文 cmd" in result["stdout"]
    assert "\ufffd" not in result["stdout"]


@requires_powershell
async def test_powershell_own_chinese_output(monkeypatch):
    result = await _run(monkeypatch, "Write-Output '中文输出'")
    assert result["stdout"].strip() == "中文输出"
    assert "\ufffd" not in result["stdout"]


@requires_powershell
async def test_git_output_through_pipe_matches_direct(monkeypatch):
    """git 中文提交信息直连与经 PowerShell 管道结果一致（乱码回归）。"""
    direct = await _run(monkeypatch, "git log --format=%s -1")
    if direct["returncode"] != 0 or not direct["stdout"].strip():
        pytest.skip("当前仓库没有可用的 git 提交")
    piped = await _run(monkeypatch, "git log --format=%s -1 | Out-String")
    assert piped["returncode"] == 0
    assert piped["stdout"].strip() == direct["stdout"].strip()
    assert "绯荤粺" not in piped["stdout"]
    assert "\ufffd" not in piped["stdout"]


# ── 3. ``&&`` 提示的端到端（真实 PowerShell 语法错误） ────

@requires_powershell
async def test_ampersand_ampersand_is_rewritten_for_powershell_51(monkeypatch):
    """Windows PowerShell 5.1 不支持 ``&&``：包装层自动改写成等价语句块。

    修复前：``a && b`` 直接语法错误（只在 stderr 追加改写提示）。
    修复后：自动改写为 ``a; if ($?) { b }`` 语义，链式命令可直接执行
    （pwsh 6+ 原生支持 ``&&``，不改写，结果同样成功）。
    """
    result = await _run(monkeypatch, 'Write-Output "a" && Write-Output "b"')
    assert result["returncode"] == 0
    assert result["stdout"].splitlines() == ["a", "b"]


@requires_powershell
async def test_incomplete_chain_still_reports_syntax_hint(monkeypatch):
    """残缺的链式命令（``a &&``）不改写，仍由 PowerShell 报错并附环境提示。"""
    result = await _run(monkeypatch, 'Write-Output "a" &&')
    assert result["returncode"] != 0
    text = result["stdout"] + result["stderr"]
    assert "环境提示" in text
    assert "PowerShell 5.1" in text


@requires_powershell
async def test_semicolon_works_as_sequencing(monkeypatch):
    result = await _run(monkeypatch, 'Write-Output "a"; Write-Output "b"')
    assert result["returncode"] == 0
    assert result["stdout"].splitlines() == ["a", "b"]
