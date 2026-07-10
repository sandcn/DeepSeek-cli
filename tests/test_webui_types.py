"""测试 src/webui/types.py — WSMsgType 常量、消息构建函数、__all__ 导出

覆盖要求：
1. WSMsgType 常量不重复、不为空、格式为 snake_case
2. 每个 msg_* 构建函数返回的 dict 包含正确的 type 字段
3. msg_user_select_needed 的 options / default_options 在 list 和 tuple 时行为一致
4. msg_tool_summary 处理成功/失败工具列表格式
5. msg_agent_added 在有/无 source 和 dispatch_label 时 dict 结构不同
6. 所有构建函数返回的 dict 不包含多余的 key
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from src.webui.types import (
    WSMsgType,
    msg_agent_added,
    msg_agent_status,
    msg_command_output,
    msg_content_chunk,
    msg_display_started,
    msg_display_stopped,
    msg_live_input,
    msg_live_output,
    msg_model_phase,
    msg_parse_info,
    msg_phase_done,
    msg_reasoning_chunk,
    msg_session_deleted,
    msg_session_loaded,
    msg_sessions_list,
    msg_speed_update,
    msg_tool_batch_start,
    msg_tool_done,
    msg_tool_output_chunk,
    msg_tool_parsing,
    msg_tool_started,
    msg_tool_status,
    msg_tool_summary,
    msg_usage_update,
    msg_user_select_needed,
    __all__,
)


# ═══════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════

def _get_all_wsmsgtype_values() -> dict[str, str]:
    """收集 WSMsgType 中所有非下划线开头的常量名→值映射。"""
    return {
        name: value
        for name, value in vars(WSMsgType).items()
        if isinstance(value, str) and not name.startswith("_")
    }


# ═══════════════════════════════════════════════════════════════
# 1. WSMsgType 常量测试 — 不重复、不为空、格式正确
# ═══════════════════════════════════════════════════════════════

class TestWSMsgTypeConstants:
    """验证 WSMsgType 所有常量值唯一、非空、符合 snake_case 格式。"""

    SNAKE_CASE_RE = re.compile(r"^[a-z][a-z0-9_]*$")

    def test_all_values_non_empty(self) -> None:
        """每个常量值不能为空字符串。"""
        for name, value in _get_all_wsmsgtype_values().items():
            assert value, f"WSMsgType.{name} 的值为空"

    def test_all_values_unique(self) -> None:
        """所有常量值互不相同。"""
        values = list(_get_all_wsmsgtype_values().values())
        duplicates = {v for v in values if values.count(v) > 1}
        assert not duplicates, f"存在重复的常量值: {duplicates}"

    def test_all_values_snake_case(self) -> None:
        """每个常量值必须为 snake_case 格式（小写字母+数字+下划线）。"""
        for name, value in _get_all_wsmsgtype_values().items():
            assert self.SNAKE_CASE_RE.match(value), (
                f"WSMsgType.{name} = {value!r} 不是 snake_case 格式"
            )

    def test_value_matches_name_convention(self) -> None:
        """常量值应为常量名的 lower_snake_case 版本（用下划线分隔单词）。"""
        for name, value in _get_all_wsmsgtype_values().items():
            expected = name.lower()
            assert value == expected, (
                f"WSMsgType.{name} 的值为 {value!r}，期望值为 {expected!r} "
                f"(常量名全小写)"
            )


# ═══════════════════════════════════════════════════════════════
# 2. 消息构建函数测试 — type 字段 + 无多余 key
# ═══════════════════════════════════════════════════════════════
#
# 每个测试类覆盖一个 msg_* 函数：
#   - type 字段值与对应 WSMsgType 常量一致
#   - 返回的 dict 仅包含预期 key，无多余 key
#   - 默认值/边界值行为
# ═══════════════════════════════════════════════════════════════

# ── 2.1 内容流 ──

class TestMsgContentChunk:
    EXPECTED_KEYS = {"type", "text", "label"}

    def test_type_and_keys(self) -> None:
        result = msg_content_chunk("Hello", "agent-1")
        assert result["type"] == WSMsgType.CONTENT_CHUNK
        assert set(result) == self.EXPECTED_KEYS

    def test_values(self) -> None:
        result = msg_content_chunk("Hello", "agent-1")
        assert result["text"] == "Hello"
        assert result["label"] == "agent-1"

    def test_empty_text(self) -> None:
        result = msg_content_chunk("", "")
        assert result["text"] == ""
        assert result["label"] == ""


class TestMsgReasoningChunk:
    EXPECTED_KEYS = {"type", "text", "label"}

    def test_type_and_keys(self) -> None:
        result = msg_reasoning_chunk("thinking...", "agent-1")
        assert result["type"] == WSMsgType.REASONING_CHUNK
        assert set(result) == self.EXPECTED_KEYS

    def test_values(self) -> None:
        result = msg_reasoning_chunk("思考中", "agent-2")
        assert result["text"] == "思考中"
        assert result["label"] == "agent-2"


class TestMsgPhaseDone:
    EXPECTED_KEYS = {"type", "phase", "label"}

    def test_type_and_keys(self) -> None:
        result = msg_phase_done("reasoning", "agent-1")
        assert result["type"] == WSMsgType.PHASE_DONE
        assert set(result) == self.EXPECTED_KEYS

    def test_values(self) -> None:
        result = msg_phase_done("done", "agent-1")
        assert result["phase"] == "done"
        assert result["label"] == "agent-1"


# ── 2.2 工具调用 ──

class TestMsgToolParsing:
    EXPECTED_KEYS = {"type", "label", "tool_name", "arguments"}

    def test_type_and_keys(self) -> None:
        result = msg_tool_parsing("agent-1", "read_file", '{}')
        assert result["type"] == WSMsgType.TOOL_PARSING
        assert set(result) == self.EXPECTED_KEYS

    def test_with_arguments(self) -> None:
        result = msg_tool_parsing("agent-1", "read_file", '{"path": "x.py"}')
        assert result["tool_name"] == "read_file"
        assert result["arguments"] == '{"path": "x.py"}'

    def test_default_arguments(self) -> None:
        result = msg_tool_parsing("agent-1", "read_file")
        assert result["arguments"] == ""


class TestMsgToolStarted:
    EXPECTED_KEYS = {"type", "label", "tool_name", "detail", "metadata"}

    def test_type_and_keys(self) -> None:
        result = msg_tool_started("agent-1", "read_file")
        assert result["type"] == WSMsgType.TOOL_STARTED
        assert set(result) == self.EXPECTED_KEYS

    def test_defaults(self) -> None:
        result = msg_tool_started("agent-1", "read_file")
        assert result["detail"] == ""
        assert result["metadata"] == {}

    def test_with_all_params(self) -> None:
        result = msg_tool_started("agent-1", "write_file",
                                  detail="writing...",
                                  metadata={"lines": 10})
        assert result["detail"] == "writing..."
        assert result["metadata"] == {"lines": 10}

    def test_none_metadata_defaults_to_empty_dict(self) -> None:
        result = msg_tool_started("agent-1", "read_file", metadata=None)
        assert result["metadata"] == {}


class TestMsgToolDone:
    EXPECTED_KEYS = {"type", "label", "tool_name", "success", "metadata"}

    def test_type_and_keys(self) -> None:
        result = msg_tool_done("agent-1")
        assert result["type"] == WSMsgType.TOOL_DONE
        assert set(result) == self.EXPECTED_KEYS

    def test_defaults(self) -> None:
        result = msg_tool_done("agent-1")
        assert result["tool_name"] == ""
        assert result["success"] is True
        assert result["metadata"] == {}

    def test_failure(self) -> None:
        result = msg_tool_done("agent-1", success=False, metadata={"err": "timeout"})
        assert result["success"] is False
        assert result["metadata"] == {"err": "timeout"}

    def test_none_metadata_defaults_to_empty_dict(self) -> None:
        result = msg_tool_done("agent-1", metadata=None)
        assert result["metadata"] == {}


class TestMsgToolStatus:
    EXPECTED_KEYS = {"type", "label", "status"}

    def test_type_and_keys(self) -> None:
        result = msg_tool_status("agent-1", "running")
        assert result["type"] == WSMsgType.TOOL_STATUS
        assert set(result) == self.EXPECTED_KEYS

    def test_values(self) -> None:
        result = msg_tool_status("agent-1", "done")
        assert result["label"] == "agent-1"
        assert result["status"] == "done"


class TestMsgToolOutputChunk:
    EXPECTED_KEYS = {"type", "label", "text"}

    def test_type_and_keys(self) -> None:
        result = msg_tool_output_chunk("agent-1", "output text")
        assert result["type"] == WSMsgType.TOOL_OUTPUT_CHUNK
        assert set(result) == self.EXPECTED_KEYS

    def test_values(self) -> None:
        result = msg_tool_output_chunk("agent-1", "line 1\\nline 2")
        assert result["label"] == "agent-1"
        assert result["text"] == "line 1\\nline 2"

    def test_empty_text(self) -> None:
        result = msg_tool_output_chunk("agent-1", "")
        assert result["text"] == ""


class TestMsgToolBatchStart:
    EXPECTED_KEYS = {"type", "label", "names"}

    def test_type_and_keys(self) -> None:
        result = msg_tool_batch_start("agent-1", ["read_file", "write_file"])
        assert result["type"] == WSMsgType.TOOL_BATCH_START
        assert set(result) == self.EXPECTED_KEYS

    def test_names_list(self) -> None:
        names = ["a", "b", "c"]
        result = msg_tool_batch_start("agent-1", names)
        assert result["names"] == names

    def test_empty_names(self) -> None:
        result = msg_tool_batch_start("agent-1", [])
        assert result["names"] == []


# ── 2.3 模型阶段/用量 ──

class TestMsgModelPhase:
    EXPECTED_KEYS = {"type", "label", "phase", "info"}

    def test_type_and_keys(self) -> None:
        result = msg_model_phase("agent-1", "thinking")
        assert result["type"] == WSMsgType.MODEL_PHASE
        assert set(result) == self.EXPECTED_KEYS

    def test_default_info(self) -> None:
        result = msg_model_phase("agent-1", "thinking")
        assert result["info"] == ""

    def test_with_info(self) -> None:
        result = msg_model_phase("agent-1", "generating", info="10 tokens")
        assert result["info"] == "10 tokens"


class TestMsgUsageUpdate:
    EXPECTED_KEYS = {"type", "label", "usage", "replace"}

    def test_type_and_keys(self) -> None:
        result = msg_usage_update("agent-1", {"input": 100})
        assert result["type"] == WSMsgType.USAGE_UPDATE
        assert set(result) == self.EXPECTED_KEYS

    def test_default_replace(self) -> None:
        result = msg_usage_update("agent-1", {"input": 100})
        assert result["replace"] is False

    def test_replace_true(self) -> None:
        result = msg_usage_update("agent-1", {"input": 100}, replace=True)
        assert result["replace"] is True

    def test_empty_usage(self) -> None:
        result = msg_usage_update("agent-1", {})
        assert result["usage"] == {}


# ── 2.4 Agent 生命周期 ──

class TestMsgAgentAdded:
    """特别注意：不含 source/dispatch_label 时不可有多余的 key。"""
    BASE_KEYS = {"type", "label", "description", "status"}

    def test_type_and_keys_no_extras(self) -> None:
        """不传 source 和 dispatch_label 时，只有 4 个 base keys。"""
        result = msg_agent_added("agent-1", "desc", "running")
        assert result["type"] == WSMsgType.AGENT_ADDED
        assert set(result) == self.BASE_KEYS

    def test_with_source_only(self) -> None:
        """仅传 source 时，dict 包含 source 键。"""
        result = msg_agent_added("agent-1", "desc", "running", source="parent")
        assert set(result) == self.BASE_KEYS | {"source"}
        assert result["source"] == "parent"

    def test_with_dispatch_label_only(self) -> None:
        """仅传 dispatch_label 时，dict 包含 dispatch_label 键。"""
        result = msg_agent_added("agent-1", "desc", "running",
                                 dispatch_label="sub-agent")
        assert set(result) == self.BASE_KEYS | {"dispatch_label"}
        assert result["dispatch_label"] == "sub-agent"

    def test_with_both_source_and_dispatch_label(self) -> None:
        """同时传 source 和 dispatch_label 时，dict 包含两个额外键。"""
        result = msg_agent_added("agent-1", "desc", "running",
                                 source="parent", dispatch_label="sub-agent")
        assert set(result) == self.BASE_KEYS | {"source", "dispatch_label"}
        assert result["source"] == "parent"
        assert result["dispatch_label"] == "sub-agent"

    def test_empty_source_dispatch_label(self) -> None:
        """传空字符串的 source 和 dispatch_label 时，不应出现在 dict 中。"""
        result = msg_agent_added("agent-1", "desc", "running",
                                 source="", dispatch_label="")
        assert set(result) == self.BASE_KEYS
        assert "source" not in result
        assert "dispatch_label" not in result


class TestMsgAgentStatus:
    EXPECTED_KEYS = {"type", "label", "status"}

    def test_type_and_keys(self) -> None:
        result = msg_agent_status("agent-1", "done")
        assert result["type"] == WSMsgType.AGENT_STATUS
        assert set(result) == self.EXPECTED_KEYS

    def test_values(self) -> None:
        result = msg_agent_status("agent-1", "running")
        assert result["label"] == "agent-1"
        assert result["status"] == "running"


# ── 2.5 用户交互 ──

class TestMsgUserSelectNeeded:
    EXPECTED_KEYS = {"type", "select_id", "title", "options",
                     "multi_select", "default_options", "timeout"}

    def test_type_and_keys(self) -> None:
        result = msg_user_select_needed(
            select_id="sel-1", title="选择", options=["a", "b"],
            multi_select=True, default_options=["a"], timeout=30,
        )
        assert result["type"] == WSMsgType.USER_SELECT_NEEDED
        assert set(result) == self.EXPECTED_KEYS

    def test_basic_values(self) -> None:
        result = msg_user_select_needed(
            select_id="sel-1", title="请选择文件",
            options=["a.py", "b.py"],
            multi_select=True,
            default_options=["a.py"],
            timeout=120,
        )
        assert result["select_id"] == "sel-1"
        assert result["title"] == "请选择文件"
        assert result["options"] == ["a.py", "b.py"]
        assert result["multi_select"] is True
        assert result["default_options"] == ["a.py"]
        assert result["timeout"] == 120

    def test_multi_select_false(self) -> None:
        result = msg_user_select_needed(
            select_id="sel-1", title="选择", options=["a", "b"],
            multi_select=False, default_options=[], timeout=30,
        )
        assert result["multi_select"] is False

    def test_timeout_zero(self) -> None:
        result = msg_user_select_needed(
            select_id="sel-1", title="", options=[],
            multi_select=False, default_options=[], timeout=0,
        )
        assert result["timeout"] == 0

    # ── list/tuple 一致性 ──

    @pytest.mark.parametrize("options_input,expected", [
        (["a", "b"], ["a", "b"]),
        (("a", "b"), ["a", "b"]),
        ([], []),
        ((), []),
    ])
    def test_options_list_tuple_consistency(self, options_input: Any,
                                            expected: list) -> None:
        """options 参数传入 list 或 tuple 时，返回的 dict 中始终为 list。"""
        result = msg_user_select_needed(
            select_id="s1", title="t", options=options_input,
            multi_select=False, default_options=[], timeout=10,
        )
        assert result["options"] == expected
        assert isinstance(result["options"], list)

    @pytest.mark.parametrize("default_input,expected", [
        (["a"], ["a"]),
        (("a",), ["a"]),
        ([], []),
        ((), []),
    ])
    def test_default_options_list_tuple_consistency(self, default_input: Any,
                                                    expected: list) -> None:
        """default_options 参数传入 list 或 tuple 时，返回的 dict 中始终为 list。"""
        result = msg_user_select_needed(
            select_id="s1", title="t", options=["a", "b", "c"],
            multi_select=True, default_options=default_input, timeout=10,
        )
        assert result["default_options"] == expected
        assert isinstance(result["default_options"], list)

    def test_options_tuple_preserves_order(self) -> None:
        """传入 tuple 时顺序保持不变。"""
        result = msg_user_select_needed(
            select_id="s1", title="t",
            options=("c", "a", "b"),
            multi_select=False, default_options=[], timeout=10,
        )
        assert result["options"] == ["c", "a", "b"]


class TestMsgCommandOutput:
    EXPECTED_KEYS = {"type", "text", "level"}

    def test_type_and_keys(self) -> None:
        result = msg_command_output("hello")
        assert result["type"] == WSMsgType.COMMAND_OUTPUT
        assert set(result) == self.EXPECTED_KEYS

    def test_default_level(self) -> None:
        result = msg_command_output("hello")
        assert result["level"] == "info"

    def test_error_level(self) -> None:
        result = msg_command_output("error!", level="error")
        assert result["level"] == "error"

    def test_empty_text(self) -> None:
        result = msg_command_output("")
        assert result["text"] == ""


# ── 2.6 工具摘要 ──

class TestMsgToolSummary:
    EXPECTED_KEYS = {"type", "successful_tools", "failed_tools"}

    def test_type_and_keys(self) -> None:
        result = msg_tool_summary(successful_tools=[], failed_tools=[])
        assert result["type"] == WSMsgType.TOOL_SUMMARY
        assert set(result) == self.EXPECTED_KEYS

    def test_successful_tools_format(self) -> None:
        """successful_tools 被转为 list，内容不变。"""
        result = msg_tool_summary(
            successful_tools=("read_file", "write_file"),
            failed_tools=[],
        )
        assert result["successful_tools"] == ["read_file", "write_file"]
        assert isinstance(result["successful_tools"], list)

    def test_successful_tools_object_types(self) -> None:
        """successful_tools 的元素可以是任意类型（不仅仅是字符串）。"""
        tools = [{"name": "read_file"}, 42, None]
        result = msg_tool_summary(successful_tools=tools, failed_tools=[])
        assert result["successful_tools"] == tools

    def test_failed_tools_format(self) -> None:
        """failed_tools 中的每个 (name, error) 被转为 {"name": ..., "error": ...}。"""
        result = msg_tool_summary(
            successful_tools=[],
            failed_tools=[("tool_a", "timeout"), ("tool_b", "permission denied")],
        )
        assert result["failed_tools"] == [
            {"name": "tool_a", "error": "timeout"},
            {"name": "tool_b", "error": "permission denied"},
        ]

    def test_failed_tools_tuple_input(self) -> None:
        """failed_tools 本身传入 tuple 时也能正确处理。"""
        result = msg_tool_summary(
            successful_tools=[],
            failed_tools=(("bad_one", "err1"), ("bad_two", "err2")),
        )
        assert result["failed_tools"] == [
            {"name": "bad_one", "error": "err1"},
            {"name": "bad_two", "error": "err2"},
        ]

    def test_failed_tools_empty_error_string(self) -> None:
        """error 为空字符串时依然生成正确格式。"""
        result = msg_tool_summary(
            successful_tools=[],
            failed_tools=[("tool_x", "")],
        )
        assert result["failed_tools"] == [{"name": "tool_x", "error": ""}]

    def test_both_empty(self) -> None:
        result = msg_tool_summary(successful_tools=[], failed_tools=[])
        assert result["successful_tools"] == []
        assert result["failed_tools"] == []


# ── 2.7 实时指标 ──

class TestMsgParseInfo:
    EXPECTED_KEYS = {"type", "label", "tool_name", "tokens", "elapsed"}

    def test_type_and_keys(self) -> None:
        result = msg_parse_info("agent-1", "read_file", 100, 0.5)
        assert result["type"] == WSMsgType.PARSE_INFO
        assert set(result) == self.EXPECTED_KEYS

    def test_values(self) -> None:
        result = msg_parse_info("agent-1", "read_file", 150, 0.35)
        assert result["label"] == "agent-1"
        assert result["tool_name"] == "read_file"
        assert result["tokens"] == 150
        assert result["elapsed"] == 0.35


class TestMsgSpeedUpdate:
    EXPECTED_KEYS = {"type", "label", "speed"}

    def test_type_and_keys(self) -> None:
        result = msg_speed_update("agent-1", 15.5)
        assert result["type"] == WSMsgType.SPEED_UPDATE
        assert set(result) == self.EXPECTED_KEYS

    def test_values(self) -> None:
        result = msg_speed_update("agent-1", 10.2)
        assert result["label"] == "agent-1"
        assert result["speed"] == 10.2

    def test_zero_speed(self) -> None:
        result = msg_speed_update("agent-1", 0.0)
        assert result["speed"] == 0.0


class TestMsgLiveInput:
    EXPECTED_KEYS = {"type", "label", "tokens"}

    def test_type_and_keys(self) -> None:
        result = msg_live_input("agent-1", 42)
        assert result["type"] == WSMsgType.LIVE_INPUT
        assert set(result) == self.EXPECTED_KEYS

    def test_values(self) -> None:
        result = msg_live_input("agent-1", 100)
        assert result["label"] == "agent-1"
        assert result["tokens"] == 100

    def test_zero_tokens(self) -> None:
        result = msg_live_input("agent-1", 0)
        assert result["tokens"] == 0


class TestMsgLiveOutput:
    EXPECTED_KEYS = {"type", "label", "tokens"}

    def test_type_and_keys(self) -> None:
        result = msg_live_output("agent-1", 128)
        assert result["type"] == WSMsgType.LIVE_OUTPUT
        assert set(result) == self.EXPECTED_KEYS

    def test_values(self) -> None:
        result = msg_live_output("agent-1", 256)
        assert result["label"] == "agent-1"
        assert result["tokens"] == 256

    def test_zero_tokens(self) -> None:
        result = msg_live_output("agent-1", 0)
        assert result["tokens"] == 0


# ── 2.8 生命周期 ──

class TestMsgDisplayStarted:
    EXPECTED_KEYS = {"type"}

    def test_type_and_keys(self) -> None:
        result = msg_display_started()
        assert result["type"] == WSMsgType.DISPLAY_STARTED
        assert set(result) == self.EXPECTED_KEYS

    def test_returns_single_key_dict(self) -> None:
        """只包含 type 一个 key。"""
        result = msg_display_started()
        assert len(result) == 1
        assert list(result.keys()) == ["type"]


class TestMsgDisplayStopped:
    EXPECTED_KEYS = {"type", "final"}

    def test_type_and_keys(self) -> None:
        result = msg_display_stopped()
        assert result["type"] == WSMsgType.DISPLAY_STOPPED
        assert set(result) == self.EXPECTED_KEYS

    def test_default_final(self) -> None:
        result = msg_display_stopped()
        assert result["final"] is False

    def test_final_true(self) -> None:
        result = msg_display_stopped(final=True)
        assert result["final"] is True


# ── 2.9 会话历史 ──

class TestMsgSessionsList:
    EXPECTED_KEYS = {"type", "sessions", "current_id"}

    def test_type_and_keys(self) -> None:
        result = msg_sessions_list([], current_id="")
        assert result["type"] == WSMsgType.SESSIONS_LIST
        assert set(result) == self.EXPECTED_KEYS

    def test_with_sessions(self) -> None:
        sessions = [{"id": "s1", "title": "Chat 1"}]
        result = msg_sessions_list(sessions, current_id="s1")
        assert result["sessions"] == [{"id": "s1", "title": "Chat 1"}]
        assert result["current_id"] == "s1"

    def test_default_current_id(self) -> None:
        result = msg_sessions_list([])
        assert result["current_id"] == ""


class TestMsgSessionDeleted:
    EXPECTED_KEYS = {"type", "session_id"}

    def test_type_and_keys(self) -> None:
        result = msg_session_deleted("s1")
        assert result["type"] == WSMsgType.SESSION_DELETED
        assert set(result) == self.EXPECTED_KEYS

    def test_values(self) -> None:
        result = msg_session_deleted("session-abc")
        assert result["session_id"] == "session-abc"

    def test_empty_id(self) -> None:
        result = msg_session_deleted("")
        assert result["session_id"] == ""


class TestMsgSessionLoaded:
    EXPECTED_KEYS = {"type", "session_id", "model", "messages"}

    def test_type_and_keys(self) -> None:
        result = msg_session_loaded("s1", "gpt-4", [])
        assert result["type"] == WSMsgType.SESSION_LOADED
        assert set(result) == self.EXPECTED_KEYS

    def test_values(self) -> None:
        messages = [{"role": "user", "content": "hi"}]
        result = msg_session_loaded("s1", "gpt-4", messages)
        assert result["session_id"] == "s1"
        assert result["model"] == "gpt-4"
        assert result["messages"] == [{"role": "user", "content": "hi"}]

    def test_empty_messages(self) -> None:
        result = msg_session_loaded("s1", "gpt-4", [])
        assert result["messages"] == []


# ═══════════════════════════════════════════════════════════════
# 3. __all__ 导出完整性测试
# ═══════════════════════════════════════════════════════════════

class TestModuleExports:
    """验证 __all__ 包含所有必要的导出项且无多余项。"""

    EXPECTED_ALL = {
        "WSMsgType",
        "msg_content_chunk", "msg_reasoning_chunk", "msg_phase_done",
        "msg_tool_parsing", "msg_tool_started", "msg_tool_done",
        "msg_tool_status", "msg_tool_summary", "msg_tool_batch_start",
        "msg_tool_output_chunk",
        "msg_model_phase", "msg_usage_update",
        "msg_agent_added", "msg_agent_status",
        "msg_command_output",
        "msg_user_select_needed",
        "msg_speed_update", "msg_live_input", "msg_live_output",
        "msg_parse_info",
        "msg_sessions_list", "msg_session_deleted", "msg_session_loaded",
        "msg_display_started", "msg_display_stopped",
        "msg_output_frame",
        "msg_round_cost",
        "msg_agent_tool_parsing", "msg_agent_tool_started", "msg_agent_tool_done",
    }

    def test_wsmsgtype_in_all(self) -> None:
        assert "WSMsgType" in __all__

    def test_all_builder_functions_present(self) -> None:
        """__all__ 包含所有预期的构建函数名。"""
        for name in sorted(self.EXPECTED_ALL):
            assert name in __all__, f"__all__ 缺少 {name}"

    def test_all_no_unexpected_items(self) -> None:
        """__all__ 不包含多余导出项。"""
        extra = set(__all__) - self.EXPECTED_ALL
        missing = self.EXPECTED_ALL - set(__all__)
        assert not extra, f"__all__ 包含多余项: {extra}"
        assert not missing, f"__all__ 缺少预期项: {missing}"

    def test_all_count_matches(self) -> None:
        """__all__ 预期元素数量一致（防止遗漏增减未同步）。"""
        assert len(__all__) == len(self.EXPECTED_ALL), (
            f"__all__ 元素数量 {len(__all__)} 与预期 {len(self.EXPECTED_ALL)} 不一致"
        )
