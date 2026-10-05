"""prompts_export_main_empty.md「强制禁止用 bash 修改文件」规则回归测试。

需求（2026-08-20）：全局约束中强制禁止用 bash 修改文件，
原因是没有文件沙盒（bash 直接修改文件无保护、不可撤回），
必须改用内部工具 write_file/update_file/mkdir/cp/mv/rm。
"""

from __future__ import annotations

from pathlib import Path

import pytest

MAIN_PROMPT = Path(__file__).resolve().parent.parent / "prompts" / "prompts_export_main_empty.md"


@pytest.fixture(scope="module")
def main_prompt_text() -> str:
    assert MAIN_PROMPT.exists(), f"缺少文件: {MAIN_PROMPT}"
    return MAIN_PROMPT.read_text(encoding="utf-8")


class TestMainNoBashFileModifyRule:

    def test_prompt_exists(self):
        """提示词文件存在。"""
        assert MAIN_PROMPT.exists()

    def test_prompt_forbids_bash_file_modify(self, main_prompt_text: str):
        """提词须含「强制禁止用 bash 修改文件」。"""
        assert "强制禁止用 bash 修改文件" in main_prompt_text

    def test_prompt_gives_reason_no_sandbox(self, main_prompt_text: str):
        """提词须说明原因：没有文件沙盒。"""
        assert "没有文件沙盒" in main_prompt_text
        assert "无保护" in main_prompt_text
        assert "不可撤回" in main_prompt_text

    def test_prompt_fallbacks_to_internal_tools(self, main_prompt_text: str):
        """须指明改用内部工具 write_file/update_file/mkdir/cp/mv/rm。"""
        for tool in ("write_file", "update_file", "mkdir", "cp", "mv", "rm"):
            assert tool in main_prompt_text, f"应点名内部工具 {tool}"

    def test_prompt_marks_rule_as_red_line(self, main_prompt_text: str):
        """该规则须位于「强制用内部工具实现所有（红线 · 一票否决）」条目内。"""
        assert "强制用内部工具实现所有（红线 · 一票否决）" in main_prompt_text

    def test_prompt_still_bans_bash_alternatives(self, main_prompt_text: str):
        """仍须禁止 bash 替代内部工具的读写手段（cat/grep/sed/awk/echo/find/ls）。"""
        for cmd in ("cat", "grep", "sed", "awk", "echo", "find", "ls"):
            assert cmd in main_prompt_text, f"应禁止 bash 使用 {cmd} 替代内部工具"

    def test_prompt_keeps_bash_exception(self, main_prompt_text: str):
        """bash 仅限内部工具无法覆盖的场景且需注明例外原因。"""
        assert "bash 仅限内部工具无法覆盖的场景" in main_prompt_text
        assert "注明例外原因" in main_prompt_text

    def test_prompt_bans_fs_mutating_bash(self, main_prompt_text: str):
        """禁止 rm -rf / mkfs / dd / chmod 777 / sudo / chown 仍保留。"""
        for expr in ("rm -rf", "mkfs", "dd", "chmod 777", "sudo", "chown"):
            assert expr in main_prompt_text, f"应禁止 {expr}"


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
