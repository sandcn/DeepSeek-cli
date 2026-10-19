"""PowerShell 包装脚本的默认读取编码（Get-Content / Select-String / Import-Csv）。

背景（实测）：Windows PowerShell 5.1 的 ``Get-Content`` 默认按 **ANSI 代码页**
（简体中文 cp936/GBK）解码无 BOM 文件——本项目与多数现代仓库是 UTF-8（无
BOM），于是 ``Get-Content README.md`` 的中文全部变成「涓枃缂栫爜」式乱码。
包装脚本统一把读类 cmdlet 的默认编码设为 UTF-8。
"""

from __future__ import annotations

import asyncio
import os

import pytest

import src.tools.bash as bash_mod
from src.tools._process_output import decode_output


def test_encoding_line_sets_default_read_encoding():
    line = bash_mod._powershell_encoding_line()
    assert "$PSDefaultParameterValues['Get-Content:Encoding'] = 'utf8'" in line
    assert "$PSDefaultParameterValues['Select-String:Encoding'] = 'utf8'" in line
    assert "$PSDefaultParameterValues['Import-Csv:Encoding'] = 'utf8'" in line


def test_powershell_script_contains_default_read_encoding():
    script = bash_mod._powershell_script("Get-Content a.txt")
    assert "$PSDefaultParameterValues['Get-Content:Encoding'] = 'utf8'" in script


async def _run_ps(command: str) -> tuple:
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
    return process.returncode, decode_output(stdout), decode_output(stderr)


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "nt", reason="仅 Windows 有 Windows PowerShell")
async def test_get_content_reads_utf8_without_bom(tmp_path):
    """UTF-8（无 BOM）文件的中文必须正确显示，而不是乱码。"""
    path = tmp_path / "utf8_probe.txt"
    path.write_text("中文编码测试：行一\n第二行\n", encoding="utf-8")
    code, stdout, stderr = await _run_ps(f"Get-Content '{path}'")
    assert code == 0, stderr
    assert "中文编码测试" in stdout
    assert "涓枃" not in stdout  # 典型的 GBK 误读产物


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "nt", reason="仅 Windows 有 Windows PowerShell")
async def test_select_string_reads_utf8_without_bom(tmp_path):
    path = tmp_path / "utf8_probe2.txt"
    path.write_text("目标关键词：自动化测试\n", encoding="utf-8")
    code, stdout, stderr = await _run_ps(f"Select-String -Pattern '关键词' -Path '{path}'")
    assert code == 0, stderr
    assert "关键词" in stdout
