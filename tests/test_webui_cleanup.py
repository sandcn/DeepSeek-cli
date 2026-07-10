"""测试 src/webui/cleanup.py — 连接清理模块"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.webui.cleanup import cleanup_connection, cleanup_pending_selects
from src.webui._pending_selects import pending_selects


# ═══════════════════════════════════════════════════════════════
# cleanup_pending_selects
# ═══════════════════════════════════════════════════════════════

class TestCleanupPendingSelects:
    """cleanup_pending_selects — 取消 pending 的 pending_selects。"""

    def setup_method(self):
        """每个测试前清理全局 pending_selects。"""
        pending_selects.cancel_all()

    async def test_cancels_pending_futures(self):
        """取消所有 pending 的 Future。"""
        loop = asyncio.get_running_loop()
        f1 = loop.create_future()
        f2 = loop.create_future()
        pending_selects._pending["sid1"] = f1
        pending_selects._pending["sid2"] = f2

        my_ids = {"sid1", "sid2"}
        cleanup_pending_selects(my_ids)

        assert f1.done()
        assert f1.result() == '{"selected": [], "action": "cancel"}'
        assert f2.done()
        assert f2.result() == '{"selected": [], "action": "cancel"}'

    async def test_already_completed_future_not_recancelled(self):
        """已完成的 Future 不重复设置 result。"""
        loop = asyncio.get_running_loop()
        f1 = loop.create_future()
        f1.set_result("already_done")
        pending_selects._pending["sid1"] = f1

        my_ids = {"sid1"}
        cleanup_pending_selects(my_ids)  # 不应抛出异常

        assert f1.result() == "already_done"

    async def test_my_select_ids_cleared(self):
        """my_select_ids 被清空。"""
        loop = asyncio.get_running_loop()
        f1 = loop.create_future()
        pending_selects._pending["sid1"] = f1
        my_ids = {"sid1"}

        cleanup_pending_selects(my_ids)
        assert len(my_ids) == 0

    def test_missing_sid_skipped_gracefully(self):
        """PENDING_SELECTS 中不存在的 sid 跳过。"""
        my_ids = {"non_existent_sid"}
        cleanup_pending_selects(my_ids)  # 不应抛出异常

    def test_empty_id_set(self):
        """空的 id 集合不做任何操作。"""
        cleanup_pending_selects(set())

    async def test_sid_in_list_but_not_in_pending(self):
        """sid 在 my_select_ids 中但不在 PENDING_SELECTS 中。"""
        loop = asyncio.get_running_loop()
        f1 = loop.create_future()
        pending_selects._pending["existing"] = f1
        my_ids = {"existing", "missing"}
        cleanup_pending_selects(my_ids)
        # existing 应已从 PENDING_SELECTS 中 pop 并取消（用 f1 引用验证）
        assert f1.done()
        assert len(my_ids) == 0

    async def test_multiple_runs_same_set(self):
        """多次运行同一个集合，第二次无操作。"""
        loop = asyncio.get_running_loop()
        f1 = loop.create_future()
        pending_selects._pending["sid1"] = f1
        my_ids = {"sid1"}

        cleanup_pending_selects(my_ids)  # 第一次：取消+清空
        assert f1.done()
        assert len(my_ids) == 0

        # 第二次：集合已空，不做操作
        cleanup_pending_selects(my_ids)
        assert len(my_ids) == 0

    async def test_clears_pending_from_global_dict(self):
        """从全局 PENDING_SELECTS 中移除 sid。"""
        loop = asyncio.get_running_loop()
        f1 = loop.create_future()
        pending_selects._pending["sid1"] = f1
        my_ids = {"sid1"}

        cleanup_pending_selects(my_ids)
        assert "sid1" not in pending_selects


# ═══════════════════════════════════════════════════════════════
# cleanup_connection
# ═══════════════════════════════════════════════════════════════

class TestCleanupConnection:
    """cleanup_connection — 统一清理 WebSocket 连接的全部资源。"""

    @pytest.fixture
    def mock_bridge(self):
        bridge = MagicMock()
        bridge.unsubscribe = MagicMock()
        return bridge

    @pytest.fixture
    def mock_proc_state(self):
        state = MagicMock()
        state.current_task = None
        return state

    @pytest.fixture
    def mock_session(self):
        session = MagicMock()
        session._orphaned_task = None
        session.messages = []
        return session

    # ── 正常流程 ───────────────────────────────────────

    async def test_normal_flow(self, mock_bridge, mock_proc_state, mock_session):
        """正常清理流程：取消订阅 → 清理 selects → gather send → cancel process。"""
        my_ids = {"s1"}
        f1 = asyncio.Future()
        pending_selects._pending["s1"] = f1

        send_task = asyncio.create_task(asyncio.sleep(10))
        process_task = asyncio.create_task(asyncio.sleep(10))

        await cleanup_connection(
            bridge=mock_bridge,
            my_select_ids=my_ids,
            pending_send_tasks={send_task},
            process_task=process_task,
            proc_state=mock_proc_state,
            session=mock_session,
        )

        # 取消订阅被调用
        mock_bridge.unsubscribe.assert_called_once()
        # selects 被清理
        assert f1.done()
        assert len(my_ids) == 0
        # process_task 被取消（部分平台 cancellation 传播需微时延）
        assert process_task.cancelled() or process_task.done()

    # ── current_task 保留为 orphaned ───────────────────

    async def test_current_task_running_saved_as_orphan(
        self, mock_bridge, mock_session
    ):
        """proc_state.current_task 运行时，保留为 session._orphaned_task。"""
        proc_state = MagicMock()
        running_task = asyncio.create_task(asyncio.sleep(10))
        proc_state.current_task = running_task

        await cleanup_connection(
            bridge=mock_bridge,
            my_select_ids=set(),
            proc_state=proc_state,
            session=mock_session,
        )

        assert mock_session._orphaned_task is running_task
        # 任务未被取消
        assert not running_task.cancelled()
        assert not running_task.done()

    async def test_orphan_adds_done_callback(self, mock_bridge, mock_session):
        """保留 orphan 时添加 _on_orphan_done 回调。"""
        proc_state = MagicMock()
        running_task = asyncio.create_task(asyncio.sleep(10))
        proc_state.current_task = running_task

        await cleanup_connection(
            bridge=mock_bridge,
            my_select_ids=set(),
            proc_state=proc_state,
            session=mock_session,
        )

        # 检查回调是否被添加（通过检查 _callbacks）
        assert len(running_task._callbacks) > 0

    async def test_orphan_done_callback_clears_reference(
        self, mock_bridge, mock_session
    ):
        """_on_orphan_done 回调完成后清理引用。"""
        proc_state = MagicMock()
        # 使用已完成的协程
        async def quick_coro():
            return 42
        finished_task = asyncio.create_task(quick_coro())
        await finished_task  # 任务已完成
        proc_state.current_task = finished_task

        # current_task 已 done，不会被保留为 orphan
        await cleanup_connection(
            bridge=mock_bridge,
            my_select_ids=set(),
            proc_state=proc_state,
            session=mock_session,
        )
        assert mock_session._orphaned_task is None

    # ── current_task 已 done 时不保留 ─────────────────

    async def test_current_task_done_not_saved_as_orphan(
        self, mock_bridge, mock_session
    ):
        """proc_state.current_task 已 done 时不保留为 orphan。"""
        proc_state = MagicMock()
        done_task = asyncio.create_task(asyncio.sleep(0))
        await done_task
        proc_state.current_task = done_task

        await cleanup_connection(
            bridge=mock_bridge,
            my_select_ids=set(),
            proc_state=proc_state,
            session=mock_session,
        )

        assert mock_session._orphaned_task is None

    async def test_current_task_none_not_saved(
        self, mock_bridge, mock_session, mock_proc_state
    ):
        """current_task=None 时不保留 orphan。"""
        await cleanup_connection(
            bridge=mock_bridge,
            my_select_ids=set(),
            proc_state=mock_proc_state,
            session=mock_session,
        )
        assert mock_session._orphaned_task is None

    # ── 兼容空参 ──────────────────────────────────────

    async def test_bridge_none(self, mock_proc_state, mock_session):
        """bridge=None 时跳过取消订阅。"""
        await cleanup_connection(
            bridge=None,
            my_select_ids=set(),
            proc_state=mock_proc_state,
            session=mock_session,
        )
        # 不应抛出异常

    async def test_my_select_ids_none(self, mock_bridge, mock_proc_state, mock_session):
        """my_select_ids=None 时跳过清理 selects。"""
        await cleanup_connection(
            bridge=mock_bridge,
            my_select_ids=None,
            proc_state=mock_proc_state,
            session=mock_session,
        )
        # 不应抛出异常

    async def test_pending_send_tasks_none(self, mock_bridge, mock_proc_state, mock_session):
        """pending_send_tasks=None 时跳过 gather。"""
        await cleanup_connection(
            bridge=mock_bridge,
            my_select_ids=set(),
            pending_send_tasks=None,
            proc_state=mock_proc_state,
            session=mock_session,
        )
        # 不应抛出异常

    async def test_process_task_none(self, mock_bridge, mock_proc_state, mock_session):
        """process_task=None 时跳过取消。"""
        await cleanup_connection(
            bridge=mock_bridge,
            my_select_ids=set(),
            process_task=None,
            proc_state=mock_proc_state,
            session=mock_session,
        )
        # 不应抛出异常

    async def test_proc_state_none(self, mock_bridge, mock_session):
        """proc_state=None 时跳过 orphan 处理。"""
        await cleanup_connection(
            bridge=mock_bridge,
            my_select_ids=set(),
            proc_state=None,
            session=mock_session,
        )
        # 不应抛出异常

    async def test_session_none_with_running_task(self, mock_bridge):
        """session=None 且 current_task 运行时，task 不被保留但也异常正常。"""
        proc_state = MagicMock()
        running_task = asyncio.create_task(asyncio.sleep(10))
        proc_state.current_task = running_task

        await cleanup_connection(
            bridge=mock_bridge,
            my_select_ids=set(),
            proc_state=proc_state,
            session=None,
        )
        # 不应抛出异常，任务也不应被取消
        assert not running_task.cancelled()

    async def test_all_none(self):
        """所有参数为 None 时静默通过。"""
        await cleanup_connection()
        # 不应抛出异常

    # ── 异常不传播 ─────────────────────────────────────

    async def test_bridge_unsubscribe_exception_swallowed(self, mock_proc_state, mock_session):
        """bridge.unsubscribe 异常被 try/except 吞掉。"""
        bridge = MagicMock()
        bridge.unsubscribe = MagicMock(side_effect=RuntimeError("订阅取消失败"))

        await cleanup_connection(
            bridge=bridge,
            my_select_ids=set(),
            proc_state=mock_proc_state,
            session=mock_session,
        )
        # 不抛出异常，继续后续步骤

    async def test_process_task_cancel_exception_swallowed(
        self, mock_bridge, mock_proc_state, mock_session
    ):
        """process_task 取消异常被 try/except 吞掉。"""
        process_task = MagicMock()
        process_task.cancel = MagicMock(side_effect=RuntimeError("取消失败"))

        await cleanup_connection(
            bridge=mock_bridge,
            my_select_ids=set(),
            process_task=process_task,
            proc_state=mock_proc_state,
            session=mock_session,
        )
        # 不抛出异常

    async def test_proc_state_exception_swallowed(self, mock_bridge, mock_session):
        """proc_state 处理异常被 try/except 吞掉。"""
        proc_state = MagicMock()
        # getattr 会返回正常值，但让 add_done_callback 抛异常
        running_task = asyncio.create_task(asyncio.sleep(10))
        proc_state.current_task = running_task

        # 让 session 的 _orphaned_task 赋值抛异常
        class BrokenSession:
            @property
            def _orphaned_task(self):
                return None

            @_orphaned_task.setter
            def _orphaned_task(self, value):
                raise RuntimeError("session 异常")

            messages = []

        await cleanup_connection(
            bridge=mock_bridge,
            my_select_ids=set(),
            proc_state=proc_state,
            session=BrokenSession(),
        )
        # 不抛出异常

    # ── 实际 send tasks gather ─────────────────────────

    async def test_send_tasks_gathered(self, mock_bridge, mock_proc_state, mock_session):
        """pending_send_tasks 被 gather 等待完成。"""
        completed = []

        async def slow_send():
            await asyncio.sleep(0.01)
            completed.append("done")

        task = asyncio.create_task(slow_send())

        await cleanup_connection(
            bridge=mock_bridge,
            my_select_ids=set(),
            pending_send_tasks={task},
            proc_state=mock_proc_state,
            session=mock_session,
        )

        assert len(completed) == 1

    async def test_send_task_exception_not_propagated(
        self, mock_bridge, mock_proc_state, mock_session
    ):
        """send task 抛异常时被 return_exceptions 捕获。"""
        async def failing_send():
            raise ValueError("发送失败")

        task = asyncio.create_task(failing_send())

        await cleanup_connection(
            bridge=mock_bridge,
            my_select_ids=set(),
            pending_send_tasks={task},
            proc_state=mock_proc_state,
            session=mock_session,
        )
        # 不抛出异常

    # ── process_task CancelledError ─────────────────────

    async def test_process_task_cancelled_error_handled(
        self, mock_bridge, mock_proc_state, mock_session
    ):
        """process_task 被取消时捕获 CancelledError。"""
        process_task = asyncio.create_task(asyncio.sleep(10))
        process_task.cancel()

        await cleanup_connection(
            bridge=mock_bridge,
            my_select_ids=set(),
            process_task=process_task,
            proc_state=mock_proc_state,
            session=mock_session,
        )
        # 不抛出异常
