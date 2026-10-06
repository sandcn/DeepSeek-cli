"""prompts_export_main_empty.md「git 读操作免用户同意」回归测试。

需求（2026-10-05）：在空模式提词中明确 git 的读操作可以不经过用户同意，
其余全局约束保持不变。
"""

from __future__ import annotations

from pathlib import Path

import pytest

MAIN_PROMPT = Path(__file__).resolve().parent.parent / "prompts" / "prompts_export_main_empty.md"


@pytest.fixture(scope="module")
def main_prompt_text() -> str:
    assert MAIN_PROMPT.exists(), f"缺少文件: {MAIN_PROMPT}"
    return MAIN_PROMPT.read_text(encoding="utf-8")


class TestGitReadAllowedRule:

    def test_prompt_exists(self):
        """提示词文件存在。"""
        assert MAIN_PROMPT.exists()

    def test_git_read_read_allowed_clause_present(self, main_prompt_text: str):
        """git 读操作免用户同意的约定须存在。"""
        assert "git 的读操作可以不经过用户同意" in main_prompt_text

    def test_git_write_ban_retained(self, main_prompt_text: str):
        """禁止未经用户确认操作 git svn 的主体约束仍保留。"""
        assert "操作 git svn" in main_prompt_text
        assert "必须用 user_select 询问用户是否同意" in main_prompt_text

    def test_git_read_clause_on_same_line(self, main_prompt_text: str):
        """读操作豁免须挂在 git svn 约束同一行，避免语义脱离。"""
        line = next(
            (ln for ln in main_prompt_text.splitlines() if "操作 git svn" in ln),
            "",
        )
        assert "git 的读操作可以不经过用户同意" in line

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
        assert "git 的读操作可以不经过用户同意" not in simple.read_text(encoding="utf-8")


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
