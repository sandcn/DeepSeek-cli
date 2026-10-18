"""空模式提词：调试、运行与后台交互使用 bash_opt。"""

from __future__ import annotations

from tests._prompt_semantics import assert_contains, read_prompt


def test_debug_run_ops_via_bash_opt():
    text = read_prompt()
    assert_contains(
        text,
        "background=true",
        "`bash_opt`",
        "task_id",
        "read",
        "wait",
        "stdin",
        "keys",
        "screenshot",
    )
