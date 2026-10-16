"""prompts_export_main_empty.md「强制使用现有代码实现功能」规则回归测试。

需求（2026-10-17）：全局约束中新增规则，强制实现功能时使用项目现有代码——
实现前先检索项目内是否已有可直接复用或扩展的实现，优先复用/扩展现有代码，
禁止在已有等价能力时另起炉灶、重复实现或重复造轮子。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.prompt_builder import builder

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
MAIN_PROMPT = _PROJECT_ROOT / "prompts" / "prompts_export_main_empty.md"

RULE_TITLE = "强制使用现有代码实现功能"


@pytest.fixture(scope="module")
def main_prompt_text() -> str:
    assert MAIN_PROMPT.exists(), f"缺少文件: {MAIN_PROMPT}"
    return MAIN_PROMPT.read_text(encoding="utf-8")


def _rule_line(text: str) -> str:
    return next((ln for ln in text.splitlines() if RULE_TITLE in ln), "")


class TestMainReuseExistingCodeRule:

    def test_prompt_exists(self):
        """提示词文件存在。"""
        assert MAIN_PROMPT.exists()

    def test_rule_present(self, main_prompt_text: str):
        """提词须含「强制使用现有代码实现功能」规则。"""
        assert RULE_TITLE in main_prompt_text

    def test_rule_is_red_line(self, main_prompt_text: str):
        """该规则须为「红线 · 一票否决」级别。"""
        assert "（红线 · 一票否决）" in _rule_line(main_prompt_text)

    def test_rule_requires_search_before_implement(self, main_prompt_text: str):
        """须要求实现前先检索项目现有实现（search/find/read_file）。"""
        line = _rule_line(main_prompt_text)
        assert "`search`" in line, "应点名 search 工具检索现有代码"
        assert "`find`" in line, "应点名 find 工具检索现有文件"
        assert "`read_file`" in line, "应点名 read_file 读取现有实现"
        assert "检索" in line

    def test_rule_prefers_reuse_over_rewrite(self, main_prompt_text: str):
        """须明确复用优先于重写、扩展优先于新建。"""
        line = _rule_line(main_prompt_text)
        assert "复用优先于重写" in line
        assert "扩展优先于新建" in line

    def test_rule_forbids_duplicate_implementation(self, main_prompt_text: str):
        """须禁止另起炉灶、重复实现、重复造轮子、重复代码。"""
        line = _rule_line(main_prompt_text)
        for word in ("另起炉灶", "重复实现", "重复造轮子", "重复代码"):
            assert word in line, f"应禁止{word}"

    def test_rule_connects_existing_architecture(self, main_prompt_text: str):
        """须要求新增能力接入既有架构与抽象（开闭原则）。"""
        line = _rule_line(main_prompt_text)
        assert "既有架构与抽象" in line
        assert "开闭原则" in line

    def test_rule_requires_reason_when_new_code(self, main_prompt_text: str):
        """确无可复用实现时才允许新增，且须说明原因。"""
        line = _rule_line(main_prompt_text)
        assert "确无可复用实现" in line
        assert "说明无法复用现有实现的原因" in line

    def test_rule_is_single_entry(self, main_prompt_text: str):
        """该规则不得拆成多条重复条目。"""
        entries = [
            ln for ln in main_prompt_text.splitlines()
            if ln.startswith("- ") and RULE_TITLE in ln
        ]
        assert len(entries) == 1, "该规则条目应唯一"

    def test_rule_inside_maintainability_section(self, main_prompt_text: str):
        """新规则落在「四、可维护性与完整实现」章节内。"""
        section_start = main_prompt_text.index("## 四、可维护性与完整实现")
        section_end = main_prompt_text.index("## 五、认知、探索与规划纪律")
        assert section_start < main_prompt_text.index(RULE_TITLE) < section_end

    def test_keeps_existing_maintainability_rule(self, main_prompt_text: str):
        """不得破坏原有可维护性红线与复用单元表述。"""
        assert "强制实现代码时必须考虑后续扩展和维护（红线 · 一票否决）" in main_prompt_text
        assert "必须降低重复代码（公共逻辑抽为复用单元）" in main_prompt_text
        assert "必须与项目既有架构、约定、目录规范保持一致" in main_prompt_text

    def test_no_duplicate_entries(self, main_prompt_text: str):
        """新增条目后条目行仍无完全重复。"""
        entries = [ln for ln in main_prompt_text.splitlines() if ln.startswith("- ")]
        assert len(entries) == len(set(entries)), "存在重复条目行"

    def test_loaded_prompt_contains_rule(self, monkeypatch):
        """空模式下实际加载的首段提词包含该规则。"""
        monkeypatch.setattr(builder, "build_work_md", lambda *a, **k: "")
        monkeypatch.setattr(builder, "build_environment_info", lambda *a, **k: "")
        monkeypatch.setattr(builder, "build_skills_prompt_section", lambda *a, **k: "")
        monkeypatch.setattr(builder, "_build_mcp_section", lambda *a, **k: "")
        original = builder.get_mode()
        try:
            builder.set_mode("empty")
            parts = builder.build_system_prompt(cwd=str(_PROJECT_ROOT))
            assert parts
            assert RULE_TITLE in parts[0]
        finally:
            builder.set_mode(original)


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
