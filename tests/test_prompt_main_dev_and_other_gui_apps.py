"""prompts_export_main_empty.md「能用 bash_opt 操作和调试当前开发应用与别的 GUI 应用」规则回归测试。

需求（2026-10-07）：空模式提词须明确——`bash_opt` 不仅能调试/运行/操作
后台任务，还能操作和调试**当前正在开发的应用**（本项目开发中的图形程序）
以及**别的 GUI 应用**（本项目之外的任意独立 GUI 程序）；两者都先用 `bash`
的 `background=true` 启动并把目标进程纳入该后台任务的进程树取得 `task_id`，
再经 `bash_opt` 的窗口/输入 op 操作与调试；对任何 GUI 应用的操作和调试
一律强制使用 `bash_opt`，禁止改用其它任何方式。
"""

from __future__ import annotations

from pathlib import Path

import pytest

MAIN_PROMPT = Path(__file__).resolve().parent.parent / "prompts" / "prompts_export_main_empty.md"
SIMPLE_PROMPT = MAIN_PROMPT.parent / "prompts_export_main_simple.md"
FULL_PROMPT = MAIN_PROMPT.parent / "prompts_export_main.md"

CLAUSE_MARKER = "调试/运行/操作强制用 `bash_opt`"
NEW_CLAUSE_MARKER = "能用 `bash_opt` 操作和调试当前开发应用与别的 GUI 应用（强制）"

#: 新增条目须在同一行内点名的关键语义点
REQUIRED_TOKENS = (
    "当前正在开发的应用",
    "别的 GUI 应用",
    "本项目之外的任意独立 GUI 程序",
    "`bash`",
    "background=true",
    "进程树",
    "`task_id`",
    "`op=windows`",
    "`op=screenshot`",
    "`read_image`",
    "`op=move`",
    "`op=click`",
    "`op=drag`",
    "`op=scroll`",
    "`op=key`",
    "`op=type`",
    "`op=keys`",
    "`op=window`",
    "强制",
    "只能使用 `bash_opt`",
    "禁止改用其它任何方式",
)


@pytest.fixture(scope="module")
def main_prompt_text() -> str:
    assert MAIN_PROMPT.exists(), f"缺少文件: {MAIN_PROMPT}"
    return MAIN_PROMPT.read_text(encoding="utf-8")


def _new_clause_line(main_prompt_text: str) -> str:
    return next(
        (ln for ln in main_prompt_text.splitlines() if NEW_CLAUSE_MARKER in ln),
        "",
    )


class TestDevAndOtherGuiAppsClause:

    def test_prompt_exists(self):
        """提示词文件存在。"""
        assert MAIN_PROMPT.exists()

    def test_new_clause_present(self, main_prompt_text: str):
        """须新增「操作和调试当前开发应用与别的 GUI 应用」条目。"""
        assert NEW_CLAUSE_MARKER in main_prompt_text, "提词缺少当前开发应用与别的 GUI 应用的 bash_opt 规则"

    def test_new_clause_on_bash_opt_clause_line(self, main_prompt_text: str):
        """新条目须并入既有 bash_opt 条目行，避免语义脱离。"""
        clause = next(
            (ln for ln in main_prompt_text.splitlines() if CLAUSE_MARKER in ln),
            "",
        )
        assert clause, "提词缺少 bash_opt 调试/运行/操作条目"
        assert NEW_CLAUSE_MARKER in clause

    def test_new_clause_marks_forced(self, main_prompt_text: str):
        """新条目须带强制语义与红线一票否决语义。"""
        line = _new_clause_line(main_prompt_text)
        assert "强制" in line
        assert "一票否决" in main_prompt_text

    @pytest.mark.parametrize("token", REQUIRED_TOKENS)
    def test_new_clause_lists_token(self, main_prompt_text: str, token: str):
        """新条目须点名启动方式、窗口/输入 op 与强制约束。"""
        line = _new_clause_line(main_prompt_text)
        assert token in line, f"提词缺少语义点: {token}"

    def test_new_clause_covers_dev_app_and_other_gui_app(self, main_prompt_text: str):
        """新条目须同时覆盖当前开发应用与别的 GUI 应用两类对象。"""
        line = _new_clause_line(main_prompt_text)
        assert "当前正在开发的应用" in line
        assert "别的 GUI 应用" in line
        assert "本项目之外的任意独立 GUI 程序" in line

    def test_new_clause_forbids_non_bash_opt_ways(self, main_prompt_text: str):
        """新条目须禁止用其它任何方式操作/调试 GUI 应用。"""
        line = _new_clause_line(main_prompt_text)
        assert "只能使用 `bash_opt`" in line
        assert "禁止改用其它任何方式" in line
        assert "外部脚本" in line
        assert "shell 命令" in line

    def test_new_clause_requires_background_start(self, main_prompt_text: str):
        """新条目须说明先用 bash background=true 启动目标应用取得 task_id。"""
        line = _new_clause_line(main_prompt_text)
        assert "background=true" in line
        assert "进程树" in line
        assert "`task_id`" in line


class TestExistingBashOptRulesRetained:

    def test_debug_run_ops_clause_retained(self, main_prompt_text: str):
        """既有后台任务管理 op（read/wait/stdin/keys/kill）条目未被破坏。"""
        clause = next(
            (ln for ln in main_prompt_text.splitlines() if CLAUSE_MARKER in ln),
            "",
        )
        for token in ("op=read", "op=wait", "op=stdin", "op=keys", "op=kill"):
            assert token in clause, f"提词缺少 {token}"

    def test_screenshot_clause_retained(self, main_prompt_text: str):
        """既有截图用 bash_opt 的条目未被误删。"""
        assert "截图用 `bash_opt` 实现" in main_prompt_text

    def test_window_input_clause_retained(self, main_prompt_text: str):
        """既有操作 GUI 窗口（bash_opt 输入 op）的条目未被误删。"""
        assert "操作 GUI 窗口用 `bash_opt` 的输入 op" in main_prompt_text

    def test_bypass_forbidden_retained(self, main_prompt_text: str):
        """既有禁止绕过 bash_opt 的约束未被误删。"""
        assert "强制禁止绕过 `bash_opt`" in main_prompt_text


class TestOtherConstraintsUntouched:

    def test_header_and_global_section(self, main_prompt_text: str):
        """文件头与「# 全局约束」章节保持不变。"""
        assert main_prompt_text.startswith("你是一位乐于助人的软件工程师助手。")
        assert "# 全局约束" in main_prompt_text

    def test_other_constraints_kept(self, main_prompt_text: str):
        """其余全局约束不受影响。"""
        assert "思考跟回答强制简体中文输出" in main_prompt_text
        assert "禁止采信代码的任何注释" in main_prompt_text
        assert "plan execute agent 强制串行" in main_prompt_text
        assert "图片强制用 `read_image` 读取" in main_prompt_text

    def test_no_duplicate_entry_lines(self, main_prompt_text: str):
        """新增内容并入既有条目行，不产生完全重复条目。"""
        entries = [ln for ln in main_prompt_text.splitlines() if ln.startswith("- ")]
        assert len(entries) == len(set(entries)), "存在重复条目行"

    def test_only_single_clause_marker(self, main_prompt_text: str):
        """新增语义并入同一条目，不新增第二条 bash_opt 调试/运行/操作条目。"""
        lines = [ln for ln in main_prompt_text.splitlines() if CLAUSE_MARKER in ln]
        assert len(lines) == 1, "bash_opt 调试/运行/操作条目应只有一条"


class TestOtherPromptVariantsUntouched:

    def test_simple_prompt_not_touched(self):
        """本次仅修改 empty 版本，simple 版本不受影响。"""
        assert SIMPLE_PROMPT.exists()
        assert NEW_CLAUSE_MARKER not in SIMPLE_PROMPT.read_text(encoding="utf-8")

    def test_full_prompt_not_touched(self):
        """本次仅修改 empty 版本，完整版不受影响。"""
        assert FULL_PROMPT.exists()
        assert NEW_CLAUSE_MARKER not in FULL_PROMPT.read_text(encoding="utf-8")


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
