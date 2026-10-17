"""prompts_export_main_empty.md 旧「后台任务与后台 subagent 执行纪律」不得恢复的回归测试。

需求（2026-10-18）：删除 bash 与 subagent 后台运行后「必须继续做别的操作、
禁止原地空等、做完全部别的操作才允许取回结果、同轮并行启动」的执行纪律；
其余全局约束保持不变。
"""

from __future__ import annotations

import pytest

from tests._paths import prompts_dir

MAIN_PROMPT = prompts_dir() / "prompts_export_main_empty.md"

REMOVED_CLAUSES = (
    "后台任务与后台 subagent 执行纪律",
    "必须立即继续做别的操作",
    "禁止原地空等",
    "禁止立刻阻塞等待结果",
    "禁止以等待为由停顿或空转",
    "只有确实没有任何其他可做操作时",
    "才允许按 `task_id`/任务 ID 取回结果",
    "互不依赖的多个后台任务与后台 subagent 必须在同一轮并行启动",
    "禁止一个一个启动后逐个等待",
    "所有后台结果都必须取回并校验后才能继续后续流程或声明完成",
    "禁止把「等结果」当作停止行动的理由",
)


@pytest.fixture(scope="module")
def main_prompt_text() -> str:
    assert MAIN_PROMPT.exists(), f"缺少文件: {MAIN_PROMPT}"
    return MAIN_PROMPT.read_text(encoding="utf-8")


class TestEmptyPromptNoBackgroundAfterOpsRule:

    def test_prompt_exists(self):
        """提示词文件存在。"""
        assert MAIN_PROMPT.exists()

    @pytest.mark.parametrize("clause", REMOVED_CLAUSES)
    def test_removed_clause_absent(self, main_prompt_text: str, clause: str):
        """旧后台执行纪律的关键表述不得再出现。"""
        assert clause not in main_prompt_text, f"旧后台执行纪律未删除: {clause}"

    def test_no_background_after_ops_entry_line(self, main_prompt_text: str):
        """不得存在以后台任务为主题的旧执行纪律条目。"""
        entries = [ln for ln in main_prompt_text.splitlines() if ln.startswith("- ")]
        assert all("后台任务与后台 subagent" not in ln for ln in entries)

    def test_kept_neighbor_rules_untouched(self, main_prompt_text: str):
        """相邻调度/提交流程规则未被误删。"""
        assert "未经用户指定，强制禁止调用 subagent" in main_prompt_text
        assert "plan execute agent 强制串行" in main_prompt_text
        assert "「提交（commit）」与「push」强制用 user_select 向用户确认" in main_prompt_text
        assert "先充分认知再分析、再动手" in main_prompt_text
        assert "只在思考和回答中输出计划" in main_prompt_text

    def test_section_titles_intact(self, main_prompt_text: str):
        """章节结构不受影响。"""
        assert "## 五、认知、探索与规划纪律" in main_prompt_text
        assert "## 六、实现、工具与验证规范" in main_prompt_text

    def test_no_duplicate_entry_lines(self, main_prompt_text: str):
        """条目行无完全重复。"""
        entries = [ln for ln in main_prompt_text.splitlines() if ln.startswith("- ")]
        assert len(entries) == len(set(entries)), "存在重复条目行"

    def test_other_prompts_not_touched(self):
        """本次仅修改 empty 版本，其它提示词文件不受影响。"""
        base = prompts_dir()
        for other in ("prompts_export_main.md", "prompts_export_main_simple.md"):
            content = (base / other).read_text(encoding="utf-8")
            assert "后台任务与后台 subagent 执行纪律" not in content


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
