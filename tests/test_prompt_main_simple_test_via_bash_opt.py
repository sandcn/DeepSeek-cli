"""prompts_export_main_simple.md「测试执行必须用 bash_opt」规则回归测试。

需求（2026-10-08）：简单模式提词须明确——运行测试与验证一律用 `bash_opt`
按 `task_id` 实现：先用 `bash` 的 `background=true` 启动测试命令取得 `task_id`，
其后经 `bash_opt` 读取输出（op=read）、等待完成（op=wait）与终止（op=kill）；
禁止绕过 `bash_opt` 以其它方式运行或交互测试进程，测试执行未用 `bash_opt`
一律不算完成。
"""

from __future__ import annotations

from pathlib import Path

import pytest

SIMPLE_PROMPT = Path(__file__).resolve().parent.parent / "prompts" / "prompts_export_main_simple.md"

RULE_KEY = "强制测试执行必须用 `bash_opt`"
PREVIOUS_RULE_KEY = "强制测试没到位不算完成"


@pytest.fixture(scope="module")
def simple_prompt_text() -> str:
    assert SIMPLE_PROMPT.exists(), f"缺少文件: {SIMPLE_PROMPT}"
    return SIMPLE_PROMPT.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def rule_line(simple_prompt_text: str) -> str:
    matched = [ln for ln in simple_prompt_text.splitlines() if ln.startswith("- ") and RULE_KEY in ln]
    assert matched, f"缺少约束: {RULE_KEY}"
    return matched[0]


class TestSimplePromptTestViaBashOptRule:

    def test_prompt_exists(self):
        """提示词文件存在。"""
        assert SIMPLE_PROMPT.exists()

    def test_rule_present(self, rule_line: str):
        """提词须含「测试执行必须用 bash_opt」约束。"""
        assert RULE_KEY in rule_line
        assert "`bash_opt`" in rule_line

    def test_rule_is_red_line(self, rule_line: str):
        """该规则须为「红线 · 一票否决」级别。"""
        assert "（红线 · 一票否决）" in rule_line

    def test_rule_names_background_start_and_task_id(self, rule_line: str):
        """须说明先用 bash background=true 启动测试命令取得 task_id。"""
        assert "`bash`" in rule_line
        assert "background=true" in rule_line
        assert "`task_id`" in rule_line

    def test_rule_lists_bash_opt_ops(self, rule_line: str):
        """须点名读取输出/等待完成/终止三个 bash_opt 操作。"""
        for token in ("op=read", "op=wait", "op=kill"):
            assert token in rule_line, f"应点名「{token}」"

    def test_rule_forbids_bypass(self, rule_line: str):
        """须禁止绕过 bash_opt 以其它方式运行或交互测试进程。"""
        assert "禁止绕过 `bash_opt`" in rule_line

    def test_rule_marks_incomplete_when_not_used(self, rule_line: str):
        """测试执行未用 bash_opt 须视为未完成。"""
        assert "测试执行未用 `bash_opt` 一律不算完成" in rule_line
        assert "禁止标记任务完成" in rule_line
        assert "禁止交付" in rule_line

    def test_rule_follows_test_incomplete_rule(self, simple_prompt_text: str):
        """该规则须紧随「测试没到位不算完成」条目之后，逻辑连贯。"""
        assert simple_prompt_text.index(PREVIOUS_RULE_KEY) < simple_prompt_text.index(RULE_KEY)

    def test_previous_rule_kept(self, simple_prompt_text: str):
        """上一条「测试没到位不算完成」条目未被误删。"""
        assert PREVIOUS_RULE_KEY in simple_prompt_text

    def test_other_constraints_untouched(self, simple_prompt_text: str):
        """其余全局约束不受影响，未被误删。"""
        assert simple_prompt_text.startswith("你是一位乐于助人的软件工程师助手。")
        assert "# 全局约束" in simple_prompt_text
        assert "推理跟回答纯中文输出" in simple_prompt_text
        assert "强制完整实现用户的所有要求" in simple_prompt_text
        assert "禁止采信代码的任何注释" in simple_prompt_text

    def test_only_simple_version_changed(self):
        """本次仅修改 simple 版本，其它提词文件不受影响。"""
        base = SIMPLE_PROMPT.parent
        for other in ("prompts_export_main_empty.md", "prompts_export_main.md"):
            content = (base / other).read_text(encoding="utf-8")
            assert RULE_KEY not in content, f"{other} 不应包含本次修改约束"


class TestNoConflictWithEmptyVariant:

    def test_does_not_use_empty_variant_markers(self, simple_prompt_text: str):
        """不应套用 empty 版本专用的 bash_opt 条目措辞，避免两版本语义混淆。"""
        assert "调试/运行/操作强制用 `bash_opt`" not in simple_prompt_text
        assert "能用 `bash_opt` 操作和调试当前开发应用与别的 GUI 应用（强制）" not in simple_prompt_text
        assert "op=screenshot" not in simple_prompt_text

    def test_no_duplicate_entry_lines(self, simple_prompt_text: str):
        """新增条目不产生完全重复条目行。"""
        entries = [ln for ln in simple_prompt_text.splitlines() if ln.startswith("- ")]
        assert len(entries) == len(set(entries)), "存在重复条目行"


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
