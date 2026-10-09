"""prompts_export_main_empty.md 旧「一次输出所有计划」句式不得恢复的回归测试。

现行空模式提词以「做任何事情前强制输出计划」表达计划约束；旧的
「强制读完所有相关的源码后，在强制一次输出所有计划，强制按照计划执行」
句式不再使用，其余全局约束保持不变。
"""

from __future__ import annotations

from pathlib import Path

import pytest

MAIN_PROMPT = Path(__file__).resolve().parent.parent / "prompts" / "prompts_export_main_empty.md"


@pytest.fixture(scope="module")
def main_prompt_text() -> str:
    assert MAIN_PROMPT.exists(), f"缺少文件: {MAIN_PROMPT}"
    return MAIN_PROMPT.read_text(encoding="utf-8")


class TestEmptyPromptNoPlanRule:

    def test_prompt_exists(self):
        """提示词文件存在。"""
        assert MAIN_PROMPT.exists()

    def test_plan_directive_removed(self, main_prompt_text: str):
        """旧「读完源码后一次性输出所有计划并按计划执行」句式不得再出现。"""
        assert "强制读完所有相关的源码后" not in main_prompt_text
        assert "输出所有计划" not in main_prompt_text
        assert "强制按照计划执行" not in main_prompt_text

    def test_kept_constraints_untouched(self, main_prompt_text: str):
        """其余全局约束不受影响，未被误删。"""
        assert "你是一位乐于助人的软件工程师助手。" in main_prompt_text
        assert "# 全局约束" in main_prompt_text
        assert "思考跟回答强制简体中文输出" in main_prompt_text
        assert "禁止采信代码的任何注释" in main_prompt_text
        assert "plan execute agent 强制串行" in main_prompt_text

    def test_simple_prompt_not_touched(self):
        """本次仅修改 empty 版本，simple 版本不受影响。"""
        simple = MAIN_PROMPT.parent / "prompts_export_main_simple.md"
        assert simple.exists()
        assert "强制读完所有相关的源码后" in simple.read_text(encoding="utf-8")


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
