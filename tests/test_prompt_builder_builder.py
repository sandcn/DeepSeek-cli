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
    _build_prompt,
    build_system_prompt,
    build_subagent_system_prompt,
    build_map_agent_system_prompt,
    build_review_agent_system_prompt,
    build_plan_agent_system_prompt,
    build_read_memory_agent_system_prompt,
    build_write_memory_agent_system_prompt,
    build_execute_agent_system_prompt,
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
            "prompts_export_plan",
            "prompts_export_execute",
            "prompts_export_read_memory",
            "prompts_export_write_memory",
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
            "验证修改",
            "review — 代码审查",
            "map — 代码分析/探底",
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

    def test_has_build_compile_rule(self):
        """验证修改章节中包含构建/编译规则"""
        result = build_system_prompt()
        full = "\n".join(result)
        # 确保「验证修改」章节中包含构建/编译规则
        assert "构建/编译" in full, "系统提示词中缺少「构建/编译」规则"
        # 确保包含常见构建系统关键词（至少2个）
        # 使用高唯一性关键词（仅在构建/编译规则中出现，其他章节不包含）确保分辨力
        build_keywords = ["package.json", "go.mod", "pyproject.toml"]
        found = [kw for kw in build_keywords if kw in full]
        assert len(found) >= 2, f"构建系统关键词缺失，仅找到: {found}"

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

    def test_has_bug_confirm_rule(self):
        """验证系统提示词包含「Bug 确认 — 日志或测试重现（强制）」规则"""
        result = build_system_prompt()
        full = "\n".join(result)
        assert "Bug 确认 — 日志或测试重现（强制）" in full


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
        assert "版本控制" not in last_part, "include_version_control=False 时不应包含版本控制信息"

    def test_has_bug_confirm_rule(self):
        """验证子代理系统提示词包含「Bug 确认 — 日志或测试重现（强制）」规则"""
        result = build_subagent_system_prompt()
        full = "\n".join(result)
        assert "Bug 确认 — 日志或测试重现（强制）" in full


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
        """文件丢失时仍应包含运行时环境信息"""
        result = build_system_prompt()
        full = "\n".join(result)
        assert "当前执行环境" in full

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
        """文件丢失时仍应包含运行时环境信息"""
        result = build_subagent_system_prompt()
        full = "\n".join(result)
        assert "当前执行环境" in full

    def test_both_fallbacks_have_tool_usage(self):
        """兜底提示词应包含基本的工具使用说明"""
        main = "\n".join(build_system_prompt())
        sub = "\n".join(build_subagent_system_prompt())
        assert "read_file" in main
        assert "read_file" in sub

    def test_fallback_parts_count(self):
        """文件丢失时仍应包含 2+ 个 part（兜底 + 环境信息）"""
        result = build_system_prompt()
        assert len(result) >= 2
        result = build_subagent_system_prompt()
        assert len(result) >= 2

# ═══════════════════════════════════════════════════════════
# include_init_md 参数行为测试
# ═══════════════════════════════════════════════════════════

class TestIncludeInitMdParam:
    """验证 _build_prompt() 的 include_init_md 参数控制 init.md 加载行为"""

    MOCK_INIT_MD = (
        "# 测试项目\n"
        "这是一个测试项目。\n\n"
        "## 核心功能\n"
        "- 功能A：自动化测试\n"
        "- 功能B：代码生成\n\n"
        "## 技术栈\n"
        "- Python 3.13\n"
        "- pytest\n"
    )

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path, monkeypatch):
        """在每个测试前：
        1. 在 tmp_path 创建 init.md
        2. mock _load_prompt 返回固定假内容，避免依赖 prompts/ 目录
        """
        # 创建 init.md 供 build_init_md_summary 读取
        init_path = tmp_path / "init.md"
        init_path.write_text(self.MOCK_INIT_MD, encoding="utf-8")

        # mock _load_prompt 返回固定内容
        monkeypatch.setattr(
            "src.prompt_builder.builder._load_prompt",
            lambda name: "MOCKED_PROMPT_CONTENT",
        )

        self.cwd = str(tmp_path)

    def test_include_init_md_true_includes_summary(self):
        """include_init_md=True 时输出应包含「项目摘要」"""
        result = _build_prompt(
            "test_export", "fallback", cwd=self.cwd, include_init_md=True
        )
        full = "\n".join(result)
        assert "项目摘要" in full, "include_init_md=True 时应包含项目摘要"
        assert "功能A" in full, "摘要应包含核心功能项"

    def test_include_init_md_false_excludes_summary(self):
        """include_init_md=False 时输出不应包含「项目摘要」"""
        result = _build_prompt(
            "test_export", "fallback", cwd=self.cwd, include_init_md=False
        )
        full = "\n".join(result)
        assert "项目摘要" not in full, "include_init_md=False 时不应包含项目摘要"
        assert "功能A" not in full, "include_init_md=False 时不应包含核心功能项"

    def test_include_init_md_default_is_true(self):
        """include_init_md 默认值为 True"""
        result = _build_prompt("test_export", "fallback", cwd=self.cwd)
        full = "\n".join(result)
        assert "项目摘要" in full, "include_init_md 默认值应为 True"


# ═══════════════════════════════════════════════════════════
# SubAgent 排除 init.md 测试
# ═══════════════════════════════════════════════════════════

class TestSubAgentExcludesInitMd:
    """验证所有 7 种 SubAgent 类型的 system prompt 不包含 init.md 项目摘要"""

    MOCK_INIT_MD = (
        "# 测试项目\n"
        "这是一个测试项目。\n\n"
        "## 核心功能\n"
        "- 功能A：自动化测试\n"
        "- 功能B：代码生成\n\n"
        "## 技术栈\n"
        "- Python 3.13\n"
        "- pytest\n"
    )

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path, monkeypatch):
        """在 tmp_path 创建 init.md，mock _load_prompt 隔离 prompts 文件依赖"""
        init_path = tmp_path / "init.md"
        init_path.write_text(self.MOCK_INIT_MD, encoding="utf-8")
        assert init_path.exists(), "init.md 应已创建"

        # mock _load_prompt 返回固定内容，避免依赖真实 prompts/ 目录
        monkeypatch.setattr(
            "src.prompt_builder.builder._load_prompt",
            lambda name: "MOCKED_PROMPT_CONTENT",
        )

        self.cwd = str(tmp_path)

    def test_sub_agent_excludes_init_md(self):
        """sub 类型不应包含项目摘要"""
        result = build_subagent_system_prompt(cwd=self.cwd)
        full = "\n".join(result)
        assert "项目摘要" not in full

    def test_map_agent_excludes_init_md(self):
        """map 类型不应包含项目摘要"""
        result = build_map_agent_system_prompt(cwd=self.cwd)
        full = "\n".join(result)
        assert "项目摘要" not in full

    def test_review_agent_excludes_init_md(self):
        """review 类型不应包含项目摘要"""
        result = build_review_agent_system_prompt(cwd=self.cwd)
        full = "\n".join(result)
        assert "项目摘要" not in full

    def test_plan_agent_excludes_init_md(self):
        """plan 类型不应包含项目摘要"""
        result = build_plan_agent_system_prompt(cwd=self.cwd)
        full = "\n".join(result)
        assert "项目摘要" not in full

    def test_read_memory_agent_excludes_init_md(self):
        """read_memory 类型不应包含项目摘要"""
        result = build_read_memory_agent_system_prompt(cwd=self.cwd)
        full = "\n".join(result)
        assert "项目摘要" not in full

    def test_write_memory_agent_excludes_init_md(self):
        """write_memory 类型不应包含项目摘要"""
        result = build_write_memory_agent_system_prompt(cwd=self.cwd)
        full = "\n".join(result)
        assert "项目摘要" not in full

    def test_execute_agent_excludes_init_md(self):
        """execute 类型不应包含项目摘要"""
        result = build_execute_agent_system_prompt(cwd=self.cwd)
        full = "\n".join(result)
        assert "项目摘要" not in full


# ═══════════════════════════════════════════════════════════
# MainAgent 包含 init.md 测试
# ═══════════════════════════════════════════════════════════

class TestMainAgentIncludesInitMd:
    """验证 Main Agent 的 system prompt 包含 init.md 项目摘要"""

    MOCK_INIT_MD = (
        "# 测试项目\n"
        "这是一个测试项目。\n\n"
        "## 核心功能\n"
        "- 功能A：自动化测试\n"
        "- 功能B：代码生成\n\n"
        "## 技术栈\n"
        "- Python 3.13\n"
        "- pytest\n"
    )

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path, monkeypatch):
        """在 tmp_path 创建 init.md，mock _load_prompt 隔离 prompts 文件依赖"""
        init_path = tmp_path / "init.md"
        init_path.write_text(self.MOCK_INIT_MD, encoding="utf-8")
        assert init_path.exists(), "init.md 应已创建"

        # mock _load_prompt 返回固定内容，避免依赖真实 prompts/ 目录
        monkeypatch.setattr(
            "src.prompt_builder.builder._load_prompt",
            lambda name: "MOCKED_PROMPT_CONTENT",
        )

        self.cwd = str(tmp_path)

    def test_main_agent_includes_init_md(self):
        """Main Agent 应包含项目摘要（include_init_md 默认 True）"""
        result = build_system_prompt(cwd=self.cwd)
        full = "\n".join(result)
        assert "项目摘要" in full, "Main Agent 应包含项目摘要"
        assert "功能A" in full, "摘要应包含核心功能项"


# ═══════════════════════════════════════════════════════════
# Plan Agent 系统提示词构建测试
# ═══════════════════════════════════════════════════════════

class TestBuildPlanAgentSystemPrompt:
    def test_returns_list_of_strings(self):
        result = build_plan_agent_system_prompt()
        assert isinstance(result, list)
        assert all(isinstance(p, str) for p in result)

    def test_has_security_rules(self):
        """Plan 提示词应包含安全红线"""
        result = build_plan_agent_system_prompt()
        full = "\n".join(result)
        assert "禁止读写传密钥" in full

    def test_has_decision_framework(self):
        """Plan 提示词应包含决策框架"""
        result = build_plan_agent_system_prompt()
        full = "\n".join(result)
        assert "决策框架" in full

    def test_has_environment_info(self):
        """Plan 提示词应包含运行时环境信息"""
        result = build_plan_agent_system_prompt()
        full = "\n".join(result)
        assert "当前执行环境" in full

    def test_content_non_empty(self):
        """Plan 提示词应非空"""
        result = build_plan_agent_system_prompt()
        assert len(result) > 0
        assert any(len(p.strip()) > 0 for p in result)

    def test_can_exclude_version_control(self):
        """Plan 提示词支持 include_version_control=False"""
        result = build_plan_agent_system_prompt(include_version_control=False)
        last_part = result[-1]
        assert "版本控制" not in last_part


# ═══════════════════════════════════════════════════════════
# ReadMemory Agent 系统提示词构建测试
# ═══════════════════════════════════════════════════════════

class TestBuildReadMemoryAgentSystemPrompt:
    def test_returns_list_of_strings(self):
        result = build_read_memory_agent_system_prompt()
        assert isinstance(result, list)
        assert all(isinstance(p, str) for p in result)

    def test_has_read_only_constraint(self):
        """ReadMemory 提示词应包含只读约束说明"""
        result = build_read_memory_agent_system_prompt()
        full = "\n".join(result)
        assert "只读" in full

    def test_has_memory_directory_focus(self):
        """ReadMemory 提示词应聚焦 .chat/memory/ 目录"""
        result = build_read_memory_agent_system_prompt()
        full = "\n".join(result)
        assert ".chat/memory/" in full

    def test_has_environment_info(self):
        """ReadMemory 提示词应包含运行时环境信息"""
        result = build_read_memory_agent_system_prompt()
        full = "\n".join(result)
        assert "当前执行环境" in full

    def test_content_non_empty(self):
        """ReadMemory 提示词应非空"""
        result = build_read_memory_agent_system_prompt()
        assert len(result) > 0
        assert any(len(p.strip()) > 0 for p in result)

    def test_can_exclude_version_control(self):
        """ReadMemory 提示词支持 include_version_control=False"""
        result = build_read_memory_agent_system_prompt(include_version_control=False)
        last_part = result[-1]
        assert "版本控制" not in last_part


# ═══════════════════════════════════════════════════════════
# WriteMemory Agent 系统提示词构建测试
# ═══════════════════════════════════════════════════════════

class TestBuildWriteMemoryAgentSystemPrompt:
    def test_returns_list_of_strings(self):
        result = build_write_memory_agent_system_prompt()
        assert isinstance(result, list)
        assert all(isinstance(p, str) for p in result)

    def test_has_security_rules(self):
        """WriteMemory 提示词应包含安全红线"""
        result = build_write_memory_agent_system_prompt()
        full = "\n".join(result)
        assert "禁止读写传密钥" in full

    def test_has_hallucination_prevention(self):
        """WriteMemory 提示词应包含幻觉防止规范"""
        result = build_write_memory_agent_system_prompt()
        full = "\n".join(result)
        assert "大模型幻觉防止" in full

    def test_has_environment_info(self):
        """WriteMemory 提示词应包含运行时环境信息"""
        result = build_write_memory_agent_system_prompt()
        full = "\n".join(result)
        assert "当前执行环境" in full

    def test_content_non_empty(self):
        """WriteMemory 提示词应非空"""
        result = build_write_memory_agent_system_prompt()
        assert len(result) > 0
        assert any(len(p.strip()) > 0 for p in result)

    def test_can_exclude_version_control(self):
        """WriteMemory 提示词支持 include_version_control=False"""
        result = build_write_memory_agent_system_prompt(include_version_control=False)
        last_part = result[-1]
        assert "版本控制" not in last_part


# ═══════════════════════════════════════════════════════════
# Execute Agent 系统提示词构建测试
# ═══════════════════════════════════════════════════════════

class TestBuildExecuteAgentSystemPrompt:
    def test_returns_list_of_strings(self):
        result = build_execute_agent_system_prompt()
        assert isinstance(result, list)
        assert all(isinstance(p, str) for p in result)

    def test_has_security_rules(self):
        """Execute 提示词应包含安全红线"""
        result = build_execute_agent_system_prompt()
        full = "\n".join(result)
        assert "禁止读写传密钥" in full

    def test_has_decision_framework(self):
        """Execute 提示词应包含决策框架"""
        result = build_execute_agent_system_prompt()
        full = "\n".join(result)
        assert "决策框架" in full

    def test_has_environment_info(self):
        """Execute 提示词应包含运行时环境信息"""
        result = build_execute_agent_system_prompt()
        full = "\n".join(result)
        assert "当前执行环境" in full

    def test_content_non_empty(self):
        """Execute 提示词应非空"""
        result = build_execute_agent_system_prompt()
        assert len(result) > 0
        assert any(len(p.strip()) > 0 for p in result)

    def test_can_exclude_version_control(self):
        """Execute 提示词支持 include_version_control=False"""
        result = build_execute_agent_system_prompt(include_version_control=False)
        last_part = result[-1]
        assert "版本控制" not in last_part
