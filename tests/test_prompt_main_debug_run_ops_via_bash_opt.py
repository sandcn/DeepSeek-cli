"""prompts_export_main_empty.md「调试/运行/操作强制用 bash_opt」规则回归测试。

需求（2026-10-07）：空模式提词须明确——调试程序、运行程序/命令、与后台
任务交互（读取输出/等待/发送输入/发按键/终止/截图/注入鼠标键盘）时，
一律强制用 `bash_opt` 按 `task_id` 实现；先用 `bash` 的 `background=true`
取得 `task_id`，其后一切调试、运行、操作只能经 `bash_opt` 完成，禁止绕过。
"""

from __future__ import annotations

from pathlib import Path

import pytest

MAIN_PROMPT = Path("prompts/prompts_export_main_empty.md")

CLAUSE_MARKER = "调试/运行/操作强制用 `bash_opt`"


@pytest.fixture(scope="module")
def main_prompt_text() -> str:
    assert MAIN_PROMPT.exists(), f"缺少文件: {MAIN_PROMPT}"
    return MAIN_PROMPT.read_text(encoding="utf-8")


def _clause_line(main_prompt_text: str) -> str:
    return next(
        (ln for ln in main_prompt_text.splitlines() if CLAUSE_MARKER in ln),
        "",
    )


def test_prompt_exists():
    """提示词文件存在。"""
    assert MAIN_PROMPT.exists()


def test_clause_present_on_single_line(main_prompt_text: str):
    """调试/运行/操作强制用 bash_opt 的条目须落在同一行，避免语义脱离。"""
    assert _clause_line(main_prompt_text), "提词缺少「调试/运行/操作强制用 bash_opt」规则"


def test_clause_marks_redline(main_prompt_text: str):
    """条目须带红线一票否决标记。"""
    line = _clause_line(main_prompt_text)
    assert "红线" in line
    assert "一票否决" in line


def test_clause_covers_debug_run_operate(main_prompt_text: str):
    """条目须同时覆盖调试、运行、操作三类场景。"""
    line = _clause_line(main_prompt_text)
    assert "调试" in line
    assert "运行" in line
    assert "操作" in line


def test_clause_names_bash_opt_and_task_id(main_prompt_text: str):
    """条目须点名 bash_opt 并按 task_id 执行。"""
    line = _clause_line(main_prompt_text)
    assert "bash_opt" in line
    assert "task_id" in line
    assert "`bash`" in line
    assert "background=true" in line


def test_clause_lists_management_ops(main_prompt_text: str):
    """条目须覆盖后台任务管理 op：读取/等待/输入/按键/终止。"""
    line = _clause_line(main_prompt_text)
    for token in ("op=read", "op=wait", "op=stdin", "op=keys", "op=kill"):
        assert token in line, f"提词缺少 {token}"


def test_clause_lists_gui_ops(main_prompt_text: str):
    """条目须覆盖截图与鼠标键盘注入 op。"""
    line = _clause_line(main_prompt_text)
    for token in ("op=screenshot", "op=move", "op=click", "op=drag",
                  "op=scroll", "op=key", "op=type"):
        assert token in line, f"提词缺少 {token}"


def test_clause_forbids_bypass(main_prompt_text: str):
    """条目须强制禁止绕过 bash_opt。"""
    line = _clause_line(main_prompt_text)
    assert "禁止绕过" in line


def test_existing_bash_opt_rules_retained(main_prompt_text: str):
    """既有截图与 GUI 输入两条 bash_opt 规则未被误删。"""
    assert "截图用 `bash_opt` 实现" in main_prompt_text
    assert "操作 GUI 窗口用 `bash_opt` 的输入 op" in main_prompt_text


def test_other_constraints_untouched(main_prompt_text: str):
    """其余全局约束不受影响。"""
    assert "你是一位乐于助人的软件工程师助手。" in main_prompt_text
    assert "# 全局约束" in main_prompt_text
    assert "思考跟回答强制简体中文输出" in main_prompt_text
    assert "plan execute agent 强制串行" in main_prompt_text


def test_simple_prompt_not_touched():
    """本次仅修改 empty 版本，simple 版本不受影响。"""
    simple = MAIN_PROMPT.parent / "prompts_export_main_simple.md"
    assert simple.exists()
    assert CLAUSE_MARKER not in simple.read_text(encoding="utf-8")


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
