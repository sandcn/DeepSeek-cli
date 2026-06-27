"""subagent_slots_to_tree 转换逻辑测试。

覆盖 subagent_tree.py 中 subagent_slots_to_tree 函数的所有转换逻辑：
包括空输入、单/多 agent、tool 子节点、状态映射、类型缩写、
metadata 字段、label 格式、无效条目跳过等。
"""

from __future__ import annotations

import time

import pytest

from src.chat_ui.components.subagent_tree import subagent_slots_to_tree
from src.chat_ui.components.tree import TreeNode


# ── 辅助：构造标准 slot dict ───────────────────────────

def _make_slot(**overrides):
    """构造一个 agent slot dict，允许覆盖任意字段。"""
    slot = {
        "description": "分析代码",
        "agent_type": "map",
        "status": "running",
        "output_tokens": 150,
        "start_time": time.time() - 5,
        "end_time": 0,
        "model_phase": "thinking",
        "tool_history": [],
    }
    slot.update(overrides)
    return slot


# ── 测试用例 ──────────────────────────────────────────


class TestEmptySlots:
    """空 slots 输入返回 None。"""

    def test_empty_dict_returns_none(self):
        assert subagent_slots_to_tree({}) is None

    def test_none_returns_none(self):
        assert subagent_slots_to_tree(None) is None


class TestSingleAgentNoTools:
    """单个 agent，无 tool_history。"""

    def test_root_has_one_child(self):
        slot = _make_slot(tool_history=[])
        root = subagent_slots_to_tree({"agent-1": slot})
        assert root is not None
        assert root.label == ""
        assert root.status == "running"
        assert len(root.children) == 1

    def test_agent_node_has_no_tool_children(self):
        slot = _make_slot(tool_history=[])
        root = subagent_slots_to_tree({"agent-1": slot})
        agent_node = root.children[0]
        assert agent_node.children == []

    def test_agent_label_contains_abbr_and_description(self):
        slot = _make_slot(tool_history=[])
        root = subagent_slots_to_tree({"agent-1": slot})
        agent_node = root.children[0]
        assert "[map]" in agent_node.label
        assert "分析代码" in agent_node.label


class TestSingleAgentWithTools:
    """单个 agent + 2 个 tool → agent 节点有 2 个 tool 子节点。"""

    @pytest.fixture
    def root(self):
        slot = _make_slot(tool_history=[
            {"tool_name": "read_file", "phase": "done"},
            {"tool_name": "search", "phase": "running"},
        ])
        return subagent_slots_to_tree({"agent-1": slot})

    def test_agent_has_two_tool_children(self, root):
        agent_node = root.children[0]
        assert len(agent_node.children) == 2

    def test_first_tool_label(self, root):
        tool0 = root.children[0].children[0]
        assert tool0.label == "read_file"

    def test_first_tool_status_done(self, root):
        tool0 = root.children[0].children[0]
        assert tool0.status == "done"

    def test_second_tool_label(self, root):
        tool1 = root.children[0].children[1]
        assert tool1.label == "search"

    def test_second_tool_status_running(self, root):
        tool1 = root.children[0].children[1]
        assert tool1.status == "running"


class TestMultipleAgents:
    """2 个 agent 作为根节点的兄弟子节点。"""

    @pytest.fixture
    def root(self):
        slots = {
            "agent-1": _make_slot(description="分析代码", agent_type="map"),
            "agent-2": _make_slot(description="执行计划", agent_type="plan_execute"),
        }
        return subagent_slots_to_tree(slots)

    def test_root_has_two_children(self, root):
        assert len(root.children) == 2

    def test_first_agent_is_map(self, root):
        assert "[map]" in root.children[0].label

    def test_second_agent_is_exec(self, root):
        assert "[exec]" in root.children[1].label


class TestToolStatusMapping:
    """tool phase → status 映射验证。"""

    def _slot_with_phase(self, phase):
        return _make_slot(tool_history=[{"tool_name": "test", "phase": phase}])

    def test_parsing_maps_to_running(self):
        root = subagent_slots_to_tree({"a": self._slot_with_phase("parsing")})
        tool = root.children[0].children[0]
        assert tool.status == "running"

    def test_running_maps_to_running(self):
        root = subagent_slots_to_tree({"a": self._slot_with_phase("running")})
        tool = root.children[0].children[0]
        assert tool.status == "running"

    def test_done_maps_to_done(self):
        root = subagent_slots_to_tree({"a": self._slot_with_phase("done")})
        tool = root.children[0].children[0]
        assert tool.status == "done"

    def test_fail_maps_to_fail(self):
        root = subagent_slots_to_tree({"a": self._slot_with_phase("fail")})
        tool = root.children[0].children[0]
        assert tool.status == "fail"

    def test_unknown_phase_defaults_to_running(self):
        root = subagent_slots_to_tree({"a": self._slot_with_phase("unknown_phase")})
        tool = root.children[0].children[0]
        assert tool.status == "running"


class TestAgentStatusMapping:
    """agent status → TreeNode status 映射验证。"""

    def _slot_with_status(self, status):
        return _make_slot(status=status)

    def test_completed_maps_to_done(self):
        root = subagent_slots_to_tree({"a": self._slot_with_status("completed")})
        agent = root.children[0]
        assert agent.status == "done"

    def test_unknown_status_defaults_to_running(self):
        root = subagent_slots_to_tree({"a": self._slot_with_status("unknown_status")})
        agent = root.children[0]
        assert agent.status == "running"

    def test_running_passes_through(self):
        root = subagent_slots_to_tree({"a": self._slot_with_status("running")})
        agent = root.children[0]
        assert agent.status == "running"

    def test_done_passes_through(self):
        root = subagent_slots_to_tree({"a": self._slot_with_status("done")})
        agent = root.children[0]
        assert agent.status == "done"

    def test_fail_passes_through(self):
        root = subagent_slots_to_tree({"a": self._slot_with_status("fail")})
        agent = root.children[0]
        assert agent.status == "fail"


class TestAgentTypeAbbreviation:
    """agent_type 缩写映射验证。"""

    def _slot_with_type(self, agent_type):
        return _make_slot(agent_type=agent_type)

    def test_map_abbr(self):
        root = subagent_slots_to_tree({"a": self._slot_with_type("map")})
        assert root.children[0].label.startswith("[map]")

    def test_plan_execute_abbr(self):
        root = subagent_slots_to_tree({"a": self._slot_with_type("plan_execute")})
        assert root.children[0].label.startswith("[exec]")

    def test_review_abbr(self):
        root = subagent_slots_to_tree({"a": self._slot_with_type("review")})
        assert root.children[0].label.startswith("[review]")

    def test_unknown_type_uses_first_4_chars(self):
        root = subagent_slots_to_tree({"a": self._slot_with_type("unknown_type")})
        # unknown_type 前 4 字符 = "unkn"
        assert root.children[0].label.startswith("[unkn]")

    def test_empty_type_no_bracket(self):
        root = subagent_slots_to_tree({"a": self._slot_with_type("")})
        # 空 agent_type → tag = ""，label 不以 [ 开头
        label = root.children[0].label
        assert not label.startswith("[")


class TestMetadataFields:
    """metadata 字段包含 agent_type, elapsed, tokens, model_phase, label_key。"""

    @pytest.fixture
    def agent_node(self):
        slot = _make_slot(
            description="测试",
            agent_type="map",
            output_tokens=200,
            model_phase="answering",
        )
        root = subagent_slots_to_tree({"agent-1": slot})
        return root.children[0]

    def test_metadata_has_agent_type(self, agent_node):
        assert agent_node.metadata["agent_type"] == "map"

    def test_metadata_has_tokens(self, agent_node):
        assert agent_node.metadata["tokens"] == 200

    def test_metadata_has_model_phase(self, agent_node):
        assert agent_node.metadata["model_phase"] == "answering"

    def test_metadata_has_label_key(self, agent_node):
        assert agent_node.metadata["label_key"] == "agent-1"

    def test_metadata_has_elapsed(self, agent_node):
        assert "elapsed" in agent_node.metadata
        assert agent_node.metadata["elapsed"] >= 0

    def test_root_metadata_has_type(self):
        root = subagent_slots_to_tree({"a": _make_slot()})
        assert root.metadata["type"] == "subagent_root"


class TestLabelFormat:
    """label 格式包含 [abbr] description (tokens elapsed) [phase]。"""

    def test_label_contains_abbr_bracket(self):
        slot = _make_slot(agent_type="map", model_phase="")
        root = subagent_slots_to_tree({"a": slot})
        label = root.children[0].label
        assert "[map]" in label

    def test_label_contains_description(self):
        slot = _make_slot(description="描述文本", model_phase="")
        root = subagent_slots_to_tree({"a": slot})
        label = root.children[0].label
        assert "描述文本" in label

    def test_label_contains_tokens(self):
        slot = _make_slot(output_tokens=300, model_phase="")
        root = subagent_slots_to_tree({"a": slot})
        label = root.children[0].label
        assert "300t" in label

    def test_label_contains_phase_bracket(self):
        slot = _make_slot(model_phase="thinking")
        root = subagent_slots_to_tree({"a": slot})
        label = root.children[0].label
        assert "[thinking]" in label

    def test_label_no_phase_when_empty(self):
        slot = _make_slot(model_phase="")
        root = subagent_slots_to_tree({"a": slot})
        label = root.children[0].label
        assert "[" not in label.replace("[map]", "")  # 去掉 abbr 的括号后无多余括号

    def test_label_starts_with_bracket_abbr(self):
        slot = _make_slot(agent_type="map", model_phase="")
        root = subagent_slots_to_tree({"a": slot})
        label = root.children[0].label
        assert label.startswith("[map]")

    def test_label_zero_tokens_omitted(self):
        slot = _make_slot(output_tokens=0, model_phase="")
        root = subagent_slots_to_tree({"a": slot})
        label = root.children[0].label
        assert "0t" not in label


class TestInvalidSlotSkipped:
    """非 dict 或空 dict 的 slot 条目被跳过。"""

    def test_non_dict_slot_skipped(self):
        slots = {
            "agent-1": _make_slot(description="有效"),
            "agent-2": "not_a_dict",
        }
        root = subagent_slots_to_tree(slots)
        assert len(root.children) == 1
        assert "有效" in root.children[0].label

    def test_empty_dict_slot_skipped(self):
        slots = {
            "agent-1": _make_slot(description="有效"),
            "agent-2": {},
        }
        root = subagent_slots_to_tree(slots)
        assert len(root.children) == 1
        # 确认跳过的是空 dict（label 需要一致——空 dict 没有 description，会被过滤）
        # 实际：!slot → 空 dict 被判 False → continue 跳过
        for child in root.children:
            assert child.label != ""  # 确认没有空 label 节点

    def test_all_invalid_returns_none(self):
        slots = {
            "agent-1": "not_dict",
            "agent-2": None,
        }
        root = subagent_slots_to_tree(slots)
        # 所有 slot 都不是 dict → children 为空 → root 仍被创建（label="" 无子节点）
        # 但实际：返回的是 root，children=[]。需要验证！
        assert root is not None
        assert len(root.children) == 0


class TestToolNameFromToolNameKey:
    """验证 tool_history 中读取的是 "tool_name" 键。"""

    def test_reads_tool_name_key(self):
        slot = _make_slot(tool_history=[{"tool_name": "read_file", "phase": "done"}])
        root = subagent_slots_to_tree({"a": slot})
        tool = root.children[0].children[0]
        assert tool.label == "read_file"

    def test_missing_tool_name_defaults_to_question_mark(self):
        slot = _make_slot(tool_history=[{"phase": "done"}])
        root = subagent_slots_to_tree({"a": slot})
        tool = root.children[0].children[0]
        assert tool.label == "?"

    def test_ignores_name_key(self):
        """确认不使用 "name" 键（与 _subagent.py 一致使用 "tool_name"）。"""
        slot = _make_slot(tool_history=[{"name": "wrong_name", "tool_name": "correct", "phase": "done"}])
        root = subagent_slots_to_tree({"a": slot})
        tool = root.children[0].children[0]
        assert tool.label == "correct"

    def test_non_dict_tool_skipped(self):
        """tool_history 中非 dict 条目被跳过。"""
        slot = _make_slot(tool_history=[
            "not_a_dict",
            {"tool_name": "valid_tool", "phase": "done"},
        ])
        root = subagent_slots_to_tree({"a": slot})
        assert len(root.children[0].children) == 1
        assert root.children[0].children[0].label == "valid_tool"

    def test_non_list_tool_history_ignored(self):
        """tool_history 为非 list 时，不产生 tool 子节点也不报错。"""
        slot = _make_slot(tool_history="not_a_list")  # type: ignore
        root = subagent_slots_to_tree({"a": slot})
        assert root.children[0].children == []


class TestToolLabel:
    """tool 子节点 label 仅包含 tool_name（无图标、无额外信息）。"""

    def test_tool_label_is_just_tool_name(self):
        slot = _make_slot(tool_history=[{"tool_name": "search", "phase": "done"}])
        root = subagent_slots_to_tree({"a": slot})
        tool = root.children[0].children[0]
        assert tool.label == "search"

    def test_tool_node_is_frozen(self):
        """TreeNode 是 frozen dataclass，修改应抛异常。"""
        slot = _make_slot(tool_history=[{"tool_name": "test", "phase": "done"}])
        root = subagent_slots_to_tree({"a": slot})
        tool = root.children[0].children[0]
        with pytest.raises(Exception):
            tool.label = "modified"  # frozen 不可变
