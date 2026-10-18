"""空模式提词：不再恢复旧版「后台任务空等」纪律，只保留 bash_opt 流程。"""

from __future__ import annotations

from tests._prompt_semantics import assert_contains, read_prompt

REMOVED_CLAUSES = (
    "禁止原地空等",
    "禁止立刻阻塞等待结果",
    "同一轮并行启动",
    "只有确实没有任何其他可做操作时",
)


def test_old_background_waiting_discipline_absent():
    text = read_prompt()
    for clause in REMOVED_CLAUSES:
        assert clause not in text, f"旧后台纪律未删除: {clause}"


def test_bash_opt_flow_kept():
    text = read_prompt()
    assert_contains(text, "background=true", "bash_opt", "task_id")
