"""review agent 删除 bash/bash_opt 工具 回归测试。

需求（2026-08-21）：
1. review 类型 SubAgent 的排除表包含 bash/bash_opt（彻底无 shell 执行能力）。
2. Func.can_use：review 类型不可调用 bash / bash_opt。
3. 提词（prompts_export_review.md）中不再声明 bash 工具相关使用规则。
"""

from __future__ import annotations

from pathlib import Path

import pytest

REVIEW_PROMPT = Path(__file__).resolve().parent.parent / "prompts" / "prompts_export_review.md"


def _review_excluded() -> set:
    from src.core.subagent import _get_excluded_tools
    return _get_excluded_tools("review")


def _can_use(tool_name: str, agent_type: str) -> bool:
    from src.tools.base import Func
    ok, _err = Func.can_use(tool_name, agent_type=agent_type)
    return ok


@pytest.fixture(scope="module")
def review_prompt_text() -> str:
    assert REVIEW_PROMPT.exists(), f"缺少文件: {REVIEW_PROMPT}"
    return REVIEW_PROMPT.read_text(encoding="utf-8")


class TestReviewBashToolRemoved:

    def test_review_exclude_bash(self):
        """review 排除表含 bash / bash_opt（已删除）。"""
        excluded = _review_excluded()
        assert "bash" in excluded
        assert "bash_opt" in excluded

    def test_review_cannot_use_bash(self):
        """Func.can_use：review 类型不可调用 bash / bash_opt。"""
        assert _can_use("bash", "review") is False
        assert _can_use("bash_opt", "review") is False

    def test_review_still_exclude_write_tools(self):
        """review 仍排除全部内部写入类工具（write_file/update_file/rm/mv/cp/mkdir）。"""
        excluded = _review_excluded()
        for tool in ("write_file", "update_file", "rm", "mv", "cp", "mkdir"):
            assert tool in excluded, f"review 应排除内部写入工具 {tool}"

    def test_review_keep_readonly_and_web_search(self):
        """review 保留只读工具与 web_search（read_file/search/find/ls/web_search 不在排除表）。"""
        excluded = _review_excluded()
        for tool in ("read_file", "search", "find", "ls", "web_search"):
            assert tool not in excluded, f"review 应保留只读工具 {tool}"

    def test_review_still_exclude_subagent_and_user_select(self):
        """review 仍排除 subagent/subagent_opt/user_select。"""
        excluded = _review_excluded()
        for tool in ("subagent", "subagent_opt", "user_select"):
            assert tool in excluded, f"review 应排除 {tool}"


class TestReviewPromptNoBashRule:

    def test_prompt_does_not_mention_bash_tools(self, review_prompt_text: str):
        """提词不再声明 bash/bash_opt 工具使用规则（review 无 shell 执行能力）。"""
        assert "bash" not in review_prompt_text
        assert "bash_opt" not in review_prompt_text

    def test_prompt_states_readonly_toolset(self, review_prompt_text: str):
        """提词仍声明只读工具集（read_file/search/find/ls/web_search）且禁止任何修改操作。"""
        assert "read_file" in review_prompt_text
        assert "search" in review_prompt_text
        assert "find" in review_prompt_text
        assert "ls" in review_prompt_text
        assert "web_search" in review_prompt_text
        assert "禁止任何修改操作" in review_prompt_text


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
