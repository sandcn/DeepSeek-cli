"""测试 src/webui/ws_handler/utils.py — _rebuild_message_indices"""

from __future__ import annotations

from copy import deepcopy

import pytest

from src.webui.ws_handler.utils import _rebuild_message_indices


class TestRebuildMessageIndices:
    """_rebuild_message_indices — 为非 system 消息重新分配 msg_index。"""

    # ── 边界情况 ───────────────────────────────────────

    def test_empty_list(self):
        """空列表 → 空列表。"""
        result = _rebuild_message_indices([])
        assert result == []

    def test_only_system_messages(self):
        """仅有 system 消息 → 空列表。"""
        messages = [
            {"role": "system", "content": "你是一个助手"},
            {"role": "system", "content": "附加指令"},
        ]
        result = _rebuild_message_indices(messages)
        assert result == []

    def test_single_system_message(self):
        """单个 system 消息 → 空列表。"""
        messages = [{"role": "system", "content": "system prompt"}]
        result = _rebuild_message_indices(messages)
        assert result == []

    # ── 基本 user/assistant ────────────────────────────

    def test_single_user(self):
        """单个 user 消息分配 msg_index=0。"""
        messages = [{"role": "user", "content": "hello"}]
        result = _rebuild_message_indices(messages)
        assert len(result) == 1
        assert result[0]["msg_index"] == 0
        assert result[0]["content"] == "hello"

    def test_single_assistant(self):
        """单个 assistant 消息分配 content_msg_index=0, reasoning_msg_index=0。"""
        messages = [{"role": "assistant", "content": "你好"}]
        result = _rebuild_message_indices(messages)
        assert len(result) == 1
        assert result[0]["content_msg_index"] == 0
        assert result[0]["reasoning_msg_index"] == 0

    def test_user_then_assistant(self):
        """user(0) → assistant(1)。"""
        messages = [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好！有什么可以帮助你的吗？"},
        ]
        result = _rebuild_message_indices(messages)
        assert len(result) == 2
        assert result[0]["msg_index"] == 0
        assert result[1]["content_msg_index"] == 1
        assert result[1]["reasoning_msg_index"] == 1

    # ── system 被过滤 ──────────────────────────────────

    def test_system_before_user(self):
        """system 消息被过滤，user 从 0 开始。"""
        messages = [
            {"role": "system", "content": "你是一个 AI"},
            {"role": "user", "content": "hello"},
        ]
        result = _rebuild_message_indices(messages)
        assert len(result) == 1
        assert result[0]["msg_index"] == 0

    def test_system_mixed_with_non_system(self):
        """system 穿插在 user/assistant 中，索引按非 system 顺序。"""
        messages = [
            {"role": "system", "content": "s1"},
            {"role": "user", "content": "u1"},
            {"role": "system", "content": "s2"},
            {"role": "assistant", "content": "a1"},
            {"role": "system", "content": "s3"},
            {"role": "user", "content": "u2"},
        ]
        result = _rebuild_message_indices(messages)
        assert len(result) == 3  # u1, a1, u2
        assert result[0]["role"] == "user"
        assert result[0]["msg_index"] == 0
        assert result[1]["role"] == "assistant"
        assert result[1]["content_msg_index"] == 1
        assert result[1]["reasoning_msg_index"] == 1
        assert result[2]["role"] == "user"
        assert result[2]["msg_index"] == 2

    # ── assistant 含 tool_calls ────────────────────────

    def test_assistant_with_tool_calls(self):
        """assistant 消息含 tool_calls 时，每个 tc 分配 msg_index。"""
        messages = [
            {"role": "user", "content": "搜索一下"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": "call_1", "function": {"name": "search"}},
                    {"id": "call_2", "function": {"name": "read"}},
                ],
            },
        ]
        result = _rebuild_message_indices(messages)
        assert len(result) == 2
        assert result[0]["msg_index"] == 0
        # assistant
        assert result[1]["content_msg_index"] == 1
        assert result[1]["reasoning_msg_index"] == 1
        # tool_calls 中的每个 tc 分配 msg_index
        assert result[1]["tool_calls"][0]["msg_index"] == 1
        assert result[1]["tool_calls"][1]["msg_index"] == 1

    def test_assistant_without_tool_calls(self):
        """assistant 消息不含 tool_calls 时仍然设置 content/reasoning msg_index。"""
        messages = [{"role": "assistant", "content": "直接回复"}]
        result = _rebuild_message_indices(messages)
        assert result[0]["content_msg_index"] == 0
        assert result[0]["reasoning_msg_index"] == 0
        assert "tool_calls" not in result[0]

    def test_assistant_with_empty_tool_calls(self):
        """tool_calls=[] 时跳过 tc 索引分配。"""
        messages = [{"role": "assistant", "content": None, "tool_calls": []}]
        result = _rebuild_message_indices(messages)
        assert result[0]["content_msg_index"] == 0
        assert result[0]["reasoning_msg_index"] == 0
        assert result[0]["tool_calls"] == []

    # ── deepcopy 不污染原始数据 ────────────────────────

    def test_does_not_mutate_original(self):
        """使用 deepcopy，不修改原始列表/字典。"""
        original = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        original_copy = deepcopy(original)
        result = _rebuild_message_indices(original)
        # 结果中有新字段
        assert "msg_index" in result[0]
        assert "content_msg_index" in result[1]
        # 原始数据不变
        assert "msg_index" not in original[0]
        assert "content_msg_index" not in original[1]
        assert original == original_copy

    def test_tool_calls_not_shared(self):
        """tool_calls 中的字典被 deepcopy，修改结果不污染原始数据。"""
        original_tc = {"id": "call_1", "function": {"name": "search"}}
        original = [
            {"role": "user", "content": "search"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [original_tc],
            },
        ]
        result = _rebuild_message_indices(original)
        # 结果中的 tool_calls 添加了 msg_index
        result[1]["tool_calls"][0]["msg_index"] = 1
        # 原始数据中的 tool_calls 不应有 msg_index
        assert "msg_index" not in original[1]["tool_calls"][0]

    # ── 复杂场景 ───────────────────────────────────────

    def test_multiple_user_assistant_alternating(self):
        """多个 user/assistant 交替，索引正确递增。"""
        messages = [
            {"role": "user", "content": "第一轮提问"},
            {"role": "assistant", "content": "第一轮回答"},
            {"role": "user", "content": "第二轮提问"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "tc1", "function": {"name": "calc"}},
            ]},
            {"role": "tool", "content": "计算结果", "tool_call_id": "tc1"},
            {"role": "assistant", "content": "第二轮回答"},
        ]
        result = _rebuild_message_indices(messages)

        assert len(result) == 6
        # idx 0: user
        assert result[0]["msg_index"] == 0
        # idx 1: assistant
        assert result[1]["content_msg_index"] == 1
        assert result[1]["reasoning_msg_index"] == 1
        # idx 2: user
        assert result[2]["msg_index"] == 2
        # idx 3: assistant with tool_calls
        assert result[3]["content_msg_index"] == 3
        assert result[3]["reasoning_msg_index"] == 3
        assert result[3]["tool_calls"][0]["msg_index"] == 3
        # idx 4: tool — 不检查 msg_index（只有 user/assistant 才有）
        # idx 5: assistant
        assert result[5]["content_msg_index"] == 5
        assert result[5]["reasoning_msg_index"] == 5

    def test_only_assistant_messages(self):
        """只有 assistant 消息时每个都正确分配索引。"""
        messages = [
            {"role": "assistant", "content": "a1"},
            {"role": "assistant", "content": "a2"},
        ]
        result = _rebuild_message_indices(messages)
        assert len(result) == 2
        assert result[0]["content_msg_index"] == 0
        assert result[0]["reasoning_msg_index"] == 0
        assert result[1]["content_msg_index"] == 1
        assert result[1]["reasoning_msg_index"] == 1

    def test_mixed_roles_with_system_at_end(self):
        """system 在末尾时被正确过滤。"""
        messages = [
            {"role": "user", "content": "u1"},
            {"role": "system", "content": "s1"},
            {"role": "assistant", "content": "a1"},
            {"role": "system", "content": "s2"},
        ]
        result = _rebuild_message_indices(messages)
        assert len(result) == 2
        assert result[0]["msg_index"] == 0  # user
        assert result[1]["content_msg_index"] == 1  # assistant

    def test_tool_message_indexing(self):
        """tool 角色消息不添加 msg_index（只有 user/assistant 才有）。"""
        messages = [
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "tc1", "function": {"name": "search"}},
            ]},
            {"role": "tool", "content": "结果", "tool_call_id": "tc1"},
        ]
        result = _rebuild_message_indices(messages)
        assert len(result) == 3
        assert result[0]["msg_index"] == 0  # user
        assert result[1]["content_msg_index"] == 1  # assistant
        assert result[1]["tool_calls"][0]["msg_index"] == 1
        # tool 消息不添加 msg_index

    def test_complex_scenario(self):
        """复杂交替场景 — 多轮对话 + tool_calls + system 穿插。"""
        messages = [
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "第一问"},
            {"role": "assistant", "content": "第一答"},
            {"role": "user", "content": "第二问"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "c1", "function": {"name": "search", "arguments": "{}"}},
                {"id": "c2", "function": {"name": "read", "arguments": "{}"}},
            ]},
            {"role": "tool", "content": "search结果", "tool_call_id": "c1"},
            {"role": "tool", "content": "read结果", "tool_call_id": "c2"},
            {"role": "assistant", "content": "最终回答"},
            {"role": "system", "content": "附加系统消息"},
            {"role": "user", "content": "第三问"},
        ]
        result = _rebuild_message_indices(messages)
        # 非 system: user(u1), assistant(a1), user(u2), assistant(a2+tcs),
        #            tool(t1), tool(t2), assistant(a3), user(u3) = 8
        assert len(result) == 8

        # u1 (idx 0)
        assert result[0]["msg_index"] == 0
        # a1 (idx 1)
        assert result[1]["content_msg_index"] == 1
        assert result[1]["reasoning_msg_index"] == 1
        # u2 (idx 2)
        assert result[2]["msg_index"] == 2
        # a2 with tool_calls (idx 3)
        assert result[3]["content_msg_index"] == 3
        assert result[3]["reasoning_msg_index"] == 3
        assert result[3]["tool_calls"][0]["msg_index"] == 3
        assert result[3]["tool_calls"][1]["msg_index"] == 3
        # t1 (idx 4) — tool 消息，不添加 msg_index
        # t2 (idx 5) — tool 消息，不添加 msg_index
        # a3 (idx 6)
        assert result[6]["content_msg_index"] == 6
        assert result[6]["reasoning_msg_index"] == 6
        # u3 (idx 7)
        assert result[7]["msg_index"] == 7

    def test_original_list_unchanged(self):
        """原始列表本身不被修改。"""
        messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        original_len = len(messages)
        result = _rebuild_message_indices(messages)
        assert len(messages) == original_len
        assert "msg_index" not in messages[0]
        assert "content_msg_index" not in messages[1]

    def test_return_type_is_list(self):
        """返回值是 list 类型。"""
        result = _rebuild_message_indices([])
        assert isinstance(result, list)
