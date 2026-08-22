"""prompts_export_main_empty.md「UI/渲染界面必须用 read_image 验证」规则回归测试。

需求（2026-08-22）：全局约束中新增规则，凡有 UI 或渲染的界面
（图片、图表、渲染输出等），必须强制用 read_image 工具读取并验证正确性。
"""

from __future__ import annotations

from pathlib import Path

import pytest

MAIN_PROMPT = Path(__file__).resolve().parent.parent / "prompts" / "prompts_export_main_empty.md"


@pytest.fixture(scope="module")
def main_prompt_text() -> str:
    assert MAIN_PROMPT.exists(), f"缺少文件: {MAIN_PROMPT}"
    return MAIN_PROMPT.read_text(encoding="utf-8")


class TestMainReadImageVerifyRule:

    def test_prompt_exists(self):
        """提示词文件存在。"""
        assert MAIN_PROMPT.exists()

    def test_prompt_mentions_ui_or_render(self, main_prompt_text: str):
        """提词须提及「UI」或「渲染」界面。"""
        assert "UI" in main_prompt_text or "渲染" in main_prompt_text

    def test_prompt_requires_read_image(self, main_prompt_text: str):
        """提词须要求使用 read_image 工具。"""
        assert "read_image" in main_prompt_text

    def test_prompt_mandates_verify(self, main_prompt_text: str):
        """提词须强调验证正确性。"""
        assert "验证正确性" in main_prompt_text

    def test_prompt_mentions_examples(self, main_prompt_text: str):
        """提词须给出典型例子（图片/图表/渲染输出等）。"""
        assert "图片" in main_prompt_text
        assert "图表" in main_prompt_text
        assert "渲染输出" in main_prompt_text

    def test_prompt_uses_force_word(self, main_prompt_text: str):
        """提词须用「必须强制」强调。"""
        assert "必须强制" in main_prompt_text


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
