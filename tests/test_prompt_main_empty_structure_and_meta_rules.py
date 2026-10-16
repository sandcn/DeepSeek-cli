"""prompts_export_main_empty.md 结构整理与元规则补充的回归测试。

本轮改动（在保持既有测试所锁定的逐字规则串不变的前提下）：
1. 章节分组标题（总则/需求/语言/可维护性/认知规划/工具验证/安全/技能/交付）；
2. 总则条目——规则分级（红线 > 强制 > 默认）与同级冲突裁决；
3. 完成输出条目——任务结束须给出含验证结论的变更说明；
4. 补齐若干语义澄清：工作量禁令的用户询问例外、bash_opt 与内部工具的
   边界、注释不得作为事实依据、以及若干语病修正。

后续改动：撤除文件头部的模式自描述两行（「空模式精简规则集」与
「Ctrl+B 循环切换」说明），故新增反向断言锁定其不再出现。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.prompt_builder import builder

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
MAIN_PROMPT = _PROJECT_ROOT / "prompts" / "prompts_export_main_empty.md"

SECTION_TITLES = (
    "## 一、总则：规则分级与冲突裁决",
    "## 二、需求完整性与交付红线",
    "## 三、语言与代码风格基线",
    "## 四、可维护性与完整实现",
    "## 五、认知、探索与规划纪律",
    "## 六、实现、工具与验证规范",
    "## 七、安全与文件保护",
    "## 八、技能（Skills）",
    "## 九、交付输出",
)


@pytest.fixture(scope="module")
def main_prompt_text() -> str:
    assert MAIN_PROMPT.exists(), f"缺少文件: {MAIN_PROMPT}"
    return MAIN_PROMPT.read_text(encoding="utf-8")


def _entry_lines(text: str) -> list:
    return [ln for ln in text.splitlines() if ln.startswith("- ")]


class TestEmptyPromptModeSelfDescription:

    def test_prompt_exists(self):
        assert MAIN_PROMPT.exists()

    def test_header_kept(self, main_prompt_text: str):
        """文件头与「# 全局约束」章节保持不变。"""
        assert main_prompt_text.startswith("你是一位乐于助人的软件工程师助手。")
        assert "# 全局约束" in main_prompt_text

    def test_mode_self_description_removed(self, main_prompt_text: str):
        """文件头部的模式自描述两行须已撤除（不留「空模式精简规则集/Ctrl+B」说明）。"""
        assert "精简规则集" not in main_prompt_text
        assert "空模式（empty）" not in main_prompt_text
        assert "Ctrl+B" not in main_prompt_text
        first_lines = main_prompt_text.splitlines()[:4]
        assert all("简单模式" not in ln and "标准模式" not in ln for ln in first_lines)

    def test_baseline_flow_hint_present(self, main_prompt_text: str):
        """须给出未覆盖细节的基本流程兜底（认知 → 计划 → 实施 → 验证）。"""
        assert "先充分认知" in main_prompt_text
        assert "先输出计划" in main_prompt_text
        assert "实施 → 验证" in main_prompt_text


class TestEmptyPromptSectioning:

    @pytest.mark.parametrize("title", SECTION_TITLES)
    def test_section_title_present(self, main_prompt_text: str, title: str):
        """新增的章节分组标题须存在。"""
        assert title in main_prompt_text, f"缺少章节标题: {title}"

    def test_section_titles_are_not_entries(self, main_prompt_text: str):
        """章节标题不得写成规则条目（避免参与条目唯一性检查）。"""
        for title in SECTION_TITLES:
            assert not title.startswith("- ")

    def test_section_titles_ordered(self, main_prompt_text: str):
        """章节标题须按声明顺序出现。"""
        positions = [main_prompt_text.index(title) for title in SECTION_TITLES]
        assert positions == sorted(positions), "章节标题顺序错乱"

    def test_role_line_still_first(self, main_prompt_text: str):
        """文件首行仍为角色设定行。"""
        assert main_prompt_text.splitlines()[0] == "你是一位乐于助人的软件工程师助手。"


class TestEmptyPromptMetaRules:

    def test_grading_rule_present(self, main_prompt_text: str):
        """总则条目须给出规则分级与冲突裁决。"""
        line = next(
            (ln for ln in _entry_lines(main_prompt_text) if "规则分级与冲突裁决" in ln),
            "",
        )
        assert line, "缺少规则分级与冲突裁决条目"
        assert "「红线 · 一票否决」 > 「强制」 > 「默认」" in line
        assert "未标注级别者一律按「强制」执行" in line

    def test_grading_rule_conflict_resolution(self, main_prompt_text: str):
        """总则条目须给出冲突裁决顺序与用户指令优先。"""
        line = next(
            (ln for ln in _entry_lines(main_prompt_text) if "规则分级与冲突裁决" in ln),
            "",
        )
        assert "安全 > 正确性 > 可维护性 > 性能" in line
        assert "保护性规则" in line
        assert "以用户指令为准" in line

    def test_grading_rule_is_forced_not_red_line(self, main_prompt_text: str):
        """总则条目标注「强制」，避免加剧红线通胀。"""
        line = next(
            (ln for ln in _entry_lines(main_prompt_text) if "规则分级与冲突裁决" in ln),
            "",
        )
        assert "（强制）" in line
        assert "（红线 · 一票否决）" not in line

    def test_completion_output_rule_present(self, main_prompt_text: str):
        """须新增「完成输出」条目并点名验证结论。"""
        line = next(
            (ln for ln in _entry_lines(main_prompt_text) if "完成输出" in ln),
            "",
        )
        assert line, "缺少完成输出条目"
        assert "修改范围" in line
        assert "改动内容与动机" in line
        assert "未做事项" in line
        assert "验证结论" in line

    def test_workload_ban_has_user_query_exception(self, main_prompt_text: str):
        """工作量禁令须补上「用户直接询问时如实说明」的例外。"""
        line = next(
            (ln for ln in _entry_lines(main_prompt_text) if "强制禁止讨论、评估、提及、暗示任何工作量相关内容" in ln),
            "",
        )
        assert line, "缺少工作量禁令条目"
        assert "用户直接询问工作量" in line
        assert "如实说明" in line

    def test_bash_opt_and_internal_tool_boundary(self, main_prompt_text: str):
        """内部工具条目须澄清 bash_opt 与 bash 的写文件边界。"""
        line = next(
            (ln for ln in _entry_lines(main_prompt_text) if "强制用内部工具实现所有" in ln),
            "",
        )
        assert line, "缺少内部工具条目"
        assert "不得借其读写任何文件" in line
        assert "写文件与改代码一律走" in line

    def test_comment_rule_clarified(self, main_prompt_text: str):
        """注释禁令须澄清「注释不得作为事实依据」。"""
        line = next(
            (ln for ln in _entry_lines(main_prompt_text) if "禁止采信代码的任何注释" in ln),
            "",
        )
        assert line, "缺少注释约束条目"
        assert "不得作为事实依据" in line
        assert "以代码实际逻辑为准" in line

    def test_completion_rule_adds_standard_order(self, main_prompt_text: str):
        """完成与交付条目须补上标准顺序（修改 → 运行验证 → 审查 → 验收）。"""
        line = next(
            (ln for ln in _entry_lines(main_prompt_text) if "强制所有修改完成后才能检查与交付" in ln),
            "",
        )
        assert line, "缺少完成与交付条目"
        assert "标准顺序为" in line
        assert "全部修改完成 → 直接运行/操作验证 → 审查 → 逐项验收 → 声明完成" in line

    def test_unit_test_rule_adds_scope(self, main_prompt_text: str):
        """单元测试条目须补上适用范围（代码改动 vs 文档/配置/提示词改动）。"""
        line = next(
            (ln for ln in _entry_lines(main_prompt_text) if "每次修改文件后必须补充对应单元测试" in ln),
            "",
        )
        assert line, "缺少单元测试条目"
        assert "代码类改动一律必须补测试" in line
        assert "项目既有测试框架与目录约定" in line

    def test_meta_file_rule_adds_reason(self, main_prompt_text: str):
        """元文件保护条目须补上原因说明。"""
        line = next(
            (ln for ln in _entry_lines(main_prompt_text) if "元文件保护" in ln),
            "",
        )
        assert line, "缺少元文件保护条目"
        assert "运行时状态文件" in line
        assert "跨对话一致性" in line

    def test_dangerous_command_rule_adds_clause(self, main_prompt_text: str):
        """危险命令条目须补充「未经用户明确授权不得执行」的限定。"""
        line = next(
            (ln for ln in _entry_lines(main_prompt_text) if "chmod 777" in ln),
            "",
        )
        assert line, "缺少危险命令条目"
        assert "未经用户明确授权一律不得执行" in line

    def test_subagent_rule_refined(self, main_prompt_text: str):
        """subagent 禁令须修正语病并保留强制性。"""
        line = next(
            (ln for ln in _entry_lines(main_prompt_text) if "调用 subagent" in ln),
            "",
        )
        assert line, "缺少 subagent 约束条目"
        assert "未经用户指定，强制禁止调用 subagent" in line

    def test_serial_dispatch_rule_clarified(self, main_prompt_text: str):
        """串行规则须保留原串并补充说明。"""
        line = next(
            (ln for ln in _entry_lines(main_prompt_text) if "plan execute agent 强制串行" in ln),
            "",
        )
        assert line, "缺少串行约束条目"
        assert "一律串行执行" in line
        assert "禁止同一批并行派发" in line


class TestEmptyPromptNoRegressionOnLockedClauses:

    def test_no_duplicate_entries(self, main_prompt_text: str):
        """条目行仍无完全重复。"""
        entries = _entry_lines(main_prompt_text)
        assert len(entries) == len(set(entries)), "存在重复条目行"

    def test_bash_opt_clause_marker_single(self, main_prompt_text: str):
        """bash_opt 调试/运行/操作 marker 仍只出现一次。"""
        assert main_prompt_text.count("调试/运行/操作强制用 `bash_opt`") == 1

    def test_reduction_enumeration_single(self, main_prompt_text: str):
        """同模板的禁止行为枚举仍只出现一次。"""
        template = "跳过、省略、删减、简化、降级、拆分、拖延"
        assert main_prompt_text.count(template) == 1

    def test_complete_execution_statement_single(self, main_prompt_text: str):
        """「必须完整读取所需内容并完整执行到位」仍只出现一次。"""
        assert main_prompt_text.count("必须完整读取所需内容并完整执行到位") == 1

    def test_new_content_not_leaking_into_other_variants(self):
        """新增语义仅落在 empty 版本，不污染其它提词文件。"""
        for other in ("prompts_export_main.md", "prompts_export_main_simple.md"):
            content = (MAIN_PROMPT.parent / other).read_text(encoding="utf-8")
            assert "规则分级与冲突裁决" not in content, f"{other} 不应包含总则条目"
            assert "完成输出（强制）" not in content, f"{other} 不应包含完成输出条目"

    def test_loaded_prompt_matches_file(self, monkeypatch):
        """空模式下实际加载的首段提词仍等于文件内容。"""
        monkeypatch.setattr(builder, "build_work_md", lambda *a, **k: "")
        monkeypatch.setattr(builder, "build_environment_info", lambda *a, **k: "")
        monkeypatch.setattr(builder, "build_skills_prompt_section", lambda *a, **k: "")
        monkeypatch.setattr(builder, "_build_mcp_section", lambda *a, **k: "")
        original = builder.get_mode()
        try:
            builder.set_mode("empty")
            parts = builder.build_system_prompt(cwd=str(_PROJECT_ROOT))
            assert parts
            assert parts[0] == MAIN_PROMPT.read_text(encoding="utf-8")
        finally:
            builder.set_mode(original)


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
