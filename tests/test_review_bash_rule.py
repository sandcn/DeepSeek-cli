"""review agent 放开 bash 工具 + 提词强制禁止用 bash 改文件 回归测试。

需求（2026-08-20）：
1. review 类型 SubAgent 增加 bash/bash_opt 工具（可做只读查询：系统信息/进程状态/日志查看）。
2. 提词（prompts_export_review.md）中强制禁止 review 用 bash 修改文件
   （重定向写文件、sed -i、echo 落盘、rm/mv 等一律禁止）。
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


class TestReviewBashToolEnabled:

    def test_review_not_exclude_bash(self):
        """review 排除表不含 bash / bash_opt（已放开）。"""
        excluded = _review_excluded()
        assert "bash" not in excluded
        assert "bash_opt" not in excluded

    def test_review_can_use_bash(self):
        """Func.can_use：review 类型可调用 bash / bash_opt。"""
        assert _can_use("bash", "review") is True
        assert _can_use("bash_opt", "review") is True

    def test_review_still_exclude_write_tools(self):
        """review 仍排除全部内部写入类工具（write_file/update_file/rm/mv/cp/mkdir）。"""
        excluded = _review_excluded()
        for tool in ("write_file", "update_file", "rm", "mv", "cp", "mkdir"):
            assert tool in excluded, f"review 应排除内部写入工具 {tool}"


class TestReviewBashNoFileModifyRule:

    def test_prompt_allows_bash_readonly(self, review_prompt_text: str):
        """提词须声明 bash 仅限只读查询用途。"""
        assert "bash" in review_prompt_text
        assert "只读查询" in review_prompt_text

    def test_prompt_forbids_bash_file_modify(self, review_prompt_text: str):
        """提词须含强制禁止用 bash 修改文件的规则。"""
        assert "禁止" in review_prompt_text and "bash" in review_prompt_text
        assert "修改" in review_prompt_text and "文件" in review_prompt_text

    def test_prompt_covers_redirect_write(self, review_prompt_text: str):
        """提词须点名重定向写文件（> / >>）与原地编辑（sed -i）等典型改文件手段。"""
        assert ">" in review_prompt_text and ">>" in review_prompt_text
        assert "sed -i" in review_prompt_text

    def test_prompt_covers_fs_mutating_commands(self, review_prompt_text: str):
        """提词须点名 rm/mv/cp/mkdir 等文件系统变更命令被禁止。"""
        for cmd in ("rm", "mv", "cp", "mkdir", "touch", "chmod"):
            assert cmd in review_prompt_text, f"提词应禁止 bash 执行 {cmd}"

    def test_prompt_marks_rule_as_red_line(self, review_prompt_text: str):
        """禁止 bash 改文件须标注为红线/强制（一票否决语义）。"""
        assert "红线" in review_prompt_text or "强制" in review_prompt_text


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
