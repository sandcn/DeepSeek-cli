"""SubAgent dispatch_label 持久化测试（用户需求：agent 内容合并到 subagent）。

背景（2026-08-17）：SubAgent 创建时经 ``_spawn_subagent`` 注入所属
subagent 的 tool_call_id（spec["tool_label"]，subagent 工具调用 id）；
``_record_to_parent`` 把该 id 写入会话存档（subagents 条目新增
"dispatch_label" 字段）——/load、--load、webui 加载会话后
``restore_trace_archive`` 凭此把历史 subagent 合并到主轨迹对应的
subagent 工具记录（用户需求：load 命令后也要合并；旧会话无该字段
→ 空串，独立 subagent 记录兼容）。
"""
from __future__ import annotations

from unittest.mock import MagicMock

from src.core.subagent import SubAgent


def _make_subagent(dispatch_label: str = "") -> SubAgent:
    """构造最小可用的 SubAgent（parent 全部 mock，对齐 test_subagent_tool_response）。"""
    parent = MagicMock()
    parent.get_tool_registry.return_value = MagicMock()
    parent.model = "test-model"
    parent._event_port = MagicMock()
    parent._async_model_port = None
    # _record_to_parent 会把记录追加到父 agent 的 _subagent_records——
    # MagicMock 的 getattr 会返回 mock（非 None），导致 append 落在 mock 上，
    # 显式置真实列表（与父 agent 真实行为一致）
    parent._subagent_records = []
    prompt_builder = MagicMock()
    prompt_builder.build_map_agent_prompt.return_value = ["system"]
    prompt_builder.build_review_agent_prompt.return_value = ["system"]
    prompt_builder.build_plan_agent_prompt.return_value = ["system"]
    prompt_builder.build_execute_agent_system_prompt.return_value = ["system"]
    prompt_builder.build_subagent_prompt.return_value = ["system"]
    parent.get_prompt_builder_port.return_value = prompt_builder

    return SubAgent(
        "agent-1", "审查 API 层", "prompt", parent,
        dispatch_label=dispatch_label,
    )


def test_subagent_init_stores_dispatch_label():
    """SubAgent.__init__ 保存所属 subagent 的 tool_call_id。"""
    sa = _make_subagent("call_d1")
    assert sa.dispatch_label == "call_d1"


def test_subagent_default_dispatch_label_empty():
    """独立模式/旧路径（未指定 dispatch_label）→ 空串。"""
    sa = _make_subagent()
    assert sa.dispatch_label == ""


def test_record_to_parent_persists_dispatch_label():
    """_record_to_parent 把 dispatch_label 写入会话存档（subagents 条目）。"""
    sa = _make_subagent("call_d1")
    sa.result = "审查完成。"
    sa.messages.append({"role": "assistant", "content": "ok"})
    sa._record_to_parent()
    record = sa.parent._subagent_records[0]
    assert record["label"] == "agent-1"
    assert record["dispatch_label"] == "call_d1"
    assert record["result"] == "审查完成。"


def test_record_to_parent_dispatch_label_empty_for_independent():
    """独立执行（无 dispatch_label）→ 存档条目 dispatch_label 为空串。"""
    sa = _make_subagent()
    sa._record_to_parent()
    record = sa.parent._subagent_records[0]
    assert record["dispatch_label"] == ""
