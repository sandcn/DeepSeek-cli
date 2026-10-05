"""prompts_export_main_empty.md「强制只能用 update_file 和 write_file 修改代码」规则回归测试。

需求（2026-10-06）：全局约束中新增规则，修改代码一律只能使用内部工具
update_file 或 write_file，禁止用 bash、脚本、外部命令或编辑器等任何
其他方式改动代码。
"""

from __future__ import annotations

from pathlib import Path

import pytest

MAIN_PROMPT = Path(__file__).resolve().parent.parent / "prompts" / "prompts_export_main_empty.md"


@pytest.fixture(scope="module")
def main_prompt_text() -> str:
    assert MAIN_PROMPT.exists(), f"缺少文件: {MAIN_PROMPT}"
    return MAIN_PROMPT.read_text(encoding="utf-8")


class TestMainOnlyWriteUpdateToolsRule:

    def test_prompt_exists(self):
        """提示词文件存在。"""
        assert MAIN_PROMPT.exists()

    def test_prompt_requires_only_write_update(self, main_prompt_text: str):
        """提词须含「强制只能用 update_file 和 write_file 修改代码」。"""
        assert "强制只能用 update_file 和 write_file 修改代码" in main_prompt_text

    def test_prompt_marks_rule_as_red_line(self, main_prompt_text: str):
        """该规则须为「红线 · 一票否决」级别。"""
        assert "强制只能用 update_file 和 write_file 修改代码（红线 · 一票否决）" in main_prompt_text

    def test_prompt_names_both_tools(self, main_prompt_text: str):
        """须点名只允许 update_file 与 write_file 两种工具。"""
        assert "`update_file`" in main_prompt_text
        assert "`write_file`" in main_prompt_text

    def test_prompt_forbids_other_ways(self, main_prompt_text: str):
        """须禁止 bash、脚本、外部命令、编辑器等方式修改代码。"""
        for word in ("bash", "脚本", "外部命令", "编辑器"):
            assert word in main_prompt_text, f"应禁止以「{word}」方式修改代码"

    def test_prompt_bans_bash_write_commands(self, main_prompt_text: str):
        """须点名禁止的 bash 写文件命令。"""
        for cmd in ("sed", "awk", "echo", "tee", "printf", "重定向"):
            assert cmd in main_prompt_text, f"应禁止 bash 使用 {cmd} 修改代码"

    def test_prompt_names_script_languages(self, main_prompt_text: str):
        """须点名禁止的脚本语言。"""
        for lang in ("python", "node", "perl"):
            assert lang in main_prompt_text, f"应禁止用 {lang} 脚本修改代码"

    def test_prompt_specifies_usage(self, main_prompt_text: str):
        """须说明使用方式：改已有文件用 update_file，新建/整体重写用 write_file。"""
        assert "修改已有代码用 `update_file`" in main_prompt_text
        assert "新建文件或整体重写用 `write_file`" in main_prompt_text

    def test_prompt_veto_on_violation(self, main_prompt_text: str):
        """须声明以其他方式改动代码一律一票否决。"""
        assert "一律视为一票否决" in main_prompt_text

    def test_prompt_keeps_neighbor_internal_tool_rule(self, main_prompt_text: str):
        """不得破坏相邻原有约束（强制用内部工具实现所有）。"""
        assert "强制用内部工具实现所有（红线 · 一票否决）" in main_prompt_text

    def test_prompt_keeps_neighbor_relative_path_rule(self, main_prompt_text: str):
        """不得破坏相邻原有约束（只能使用相对路径）。"""
        assert "只能使用相对路径（红线 · 一票否决）" in main_prompt_text


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
