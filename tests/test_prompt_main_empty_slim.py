"""prompts_export_main_empty.md「精简版」回归测试。

需求（2026-09-10）：该提词文件精简为极限版 —— 只保留红线级与安全类约束，
删除非红线条目（流程性/风格性要求），同时保持既有三条红线规则完整可断言。
"""

from __future__ import annotations

from pathlib import Path

import pytest

MAIN_PROMPT = Path(__file__).resolve().parent.parent / "prompts" / "prompts_export_main_empty.md"

KEPT_RULES = (
    "测试强制（红线 · 零豁免）",
    "强制完整实现用户的所有要求（红线 · 零豁免）",
    "强制用内部工具实现所有（红线 · 一票否决）",
    "只能使用相对路径（红线 · 一票否决）",
    "元文件保护",
    "read_image",
    "禁止 rm -rf / mkfs / dd / chmod 777 / sudo / chown",
)

REMOVED_RULES = (
    "强势禁止因为难就不做",
    "禁止采信代码的任何注释",
    "碰到失败，分析失败原因",
    "禁止用时间去评任务的复杂度",
    "plan execute agent 强制串行",
    "禁止说工作量太大",
    "必须增加对应的单元测试",
    "review agent 的提词",
    "强制禁止调用subagent",
)


@pytest.fixture(scope="module")
def main_prompt_text() -> str:
    assert MAIN_PROMPT.exists(), f"缺少文件: {MAIN_PROMPT}"
    return MAIN_PROMPT.read_text(encoding="utf-8")


class TestMainPromptSlim:

    def test_prompt_exists(self):
        """提词文件存在。"""
        assert MAIN_PROMPT.exists()

    def test_prompt_is_slim(self, main_prompt_text: str):
        """精简后行数仍受限（不含空行统计）；红线级条目增补后上限同步放宽。"""
        lines = [ln for ln in main_prompt_text.splitlines() if ln.strip()]
        assert len(lines) <= 12, f"精简后非空行数应 <= 12，实际 {len(lines)}"

    def test_prompt_keeps_red_line_rules(self, main_prompt_text: str):
        """红线级与安全类规则必须保留。"""
        for rule in KEPT_RULES:
            assert rule in main_prompt_text, f"应保留规则: {rule}"

    def test_prompt_removes_non_red_line_rules(self, main_prompt_text: str):
        """非红线条目应被删除。"""
        for rule in REMOVED_RULES:
            assert rule not in main_prompt_text, f"应删除非红线条目: {rule}"

    def test_prompt_starts_with_role(self, main_prompt_text: str):
        """首行保留角色设定。"""
        assert main_prompt_text.splitlines()[0].strip() == "你是一位乐于助人的软件工程师助手。"


class TestMainPromptSlimBuildIntegration:

    def test_builder_loads_slim_prompt(self):
        """builder 空模式仍能加载精简后的提词（不破坏系统提示词构建）。"""
        from src.prompt_builder import builder

        origin = builder.is_empty_mode()
        builder.set_empty_mode(True)
        try:
            prompts = builder.build_system_prompt(cwd=".")
        finally:
            builder.set_empty_mode(origin)
        assert prompts, "系统提示词不应为空"
        assert any("强制用内部工具实现所有（红线 · 一票否决）" in p for p in prompts)
        assert any("只能使用相对路径（红线 · 一票否决）" in p for p in prompts)


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
