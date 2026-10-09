"""压缩提示词必须保留未结束的后台 bash / subagent task_id（用户需求）。

用户在压缩上下文时，未结束的后台任务必须仍可经 ``bash_opt`` / ``subagent_opt``
按 task_id 继续管理，因此两条压缩路径的提词都必须明确要求逐字保留：

  - dsh 同款压缩引擎的 ``COMPACTION_INSTRUCTION``（src/core/compaction/checkpoint.py）
  - 旧策略链的结构化摘要提示词（src/core/context_summarizer.py）
"""

from __future__ import annotations

from src.core import context_summarizer as cs
from src.core.base_agent import BaseAgent
from src.core.compaction.checkpoint import COMPACTION_INSTRUCTION
from src.core.compaction.summarizer import build_summarization_messages, summarize_region
from src.core.context_manager import ContextManager


# ── dsh 同款压缩引擎指令 ──────────────────────────────────

def test_compaction_instruction_preserves_background_task_ids():
    text = COMPACTION_INSTRUCTION
    assert "bg-" in text
    assert "sa-" in text
    assert "bash_opt" in text
    assert "subagent_opt" in text
    assert "verbatim" in text


def test_compaction_instruction_pending_jobs_mentions_task_ids():
    pending_section = COMPACTION_INSTRUCTION.split("## Pending Jobs", 1)[1].split("##", 1)[0]
    assert "bg-" in pending_section
    assert "sa-" in pending_section


def test_build_summarization_messages_instruction_mentions_task_ids():
    messages = build_summarization_messages(
        [{"role": "system", "content": "sys"}],
        [{"role": "user", "content": "started a background task"}],
    )
    instruction = messages[-1]["content"]
    assert "bg-" in instruction
    assert "sa-" in instruction
    assert "bash_opt" in instruction
    assert "subagent_opt" in instruction


# ── 旧策略链结构化摘要提示词 ──────────────────────────────

def test_legacy_summary_system_prompt_preserves_task_ids():
    assert "bg-xxx" in cs._SUMMARY_SYSTEM
    assert "sa-xxx" in cs._SUMMARY_SYSTEM
    assert "逐字保留" in cs._SUMMARY_SYSTEM


def test_build_summary_prompt_preserves_task_ids():
    prompt = cs.build_summary_prompt(
        [{"role": "user", "content": "bash background=true"}],
        has_prior_summary=False,
    )
    assert "bg-xxx" in prompt
    assert "sa-xxx" in prompt
    assert "逐字保留" in prompt


# ── 未结束后台任务清单的采集（Agent） ────────────────────

class _FakeAgent:
    def __init__(self, bash=None, subagent=None):
        self._background_tasks = bash or {}
        self._subagent_tasks = subagent or {}


def test_running_background_jobs_collects_unfinished_only():
    agent = _FakeAgent(
        bash={
            "bg-running": {"done": False, "status": "running"},
            "bg-done": {"done": True, "status": "completed"},
        },
        subagent={
            "sa-running": {"done": False, "description": "扫描模块", "status": "running"},
            "sa-done": {"done": True, "description": "已完成", "status": "completed"},
        },
    )
    jobs = BaseAgent._running_background_jobs(agent)
    ids = {j["task_id"] for j in jobs}
    assert ids == {"bg-running", "sa-running"}
    by_id = {j["task_id"]: j for j in jobs}
    assert by_id["bg-running"]["kind"] == "bash"
    assert by_id["sa-running"]["kind"] == "subagent"
    assert by_id["sa-running"]["detail"] == "扫描模块"


def test_running_background_jobs_empty_without_tables():
    assert BaseAgent._running_background_jobs(_FakeAgent()) == []


# ── 指令注入（清单 → 摘要提词） ──────────────────────────

def test_build_summarization_messages_injects_pending_job_ids():
    messages = build_summarization_messages(
        [],
        [{"role": "user", "content": "hi"}],
        pending_jobs=[
            {"task_id": "bg-1234567890ab", "kind": "bash", "detail": "", "status": "running"},
            {"task_id": "sa-abcdef123456", "kind": "subagent", "detail": "扫描模块",
             "status": "running"},
        ],
    )
    instruction = messages[-1]["content"]
    # 指令主体仍在前（既有前缀复用与 startswith 断言不受影响）
    assert instruction.startswith("You are now acting as a compaction engine")
    assert "bg-1234567890ab" in instruction
    assert "sa-abcdef123456" in instruction
    assert "bash_opt" in instruction
    assert "subagent_opt" in instruction


def test_build_summarization_messages_no_section_without_jobs():
    messages = build_summarization_messages(
        [], [{"role": "user", "content": "hi"}], pending_jobs=[],
    )
    assert "## Live Background Jobs" not in messages[-1]["content"]


def test_summarize_region_forwards_pending_jobs():
    captured = {}

    def fake_summarize(messages, model=None):
        captured["messages"] = messages
        return ("", "## Primary Request and Intent\n- x", {}, [])

    summarize_region(
        [{"role": "system", "content": "sys"}],
        [{"role": "user", "content": "hi"}],
        fake_summarize,
        "m",
        pending_jobs=[{"task_id": "bg-deadbeef0000", "kind": "bash", "status": "running"}],
    )
    assert "bg-deadbeef0000" in captured["messages"][-1]["content"]


# ── 端到端：ContextManager → CompactionEngine 注入清单 ────

def _make_cm_with_jobs(jobs_fn):
    from src.core.adapters.config import MockConfigAdapter

    captured = []

    def fake_summarize(messages, model=None):
        captured.append(messages)
        return ("", "## Primary Request and Intent\n- condensed summary",
                {"input": 5, "output": 5}, [])

    messages = [{"role": "system", "content": "sys prompt"}]
    for i in range(20):
        messages.append({
            "role": "user" if i % 2 == 0 else "assistant",
            "content": f"m{i}-" + "x" * 400,
        })
    config_port = MockConfigAdapter({
        "model_context_tokens": 2000,
        "provider": "deepseek",
        "compaction": {
            "threshold_ratio": 0.5, "headroom_tokens": 200,
            "retain_ratio": 0.1, "model_policies": [],
        },
    })
    cm = ContextManager(
        messages, "test-model", summarize_fn=fake_summarize,
        config_port=config_port, pending_jobs_fn=jobs_fn,
    )
    return cm, captured


def test_engine_injects_pending_job_ids_into_summary_instruction():
    cm, captured = _make_cm_with_jobs(lambda: [
        {"task_id": "bg-1234567890ab", "kind": "bash", "detail": "", "status": "running"},
        {"task_id": "sa-abcdef123456", "kind": "subagent", "detail": "扫描模块",
         "status": "running"},
    ])
    result = cm.compact_now()
    assert result is not None and result.success is True
    instruction = captured[0][-1]["content"]
    assert "bg-1234567890ab" in instruction
    assert "sa-abcdef123456" in instruction
    assert "bash_opt" in instruction
    assert "subagent_opt" in instruction


def test_engine_tolerates_pending_jobs_failure():
    def boom():
        raise RuntimeError("任务表读取失败")

    cm, captured = _make_cm_with_jobs(boom)
    result = cm.compact_now()
    assert result is not None and result.success is True
    instruction = captured[0][-1]["content"]
    assert instruction.startswith("You are now acting as a compaction engine")
    assert "## Live Background Jobs" not in instruction
