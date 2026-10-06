"""prompts_export_main_empty.md 同类规则合并后的结构回归测试。

需求（2026-10-07）：整理并合并空模式提词中相同逻辑的全局约束
（如同类的「不得因 X 而简化/降级/跳过」多条并入统一红线条目），
语义保持不变，条目去重且不丢失原有约束点；同模板的重复表述
（禁止行为枚举、完整执行要求、复杂借口收尾语）一并合并去重。
"""

from __future__ import annotations

from pathlib import Path

import pytest

MAIN_PROMPT = Path(__file__).resolve().parent.parent / "prompts" / "prompts_export_main_empty.md"

MERGED_RULES = {
    "借口跳过": "强制禁止以任何借口跳过、简化、降级或拒绝任何需求（红线 · 一票否决）",
    "降级交付": "强制禁止一切降级实现与非完整交付（红线 · 一票否决）",
    "工作量借口": "强制禁止讨论、评估、提及、暗示任何工作量相关内容（红线 · 一票否决）",
    "先认知后动手": "先充分认知再分析、再动手，禁止未充分了解就动手（红线 · 一票否决）",
    "单元测试": "每次修改文件后必须补充对应单元测试",
    "完成与交付": "强制所有修改完成后才能检查与交付（红线 · 一票否决）",
    "内部工具": "强制用内部工具实现所有（红线 · 一票否决）",
    "bash_opt": "调试/运行/操作/截图/GUI 输入强制用 `bash_opt`（红线 · 一票否决）",
}

SEMANTIC_POINTS = (
    "强势禁止因为难就不做",
    "强制禁止因为复杂就选择简单实现",
    "强制禁止因为上下文大小就不执行",
    "强制禁止因收益小就不做",
    "复杂",
    "难度大",
    "工作量大",
    "上下文不足",
    "上下文太大",
    "上下文窗口限制",
    "token 超限",
    "超过上下文长度",
    "上下文快满了",
    "上下文溢出",
    "上下文放不下",
    "收益小",
    "价值低",
    "回报低",
    "性价比低",
    "不值得",
    "作用不大",
    "阉割版",
    "占位符",
    "stub",
    "TODO",
    "假数据",
    "空实现",
    "最小可用实现（MVP）",
    "示例版",
    "功能子集",
)

REMOVED_REDUNDANT_TITLES = (
    "强制禁止实现简化版（红线 · 一票否决）",
    "强制禁止因为太复杂就不执行（红线 · 一票否决）",
    "强制禁止因为上下文大小就不执行（红线 · 一票否决）",
    "强制禁止因收益小就不做（红线 · 一票否决）",
    "强制禁止讨论工作量（红线 · 一票否决）",
)


@pytest.fixture(scope="module")
def main_prompt_text() -> str:
    assert MAIN_PROMPT.exists(), f"缺少文件: {MAIN_PROMPT}"
    return MAIN_PROMPT.read_text(encoding="utf-8")


class TestEmptyPromptMergedRules:

    def test_prompt_exists(self):
        """提示词文件存在。"""
        assert MAIN_PROMPT.exists()

    def test_heading_kept(self, main_prompt_text: str):
        """文件头与「# 全局约束」章节保持不变。"""
        assert main_prompt_text.startswith("你是一位乐于助人的软件工程师助手。")
        assert "# 全局约束" in main_prompt_text

    @pytest.mark.parametrize("name,title", list(MERGED_RULES.items()))
    def test_merged_rule_present(self, main_prompt_text: str, name: str, title: str):
        """合并后的同类条目须存在。"""
        assert title in main_prompt_text, f"缺少合并条目: {name}"

    @pytest.mark.parametrize("point", SEMANTIC_POINTS)
    def test_semantic_point_kept(self, main_prompt_text: str, point: str):
        """合并后不得丢失原有语义点（借口种类与降级形式）。"""
        assert point in main_prompt_text, f"合并后丢失语义点: {point}"

    def test_no_duplicate_entries(self, main_prompt_text: str):
        """合并后不应出现完全重复的条目行。"""
        entries = [ln for ln in main_prompt_text.splitlines() if ln.startswith("- ")]
        assert len(entries) == len(set(entries)), "存在重复条目行"

    def test_excuse_rules_merged_into_single_entry(self, main_prompt_text: str):
        """复杂/上下文大小/收益小三类借口禁令须合并进同一条目。"""
        entries = [ln for ln in main_prompt_text.splitlines() if ln.startswith("- ")]
        merged = [
            ln for ln in entries
            if "强制禁止因为复杂就选择简单实现" in ln
            and "强制禁止因为上下文大小就不执行" in ln
            and "强制禁止因收益小就不做" in ln
        ]
        assert len(merged) == 1, "三类借口禁令未合并为同一条目"
        assert "（红线 · 一票否决）" in merged[0]

    def test_reduction_enumeration_deduplicated(self, main_prompt_text: str):
        """复杂/上下文两段同模板的禁止行为枚举须合并为一处。"""
        template = "跳过、省略、删减、简化、降级、拆分、拖延"
        assert main_prompt_text.count(template) == 1, "同类禁止行为枚举未合并去重"

    def test_complete_execution_statement_once(self, main_prompt_text: str):
        """「必须完整读取所需内容并完整执行到位」须去重为一次。"""
        assert main_prompt_text.count("必须完整读取所需内容并完整执行到位") == 1

    def test_no_cross_entry_duplicate_downgrade_clause(self, main_prompt_text: str):
        """与「降级实现」条目重复的复杂借口收尾语不得重复出现。"""
        assert "任何需求项都不得因复杂而简化、省略、删减、跳过或以低配方案替代" not in main_prompt_text
        assert "低配方案替代完整方案" in main_prompt_text

    @pytest.mark.parametrize("old", REMOVED_REDUNDANT_TITLES)
    def test_redundant_rule_titles_removed(self, main_prompt_text: str, old: str):
        """被合并的同类旧条目不再各自独立成条。"""
        assert old not in main_prompt_text, f"同类旧条目未合并: {old}"


DEDUPED_CLAUSES = (
    "禁止以「复杂/难度大/工作量大」为由退而选择简单实现或简化方案",
    "任何形式的降级实现一律禁止",
    "强制所有修改完成了，再检查",
    "修改代码一律只能使用内部工具 `update_file` 或 `write_file`",
)

MERGED_ONCE_CLAUSES = (
    "强制只能用 update_file 和 write_file 修改代码",
    "强制禁止用 bash 修改文件",
    "每次修改文件后必须补充对应单元测试",
)


class TestEmptyPromptFurtherCondensed:
    """进一步整理合并同类逻辑后的结构回归（本轮精简）。"""

    def test_comment_rules_merged(self, main_prompt_text: str):
        """注释相关的两条短约束合并为一条。"""
        assert "- 生成代码不能太多的注释，禁止采信代码的任何注释" in main_prompt_text

    def test_comment_rules_not_separate_lines(self, main_prompt_text: str):
        """注释约束不再各自独立成条。"""
        assert "- 生成代码不能太多的注释\n" not in main_prompt_text
        assert "- 禁止采信代码的任何注释\n" not in main_prompt_text

    @pytest.mark.parametrize("clause", DEDUPED_CLAUSES)
    def test_redundant_clause_removed(self, main_prompt_text: str, clause: str):
        """本轮精简去除的冗余表述不再出现。"""
        assert clause not in main_prompt_text, f"冗余表述未精简: {clause}"

    @pytest.mark.parametrize("clause", MERGED_ONCE_CLAUSES)
    def test_merged_clause_once(self, main_prompt_text: str, clause: str):
        """合并后的关键约束各只出现一次。"""
        assert main_prompt_text.count(clause) == 1, f"{clause} 应只出现一次"


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
