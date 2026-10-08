"""prompts_export_main_empty.md「修复完代码后强制用 bash_opt 操作一次流程」规则回归测试。

需求（2026-10-09）：空模式提词须明确——修复完代码后强制用 `bash_opt`
实际操作一次完整流程（按 `task_id` 走 `op=read`/`op=wait`/`op=stdin`/`op=keys`/
`op=screenshot` 等真实操作把流程跑通一遍），操作一次流程没问题才算完成；
有问题再修改代码，修改后再次用 `bash_opt` 操作一次流程验证，直到完全没问题为止；
禁止未用 `bash_opt` 操作一次流程就声明完成，禁止以任何其它方式替代该操作验证。
"""

from __future__ import annotations

from pathlib import Path

import pytest

MAIN_PROMPT = Path(__file__).resolve().parent.parent / "prompts" / "prompts_export_main_empty.md"

RULE_KEY = "修复完代码后强制用 `bash_opt` 操作一次流程"
PREVIOUS_RULE_KEY = "强制所有修改完成后才能检查与交付"
NEXT_RULE_KEY = "强制用内部工具实现所有"


@pytest.fixture(scope="module")
def main_prompt_text() -> str:
    assert MAIN_PROMPT.exists(), f"缺少文件: {MAIN_PROMPT}"
    return MAIN_PROMPT.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def rule_line(main_prompt_text: str) -> str:
    matched = [ln for ln in main_prompt_text.splitlines() if ln.startswith("- ") and RULE_KEY in ln]
    assert matched, f"缺少约束: {RULE_KEY}"
    return matched[0]


class TestFixThenBashOptFlowRule:

    def test_prompt_exists(self):
        """提示词文件存在。"""
        assert MAIN_PROMPT.exists()

    def test_rule_present_on_single_line(self, rule_line: str):
        """提词须含该约束，且落在同一行，避免语义脱离。"""
        assert RULE_KEY in rule_line
        assert "`bash_opt`" in rule_line

    def test_rule_is_red_line(self, rule_line: str):
        """该规则须为「红线 · 一票否决」级别。"""
        assert "（红线 · 一票否决）" in rule_line

    def test_rule_after_fix(self, rule_line: str):
        """须点名「修复完代码后」触发该操作验证。"""
        assert "修复完代码后" in rule_line

    def test_rule_requires_real_operation(self, rule_line: str):
        """须要求用 bash_opt 实际跑一遍完整流程。"""
        assert "实际操作一次完整流程" in rule_line
        assert "完整跑一遍" in rule_line

    def test_rule_names_task_id_and_ops(self, rule_line: str):
        """须说明按 task_id 执行并点名关键 bash_opt 操作。"""
        assert "`task_id`" in rule_line
        for token in ("op=read", "op=wait", "op=stdin", "op=keys", "op=screenshot"):
            assert token in rule_line, f"应点名「{token}」"

    def test_rule_requires_flow_pass_to_finish(self, rule_line: str):
        """操作一次流程没问题才算完成。"""
        assert "操作一次流程没问题才算完成" in rule_line

    def test_rule_requires_fix_then_reverify(self, rule_line: str):
        """有问题须再修改代码，修改后再次用 bash_opt 操作一次流程验证循环。"""
        assert "有问题再修改代码" in rule_line
        assert "修改后再次用 `bash_opt` 操作一次流程验证" in rule_line
        assert "如此循环直到操作一次流程完全没问题为止" in rule_line

    def test_rule_forbids_finish_without_flow(self, rule_line: str):
        """禁止未用 bash_opt 操作一次流程就声明完成，禁止其它方式替代。"""
        assert "禁止未用 `bash_opt` 操作一次流程就声明完成" in rule_line
        assert "禁止以任何其它方式替代 `bash_opt` 的操作验证" in rule_line

    def test_rule_position_between_neighbors(self, main_prompt_text: str):
        """该规则须位于「强制所有修改完成后才能检查与交付」之后、内部工具条目之前。"""
        assert main_prompt_text.index(PREVIOUS_RULE_KEY) < main_prompt_text.index(RULE_KEY)
        assert main_prompt_text.index(RULE_KEY) < main_prompt_text.index(NEXT_RULE_KEY)

    def test_previous_rule_kept(self, main_prompt_text: str):
        """上一条完成与交付约束未被误删。"""
        assert PREVIOUS_RULE_KEY in main_prompt_text
        assert "方可声明完成" in main_prompt_text

    def test_no_duplicate_entry_lines(self, main_prompt_text: str):
        """新增条目不产生完全重复条目行。"""
        entries = [ln for ln in main_prompt_text.splitlines() if ln.startswith("- ")]
        assert len(entries) == len(set(entries)), "存在重复条目行"

    def test_other_constraints_untouched(self, main_prompt_text: str):
        """其余全局约束不受影响，未被误删。"""
        assert main_prompt_text.startswith("你是一位乐于助人的软件工程师助手。")
        assert "# 全局约束" in main_prompt_text
        assert "思考跟回答强制简体中文输出" in main_prompt_text
        assert "强制完整实现用户的所有要求" in main_prompt_text
        assert "调试/运行/操作/截图/GUI 输入强制用 `bash_opt`" in main_prompt_text
        assert "操作 GUI 窗口用 `bash_opt` 的输入 op" in main_prompt_text

    def test_only_empty_version_changed(self):
        """本次仅修改 empty 版本，其它提示词文件不受影响。"""
        base = MAIN_PROMPT.parent
        for other in ("prompts_export_main.md", "prompts_export_main_simple.md"):
            content = (base / other).read_text(encoding="utf-8")
            assert RULE_KEY not in content, f"{other} 不应包含本次修改约束"


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
