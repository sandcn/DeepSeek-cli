"""prompts_export_main_empty.md「强制首次读文件必须读取整个文件」规则回归测试。

需求：全局约束中新增规则，第一次读取任何文件时必须一次性完整读取全文，
禁止使用行号范围分块读取、禁止截断、禁止只读片段；仅再次读取时允许按行号范围读取。
"""

from __future__ import annotations

from pathlib import Path

import pytest

MAIN_PROMPT = Path(__file__).resolve().parent.parent / "prompts" / "prompts_export_main_empty.md"


@pytest.fixture(scope="module")
def main_prompt_text() -> str:
    assert MAIN_PROMPT.exists(), f"缺少文件: {MAIN_PROMPT}"
    return MAIN_PROMPT.read_text(encoding="utf-8")


class TestMainReadFileFullOnFirstReadRule:

    def test_prompt_exists(self):
        """提示词文件存在。"""
        assert MAIN_PROMPT.exists()

    def test_prompt_requires_full_read_on_first_read(self, main_prompt_text: str):
        """提词须含「强制首次读文件必须读取整个文件」规则。"""
        assert "强制首次读文件必须读取整个文件" in main_prompt_text

    def test_prompt_marks_rule_as_red_line(self, main_prompt_text: str):
        """该规则须为「红线 · 一票否决」级别。"""
        assert "强制首次读文件必须读取整个文件（红线 · 一票否决）" in main_prompt_text

    def test_prompt_requires_reading_all_content_at_once(self, main_prompt_text: str):
        """须要求第一次读取时一次性完整读取全文。"""
        assert "第一次读取任何文件时必须一次性完整读取全文" in main_prompt_text

    def test_prompt_bans_range_reading_on_first_read(self, main_prompt_text: str):
        """须禁止首次读取使用行号范围分块读取。"""
        assert "禁止使用行号范围" in main_prompt_text
        assert "start_line" in main_prompt_text
        assert "end_line" in main_prompt_text

    def test_prompt_bans_truncation_and_partial_read(self, main_prompt_text: str):
        """须禁止截断以及只读片段或部分内容。"""
        assert "禁止截断" in main_prompt_text
        assert "禁止只读片段或部分内容" in main_prompt_text

    def test_prompt_requires_reading_to_eof(self, main_prompt_text: str):
        """须要求读到文件末尾并建立完整认知后才能分析或修改。"""
        assert "必须读到文件末尾" in main_prompt_text
        assert "建立完整认知" in main_prompt_text

    def test_prompt_allows_range_only_on_reread(self, main_prompt_text: str):
        """仅同一文件再次读取时才允许按行号范围读取。"""
        assert "仅在同一文件再次读取时才允许按行号范围读取" in main_prompt_text


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
