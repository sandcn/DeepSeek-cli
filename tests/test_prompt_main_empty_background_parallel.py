"""prompts_export_main_empty.md「后台任务与后台 subagent 执行纪律」回归测试。

需求（2026-10-18）：使用 bash（`background=true`）与 subagent 后台执行后，
只要还能做别的操作就必须先做别的操作，把能做的操作全部做完后再等待并
取回结果；互不依赖的后台任务与后台 subagent 必须同轮并行启动，禁止
启动后原地空等、禁止逐个启动后逐个等待。
"""

from __future__ import annotations

import pytest

from tests._paths import prompts_dir

MAIN_PROMPT = prompts_dir() / "prompts_export_main_empty.md"

RULE_KEY = "后台任务与后台 subagent 执行纪律"
PREVIOUS_RULE_KEY = "plan execute agent 强制串行"
NEXT_RULE_KEY = "提交（commit）」与「push」强制用 user_select 向用户确认"


@pytest.fixture(scope="module")
def main_prompt_text() -> str:
    assert MAIN_PROMPT.exists(), f"缺少文件: {MAIN_PROMPT}"
    return MAIN_PROMPT.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def rule_line(main_prompt_text: str) -> str:
    matched = [ln for ln in main_prompt_text.splitlines() if ln.startswith("- ") and RULE_KEY in ln]
    assert matched, f"缺少约束: {RULE_KEY}"
    return matched[0]


class TestBackgroundParallelRule:

    def test_prompt_exists(self):
        """提示词文件存在。"""
        assert MAIN_PROMPT.exists()

    def test_rule_present_on_single_line(self, rule_line: str):
        """提词须含该约束，且落在同一行，避免语义脱离。"""
        assert RULE_KEY in rule_line
        assert "`bash`" in rule_line
        assert "subagent" in rule_line

    def test_rule_is_red_line(self, rule_line: str):
        """该规则须为「红线 · 一票否决」级别。"""
        assert "（红线 · 一票否决）" in rule_line

    def test_rule_covers_bash_background_and_task_id(self, rule_line: str):
        """须点名 bash 的 background=true 与 task_id。"""
        assert "`background=true`" in rule_line
        assert "`task_id`" in rule_line

    def test_rule_covers_subagent_background(self, rule_line: str):
        """须覆盖后台派发 subagent 并取得任务 ID。"""
        assert "后台派发 subagent" in rule_line
        assert "任务 ID" in rule_line

    def test_rule_forbids_idle_waiting(self, rule_line: str):
        """须禁止启动后原地空等、立刻阻塞等待结果。"""
        assert "禁止原地空等" in rule_line
        assert "禁止立刻阻塞等待结果" in rule_line

    def test_rule_requires_other_work_first(self, rule_line: str):
        """须要求只要还有可做的别的操作就立即去做，能并行就并行。"""
        assert "只要还有任何可做的别的操作" in rule_line
        assert "就必须立即去做" in rule_line
        assert "能并行做别的操作就并行做" in rule_line
        assert "必须先把所有可做的别的操作全部做完" in rule_line

    def test_rule_waits_only_after_other_work(self, rule_line: str):
        """须规定做完全部别的操作后才允许取回结果，并点名等待操作。"""
        assert "才允许按 `task_id`/任务 ID 取回结果" in rule_line
        for token in ("op=read", "op=wait", "subagent_opt", "op=wait_all"):
            assert token in rule_line, f"应点名「{token}」"

    def test_rule_continues_work_during_waiting(self, rule_line: str):
        """等待期间/取回部分结果后发现可做操作须继续做完。"""
        assert "等待期间或取回部分结果后若发现还有可做的操作" in rule_line
        assert "必须继续做完再等待/收集剩余结果" in rule_line

    def test_rule_requires_parallel_start_not_serial_wait(self, rule_line: str):
        """互不依赖的后台任务须同轮并行启动，禁止逐个启动后逐个等待。"""
        assert "互不依赖的多个后台任务与后台 subagent 必须在同一轮并行启动" in rule_line
        assert "禁止一个一个启动后逐个等待" in rule_line

    def test_rule_keeps_serial_constraints(self, rule_line: str):
        """并行启动不得突破既有串行约束（plan/execute 强制串行）。"""
        assert "plan execute agent 强制串行" in rule_line
        assert "等串行约束的除外" in rule_line

    def test_rule_requires_collect_all_results(self, rule_line: str):
        """所有结果须取回并校验，禁止只启动不收集、禁止以等结果为由停手。"""
        assert "所有后台结果都必须取回并校验后才能继续后续流程或声明完成" in rule_line
        assert "禁止只启动不收集" in rule_line
        assert "禁止把「等结果」当作停止行动的理由" in rule_line

    def test_rule_position_after_serial_dispatch_rule(self, main_prompt_text: str):
        """该规则须位于「plan execute agent 强制串行」之后、提交/push 确认规则之前。"""
        assert main_prompt_text.index(PREVIOUS_RULE_KEY) < main_prompt_text.index(RULE_KEY)
        assert main_prompt_text.index(RULE_KEY) < main_prompt_text.index(NEXT_RULE_KEY)

    def test_previous_rules_kept(self, main_prompt_text: str):
        """既有调度约束未被误删。"""
        assert "未经用户指定，强制禁止调用 subagent" in main_prompt_text
        assert "plan execute agent 强制串行" in main_prompt_text

    def test_no_duplicate_entry_lines(self, main_prompt_text: str):
        """新增条目不产生完全重复条目行。"""
        entries = [ln for ln in main_prompt_text.splitlines() if ln.startswith("- ")]
        assert len(entries) == len(set(entries)), "存在重复条目行"

    def test_only_empty_version_changed(self):
        """本次仅修改 empty 版本，其它提示词文件不受影响。"""
        base = prompts_dir()
        for other in ("prompts_export_main.md", "prompts_export_main_simple.md"):
            content = (base / other).read_text(encoding="utf-8")
            assert RULE_KEY not in content, f"{other} 不应包含本次修改约束"


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
