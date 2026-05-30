"""测试 src/webui/msg_index.py — 消息索引分配模块"""

from __future__ import annotations

import pytest

from src.webui.msg_index import (
    MsgIndexState,
    _MSG_HANDLERS,
    _handle_user_message,
    _handle_reasoning_chunk,
    _handle_content_chunk,
    _handle_phase_done,
    _handle_tool_parsing,
    _handle_tool_lifecycle,
    _handle_other,
    assign_msg_index,
    non_system_len,
)


# ═══════════════════════════════════════════════════════════════
# MsgIndexState
# ═══════════════════════════════════════════════════════════════

class TestMsgIndexState:
    """MsgIndexState 状态机基础功能测试。"""

    def test_initial_values(self):
        """初始值：reasoning_idx=-1, content_idx=-1, tool_map={}, tool_names={}."""
        state = MsgIndexState()
        assert state.reasoning_idx == -1
        assert state.content_idx == -1
        assert state.tool_map == {}
        assert state.tool_names == {}

    def test_reset(self):
        """reset() 将所有状态恢复为初始值。"""
        state = MsgIndexState()
        state.reasoning_idx = 3
        state.content_idx = 5
        state.tool_map["0"] = 2
        state.tool_names["0"] = "search"

        state.reset()
        assert state.reasoning_idx == -1
        assert state.content_idx == -1
        assert state.tool_map == {}
        assert state.tool_names == {}

    def test_repr(self):
        """__repr__ 输出格式正确包含所有属性。"""
        state = MsgIndexState()
        state.reasoning_idx = 1
        state.content_idx = 2
        state.tool_map["0"] = 3
        state.tool_names["0"] = "lookup"
        r = repr(state)
        assert "MsgIndexState(" in r
        assert "reasoning_idx=1" in r
        assert "content_idx=2" in r
        assert "tool_map=" in r
        assert "tool_names=" in r

    def test_repr_truncates_large_maps(self):
        """__repr__ 对超大 tool_map/tool_names 只展示前 5 项。"""
        state = MsgIndexState()
        for i in range(10):
            state.tool_map[str(i)] = i
            state.tool_names[str(i)] = f"tool_{i}"
        r = repr(state)
        assert "tool_map=" in r
        assert "tool_names=" in r


# ═══════════════════════════════════════════════════════════════
# non_system_len
# ═══════════════════════════════════════════════════════════════

class TestNonSystemLen:
    """non_system_len — 过滤 role=system 的消息计数。"""

    def test_empty_list(self):
        assert non_system_len([]) == 0

    def test_all_system(self):
        msgs = [{"role": "system"}, {"role": "system"}]
        assert non_system_len(msgs) == 0

    def test_all_non_system(self):
        msgs = [{"role": "user"}, {"role": "assistant"}]
        assert non_system_len(msgs) == 2

    def test_mixed(self):
        msgs = [
            {"role": "system"},
            {"role": "user"},
            {"role": "assistant"},
            {"role": "system"},
            {"role": "tool"},
        ]
        assert non_system_len(msgs) == 3

    def test_missing_role_key(self):
        """role 缺失时视为非 system（计入计数）。"""
        msgs = [{"role": "system"}, {"content": "no role"}]
        assert non_system_len(msgs) == 1


# ═══════════════════════════════════════════════════════════════
# Handler 函数
# ═══════════════════════════════════════════════════════════════

class TestHandleUserMessage:
    """_handle_user_message — 设置 msg_index=nsl，重置 reasoning/content_idx。"""

    def test_sets_msg_index_to_nsl(self):
        msg = {"type": "user_message", "content": "hello"}
        state = MsgIndexState()
        state.reasoning_idx = 2
        state.content_idx = 3
        messages = [{"role": "system"}, {"role": "user"}]
        nsl = non_system_len(messages)  # =1
        _handle_user_message(msg, state, messages, nsl)
        assert msg["msg_index"] == 1

    def test_resets_reasoning_and_content_idx(self):
        msg = {"type": "user_message"}
        state = MsgIndexState()
        state.reasoning_idx = 5
        state.content_idx = 6
        _handle_user_message(msg, state, [], 0)
        assert state.reasoning_idx == -1
        assert state.content_idx == -1

    def test_preserves_tool_map(self):
        """不清除 tool_map / tool_names（异步任务可能还未执行）。"""
        msg = {"type": "user_message"}
        state = MsgIndexState()
        state.tool_map["0"] = 3
        state.tool_names["0"] = "search"
        _handle_user_message(msg, state, [], 99)
        assert state.tool_map == {"0": 3}
        assert state.tool_names == {"0": "search"}


class TestHandleReasoningChunk:
    """_handle_reasoning_chunk — 首次设置 reasoning_idx=nsl。"""

    def test_first_reasoning_chunk_sets_reasoning_idx(self):
        msg = {"type": "reasoning_chunk"}
        state = MsgIndexState()
        messages = [{"role": "system"}, {"role": "user"}]
        nsl = non_system_len(messages)  # =1
        _handle_reasoning_chunk(msg, state, messages, nsl)
        assert state.reasoning_idx == 1
        assert msg["msg_index"] == 1

    def test_subsequent_reasoning_chunk_reuses_idx(self):
        msg = {"type": "reasoning_chunk"}
        state = MsgIndexState()
        state.reasoning_idx = 3
        _handle_reasoning_chunk(msg, state, [], 99)
        assert state.reasoning_idx == 3  # 不改变
        assert msg["msg_index"] == 3

    def test_when_content_already_started_merges_to_content_idx(self):
        """content 已开始时，reasoning chunk 并入 content 气泡。"""
        msg = {"type": "reasoning_chunk"}
        state = MsgIndexState()
        state.content_idx = 5
        _handle_reasoning_chunk(msg, state, [], 99)
        assert state.reasoning_idx == 5  # 合并到 content_idx
        assert msg["msg_index"] == 5

    def test_when_both_already_set(self):
        """reasoning 和 content 都已开始时，继续用 reasoning_idx。"""
        msg = {"type": "reasoning_chunk"}
        state = MsgIndexState()
        state.reasoning_idx = 2
        state.content_idx = 3
        _handle_reasoning_chunk(msg, state, [], 99)
        assert msg["msg_index"] == 2


class TestHandleContentChunk:
    """_handle_content_chunk — 首次设置 content_idx=nsl。"""

    def test_first_content_chunk_sets_content_idx(self):
        msg = {"type": "content_chunk"}
        state = MsgIndexState()
        messages = [{"role": "system"}, {"role": "user"}]
        nsl = non_system_len(messages)  # =1
        _handle_content_chunk(msg, state, messages, nsl)
        assert state.content_idx == 1
        assert msg["msg_index"] == 1

    def test_subsequent_content_chunk_reuses_idx(self):
        msg = {"type": "content_chunk"}
        state = MsgIndexState()
        state.content_idx = 7
        _handle_content_chunk(msg, state, [], 99)
        assert state.content_idx == 7
        assert msg["msg_index"] == 7

    def test_when_reasoning_already_started_merges_to_reasoning_idx(self):
        """reasoning 已开始时，content chunk 并入 reasoning 气泡。"""
        msg = {"type": "content_chunk"}
        state = MsgIndexState()
        state.reasoning_idx = 4
        _handle_content_chunk(msg, state, [], 99)
        assert state.content_idx == 4
        assert msg["msg_index"] == 4

    def test_when_both_already_set(self):
        state = MsgIndexState()
        state.reasoning_idx = 1
        state.content_idx = 2
        msg_dict = {"type": "content_chunk"}
        _handle_content_chunk(msg_dict, state, [], 99)
        assert msg_dict["msg_index"] == 2


class TestHandlePhaseDone:
    """_handle_phase_done — reasoning/content/segment_end 分支。"""

    def test_phase_reasoning_sets_new_idx(self):
        msg = {"type": "phase_done", "phase": "reasoning"}
        state = MsgIndexState()
        nsl = 3
        _handle_phase_done(msg, state, [], nsl)
        assert state.reasoning_idx == 3
        assert msg["msg_index"] == 3

    def test_phase_reasoning_reuses_existing_idx(self):
        msg = {"type": "phase_done", "phase": "reasoning"}
        state = MsgIndexState()
        state.reasoning_idx = 1
        _handle_phase_done(msg, state, [], 99)
        assert state.reasoning_idx == 1
        assert msg["msg_index"] == 1

    def test_phase_content_sets_new_idx(self):
        msg = {"type": "phase_done", "phase": "content"}
        state = MsgIndexState()
        nsl = 4
        _handle_phase_done(msg, state, [], nsl)
        assert state.content_idx == 4
        assert msg["msg_index"] == 4

    def test_phase_content_reuses_existing_idx(self):
        msg = {"type": "phase_done", "phase": "content"}
        state = MsgIndexState()
        state.content_idx = 2
        _handle_phase_done(msg, state, [], 99)
        assert state.content_idx == 2
        assert msg["msg_index"] == 2

    def test_phase_segment_end(self):
        """segment_end 分配新下标并重置 reasoning/content_idx。"""
        msg = {"type": "phase_done", "phase": "segment_end"}
        state = MsgIndexState()
        state.reasoning_idx = 3
        state.content_idx = 4
        nsl = 5
        _handle_phase_done(msg, state, [], nsl)
        assert msg["msg_index"] == 5
        assert state.reasoning_idx == -1
        assert state.content_idx == -1

    def test_phase_unknown(self):
        msg = {"type": "phase_done", "phase": "unknown_phase"}
        state = MsgIndexState()
        nsl = 6
        _handle_phase_done(msg, state, [], nsl)
        assert msg["msg_index"] == nsl
        # 不改变已有状态
        assert state.reasoning_idx == -1
        assert state.content_idx == -1


class TestHandleToolParsing:
    """_handle_tool_parsing — 分配 msg_index，记录到 tool_map/tool_names。"""

    def test_assigns_msg_index_and_records_tool(self):
        msg = {"type": "tool_parsing", "label": "0", "tool_name": "search"}
        state = MsgIndexState()
        nsl = 2
        _handle_tool_parsing(msg, state, [], nsl)
        assert msg["msg_index"] == 2
        assert state.tool_map["0"] == 2
        assert state.tool_names["0"] == "search"

    def test_multiple_tools(self):
        msg1 = {"type": "tool_parsing", "label": "0", "tool_name": "search"}
        msg2 = {"type": "tool_parsing", "label": "1", "tool_name": "read_file"}
        state = MsgIndexState()
        _handle_tool_parsing(msg1, state, [], 2)
        _handle_tool_parsing(msg2, state, [], 3)
        assert state.tool_map == {"0": 2, "1": 3}
        assert state.tool_names == {"0": "search", "1": "read_file"}

    def test_empty_label(self):
        msg = {"type": "tool_parsing"}
        state = MsgIndexState()
        _handle_tool_parsing(msg, state, [], 5)
        assert state.tool_map[""] == 5
        assert state.tool_names[""] == ""


class TestHandleToolLifecycle:
    """_handle_tool_lifecycle — label 命中/回退/未命中。"""

    def test_label_direct_hit(self):
        """label 直接命中 tool_map。"""
        msg = {"type": "tool_started", "label": "0", "tool_name": "search"}
        state = MsgIndexState()
        state.tool_map["0"] = 3
        state.tool_names["0"] = "search"
        _handle_tool_lifecycle(msg, state, [], 99)
        assert msg["msg_index"] == 3

    def test_label_remap_by_tool_name(self):
        """label 未直接命中，按 tool_name + 数字 key 回退匹配并重映射。"""
        msg = {"type": "tool_started", "label": "call_abc", "tool_name": "search"}
        state = MsgIndexState()
        state.tool_map["0"] = 3
        state.tool_names["0"] = "search"
        _handle_tool_lifecycle(msg, state, [], 99)
        assert msg["msg_index"] == 3
        # 重映射：旧 key "0" 被替换为 "call_abc"
        assert "call_abc" in state.tool_map
        assert "0" not in state.tool_map
        assert state.tool_names.get("call_abc") == "search"
        assert "0" not in state.tool_names

    def test_label_remap_skips_non_digit_keys(self):
        """回退匹配时跳过非数字 key。"""
        msg = {"type": "tool_done", "label": "call_xyz", "tool_name": "search"}
        state = MsgIndexState()
        state.tool_map["abc"] = 3  # 非数字，应跳过
        state.tool_names["abc"] = "search"
        _handle_tool_lifecycle(msg, state, [], 99)
        # 未匹配到，走完全未命中分支
        assert msg["msg_index"] == 99

    def test_label_remap_skips_different_tool_name(self):
        """回退匹配时跳过 tool_name 不同的条目。"""
        msg = {"type": "tool_status", "label": "call_xyz", "tool_name": "read"}
        state = MsgIndexState()
        state.tool_map["0"] = 3
        state.tool_names["0"] = "search"  # 名称不匹配
        _handle_tool_lifecycle(msg, state, [], 99)
        assert msg["msg_index"] == 99

    def test_complete_miss_assigns_new_idx(self):
        """完全未命中时分配新下标到末尾。"""
        msg = {"type": "tool_output_chunk", "label": "unknown", "tool_name": "calc"}
        state = MsgIndexState()
        nsl = 7
        _handle_tool_lifecycle(msg, state, [], nsl)
        assert msg["msg_index"] == 7
        assert state.tool_map["unknown"] == 7
        assert state.tool_names["unknown"] == "calc"

    def test_multiple_tool_lifecycles_same_tool(self):
        """同一工具的多个 lifecycle 消息使用相同下标。"""
        state = MsgIndexState()
        state.tool_map["0"] = 5
        state.tool_names["0"] = "search"

        msg1 = {"type": "tool_started", "label": "0", "tool_name": "search"}
        msg2 = {"type": "tool_done", "label": "0", "tool_name": "search"}
        _handle_tool_lifecycle(msg1, state, [], 99)
        _handle_tool_lifecycle(msg2, state, [], 99)
        assert msg1["msg_index"] == 5
        assert msg2["msg_index"] == 5


class TestHandleOther:
    """_handle_other — 各类消息统一分配 msg_index=nsl。"""

    @pytest.mark.parametrize("msg_type", [
        "tool_summary", "tool_batch_start", "agent_added", "agent_status",
    ])
    def test_other_types_assign_nsl(self, msg_type):
        msg = {"type": msg_type}
        state = MsgIndexState()
        nsl = 8
        _handle_other(msg, state, [], nsl)
        assert msg["msg_index"] == 8

    def test_preserves_state(self):
        """不修改 state 的任何属性。"""
        msg = {"type": "tool_summary"}
        state = MsgIndexState()
        state.reasoning_idx = 1
        state.content_idx = 2
        state.tool_map["x"] = 3
        _handle_other(msg, state, [], 0)
        assert state.reasoning_idx == 1
        assert state.content_idx == 2
        assert state.tool_map == {"x": 3}


# ═══════════════════════════════════════════════════════════════
# _MSG_HANDLERS 映射表完整性
# ═══════════════════════════════════════════════════════════════

class TestMsgHandlersMapping:
    """_MSG_HANDLERS 映射表完整性检查。"""

    EXPECTED_MAPPING = {
        "user_message": _handle_user_message,
        "reasoning_chunk": _handle_reasoning_chunk,
        "content_chunk": _handle_content_chunk,
        "phase_done": _handle_phase_done,
        "tool_parsing": _handle_tool_parsing,
        "tool_started": _handle_tool_lifecycle,
        "tool_done": _handle_tool_lifecycle,
        "tool_output_chunk": _handle_tool_lifecycle,
        "tool_status": _handle_tool_lifecycle,
        "tool_summary": _handle_other,
        "tool_batch_start": _handle_other,
        "agent_added": _handle_other,
        "agent_status": _handle_other,
    }

    def test_all_expected_types_present(self):
        for mt, handler in self.EXPECTED_MAPPING.items():
            assert mt in _MSG_HANDLERS, f"缺少类型: {mt}"
            assert _MSG_HANDLERS[mt] is handler, f"{mt} 映射错误"

    def test_no_extra_unexpected_types(self):
        """映射表没有多余条目。"""
        assert set(_MSG_HANDLERS.keys()) == set(self.EXPECTED_MAPPING.keys())

    def test_all_handlers_are_callable(self):
        for mt, handler in _MSG_HANDLERS.items():
            assert callable(handler), f"{mt} 对应的 handler 不可调用"

    def test_unknown_type_returns_none(self):
        """未知类型在映射表中不存在。"""
        assert "unknown_type" not in _MSG_HANDLERS


# ═══════════════════════════════════════════════════════════════
# assign_msg_index 异步函数
# ═══════════════════════════════════════════════════════════════

class TestAssignMsgIndex:
    """assign_msg_index 异步函数 — 调用正确 handler 并发送。"""

    @pytest.mark.parametrize("msg_type,expected_idx", [
        ("user_message", 0),
        ("reasoning_chunk", 0),
        ("content_chunk", 0),
        ("tool_parsing", 0),
        ("tool_summary", 0),
    ])
    async def test_known_types_get_index(self, msg_type, expected_idx):
        """已知消息类型通过 handler 分配下标。"""
        msg = {"type": msg_type}
        state = MsgIndexState()
        sent = []

        async def fake_send(m):
            sent.append(m)

        await assign_msg_index(msg, state, [], fake_send)
        assert "msg_index" in msg
        assert msg["msg_index"] >= 0
        assert len(sent) == 1
        assert sent[0] is msg

    async def test_send_called_with_modified_msg(self):
        """ws_send 被调用且传入修改后的 msg。"""
        msg = {"type": "user_message", "content": "hi"}
        state = MsgIndexState()
        sent_msg = None

        async def fake_send(m):
            nonlocal sent_msg
            sent_msg = m

        await assign_msg_index(msg, state, [], fake_send)
        assert sent_msg is msg
        assert sent_msg.get("msg_index") == 0

    async def test_unknown_type_passes_through(self):
        """未知类型没有 handler，但 ws_send 仍被调用。"""
        msg = {"type": "unknown_type", "data": "test"}
        state = MsgIndexState()
        sent = []

        async def fake_send(m):
            sent.append(m)

        await assign_msg_index(msg, state, [], fake_send)
        # 没有 handler，msg_index 不会被设置
        assert "msg_index" not in msg
        assert len(sent) == 1

    async def test_handler_exception_logged_and_msg_sent(self):
        """handler 抛出异常时记录日志并继续发送。"""
        msg = {"type": "user_message"}
        state = MsgIndexState()
        sent = []

        async def fake_send(m):
            sent.append(m)

        # 注入一个会抛异常的 handler
        original = _MSG_HANDLERS.get("user_message")
        try:
            def broken_handler(msg, state, messages, nsl):
                raise ValueError("故意异常")

            _MSG_HANDLERS["user_message"] = broken_handler
            await assign_msg_index(msg, state, [], fake_send)
            assert len(sent) == 1
        finally:
            _MSG_HANDLERS["user_message"] = original

    async def test_nsl_reflects_messages_content(self):
        """nsl 基于实际 messages 内容计算。"""
        messages = [{"role": "system"}, {"role": "user"}, {"role": "assistant"}]
        msg = {"type": "user_message"}
        state = MsgIndexState()
        sent = []

        async def fake_send(m):
            sent.append(m)

        await assign_msg_index(msg, state, messages, fake_send)
        # non_system_len = 2 (user + assistant)
        assert msg["msg_index"] == 2
