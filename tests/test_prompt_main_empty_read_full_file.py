"""prompts_export_main_empty.md 新增「首次读取文件须读完整全文」约束回归测试。

需求：若上下文中没有某文件的完整内容，第一次 read_file 强制读完整文件；
大文件、日志文件除外。
"""

from __future__ import annotations

from pathlib import Path

import pytest

MAIN_PROMPT = Path(__file__).resolve().parent.parent / "prompts" / "prompts_export_main_empty.md"

RULE_KEY = "强制首次读取文件须读完整全文"
ANCHOR = "图片强制用 `read_image` 读取"


@pytest.fixture(scope="module")
def main_prompt_text() -> str:
    assert MAIN_PROMPT.exists(), f"缺少文件: {MAIN_PROMPT}"
    return MAIN_PROMPT.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def rule_line(main_prompt_text: str) -> str:
    line = next(
        (ln for ln in main_prompt_text.splitlines() if ln.startswith("- ") and RULE_KEY in ln),
        "",
    )
    assert line, f"缺少约束条目: {RULE_KEY}"
    return line


class TestEmptyPromptReadFullFile:

    def test_prompt_exists(self):
        """提示词文件存在。"""
        assert MAIN_PROMPT.exists()

    def test_rule_present(self, main_prompt_text: str):
        """新增约束条目须存在。"""
        assert RULE_KEY in main_prompt_text, f"缺少约束: {RULE_KEY}"

    def test_rule_is_red_line(self, rule_line: str):
        """约束须标注「红线 · 一票否决」。"""
        assert "（红线 · 一票否决）" in rule_line

    def test_rule_has_context_condition(self, rule_line: str):
        """约束须以「上下文没有完整文件内容」为触发条件。"""
        assert "完整内容" in rule_line
        assert "上下文" in rule_line

    def test_rule_forces_full_read(self, rule_line: str):
        """约束须要求首次 read_file 完整读取全文、禁止分块/截断。"""
        assert "read_file" in rule_line
        assert "第一次" in rule_line
        assert "完整读取全文" in rule_line
        assert "start_line/end_line" in rule_line
        assert "禁止截断" in rule_line

    def test_rule_allows_ranged_reread(self, rule_line: str):
        """约束须允许已含完整内容或再次读取时按行号范围读取。"""
        assert "再次读取" in rule_line
        assert "按行号范围读取" in rule_line

    def test_rule_exempts_large_and_log_files(self, rule_line: str):
        """约束须给出大文件与日志文件例外。"""
        assert "大文件" in rule_line
        assert "日志文件" in rule_line
        assert ".log" in rule_line
        assert "logs/" in rule_line

    def test_rule_placed_before_image_rule(self, main_prompt_text: str):
        """约束须位于内部工具类规则之间（图片读取条目之前），保持工具规范连贯。"""
        assert main_prompt_text.index(RULE_KEY) < main_prompt_text.index(ANCHOR)

    def test_other_constraints_untouched(self, main_prompt_text: str):
        """其余全局约束不受影响，未被误删。"""
        assert main_prompt_text.startswith("你是一位乐于助人的软件工程师助手。")
        assert "# 全局约束" in main_prompt_text
        assert "思考跟回答强制简体中文输出" in main_prompt_text
        assert "禁止采信代码的任何注释" in main_prompt_text
        assert "强制用内部工具实现所有" in main_prompt_text
        assert ANCHOR in main_prompt_text

    def test_no_duplicate_entries(self, main_prompt_text: str):
        """新增约束后仍无重复条目行。"""
        entries = [ln for ln in main_prompt_text.splitlines() if ln.startswith("- ")]
        assert len(entries) == len(set(entries)), "存在重复条目行"

    def test_only_empty_version_changed(self):
        """本次仅修改 empty 版本，其它提示词文件不受影响。"""
        base = MAIN_PROMPT.parent
        for other in ("prompts_export_main.md", "prompts_export_main_simple.md"):
            content = (base / other).read_text(encoding="utf-8")
            assert RULE_KEY not in content, f"{other} 不应包含本次新增约束"


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
