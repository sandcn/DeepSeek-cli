"""prompts_export_main_empty.md「完成标准：直接运行操作没有问题、无其他 bug 且不引发回归」规则回归测试。

需求（2026-10-07）：删除全局约束中的「编译检查」，改为直接运行操作验证——
所有代码实现/修改全部完成后，必须强制直接运行操作，确认运行操作没有问题；
任务只有在直接运行操作没有问题、没有别的 bug、且没有引发别的功能出问题（无回归）时，
才叫完成；运行操作出错、仍存在任何 bug 或引发别的功能出问题，一律视为未完成，
禁止标记任务完成或交付。
"""

from __future__ import annotations

from pathlib import Path

import pytest

MAIN_PROMPT = Path(__file__).resolve().parent.parent / "prompts" / "prompts_export_main_empty.md"

RULE_KEY = "强制完成标准"
RUN_OPS_RULE_KEY = "强制实现所有代码后直接运行操作"

#: 应随「编译检查」一同删除的编译/语法检查相关表述
COMPILE_WORDS = (
    "编译检查",
    "编译通过",
    "编译失败",
    "语法检查",
    "语法正确",
    "语法错误",
    "py_compile",
    "node --check",
    "go vet",
    "cargo check",
    "tsc --noEmit",
    "javac -Xlint",
)


@pytest.fixture(scope="module")
def main_prompt_text() -> str:
    assert MAIN_PROMPT.exists(), f"缺少文件: {MAIN_PROMPT}"
    return MAIN_PROMPT.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def main_prompt_lines(main_prompt_text: str) -> list:
    return main_prompt_text.splitlines()


@pytest.fixture(scope="module")
def done_criteria_rule(main_prompt_lines: list) -> str:
    matched = [line for line in main_prompt_lines if line.startswith("- ") and RULE_KEY in line]
    assert matched, f"缺少约束: {RULE_KEY}"
    return matched[0]


class TestMainDoneCriteriaRule:

    def test_prompt_exists(self):
        """提示词文件存在。"""
        assert MAIN_PROMPT.exists()

    def test_rule_present(self, done_criteria_rule: str):
        """提词须含「完成标准」约束，且逐项点名直接运行操作/无 bug。"""
        assert RULE_KEY in done_criteria_rule
        for word in ("直接运行操作", "运行操作没有问题", "bug"):
            assert word in done_criteria_rule, f"应点名「{word}」"

    def test_rule_is_red_line(self, done_criteria_rule: str):
        """该规则须为「红线 · 一票否决」级别。"""
        assert "（红线 · 一票否决）" in done_criteria_rule

    def test_requires_run_ops_pass(self, done_criteria_rule: str):
        """须要求直接运行操作没有问题。"""
        assert "直接运行操作没有问题" in done_criteria_rule

    def test_compile_check_removed(self, done_criteria_rule: str):
        """本条规则须删除「编译检查」，不再要求编译/语法检查。"""
        for word in COMPILE_WORDS:
            assert word not in done_criteria_rule, f"不应再要求「{word}」"

    def test_compile_check_rule_removed_from_prompt(self, main_prompt_text: str):
        """整份提词不再保留原「强制实现所有代码后编译检查」红线。"""
        assert "强制实现所有代码后编译检查" not in main_prompt_text
        assert "py_compile" not in main_prompt_text

    def test_requires_no_other_bug(self, done_criteria_rule: str):
        """须要求没有别的 bug 才叫完成。"""
        assert "没有别的 bug" in done_criteria_rule
        assert "才叫完成" in done_criteria_rule

    def test_requires_no_regression(self, done_criteria_rule: str):
        """须要求没有引发别的功能出问题（无回归）才叫完成。"""
        assert "没有引发别的功能出问题" in done_criteria_rule
        assert "无回归" in done_criteria_rule

    def test_incomplete_when_any_failure(self, done_criteria_rule: str):
        """任一项失败/存在 bug/引发回归时须一律视为未完成。"""
        assert "一律视为未完成" in done_criteria_rule
        assert "禁止标记任务完成或交付" in done_criteria_rule
        assert "直接运行操作出错" in done_criteria_rule
        assert "引发别的功能出问题" in done_criteria_rule

    def test_rule_after_run_ops_rule(self, main_prompt_lines: list):
        """完成标准须位于「直接运行操作」约束之后，逻辑连贯（同条目内亦须维持先后）。"""
        text = "\n".join(main_prompt_lines)
        assert text.index(RUN_OPS_RULE_KEY) < text.index(RULE_KEY)

    def test_keeps_run_ops_rule(self, main_prompt_lines: list):
        """须以「强制实现所有代码后直接运行操作」红线取代原编译检查红线。"""
        text = "\n".join(main_prompt_lines)
        assert f"{RUN_OPS_RULE_KEY}（红线 · 一票否决）" in text

    def test_other_constraints_untouched(self, main_prompt_lines: list):
        """其余全局约束不受影响，未被误删。"""
        text = "\n".join(main_prompt_lines)
        assert "你是一位乐于助人的软件工程师助手。" in text
        assert "# 全局约束" in text
        assert "思考跟回答强制简体中文输出" in text
        assert "强制完整实现用户的所有要求" in text
        assert "禁止采信代码的任何注释" in text

    def test_only_empty_version_changed(self):
        """本次仅修改 empty 版本，其它提示词文件不受影响。"""
        base = MAIN_PROMPT.parent
        for other in ("prompts_export_main.md", "prompts_export_main_simple.md"):
            content = (base / other).read_text(encoding="utf-8")
            assert RULE_KEY not in content, f"{other} 不应包含本次修改约束"


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
