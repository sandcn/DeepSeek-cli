"""测试 SessionState — 独立数据容器单元测试

覆盖内容：
  1. 默认字段值正确
  2. pop_pending_messages() 返回并清空
  3. on/off/_emit Hook 注册与触发
  4. 多实例隔离
"""

import asyncio
import pytest
from unittest.mock import MagicMock

from src.core.hooks import CoreHooks
from src.core.internal.session._session_state import SessionState


# ===============================================================
# 1. 默认字段值
# ===============================================================

class TestDefaultValues:
    """验证 SessionState 各字段的默认值"""

    def test_session_id_default(self):
        state = SessionState()
        assert state.session_id is None

    def test_retry_pending_default(self):
        state = SessionState()
        assert state.retry_pending is False

    def test_pending_messages_default(self):
        state = SessionState()
        assert state.pending_messages == []

    def test_hooks_default(self):
        state = SessionState()
        assert isinstance(state.hooks, CoreHooks)
        # 验证通过 __getitem__ 委托，访问不存在的键返回空列表
        assert state.hooks["nonexistent"] == []

    def test_captured_prefill_default(self):
        state = SessionState()
        assert state.captured_prefill == ""

    def test_orphaned_task_default(self):
        state = SessionState()
        assert state.orphaned_task is None

    @pytest.mark.asyncio
    async def test_round_lock_default(self):
        state = SessionState()
        assert isinstance(state.round_lock, asyncio.Lock)


# ===============================================================
# 2. pop_pending_messages
# ===============================================================

class TestPopPendingMessages:
    """验证 pop_pending_messages 返回并清空"""

    def test_pop_empty_returns_empty_list(self):
        state = SessionState()
        assert state.pop_pending_messages() == []

    def test_pop_returns_copy_and_clears(self):
        state = SessionState()
        state.pending_messages = ["msg1", "msg2", "msg3"]
        result = state.pop_pending_messages()
        assert result == ["msg1", "msg2", "msg3"]
        assert state.pending_messages == []

    def test_pop_returns_new_list_object(self):
        state = SessionState()
        state.pending_messages = ["a", "b"]
        result = state.pop_pending_messages()
        # 验证返回的是新列表，修改返回的列表不影响内部状态
        result.append("c")
        assert state.pending_messages == []

    def test_double_pop_after_re_add(self):
        state = SessionState()
        state.pending_messages = ["x"]
        state.pop_pending_messages()
        state.pending_messages = ["y"]
        result = state.pop_pending_messages()
        assert result == ["y"]
        assert state.pending_messages == []


# ===============================================================
# 3. Hook 系统 (on/off/_emit)
# ===============================================================

class TestHookSystem:
    """验证 Hook 注册、注销与触发"""

    def test_on_registers_callback(self):
        state = SessionState()
        cb = MagicMock()
        state.hooks.on("round_end", cb)
        assert cb in state.hooks["round_end"]

    def test_emit_calls_callback(self):
        state = SessionState()
        cb = MagicMock()
        state.hooks.on("round_end", cb)
        state.hooks._emit("round_end", foo="bar")
        cb.assert_called_once_with(foo="bar")

    def test_emit_calls_multiple_callbacks(self):
        state = SessionState()
        cb1 = MagicMock()
        cb2 = MagicMock()
        state.hooks.on("round_end", cb1)
        state.hooks.on("round_end", cb2)
        state.hooks._emit("round_end")
        cb1.assert_called_once()
        cb2.assert_called_once()

    def test_emit_skips_unregistered_event(self):
        state = SessionState()
        cb = MagicMock()
        state.hooks.on("round_start", cb)
        state.hooks._emit("round_end")  # 不同事件
        cb.assert_not_called()

    def test_off_removes_callback(self):
        state = SessionState()
        cb = MagicMock()
        state.hooks.on("round_end", cb)
        state.hooks.off("round_end", cb)
        assert cb not in state.hooks["round_end"]

    def test_off_noop_for_unregistered_callback(self):
        state = SessionState()
        cb = MagicMock()
        # 不报错即可
        state.hooks.off("round_end", cb)

    def test_emit_exception_swallowed(self):
        """单个回调异常不应阻止其他回调执行"""
        state = SessionState()
        cb1 = MagicMock(side_effect=ValueError("模拟异常"))
        cb2 = MagicMock()

        state.hooks.on("round_end", cb1)
        state.hooks.on("round_end", cb2)

        # 不应抛出异常
        state.hooks._emit("round_end")

        cb2.assert_called_once()


# ===============================================================
# 4. 多实例隔离
# ===============================================================

class TestInstanceIsolation:
    """验证多个 SessionState 实例互不干扰"""

    def test_independent_hooks(self):
        state1 = SessionState()
        state2 = SessionState()

        cb1 = MagicMock()
        cb2 = MagicMock()

        state1.hooks.on("round_end", cb1)
        state2.hooks.on("round_end", cb2)

        state1.hooks._emit("round_end")
        cb1.assert_called_once()
        cb2.assert_not_called()

    def test_independent_pending_messages(self):
        state1 = SessionState()
        state2 = SessionState()

        state1.pending_messages = ["msg_a"]
        state2.pending_messages = ["msg_b"]

        assert state1.pop_pending_messages() == ["msg_a"]
        assert state2.pop_pending_messages() == ["msg_b"]

    def test_independent_session_id(self):
        state1 = SessionState()
        state2 = SessionState()

        state1.session_id = "id-001"
        state2.session_id = "id-002"

        assert state1.session_id == "id-001"
        assert state2.session_id == "id-002"

    def test_independent_captured_prefill(self):
        state1 = SessionState()
        state2 = SessionState()

        state1.captured_prefill = "hello"
        state2.captured_prefill = "world"

        assert state1.captured_prefill == "hello"
        assert state2.captured_prefill == "world"
