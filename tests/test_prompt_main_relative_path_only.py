"""prompts_export_main_empty.md「只能使用相对路径」规则回归测试。

需求（2026-09-08）：全局约束中新增规则，调用任何工具时的
文件/目录路径参数必须且只能使用相对路径，强制禁止绝对路径。
"""

from __future__ import annotations

from pathlib import Path

import pytest

MAIN_PROMPT = Path(__file__).resolve().parent.parent / "prompts" / "prompts_export_main_empty.md"


@pytest.fixture(scope="module")
def main_prompt_text() -> str:
    assert MAIN_PROMPT.exists(), f"缺少文件: {MAIN_PROMPT}"
    return MAIN_PROMPT.read_text(encoding="utf-8")


class TestMainRelativePathOnlyRule:

    def test_prompt_exists(self):
        """提示词文件存在。"""
        assert MAIN_PROMPT.exists()

    def test_prompt_requires_relative_paths_only(self, main_prompt_text: str):
        """提词须含「只能使用相对路径」规则。"""
        assert "只能使用相对路径" in main_prompt_text

    def test_prompt_marks_rule_as_red_line(self, main_prompt_text: str):
        """该规则须为「红线 · 一票否决」级别。"""
        assert "只能使用相对路径（红线 · 一票否决）" in main_prompt_text

    def test_prompt_applies_to_all_tool_calls(self, main_prompt_text: str):
        """规则须覆盖调用任何工具时的文件/目录路径参数。"""
        assert "调用任何工具" in main_prompt_text
        assert "路径参数" in main_prompt_text

    def test_prompt_bans_absolute_paths(self, main_prompt_text: str):
        """须强制禁止绝对路径。"""
        assert "强制禁止绝对路径" in main_prompt_text

    def test_prompt_gives_relative_examples(self, main_prompt_text: str):
        """须给出相对路径示例。"""
        assert "相对当前工作目录" in main_prompt_text
        assert "src/main.py" in main_prompt_text

    def test_prompt_requires_converting_returned_paths(self, main_prompt_text: str):
        """工具返回结果中的绝对路径须转换为相对路径后再引用/传参。"""
        assert "必须转换为相对路径" in main_prompt_text


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
