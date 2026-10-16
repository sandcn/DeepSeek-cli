"""prompts_export_main_empty.md「提交与 push 强制用 user_select 确认且默认先同意」回归测试。

需求（2026-10-16）：空模式提词中明确——git 的「提交（commit）」与「push」
必须强制用 user_select 向用户确认，且该确认的默认选项必须先设为「同意」
（default_options 默认先同意），用户超时未响应、非交互环境或未作答时
按默认「同意」回退并继续执行；同时保留「未获用户明确同意前禁止执行」
与「不得以任何理由或方式替代、绕过确认」的约束，并删除旧有的
「禁止依赖默认选项/超时回退/非交互回退视为同意」禁令。

需求（变更）：删除操作 git 的限制，只保留提交与 push——原「操作 git svn
（提交、push、回滚/还原等写操作）前必须用 user_select 询问用户是否同意」
与「git 的读操作可以不经过用户同意」等条款一并移除。
"""

from __future__ import annotations

from pathlib import Path

import pytest

MAIN_PROMPT = Path(__file__).resolve().parent.parent / "prompts" / "prompts_export_main_empty.md"

RULE_ANCHOR = "「提交（commit）」与「push」强制用 user_select 向用户确认"

REQUIRED_CLAUSES = (
    "「提交（commit）」与「push」强制用 user_select 向用户确认",
    "该确认的默认选项必须先设为「同意」",
    "default_options 默认先同意",
    "两者必须逐项由用户明确同意后才可执行",
    "未拿到用户明确同意前一律禁止执行提交与 push",
    "禁止以任何理由或用任何方式替代、绕过该确认",
    "禁止直接调用 bash/命令行执行 git commit 或 git push",
    "禁止把提交与 push 合并成一次操作顺手带过",
    "禁止静默执行或事后补确认",
    "提交与 push 的确认必须真实展示给用户并等待其反馈结果",
    "用户超时未响应、非交互环境或未作答时按默认「同意」回退并继续执行提交与 push",
)

FORBIDDEN_CLAUSES = (
    "禁止依赖 user_select 的默认选项/超时回退/非交互回退视为同意",
    "禁止依赖 user_select 的默认选项",
)

REMOVED_CLAUSES = (
    "操作 git svn",
    "必须用 user_select 询问用户是否同意",
    "git 的读操作可以不经过用户同意",
    "回滚/还原",
)

UNTOUCHED_CLAUSES = (
    "你是一位乐于助人的软件工程师助手。",
    "# 全局约束",
    "思考跟回答强制简体中文输出",
    "禁止采信代码的任何注释",
    "plan execute agent 强制串行",
    "强制禁止调用 subagent",
)


@pytest.fixture(scope="module")
def main_prompt_text() -> str:
    assert MAIN_PROMPT.exists(), f"缺少文件: {MAIN_PROMPT}"
    return MAIN_PROMPT.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def git_rule_lines(main_prompt_text: str) -> list[str]:
    return [ln for ln in main_prompt_text.splitlines() if RULE_ANCHOR in ln]


class TestGitCommitPushConfirmRule:

    def test_prompt_exists(self):
        """提示词文件存在。"""
        assert MAIN_PROMPT.exists()

    def test_git_rule_single_entry(self, git_rule_lines: list[str]):
        """提交与 push 约束仍为单条条目，未被拆散成多条。"""
        assert len(git_rule_lines) == 1, "提交与 push 约束条目数量应为 1"
        assert git_rule_lines[0].startswith("- "), "提交与 push 约束须为列表条目"

    @pytest.mark.parametrize("clause", REQUIRED_CLAUSES)
    def test_required_clause_present(self, main_prompt_text: str, clause: str):
        """提交与 push 强制确认的各关键约束点须齐全。"""
        assert clause in main_prompt_text, f"缺少约束点: {clause}"

    @pytest.mark.parametrize("clause", REQUIRED_CLAUSES)
    def test_clause_on_git_rule_line(self, git_rule_lines: list[str], clause: str):
        """所有关键约束点须挂在同一条提交与 push 条目上，避免语义脱离。"""
        assert clause in git_rule_lines[0], f"约束点未挂在提交与 push 条目上: {clause}"

    def test_commit_and_push_both_named(self, main_prompt_text: str):
        """提交（commit）与 push 均被点名，缺一不可。"""
        rule = next(ln for ln in main_prompt_text.splitlines() if RULE_ANCHOR in ln)
        assert "「提交（commit）」" in rule and "「push」" in rule

    @pytest.mark.parametrize("clause", REMOVED_CLAUSES)
    def test_only_commit_and_push_restricted(self, main_prompt_text: str, clause: str):
        """除提交与 push 之外的 git 限制须已删除。"""
        assert clause not in main_prompt_text, f"旧 git 限制仍残留: {clause}"

    @pytest.mark.parametrize("clause", UNTOUCHED_CLAUSES)
    def test_other_constraints_untouched(self, main_prompt_text: str, clause: str):
        """其余全局约束不受影响，未被误删。"""
        assert clause in main_prompt_text, f"约束被误删: {clause}"

    @pytest.mark.parametrize("clause", FORBIDDEN_CLAUSES)
    def test_forbidden_clause_absent(self, main_prompt_text: str, clause: str):
        """旧有的「禁止依赖默认选项/超时回退/非交互回退视为同意」禁令须已移除。"""
        assert clause not in main_prompt_text, f"旧禁令仍残留: {clause}"

    def test_default_option_is_agree(self, git_rule_lines: list[str]):
        """默认选项须为「同意」，即默认先同意。"""
        rule = git_rule_lines[0]
        assert "默认选项必须先设为「同意」" in rule
        assert "default_options 默认先同意" in rule

    def test_fallback_to_agree_executes(self, git_rule_lines: list[str]):
        """超时/非交互/未作答回退为「同意」并继续执行提交与 push。"""
        rule = git_rule_lines[0]
        assert "超时未响应" in rule and "非交互环境" in rule and "未作答" in rule
        assert "按默认「同意」回退并继续执行提交与 push" in rule

    def test_explicit_consent_ban_retained(self, git_rule_lines: list[str]):
        """「未拿到用户明确同意前一律禁止执行」与「禁止静默执行」仍保留。"""
        rule = git_rule_lines[0]
        assert "未拿到用户明确同意前一律禁止执行提交与 push" in rule
        assert "禁止静默执行或事后补确认" in rule
        assert "禁止直接调用 bash/命令行执行 git commit 或 git push" in rule

    def test_other_prompt_versions_untouched(self):
        """本次仅修改 empty 版本，其余版本不受影响。"""
        for name in (
            "prompts_export_main.md",
            "prompts_export_main_simple.md",
            "prompts_export_map.md",
            "prompts_export_plan.md",
            "prompts_export_review.md",
            "prompts_export_execute.md",
        ):
            other = MAIN_PROMPT.parent / name
            assert other.exists(), f"缺少文件: {other}"
            assert "两者必须逐项由用户明确同意后才可执行" not in other.read_text(encoding="utf-8")


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
