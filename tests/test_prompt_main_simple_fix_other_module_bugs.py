"""prompts_export_main_simple.md「bash_opt 操作测试中发现别的模块 bug 也强制修复」规则回归测试。

需求（2026-10-10）：简单模式提词须明确——用 `bash_opt` 运行、操作、测试、
验证本任务改动时，凡发现本项目其它模块/其它功能存在 bug，无论是否与本任务
相关、是否由本次改动引起、是否在需求范围内，一律必须强制修复：先追踪全量
调用链与全部引用，再按「上层与底层都能解决时强制修改底层」修复根因，修完
补单元测试并再次用 `bash_opt` 操作验证；否则视为未完成，禁止交付。
"""

from __future__ import annotations

from pathlib import Path

import pytest

SIMPLE_PROMPT = Path(__file__).resolve().parent.parent / "prompts" / "prompts_export_main_simple.md"

RULE_KEY = "`bash_opt` 操作测试中发现别的模块 bug 也强制修复"
PREVIOUS_RULE_KEY = "强制测试执行必须用 `bash_opt`"


@pytest.fixture(scope="module")
def simple_prompt_text() -> str:
    assert SIMPLE_PROMPT.exists(), f"缺少文件: {SIMPLE_PROMPT}"
    return SIMPLE_PROMPT.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def rule_line(simple_prompt_text: str) -> str:
    matched = [ln for ln in simple_prompt_text.splitlines() if ln.startswith("- ") and RULE_KEY in ln]
    assert matched, f"缺少约束: {RULE_KEY}"
    return matched[0]


class TestSimplePromptFixOtherModuleBugsRule:

    def test_prompt_exists(self):
        """提示词文件存在。"""
        assert SIMPLE_PROMPT.exists()

    def test_rule_present(self, rule_line: str):
        """提词须含「bash_opt 操作测试中发现别的模块 bug 也强制修复」约束。"""
        assert RULE_KEY in rule_line
        assert "`bash_opt`" in rule_line

    def test_rule_is_red_line(self, rule_line: str):
        """该规则须为「红线 · 一票否决」级别。"""
        assert "（红线 · 一票否决）" in rule_line

    def test_rule_covers_bash_opt_activities(self, rule_line: str):
        """须点名 bash_opt 的运行/操作/测试/验证四类场景。"""
        for token in ("运行", "操作", "测试", "验证"):
            assert token in rule_line, f"应点名「{token}」场景"

    def test_rule_names_other_module_scope(self, rule_line: str):
        """须点名其它模块/其它功能存在 bug 的场景与典型形态。"""
        assert "其它模块/其它功能存在 bug" in rule_line
        for token in ("报错", "异常", "崩溃", "逻辑错误", "数据损坏", "行为异常"):
            assert token in rule_line, f"应点名「{token}」形态"

    def test_rule_ignores_relevance_boundary(self, rule_line: str):
        """须明确不论是否相关、是否本次引起、是否在需求范围内都必须修。"""
        assert "无论该 bug 是否与本任务相关" in rule_line
        assert "是否由本次改动引起" in rule_line
        assert "是否在需求范围内" in rule_line
        assert "一律必须强制修复" in rule_line

    def test_rule_requires_call_chain_tracking(self, rule_line: str):
        """须要求先追踪全量调用链与全部引用。"""
        assert "`search`" in rule_line
        assert "`find`" in rule_line
        assert "全量调用链" in rule_line
        assert "全部引用" in rule_line

    def test_rule_fix_root_at_lower_layer(self, rule_line: str):
        """须遵循上层与底层都能解决时强制修改底层的原则修复根因。"""
        assert "上层与底层都能解决时强制修改底层" in rule_line
        assert "修复根因" in rule_line

    def test_rule_forbids_ignore_and_partial_fix(self, rule_line: str):
        """须禁止视而不见/跳过/只修本次改动部分/只记录不修等回避行为。"""
        assert "禁止视而不见" in rule_line
        assert "禁止跳过" in rule_line
        assert "禁止只修本次改动涉及的部分" in rule_line
        assert "禁止只记录/上报/绕过/注释掉/临时屏蔽不修" in rule_line

    def test_rule_requires_tests_and_bash_opt_reverify(self, rule_line: str):
        """须要求修完补齐单元测试并再次用 bash_opt 操作验证至不复现。"""
        assert "必须补齐对应单元测试" in rule_line
        assert "再次用 `bash_opt` 操作验证" in rule_line
        assert "直至不再复现" in rule_line

    def test_rule_marks_incomplete_and_forbids_delivery(self, rule_line: str):
        """未修完须一律视为未完成并禁止标记完成/交付。"""
        assert "一律视为未完成" in rule_line
        assert "禁止标记任务完成" in rule_line
        assert "禁止交付" in rule_line

    def test_rule_follows_test_via_bash_opt_rule(self, simple_prompt_text: str):
        """该规则须紧随「测试执行必须用 bash_opt」条目之后，逻辑连贯。"""
        assert simple_prompt_text.index(PREVIOUS_RULE_KEY) < simple_prompt_text.index(RULE_KEY)

    def test_previous_rule_kept(self, simple_prompt_text: str):
        """上一条「测试执行必须用 bash_opt」条目未被误删。"""
        assert PREVIOUS_RULE_KEY in simple_prompt_text
        assert "测试执行未用 `bash_opt` 一律不算完成" in simple_prompt_text

    def test_other_constraints_untouched(self, simple_prompt_text: str):
        """其余全局约束不受影响，未被误删。"""
        assert simple_prompt_text.startswith("你是一位乐于助人的软件工程师助手。")
        assert "# 全局约束" in simple_prompt_text
        assert "推理跟回答纯中文输出" in simple_prompt_text
        assert "强制完整实现用户的所有要求" in simple_prompt_text
        assert "禁止采信代码的任何注释" in simple_prompt_text
        assert "强制测试没到位不算完成" in simple_prompt_text

    def test_only_simple_version_changed(self):
        """本次仅修改 simple 版本，其它提词文件不受影响。"""
        base = SIMPLE_PROMPT.parent
        for other in ("prompts_export_main_empty.md", "prompts_export_main.md"):
            content = (base / other).read_text(encoding="utf-8")
            assert RULE_KEY not in content, f"{other} 不应包含本次修改约束"

    def test_no_duplicate_entry_lines(self, simple_prompt_text: str):
        """新增条目不产生完全重复条目行。"""
        entries = [ln for ln in simple_prompt_text.splitlines() if ln.startswith("- ")]
        assert len(entries) == len(set(entries)), "存在重复条目行"


class TestNoConflictWithOtherVariants:

    def test_does_not_use_empty_variant_markers(self, simple_prompt_text: str):
        """不应套用 empty 版本专用条目措辞，避免两版本语义混淆。"""
        assert "调试/运行/操作强制用 `bash_opt`" not in simple_prompt_text
        assert "能用 `bash_opt` 操作和调试当前开发应用与别的 GUI 应用（强制）" not in simple_prompt_text


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
