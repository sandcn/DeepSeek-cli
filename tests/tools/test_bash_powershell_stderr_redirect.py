"""PowerShell 会话下 bash 工具「stdout 偶发不完整」回归测试（2026-10-20）。

用户报障：``bash`` 工具执行命令时 stdout / stderr 有一定机率不完整，输出的
信息只有几行——即使命令本身完全正常。

根因（Windows PowerShell 5.1 行为）：原生命令的 stderr 一旦被**重定向**
（``2>&1`` / ``2> file``，模型写命令时极常见），PowerShell 会把它包成
``NativeCommandError`` 错误记录。修复前包装脚本用
``$ErrorActionPreference = 'Stop'`` + 外层 ``try/catch``：
  - ``try/catch`` 立刻吞掉该错误并 ``exit 1`` → 用户命令**中途被打断**，
    后续语句不再执行，stdout 只剩出错前那几行；
  - 换成 ``trap { continue }`` 也只能让「非管道」场景继续，管道
    （``cmd 2>&1 | Select-Object ...``）仍整体中止，native 命令的 stdout
    全部丢失。

修复：包装脚本恢复 PowerShell 默认的 ``Continue`` 错误语义（错误记录非终止，
脚本与管道都按 shell 自身语义继续），并把执行方式改为 ``-File``（``-File``
模式下错误按主机文本格式写 stderr，不会出现 ``#< CLIXML`` 序列化块）。

同一问题的第二处加固：``_read_loop`` 读取输出流时，非 EIO 的读错误
（Windows 上的 ``ERROR_BROKEN_PIPE`` / 连接重置等）此前会向上抛给读取循环的
调用方，那里只记一条 warning 就返回**部分**输出——模型无法区分「命令只输出
这么多」与「读流出错丢了尾部」。现统一按「流结束」处理：已读到的输出完整返回。
"""

from __future__ import annotations

import errno
import json
import os

import pytest

import src.tools.bash as bash_mod
from src.tools.bash import BashFunc


_requires_powershell = pytest.mark.skipif(
    os.name != "nt" or bash_mod._find_powershell_executable() is None,
    reason="需要 Windows PowerShell / pwsh",
)


async def _execute_powershell(monkeypatch, command: str) -> dict:
    """强制按 PowerShell 会话执行命令，返回三元 JSON 解析结果。"""
    monkeypatch.setattr(bash_mod, "detect_shell", lambda: "powershell")
    return json.loads(await BashFunc(command).execute())


# ── 1. 原生命令 stderr 被重定向：不中断、不丢输出 ─────────

@_requires_powershell
async def test_merged_stderr_does_not_abort_command(monkeypatch):
    """``2>&1`` 合并原生命令 stderr 时，后续语句必须继续执行。"""
    result = await _execute_powershell(
        monkeypatch,
        'cmd /c "(echo o1& echo e1 1>&2& echo o2)" 2>&1; Write-Output "AFTER"',
    )
    assert result["returncode"] == 0
    # PTY 合并输出：o1 / o2 / AFTER 都必须出现（修复前 NativeCommandError
    # 中断脚本 → AFTER 丢失）；e1 也完整保留（纯文本，不是 CLIXML 块）
    text = result["stdout"] + result["stderr"]
    for token in ("o1", "o2", "AFTER", "e1"):
        assert token in text
    assert "CLIXML" not in text


@_requires_powershell
async def test_stderr_file_redirect_does_not_abort_command(monkeypatch, tmp_path):
    """``2> file`` 重定向原生命令 stderr 时，后续语句必须继续执行。"""
    target = tmp_path / "dsh-err.txt"
    result = await _execute_powershell(
        monkeypatch,
        f'cmd /c "echo e1 1>&2" 2> "{target}"; Write-Output "AFTER"',
    )
    assert result["returncode"] == 0
    assert "AFTER" in result["stdout"]


@_requires_powershell
async def test_native_exit_code_survives_merged_stderr(monkeypatch):
    """``2>&1`` + 原生命令非零退出码：returncode 仍是命令退出码。"""
    result = await _execute_powershell(
        monkeypatch,
        'cmd /c "echo e1 1>&2& exit 5" 2>&1',
    )
    assert result["returncode"] == 5
    text = result["stdout"] + result["stderr"]
    assert "e1" in text
    assert "CLIXML" not in text


# ── 2. cmdlet 报错：不中断后续语句（bash ``;`` 语义） ─────

@_requires_powershell
async def test_cmdlet_error_does_not_abort_following_statements(monkeypatch):
    """单条 cmdlet 失败不应吞掉后续命令的输出（修复前 try/catch 直接 exit 1）。"""
    result = await _execute_powershell(
        monkeypatch,
        'Get-Item "definitely-not-exist-dsh"; Write-Output "AFTER"',
    )
    text = result["stdout"] + result["stderr"]
    assert "AFTER" in text
    assert result["returncode"] == 0  # bash 语义：取最后一条语句的状态
    assert "Cannot find path" in text or "找不到路径" in text
    assert "CLIXML" not in text


@_requires_powershell
async def test_cmdlet_error_alone_is_nonzero(monkeypatch):
    """仅一条失败的 cmdlet：按 bash 语义返回非零、错误为纯文本。"""
    result = await _execute_powershell(
        monkeypatch, 'Get-Item "definitely-not-exist-dsh"')
    assert result["returncode"] == 1
    text = result["stdout"] + result["stderr"]
    assert "Cannot find path" in text or "找不到路径" in text
    assert "CLIXML" not in text


# ── 3. 端到端（bash 工具整条链路） ───────────────────────

@_requires_powershell
async def test_execute_keeps_full_output_with_stderr_redirect(monkeypatch):
    """端到端：用户报障场景（``2>&1`` + 多条语句）输出必须完整。"""
    result = await _execute_powershell(
        monkeypatch,
        'cmd /c "(echo a& echo b 1>&2& echo c)" 2>&1; Write-Output "AFTER"',
    )
    assert result["returncode"] == 0
    text = result["stdout"] + result["stderr"]
    lines = [line.strip() for line in text.splitlines()]
    assert "a" in lines
    assert "c" in lines
    assert "AFTER" in lines
    assert "b" in text


@_requires_powershell
async def test_pipeline_with_merged_stderr_keeps_all_objects(monkeypatch):
    """``2>&1 | ForEach-Object``：管道里的 stdout 对象一个都不能少。

    修复前（``Stop`` + ``try/catch``/``trap``）管道会整体中止，上游 native
    命令的 stdout 全部丢失——这是「输出只有几行」的另一种形态。
    """
    result = await _execute_powershell(
        monkeypatch,
        'cmd /c "(echo p1& echo e1 1>&2& echo p2)" 2>&1 | ForEach-Object { "got: $_" }',
    )
    lines = [line.strip() for line in result["stdout"].splitlines()]
    assert "got: p1" in lines
    assert "got: p2" in lines
    assert "CLIXML" not in (result["stdout"] + result["stderr"])


# ── 3b. 输出编码：合并进来的原生命令中文不得乱码 ──────────

@_requires_powershell
async def test_merged_native_stderr_keeps_chinese(monkeypatch):
    """``2>&1`` 合并的原生命令 stderr 中文不得变成 U+FFFD。

    PowerShell 用 ``[Console]::OutputEncoding`` 解码原生命令输出；原生命令
    （Python 等）按系统代码页（简体中文 cp936）写字节，因此包装脚本必须
    跟随本机 OEM 代码页，而不是写死 UTF-8。
    """
    result = await _execute_powershell(
        monkeypatch,
        'python -c "import sys; print(\'中文stderr\', file=sys.stderr)" 2>&1',
    )
    text = result["stdout"] + result["stderr"]
    assert "中文stderr" in text
    assert "\ufffd" not in text


@_requires_powershell
async def test_powershell_own_chinese_output_decodes(monkeypatch):
    """PowerShell 自身输出的中文（按 OEM 代码页编码）仍被正确还原。"""
    result = await _execute_powershell(
        monkeypatch, 'Write-Output "中文输出正常"; Write-Output "第二行"')
    assert result["stdout"] == "中文输出正常\n第二行"
    assert "\ufffd" not in result["stdout"] + result["stderr"]


# ── 4. 读取输出流时的错误：已读到的内容不得丢弃 ──────────

class _FailingReader:
    """先返回若干数据块、随后抛出指定异常的 StreamReader 桩。"""

    def __init__(self, chunks, exc):
        self._chunks = list(chunks)
        self._exc = exc

    async def read(self, _n: int) -> bytes:
        if self._chunks:
            return self._chunks.pop(0)
        raise self._exc


class _DummyProcess:
    """最小子进程桩（_read_loop 只用 kill / returncode）。"""

    returncode = 1

    def kill(self) -> None:  # pragma: no cover - 中断路径才调用
        pass


@pytest.mark.parametrize("exc", [
    BrokenPipeError(errno.EPIPE, "pipe closed"),
    ConnectionResetError(errno.ECONNRESET, "connection reset"),
    RuntimeError("transport broken"),
])
async def test_read_loop_keeps_received_output_on_stream_error(exc):
    """读流出错（非 EIO）按「流结束」处理：已解码的行与尾部残留都要保留。"""
    reader = _FailingReader([b"line1\nline2\n"], exc)
    lines: list = []

    interrupted = await BashFunc._read_loop(reader, _DummyProcess(), lines, None)

    assert interrupted is False
    assert "".join(lines) == "line1\nline2\n"


async def test_read_loop_flushes_partial_line_before_stream_error():
    """读流出错前到达的无换行残留（EOF 语义）也要保留。"""
    reader = _FailingReader([b"line1\n", b"tail"], OSError(errno.EIO, "eio"))
    lines: list = []

    await BashFunc._read_loop(reader, _DummyProcess(), lines, None)

    assert "".join(lines) == "line1\ntail"


# ── 5. 包装脚本结构：命令不得被 Stop / try/catch 包裹 ─────

def test_powershell_script_keeps_default_error_semantics():
    """回归断言：Stop / try/catch 会把原生 stderr 变终止错误并中断命令。"""
    script = bash_mod._powershell_script("Write-Output 'x'")
    assert "$ErrorActionPreference = 'Continue'" in script
    assert "'Stop'" not in script
    head, _, tail = script.partition("Write-Output 'x'")
    assert "catch {" not in tail.split("$ok = $?")[0]
    assert "trap {" not in script


@_requires_powershell
async def test_wrapped_script_parses_and_runs(monkeypatch):
    """包装脚本能被 PowerShell 正常解析执行（trap 语法 / 变量插值无歧义）。"""
    result = await _execute_powershell(monkeypatch, "Write-Output 'wrapped-ok'")
    assert result["returncode"] == 0
    assert result["stdout"] == "wrapped-ok"
    assert result["stderr"] == ""
