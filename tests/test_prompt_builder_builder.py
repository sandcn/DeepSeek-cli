"""测试 prompt_builder 模块 — 从 prompts_export_*.md 读取并组装系统提示词

覆盖范围：
1. build_system_prompt() — 主代理系统提示词构建
2. build_subagent_system_prompt() — 子代理系统提示词构建
3. 参数控制位正确性
4. 运行时动态信息（环境、Git）正确性
"""

import re

import pytest

from src.prompt_builder.builder import (
    build_system_prompt,
    build_subagent_system_prompt,
    build_map_agent_system_prompt,
    build_review_agent_system_prompt,
    _load_prompt,
)


# ═══════════════════════════════════════════════════════════
# 基础辅助函数测试
# ═══════════════════════════════════════════════════════════

class TestLoadPrompt:
    def test_load_existing_main_export(self):
        content = _load_prompt("prompts_export_main")
        assert len(content) > 0
        assert "核心目标" in content

    def test_load_existing_sub_export(self):
        content = _load_prompt("prompts_export_sub")
        assert len(content) > 0
        assert "安全规范" in content

    def test_load_existing_memory_guide(self):
        content = _load_prompt("memory_usage_guidelines_main")
        assert len(content) > 0
        assert "记忆" in content

    def test_load_nonexistent_file(self):
        content = _load_prompt("non_existent_file_xyz")
        assert content == ""

    def test_all_existing_prompts_loadable(self):
        """当前所有存在的 prompts 文件均可正常加载"""
        existing_prompts = [
            "prompts_export_main",
            "prompts_export_sub",
            "prompts_export_map",
            "prompts_export_review",
            "memory_usage_guidelines_main",
        ]
        for name in existing_prompts:
            content = _load_prompt(name)
            assert len(content) > 0, f"prompts/{name}.md 读取失败或为空"


# ═══════════════════════════════════════════════════════════
# MainAgent 系统提示词构建测试
# ═══════════════════════════════════════════════════════════

class TestBuildSystemPrompt:
    def test_returns_list_of_strings(self):
        result = build_system_prompt()
        assert isinstance(result, list)
        assert all(isinstance(p, str) for p in result)

    def test_has_required_sections(self):
        result = build_system_prompt()
        full = "\n".join(result)
        sections = [
            "核心目标",
            "安全规范",
            "测试规范",
            "Code Review",
            "调用链分析委托 map SubAgent",
            "代码修改工作流",
            "当前执行环境",
        ]
        for section in sections:
            assert section in full, f"缺少必需章节: {section}"

    def test_no_residual_cut_markers(self):
        """build_system_prompt 的输出不应残留裁剪标记"""
        result = build_system_prompt()
        full = "\n".join(result)
        markers = re.findall(r'<!--\s*SUBAGENT_[A-Z_]+', full)
        assert len(markers) == 0, f"残留裁剪标记: {markers}"

    def test_can_exclude_version_control(self):
        result = build_system_prompt(include_version_control=False)
        full = "\n".join(result)
        # 「版本控制」作为环境信息的一部分不应出现在最后一条消息中
        # （注意：tool_usage_rules 中有"Git 版本控制目录"字样，
        #  所以不能简单搜索"版本控制"子串）
        last_part = result[-1]
        assert "版本控制" not in last_part, "环境信息中不应包含版本控制信息"

    def test_parts_are_unique_nonempty(self):
        result = build_system_prompt()
        non_empty = [p for p in result if p.strip()]
        assert len(non_empty) == len(result), "存在空 part"

    def test_old_include_code_workflow_param_raises_type_error(self):
        """已废弃的参数应抛出 TypeError"""
        with pytest.raises(TypeError):
            build_system_prompt(include_code_workflow=False)

    def test_old_include_project_summary_param_raises_type_error(self):
        """已废弃的参数应抛出 TypeError"""
        with pytest.raises(TypeError):
            build_system_prompt(include_project_summary=False)

    def test_old_extra_modules_param_raises_type_error(self):
        """已废弃的参数应抛出 TypeError"""
        with pytest.raises(TypeError):
            build_system_prompt(extra_modules=["non_existent_module"])

    def test_old_empty_extra_modules_param_raises_type_error(self):
        """已废弃的参数应抛出 TypeError"""
        with pytest.raises(TypeError):
            build_system_prompt(extra_modules=[])


# ═══════════════════════════════════════════════════════════
# SubAgent 系统提示词构建测试
# ═══════════════════════════════════════════════════════════

class TestBuildSubagentSystemPrompt:
    def test_returns_list_of_strings(self):
        result = build_subagent_system_prompt()
        assert isinstance(result, list)
        assert all(isinstance(p, str) for p in result)

    def test_has_required_sections(self):
        result = build_subagent_system_prompt()
        full = "\n".join(result)
        sections = [
            "安全规范",
            "大模型幻觉防止规范",
            "测试规范",
            "当前执行环境",
        ]
        for section in sections:
            assert section in full, f"缺少必需章节: {section}"

    def test_tool_usage_sections_present(self):
        """SubAgent 应包含工具使用相关的章节"""
        result = build_subagent_system_prompt()
        full = "\n".join(result)
        # 工具相关规则已迁移至各工具 schema（如 bash「禁止替代专用工具」），
        # 此处验证幻觉防止规范存在（工具误用核心防线）
        assert "先读后写" in full

    def test_no_residual_cut_markers(self):
        """SubAgent 输出不应残留 HTML 裁剪标记"""
        result = build_subagent_system_prompt()
        full = "\n".join(result)
        markers = re.findall(r'<!--\s*SUBAGENT_[A-Z_]+', full)
        assert len(markers) == 0, f"残留裁剪标记: {markers}"

    def test_subagent_content_non_empty(self):
        """SubAgent 输出应非空"""
        result = build_subagent_system_prompt()
        assert len(result) > 0
        assert any(len(p.strip()) > 0 for p in result)

    def test_subagent_can_exclude_version_control(self):
        """SubAgent 支持 include_version_control=False 参数"""
        result = build_subagent_system_prompt(include_version_control=False)
        full = "\n".join(result)
        last_part = result[-1]
        # 环境信息（不含版本控制）不应出现在最后一条消息中
        # 最后一条是 memory_usage_guidelines_main，搜索「版本控制」不在其中
        assert "版本控制" not in last_part, "include_version_control=False 时不应包含版本控制信息"


# ═══════════════════════════════════════════════════════════
# Map Agent 系统提示词构建测试
# ═══════════════════════════════════════════════════════════

class TestBuildMapAgentSystemPrompt:
    def test_returns_list_of_strings(self):
        result = build_map_agent_system_prompt()
        assert isinstance(result, list)
        assert all(isinstance(p, str) for p in result)

    def test_has_analysis_focus(self):
        """Map 提示词应聚焦分析而非修改"""
        result = build_map_agent_system_prompt()
        full = "\n".join(result)
        assert "项目地图" in full or "分析理解" in full or "代码理解" in full

    def test_has_security_rules(self):
        """Map 提示词应包含安全红线"""
        result = build_map_agent_system_prompt()
        full = "\n".join(result)
        assert "禁止读写传密钥" in full

    def test_no_code_modification_rules(self):
        """Map 提示词不应包含代码修改工作流"""
        result = build_map_agent_system_prompt()
        full = "\n".join(result)
        assert "代码修改工作流" not in full
        assert "Bug 修复设计优先原则" not in full

    def test_has_hallucination_prevention(self):
        """Map 提示词应包含幻觉防止规范"""
        result = build_map_agent_system_prompt()
        full = "\n".join(result)
        assert "大模型幻觉防止规范" in full
        assert "先读后写" in full

    def test_has_environment_info(self):
        """Map 提示词应包含运行时环境信息"""
        result = build_map_agent_system_prompt()
        full = "\n".join(result)
        assert "当前执行环境" in full

    def test_has_structured_output_format(self):
        """Map 提示词应包含结构化输出规范"""
        result = build_map_agent_system_prompt()
        full = "\n".join(result)
        assert "结构化输出规范" in full or "项目地图输出" in full or "调用关系输出" in full

    def test_has_project_exploration_guide(self):
        """Map 提示词应包含项目探底指南"""
        result = build_map_agent_system_prompt()
        full = "\n".join(result)
        assert "项目探底" in full

    def test_content_non_empty(self):
        """Map 提示词应非空"""
        result = build_map_agent_system_prompt()
        assert len(result) > 0
        assert any(len(p.strip()) > 0 for p in result)

    def test_can_exclude_version_control(self):
        """Map 提示词支持 include_version_control=False"""
        result = build_map_agent_system_prompt(include_version_control=False)
        last_part = result[-1]
        assert "版本控制" not in last_part

    def test_no_memory_guide(self):
        """Map 提示词不应包含跨对话记忆使用指南"""
        result = build_map_agent_system_prompt()
        full = "\n".join(result)
        assert "跨对话记忆系统使用指南" not in full
        assert "memory.md" not in full or "结构" not in full

    def test_with_cwd(self):
        """指定 cwd 不应崩溃"""
        import os
        result = build_map_agent_system_prompt(cwd=os.getcwd())
        assert isinstance(result, list)
        assert len(result) > 0


# ═══════════════════════════════════════════════════════════
# Review Agent 系统提示词构建测试
# ═══════════════════════════════════════════════════════════

class TestBuildReviewAgentSystemPrompt:
    def test_returns_list_of_strings(self):
        result = build_review_agent_system_prompt()
        assert isinstance(result, list)
        assert all(isinstance(p, str) for p in result)

    def test_has_review_focus(self):
        """Review 提示词应聚焦代码审查"""
        result = build_review_agent_system_prompt()
        full = "\n".join(result)
        assert "Code Review" in full or "代码审查" in full or "审查" in full

    def test_has_security_rules(self):
        """Review 提示词应包含安全红线"""
        result = build_review_agent_system_prompt()
        full = "\n".join(result)
        assert "禁止读写传密钥" in full

    def test_has_problem_grading(self):
        """Review 提示词应包含 P0-P3 问题分级"""
        result = build_review_agent_system_prompt()
        full = "\n".join(result)
        assert "P0" in full
        assert "P1" in full
        assert "P2" in full
        assert "P3" in full

    def test_has_review_checklist(self):
        """Review 提示词应包含自审检查清单"""
        result = build_review_agent_system_prompt()
        full = "\n".join(result)
        assert "自审检查清单" in full or "检查清单" in full

    def test_has_hallucination_prevention(self):
        """Review 提示词应包含幻觉防止规范"""
        result = build_review_agent_system_prompt()
        full = "\n".join(result)
        assert "大模型幻觉防止规范" in full
        assert "先读后写" in full

    def test_has_environment_info(self):
        """Review 提示词应包含运行时环境信息"""
        result = build_review_agent_system_prompt()
        full = "\n".join(result)
        assert "当前执行环境" in full

    def test_has_output_format(self):
        """Review 提示词应包含审查报告输出格式"""
        result = build_review_agent_system_prompt()
        full = "\n".join(result)
        assert "审查报告" in full or "输出格式" in full

    def test_no_project_exploration_guide(self):
        """Review 提示词不应包含项目探底指南（那是 map 类型的职责）"""
        result = build_review_agent_system_prompt()
        full = "\n".join(result)
        assert "项目探底" not in full

    def test_content_non_empty(self):
        """Review 提示词应非空"""
        result = build_review_agent_system_prompt()
        assert len(result) > 0
        assert any(len(p.strip()) > 0 for p in result)

    def test_can_exclude_version_control(self):
        """Review 提示词支持 include_version_control=False"""
        result = build_review_agent_system_prompt(include_version_control=False)
        last_part = result[-1]
        assert "版本控制" not in last_part

    def test_no_memory_guide(self):
        """Review 提示词不应包含跨对话记忆使用指南"""
        result = build_review_agent_system_prompt()
        full = "\n".join(result)
        assert "跨对话记忆系统使用指南" not in full

    def test_with_cwd(self):
        """指定 cwd 不应崩溃"""
        import os
        result = build_review_agent_system_prompt(cwd=os.getcwd())
        assert isinstance(result, list)
        assert len(result) > 0


# ═══════════════════════════════════════════════════════════
# 运行时环境信息测试
# ═══════════════════════════════════════════════════════════

class TestEnvironmentInfo:
    def test_environment_info_present(self):
        """系统提示词应包含当前执行环境信息"""
        result = build_system_prompt()
        full = "\n".join(result)
        assert "当前执行环境" in full
        assert "操作系统" in full
        assert "Python" in full

    def test_subagent_environment_info_present(self):
        result = build_subagent_system_prompt()
        full = "\n".join(result)
        assert "当前执行环境" in full


# ═══════════════════════════════════════════════════════════
# 边界测试 — 空/异常输入
# ═══════════════════════════════════════════════════════════

class TestEdgeCases:
    def test_build_system_prompt_with_cwd(self):
        """指定 cwd 时不应崩溃"""
        import os
        result = build_system_prompt(cwd=os.getcwd())
        assert isinstance(result, list)
        assert len(result) > 0

    def test_build_subagent_with_cwd(self):
        import os
        result = build_subagent_system_prompt(cwd=os.getcwd())
        assert isinstance(result, list)
        assert len(result) > 0

    def test_both_prompts_all_sections_non_empty(self):
        main = build_system_prompt()
        sub = build_subagent_system_prompt()
        assert all(len(p) > 0 for p in main), "MainAgent 有空 part"
        assert all(len(p) > 0 for p in sub), "SubAgent 有空 part"


# ═══════════════════════════════════════════════════════════
# 兜底回退测试 — 文件丢失时使用 _FALLBACK_*_PROMPT
# ═══════════════════════════════════════════════════════════

class TestFallbackPrompt:
    """验证 prompts_export_*.md 文件缺失时正确回退到兜底提示词"""

    @pytest.fixture(autouse=True)
    def patch_load_prompt(self, monkeypatch):
        """让 _load_prompt 对 'prompts_export_' 前缀返回空，模拟文件丢失"""
        original_load = _load_prompt

        def mock_load(name: str) -> str:
            if name.startswith("prompts_export_"):
                return ""
            return original_load(name)

        monkeypatch.setattr("src.prompt_builder.builder._load_prompt", mock_load)

    def test_main_fallback_contains_security_rules(self):
        """MainAgent 兜底提示词应包含安全红线"""
        result = build_system_prompt()
        full = "\n".join(result)
        assert "禁止读写密钥" in full
        assert "禁止 rm -rf" in full

    def test_main_fallback_contains_plan_principle(self):
        """MainAgent 兜底提示词应包含「凡事预则立」原则"""
        result = build_system_prompt()
        full = "\n".join(result)
        assert "先输出完整计划" in full

    def test_main_fallback_still_has_runtime_info(self):
        """文件丢失时仍应包含运行时环境信息和记忆指南"""
        result = build_system_prompt()
        full = "\n".join(result)
        assert "当前执行环境" in full
        assert "记忆" in full

    def test_sub_fallback_contains_security_rules(self):
        """SubAgent 兜底提示词应包含安全红线"""
        result = build_subagent_system_prompt()
        full = "\n".join(result)
        assert "禁止读写密钥" in full
        assert "禁止 rm -rf" in full

    def test_sub_fallback_contains_plan_principle(self):
        """SubAgent 兜底提示词应包含「凡事预则立」原则"""
        result = build_subagent_system_prompt()
        full = "\n".join(result)
        assert "先输出完整计划" in full

    def test_sub_fallback_still_has_runtime_info(self):
        """文件丢失时仍应包含运行时环境信息和记忆指南"""
        result = build_subagent_system_prompt()
        full = "\n".join(result)
        assert "当前执行环境" in full
        assert "记忆" in full

    def test_both_fallbacks_have_tool_usage(self):
        """兜底提示词应包含基本的工具使用说明"""
        main = "\n".join(build_system_prompt())
        sub = "\n".join(build_subagent_system_prompt())
        assert "read_file" in main
        assert "read_file" in sub

    def test_fallback_parts_count(self):
        """文件丢失时仍应包含 3+ 个 part（兜底 + 环境信息 + 记忆指南）"""
        result = build_system_prompt()
        assert len(result) >= 3
        result = build_subagent_system_prompt()
        assert len(result) >= 3
