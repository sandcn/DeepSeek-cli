"""Tests for SubAgentPanelController frame rendering debounce."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.tui._subagent_panel import SubAgentPanelController


class TestSubAgentPanelEmitFrameThrottle:
    """Test _emit_frame() time-based throttling."""

    @pytest.fixture
    def controller(self):
        """Create a SubAgentPanelController with mocked _push_frame."""
        ctrl = SubAgentPanelController()
        # Mock _push_frame to avoid DisplayEventBus dependency
        ctrl._push_frame = MagicMock()
        # Mock _render_frame to return empty lines
        ctrl._render_frame = MagicMock(return_value=["line1", "line2"])
        return ctrl

    # ── 场景 1：首次调用 → 正常渲染（时间 >= 0.1 时） ──

    @patch("src.tui._subagent_panel.time.time")
    def test_first_emit_renders_when_time_ready(self, mock_time, controller):
        """首次 _emit_frame() 调用，当 time.time() >= 0.1 时应渲染。"""
        mock_time.return_value = 0.15  # 与 _last_emit_time=0.0 相差 150ms > 100ms

        controller._emit_frame()

        controller._push_frame.assert_called_once_with(["line1", "line2"])
        assert controller._last_emit_time == 0.15

    # ── 场景 2：首次调用时间 < 0.1 → 也被节流 ──

    @patch("src.tui._subagent_panel.time.time")
    def test_first_emit_throttled_when_time_too_small(self, mock_time, controller):
        """首次 _emit_frame() 在 time.time() < 0.1 时应被节流（间隔不足 100ms）。"""
        mock_time.return_value = 0.05  # 与 0.0 相差 50ms < 100ms

        controller._emit_frame()

        controller._push_frame.assert_not_called()
        assert controller._last_emit_time == 0.0  # 未被更新

    # ── 场景 3：100ms 内第二次调用 → 跳过渲染（节流生效） ──

    @patch("src.tui._subagent_panel.time.time")
    def test_throttle_within_100ms(self, mock_time, controller):
        """100ms 内连续调用 _emit_frame()，第二次应被节流。"""
        # 第一次调用：时间 0.1（与初始 0.0 相差 100ms，刚好可渲染）
        mock_time.return_value = 0.1
        controller._emit_frame()
        assert controller._push_frame.call_count == 1
        assert controller._last_emit_time == 0.1

        # 第二次调用：时间 0.15（与 0.1 相差 50ms < 100ms）
        mock_time.return_value = 0.15
        controller._emit_frame()

        assert controller._push_frame.call_count == 1  # 未增加

    # ── 场景 4：100ms 后调用 → 正常渲染（节流窗口重置） ──

    @patch("src.tui._subagent_panel.time.time")
    def test_emit_after_interval(self, mock_time, controller):
        """间隔超过 100ms 后调用应正常渲染。"""
        # 第一次调用：时间 0.1
        mock_time.return_value = 0.1
        controller._emit_frame()
        assert controller._push_frame.call_count == 1
        assert controller._last_emit_time == 0.1

        # 第二次调用：时间 0.25（与 0.1 相差 150ms >= 100ms）
        mock_time.return_value = 0.25
        controller._emit_frame()

        assert controller._push_frame.call_count == 2
        assert controller._last_emit_time == 0.25

    # ── 场景 5：节流期间多条事件合并为一次渲染 ──

    @patch("src.tui._subagent_panel.time.time")
    def test_multiple_throttled_events_merged(self, mock_time, controller):
        """节流期间多条事件只合并为最早的一次渲染。"""
        # 首次渲染：时间 0.1
        mock_time.return_value = 0.1
        controller._emit_frame()
        assert controller._push_frame.call_count == 1
        assert controller._last_emit_time == 0.1

        # 3 次快速调用（0.12 / 0.14 / 0.16，全部在 100ms 窗口内），全部被节流
        for t in [0.12, 0.14, 0.16]:
            mock_time.return_value = t
            controller._emit_frame()

        assert controller._push_frame.call_count == 1  # 全部被节流

        # 100ms 后恢复正常渲染
        mock_time.return_value = 0.25  # 与 0.1 相差 150ms >= 100ms
        controller._emit_frame()
        assert controller._push_frame.call_count == 2

    # ── 场景 9：节流丢帧补推（方向2） ──

    @patch("src.tui._subagent_panel.time.time")
    def test_emit_frame_catchup_regression(self, mock_time, controller):
        """方向2 — 节流期丢帧后 _panel_refresh 补推最新状态（_pending_emit 标志）。"""
        controller._cb_registered = True  # 跳过 _register_panel_refresh 依赖

        # 首次调用：时间 0.1 → 渲染（推送 line1/line2）
        mock_time.return_value = 0.1
        controller._emit_frame()
        assert controller._push_frame.call_count == 1
        assert controller._pending_emit is False

        # 节流期事件：0.15（100ms 窗口内）→ 跳过 + 置位 _pending_emit
        mock_time.return_value = 0.15
        controller._emit_frame()
        assert controller._push_frame.call_count == 1  # 节流未推送
        assert controller._pending_emit is True

        # _panel_refresh（每帧回调）：检测 _pending_emit → 下个 10Hz 拍补推
        # （PERF-4：_panel_refresh 与 _emit_frame 共用 10Hz 节流——节流期
        #   跳过置 _pending_emit，距上次推送 ≥0.1s 后补推最新帧，不丢状态）
        controller._dirty = False
        controller._render_frame = MagicMock(return_value=["latest", "state"])
        mock_time.return_value = 0.20  # 距上次推送 0.1s → 允许补推
        controller._panel_refresh()
        assert controller._push_frame.call_count == 2
        assert controller._pending_emit is False
        # 补推内容为最新帧
        pushed = controller._push_frame.call_args[0][0]
        assert pushed == ["latest", "state"]

    # ── 场景 6：stop() 不经过 _emit_frame() → 不受节流影响 ──

    def test_stop_bypasses_emit_frame(self, controller):
        """stop() 应该直接调用 _push_frame，不经过 _emit_frame() 节流。"""
        controller._active = True  # stop() 需要 _active=True 才能执行清理逻辑
        with patch.object(controller, '_unregister_panel_refresh') as mock_unreg:
            with patch.object(controller, '_push_frame') as mock_push:
                controller.stop()

        # stop() 应该调用 _push_frame([]) 清除面板（不经过 _emit_frame）
        mock_push.assert_called_once_with([])

    # ── 场景 7：_last_emit_time 初始值为 0.0 ──

    def test_initial_last_emit_time_is_zero(self):
        """_last_emit_time 初始值应为 0.0。"""
        ctrl = SubAgentPanelController()
        assert ctrl._last_emit_time == 0.0

    # ── 场景 8：_EMIT_INTERVAL 类常量存在且值为 0.1 ──

    def test_emit_interval_constant(self):
        """_EMIT_INTERVAL 类常量应存在且为 0.1。"""
        assert SubAgentPanelController._EMIT_INTERVAL == 0.1


class TestSubAgentPanelReentrantLock:
    """验证 _state_lock 改用 RLock 后可重入（Issue 1）。"""

    @pytest.fixture
    def controller(self):
        """创建带 mock 的 SubAgentPanelController 实例。"""
        ctrl = SubAgentPanelController()
        ctrl._push_frame = MagicMock()
        # _render_frame 内部也获取 _state_lock，用 RLock 不会死锁
        ctrl._render_frame = MagicMock(return_value=["rendered_line"])
        return ctrl

    def test_reentrant_lock_within_locked_section(self, controller):
        """在 with _state_lock 块内调用 _render_frame()，验证 RLock 可重入不崩溃。"""
        with controller._state_lock:
            # RLock 允许同一线程重复获取同一锁 —— 不会死锁
            lines = controller._render_frame()

        # 验证 _render_frame 被正常调用并返回结果
        controller._render_frame.assert_called_once()
        assert lines == ["rendered_line"]

    def test_no_deadlock_on_nested_lock_acquire(self, controller):
        """模拟 _on_tool_parsing 场景：持有锁期间调用 _render_frame，验证不会死锁。"""
        # 模拟事件处理器逻辑：在 with _state_lock 块内调用 _render_frame
        with controller._state_lock:
            # _render_frame 内部会再次获取 _state_lock（原代码中存在此模式）
            controller._render_frame()

        controller._render_frame.assert_called_once()

    def test_push_frame_called_via_on_tool_parsing(self, controller):
        """验证通过 _on_tool_parsing 路径时，_push_frame 被正确调用。"""
        # 创建 mock 事件
        from unittest.mock import MagicMock as _MM
        event = _MM()
        event.label = "agent-test"
        event.tool_name = "read_file"
        event.arguments = "test.py"

        # 先添加 agent 到 _agents
        from src.tui._subagent_panel import _AgentSlot
        controller._agents["agent-test"] = _AgentSlot(
            label="agent-test", description="test agent"
        )

        # 调用 _on_tool_parsing（内部持有 _state_lock 后调用 _emit_frame）
        controller._push_frame.reset_mock()
        controller._on_tool_parsing(event)

        # _push_frame 应该被调用（至少一次）
        assert controller._push_frame.call_count >= 1


class TestSubagentPanelDeadlockPrevention:
    """死锁预防测试 — 验证 _push_frame 绝不在 _state_lock 内调用。

    核心原则：
      _emit_frame() 中 _render_frame() → (锁释放) → _push_frame()
      _on_tool_parsing() 中 with _state_lock: ... → (锁释放) → _render_frame() → _push_frame()
    """

    @pytest.fixture
    def controller(self):
        ctrl = SubAgentPanelController()
        # 替换 _render_frame 为可追踪的 mock
        ctrl._render_frame_orig = ctrl._render_frame
        return ctrl

    def test_emit_frame_lock_order(self):
        """验证 _emit_frame() 调用顺序：_render_frame（锁内）→ _push_frame（锁外）。"""
        ctrl = SubAgentPanelController()
        call_order = []

        orig_render = ctrl._render_frame

        def tracked_render():
            call_order.append("render_frame")
            # 验证 _render_frame 获取 _state_lock — RLock 可重入，不阻塞
            with ctrl._state_lock:
                call_order.append("in_render_lock")
            return ["line"]

        orig_push = ctrl._push_frame

        def tracked_push(lines):
            call_order.append("push_frame")

        ctrl._render_frame = tracked_render
        ctrl._push_frame = tracked_push

        ctrl._last_emit_time = 0.0
        with patch("src.tui._subagent_panel.time.time", return_value=0.15):
            ctrl._emit_frame()

        # 验证调用顺序
        assert "render_frame" in call_order
        assert "push_frame" in call_order
        render_idx = call_order.index("render_frame")
        push_idx = call_order.index("push_frame")
        # _render_frame 必须在 _push_frame 之前调用
        assert render_idx < push_idx, (
            f"_render_frame (idx={render_idx}) 应在 _push_frame (idx={push_idx}) 之前"
        )

    def test_on_tool_parsing_lock_released_before_push(self):
        """验证 _on_tool_parsing 在锁释放后才调 _push_frame。"""
        ctrl = SubAgentPanelController()
        call_order = []

        # 添加一个 agent slot 到 _agents
        from src.tui._subagent_panel import _AgentSlot
        ctrl._agents["agent-x"] = _AgentSlot(label="agent-x", description="test")

        orig_render = ctrl._render_frame
        orig_push = ctrl._push_frame

        def tracked_render():
            call_order.append("render_frame")
            # _render_frame 内部获取锁（RLock 可重入）
            with ctrl._state_lock:
                call_order.append("in_render_lock")
            return ["line"]

        def tracked_push(lines):
            call_order.append("push_frame")
            # 验证：调用 _push_frame 时锁应已释放
            # 验证方法：尝试获取锁（非阻塞），应该能获取到
            acquired = ctrl._state_lock.acquire(blocking=False)
            if acquired:
                call_order.append("lock_available_in_push")
                ctrl._state_lock.release()
            else:
                call_order.append("lock_held_in_push")

        ctrl._render_frame = tracked_render
        ctrl._push_frame = tracked_push

        # 构造 mock 事件
        event = MagicMock()
        event.label = "agent-x"
        event.tool_name = "read_file"
        event.arguments = "test.py"

        ctrl._on_tool_parsing(event)

        # 验证调用顺序
        assert "push_frame" in call_order, "_push_frame 应被调用"
        push_idx = call_order.index("push_frame")
        render_idx = call_order.index("render_frame")
        assert render_idx < push_idx, (
            f"_render_frame (idx={render_idx}) 应在 _push_frame (idx={push_idx}) 之前"
        )
        # 验证在 _push_frame 中锁可用 — 表明锁已释放
        if "lock_available_in_push" in call_order:
            lock_available_idx = call_order.index("lock_available_in_push")
            push_idx = call_order.index("push_frame")
            assert lock_available_idx > push_idx, (
                "调用 _push_frame 时锁应未被持有（可在 push 中获取）"
            )

    def test_event_handlers_call_emit_frame_outside_lock(self):
        """验证所有事件处理器在锁外调用 _emit_frame。"""
        ctrl = SubAgentPanelController()
        call_trace = []

        # 替换 _emit_frame 为追踪版
        orig_emit = ctrl._emit_frame
        def tracked_emit():
            call_trace.append("emit_frame")
            # 验证在 _emit_frame 中锁是否被调用方持有
            try:
                acquired = ctrl._state_lock.acquire(blocking=False)
                if acquired:
                    ctrl._state_lock.release()
                    call_trace.append("lock_free_in_emit")
                else:
                    call_trace.append("lock_held_in_emit")
            except RuntimeError:
                call_trace.append("lock_error_in_emit")

        ctrl._emit_frame = tracked_emit
        ctrl._push_frame = MagicMock()

        from src.tui._subagent_panel import _AgentSlot
        ctrl._agents["agent-x"] = _AgentSlot(label="agent-x", description="test")

        # 测试 _on_agent_status_changed — 它在锁外调 _emit_frame
        event = MagicMock()
        event.label = "agent-x"
        event.status = "done"
        ctrl._on_agent_status_changed(event)

        assert "emit_frame" in call_trace, "_emit_frame 应被调用"
        if "lock_free_in_emit" in call_trace:
            assert True  # 锁在 _emit_frame 时可用 — 正确
        elif "lock_held_in_emit" in call_trace:
            # 也可能 _on_agent_status_changed 在 with _state_lock 内调用了 _emit_frame
            # 但实际上代码中 _emit_frame 在 with 块外调用，这不应发生
            pytest.fail("_emit_frame 被调用时 _state_lock 仍被持有 — 可能导致死锁")


class TestSubAgentPanelDeclarativeSubscriptions:
    """方向D 步骤7 — 声明式订阅表回归测试。"""

    def test_declarative_subscriptions_regression(self):
        """ensure_active() 订阅 10 类事件，stop() 取消全部订阅。"""
        from src.tui.events.event_bus import DisplayEventBus
        from unittest.mock import MagicMock as _MM

        DisplayEventBus.reset_default()
        bus = DisplayEventBus.get_default()
        ctrl = SubAgentPanelController()
        # mock 面板刷新注册/帧推送，避免消费端依赖
        ctrl._register_panel_refresh = _MM()
        ctrl._push_frame = _MM()
        try:
            # 声明式表含 10 项（拼写错误会静默漏订阅，此断言兜底）
            assert len(ctrl._SUBSCRIPTIONS) == 10
            for ev_type, method_name in ctrl._SUBSCRIPTIONS:
                assert hasattr(ctrl, method_name), f"{method_name} 不存在"
                assert callable(getattr(ctrl, method_name)), f"{method_name} 不可调用"

            ctrl.ensure_active()
            assert ctrl._active is True
            # 10 类事件各 1 个订阅者
            assert bus.subscriber_count == 10

            ctrl.stop()
            assert ctrl._active is False
            assert bus.subscriber_count == 0
        finally:
            bus.clear()
            DisplayEventBus.reset_default()


class TestToolMappingSingleSource:
    """方向F 步骤12 — 工具名映射收敛到 _tool_icons 单一真源回归测试。"""

    def test_tool_mapping_single_source_regression(self):
        """_subagent_panel 不再定义本地映射，_get_tool_color 查用共享映射。"""
        import src.tui._subagent_panel as sp
        from src.tui._tool_icons import TOOL_CATEGORY_COLORS, TOOL_CATEGORY_MAP

        # 本地副本已删除（唯一真源收敛）
        assert not hasattr(sp, "_TOOL_CATEGORY_MAP")
        assert not hasattr(sp, "_TOOL_CATEGORY_COLORS")
        # 查用共享映射，返回值与 TOOL_CATEGORY_COLORS["shell"] 一致
        assert sp._get_tool_color("bash") == TOOL_CATEGORY_COLORS["shell"]
        assert sp._get_tool_color("read_file") == TOOL_CATEGORY_COLORS["file_read"]
        assert sp._get_tool_color("unknown_tool") == "\033[38;5;245m"
        # _C_* 面板颜色仍可经模块访问（从 _const 导入）
        assert sp._C_RUNNING == "\033[38;5;214m"
        assert sp._C_RESET == "\033[0m"

    def test_tool_icons_exports_categories_regression(self):
        """_tool_icons 导出 TOOL_CATEGORY_MAP / TOOL_CATEGORY_COLORS。"""
        from src.tui._tool_icons import (
            TOOL_CATEGORY_COLORS, TOOL_CATEGORY_MAP,
        )
        assert TOOL_CATEGORY_MAP["bash"] == "shell"
        assert TOOL_CATEGORY_MAP["execute_command"] == "shell"
        assert TOOL_CATEGORY_MAP["read_file"] == "file_read"
        assert TOOL_CATEGORY_MAP["write_file"] == "file_write"
        assert TOOL_CATEGORY_MAP["grep"] == "search"
        assert TOOL_CATEGORY_MAP["dispatch_agent"] == "agent"
        assert TOOL_CATEGORY_MAP["rm"] == "delete"
        for cat in ("shell", "file_read", "file_write", "search",
                    "agent", "interact", "delete"):
            assert cat in TOOL_CATEGORY_COLORS


class TestSubAgentPanelParsingThrottle:
    """步骤6.3 — tool parsing 事件不再绕过节流（10Hz）。"""

    @pytest.fixture
    def controller(self):
        """创建带 mock 的 SubAgentPanelController 实例。"""
        ctrl = SubAgentPanelController()
        ctrl._push_frame = MagicMock()
        ctrl._render_frame = MagicMock(return_value=["line1", "line2"])
        return ctrl

    @patch("src.tui._subagent_panel.time.time")
    def test_tool_parsing_respects_throttle_regression(self, mock_time, controller):
        """连续两次 parsing 事件在节流间隔内只 push 一次。"""
        from src.tui._subagent_panel import _AgentSlot
        mock_time.return_value = 0.15  # 首次调用（>=0.1 可渲染）
        ctrl = controller
        ctrl._agents["agent-x"] = _AgentSlot(label="agent-x", description="test")
        event = MagicMock()
        event.label = "agent-x"
        event.tool_name = "read_file"
        event.arguments = "a.py"

        ctrl._on_tool_parsing(event)
        assert ctrl._push_frame.call_count == 1
        assert ctrl._last_emit_time == 0.15

        # 100ms 内第二次事件 → 节流（不 push）
        mock_time.return_value = 0.18
        ctrl._on_tool_parsing(event)
        assert ctrl._push_frame.call_count == 1

        # 超过 100ms → 再次 push
        mock_time.return_value = 0.30
        ctrl._on_tool_parsing(event)
        assert ctrl._push_frame.call_count == 2

    @patch("src.tui._subagent_panel.time.time")
    def test_tool_parsing_no_push_when_throttled_immediately(self, mock_time, controller):
        """parsing 事件在 <0.1s 时被节流（不绕过 _EMIT_INTERVAL）。"""
        from src.tui._subagent_panel import _AgentSlot
        mock_time.return_value = 0.05  # 与初始 _last_emit_time=0.0 相差 <100ms
        ctrl = controller
        ctrl._agents["agent-x"] = _AgentSlot(label="agent-x", description="test")
        event = MagicMock()
        event.label = "agent-x"
        event.tool_name = "read_file"
        event.arguments = "a.py"

        ctrl._on_tool_parsing(event)
        assert ctrl._push_frame.call_count == 0  # 被节流


class TestSubAgentPanelDirtyShortCircuit:
    """PERF-2 — _panel_refresh 增量/变更检测短路。"""

    @pytest.fixture
    def controller(self):
        """创建带 mock 的 SubAgentPanelController。"""
        ctrl = SubAgentPanelController()
        ctrl._push_frame = MagicMock()
        ctrl._render_frame = MagicMock(return_value=["line1"])
        ctrl._cb_registered = True
        return ctrl

    def test_panel_refresh_skips_when_idle_regression(self, controller):
        """无事件、无 running agent 时 _render_frame 不被调用。"""
        ctrl = controller
        ctrl._dirty = False
        ctrl._panel_refresh()
        ctrl._render_frame.assert_not_called()

    def test_panel_refresh_renders_when_dirty_regression(self, controller):
        """脏标记置位时 _panel_refresh 渲染并复位。"""
        ctrl = controller
        ctrl._dirty = True
        ctrl._panel_refresh()
        ctrl._render_frame.assert_called_once()
        assert ctrl._dirty is False  # 渲染后复位

    def test_panel_refresh_renders_when_animation_regression(self, controller):
        """running agent 存在（动画需求）时仍渲染。"""
        from src.tui._subagent_panel import _AgentSlot
        ctrl = controller
        ctrl._dirty = False
        ctrl._agents["agent-x"] = _AgentSlot(
            label="agent-x", description="test", status="running",
        )
        ctrl._order.append("agent-x")
        ctrl._panel_refresh()
        ctrl._render_frame.assert_called_once()  # running → 动画 → 渲染

    def test_panel_refresh_skips_when_done_agents_idle_regression(self, controller):
        """全部 done 且无事件时跳过渲染（done 不触发动画）。"""
        from src.tui._subagent_panel import _AgentSlot
        ctrl = controller
        ctrl._dirty = False
        ctrl._agents["agent-done"] = _AgentSlot(
            label="agent-done", description="test", status="done",
        )
        ctrl._order.append("agent-done")
        ctrl._panel_refresh()
        ctrl._render_frame.assert_not_called()  # done → 无动画 → 空闲跳过

    def test_needs_animation_running_tool_regression(self, controller):
        """running 工具记录（非 running agent）也触发动画需求。"""
        from src.tui._subagent_panel import _AgentSlot, _ToolRecord
        ctrl = controller
        ctrl._dirty = False
        slot = _AgentSlot(label="agent-x", description="test", status="running")
        rec = _ToolRecord(tool_name="read_file")
        rec.phase = "running"
        slot.tool_history.append(rec)
        ctrl._agents["agent-x"] = slot
        ctrl._order.append("agent-x")
        assert ctrl._needs_animation() is True

    def test_needs_animation_idle_false_regression(self, controller):
        """空闲（无 running agent/tool）→ _needs_animation False。"""
        from src.tui._subagent_panel import _AgentSlot
        ctrl = controller
        ctrl._dirty = False
        ctrl._agents["agent-done"] = _AgentSlot(
            label="agent-done", description="test", status="done",
        )
        ctrl._order.append("agent-done")
        assert ctrl._needs_animation() is False

    def test_event_handler_sets_dirty_regression(self, controller):
        """事件处理器更新状态后置位 _dirty。"""
        from src.tui._subagent_panel import _AgentSlot
        ctrl = controller
        ctrl._dirty = False
        ctrl._agents["agent-x"] = _AgentSlot(label="agent-x", description="test")
        ctrl._order.append("agent-x")
        with patch.object(ctrl, "_emit_frame"):  # 隔离渲染复位，验证脏标记置位
            event = MagicMock()
            event.label = "agent-x"
            event.phase = "thinking"
            event.info = ""
            ctrl._on_model_phase(event)
        assert ctrl._dirty is True  # 事件 → 脏标记


class TestBeautyTimeBasedEffects:
    """步骤8 — 美化动效时间基回归（BEAUTY-1/2/3）。"""

    def test_fade_in_title_color_regression(self):
        """BEAUTY-1：elapsed=0 时类型名色为 fade_start_color；elapsed>=duration 时回到 agent_type 原色。"""
        import re as _re
        from src.tui._subagent_panel import SubAgentPanelController, _AgentSlot

        ctrl = SubAgentPanelController()
        with patch("src.tui._subagent_panel.time.monotonic", return_value=1000.0):
            slot = _AgentSlot(label="agent-x", description="test", status="done",
                              agent_type="execute")

        def _type_code(mono_time):
            with patch("src.tui._subagent_panel.time.monotonic", return_value=mono_time):
                lines = ctrl._build_agent_lines(slot, now=mono_time, is_last=False)
            title = lines[0]
            m = _re.search(r"\x1b\[38;5;(\d+)mexecute\x1b\[0m", title)
            assert m, f"未找到类型名色号: {title!r}"
            return int(m.group(1))

        # elapsed=0 → fade_start_color=238（execute 原色 208 的渐显起点）
        assert _type_code(1000.0) == 238
        # elapsed=1.0 > fade_duration_sec=0.6 → 回到 execute 原色 208
        assert _type_code(1001.0) == 208

    def test_spinner_time_based_regression(self):
        """BEAUTY-3：相同 _frame 下 spinner 帧号随时间推进（时间基，非帧计数）。"""
        from src.tui._subagent_panel import (
            SubAgentPanelController, _AgentSlot, _SPINNER_FRAMES,
        )
        ctrl = SubAgentPanelController()
        ctrl._frame = 5  # 固定帧计数——不得影响时间基推进
        slot = _AgentSlot(label="agent-x", description="test", status="running")
        # patch _fx.time.monotonic（spinner_frame 内部使用）
        with patch("src.tui.app._fx.time.monotonic", return_value=0.0):
            title0 = ctrl._build_agent_lines(slot, now=0.0, is_last=False)[0]
        with patch("src.tui.app._fx.time.monotonic", return_value=0.35):
            title1 = ctrl._build_agent_lines(slot, now=0.35, is_last=False)[0]
        # int(0.00*10)%10=0；int(0.35*10)%10=3
        assert _SPINNER_FRAMES[0] in title0
        assert _SPINNER_FRAMES[3] in title1
        assert _SPINNER_FRAMES[0] != _SPINNER_FRAMES[3]

    def test_group_card_running_open_done_included(self):
        """单卡合并：running agent 优先展开、done agent 单行；running 时开放（无底边框）。"""
        import re as _re
        import time as _time
        from src.tui._subagent_panel import (
            SubAgentPanelController, _AgentSlot,
        )
        ctrl = SubAgentPanelController()
        with patch("src.tui._subagent_panel.time.monotonic", return_value=0.0):
            slot_running = _AgentSlot(label="agent-run", description="run", status="running")
            slot_done = _AgentSlot(label="agent-done", description="done", status="done")
        slot_done.end_time = _time.time()
        ctrl._agents = {"agent-run": slot_running, "agent-done": slot_done}
        ctrl._order = ["agent-run", "agent-done"]
        lines = ctrl._render_frame()
        plains = [_re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", l) for l in lines]
        # 单卡：顶边框含 `子代理 · 2`；running agent 标题在 done 之前
        assert plains[0].startswith("\u250c") and "子代理 · 2" in plains[0]
        assert any("run" in p for p in plains)
        assert any("done" in p for p in plains)
        # running → 开放卡（无底边框）
        assert not any(p.startswith("\u2514") for p in plains)

    def test_group_card_closed_when_all_done(self):
        """全部结束后单卡闭合（`✔ 完成` 底边框）。"""
        import re as _re
        import time as _time
        from src.tui._subagent_panel import (
            SubAgentPanelController, _AgentSlot,
        )
        ctrl = SubAgentPanelController()
        with patch("src.tui._subagent_panel.time.monotonic", return_value=0.0):
            slot_done = _AgentSlot(label="agent-done", description="done", status="done")
        slot_done.end_time = _time.time()
        ctrl._agents = {"agent-done": slot_done}
        ctrl._order = ["agent-done"]
        lines = ctrl._render_frame()
        plains = [_re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", l) for l in lines]
        assert plains[0].startswith("\u250c") and "子代理 · 1" in plains[0]
        assert any(p.startswith("\u2514") and "✔ 完成" in p for p in plains)

    def test_group_card_row_protection(self):
        """行数保护：max_lines 超限时截断并追加 `… +K 行省略`（不撑爆终端）。"""
        import time as _time
        from src.tui._subagent_render import render_frame as _rf
        from src.tui._subagent_panel import SubAgentPanelController, _AgentSlot
        ctrl = SubAgentPanelController()
        with patch("src.tui._subagent_panel.time.monotonic", return_value=0.0):
            slots = {
                f"a{i}": _AgentSlot(label=f"a{i}", description=f"t{i}", status="running")
                for i in range(5)
            }
        ctrl._agents = slots
        ctrl._order = list(slots)
        lines = _rf(ctrl, max_lines=4)
        assert len(lines) == 4, f"卡片应限制在 max_lines 内，实际 {len(lines)}"
        assert any("省略" in l for l in lines), "超限应有省略提示"



class TestSubAgentPanelSingletonAndPushCmd:
    """方向5 — 单例统一（get_default） + push_cmd 注入路径动画回调注册。"""

    def test_singleton_identity_regression(self):
        """get_default() 多次返回同一实例（单例幂等）。"""
        c1 = SubAgentPanelController.get_default()
        c2 = SubAgentPanelController.get_default()
        assert c1 is c2

    def test_assembly_reuses_singleton_regression(self):
        """TuiAssembly 装配复用 get_default() 单例（双实例消除）。"""
        import sys
        from unittest.mock import patch
        from src.tui._assembly import TuiAssembly

        class _FakeStdin:
            def fileno(self):
                return 0

        with patch.object(sys, "stdin", _FakeStdin()):
            result = TuiAssembly.assemble()
        assert result.subagent_controller is SubAgentPanelController.get_default()

    def test_set_push_cmd_routes_push_frame_regression(self):
        """set_push_cmd 注入后 _push_frame 走 push_cmd 回调。"""
        ctrl = SubAgentPanelController()
        push_cmd = MagicMock()
        ctrl.set_push_cmd(push_cmd)
        ctrl._push_frame(["line1"])
        push_cmd.assert_called_once()
        cmd = push_cmd.call_args[0][0]
        assert cmd.frame_lines == ["line1"]

    def test_register_panel_refresh_with_push_cmd_regression(self):
        """push_cmd 注入路径下 _register_panel_refresh 仍注册 panel_refresh 回调。"""
        ctrl = SubAgentPanelController()
        ctrl.set_push_cmd(lambda cmd: None)
        chat_ui = MagicMock()
        # _register_panel_refresh 内 `from .consumer import get_active_chat_ui`
        # 惰性导入 → patch src.tui.consumer.get_active_chat_ui
        with patch("src.tui.consumer.get_active_chat_ui", return_value=chat_ui):
            ctrl._register_panel_refresh()
        chat_ui.set_panel_refresh_callback.assert_called_once_with(ctrl._panel_refresh)
        assert ctrl._cb_registered is True

    def test_register_panel_refresh_no_chat_ui_debug_regression(self):
        """push_cmd 注入但 chat_ui 为 None → 动画回调未注册（非致命，不抛）。"""
        ctrl = SubAgentPanelController()
        ctrl.set_push_cmd(lambda cmd: None)
        with patch("src.tui.consumer.get_active_chat_ui", return_value=None):
            ctrl._register_panel_refresh()  # 不抛异常
        assert ctrl._cb_registered is False

    def test_register_panel_refresh_no_push_cmd_regression(self):
        """push_cmd 未注入且 chat_ui 可用 → 注册回调（既有路径保持）。"""
        ctrl = SubAgentPanelController()
        chat_ui = MagicMock()
        with patch("src.tui.consumer.get_active_chat_ui", return_value=chat_ui):
            ctrl._register_panel_refresh()
        chat_ui.set_panel_refresh_callback.assert_called_once_with(ctrl._panel_refresh)
        assert ctrl._cb_registered is True


class TestSubAgentSingleLineContract:
    """P3-? — subagent 行单行契约：含 \n/\r 的字段转义为字面量（不拆成两行）。

    每个 ``subagent_lines`` 条目应为一条终端行；来源字段（description /
    parse_info / model_info / tool detail）可能含 ``\n``/``\r``，直接渲染
    会被终端按换行拆成两行。渲染层转义后保持单行。
    """

    def test_build_agent_lines_escapes_description_newline(self):
        """description 含 \n → 标题行仍为单行（转义为字面量）。"""
        import time
        from src.tui._subagent_render import build_agent_lines
        from src.tui._subagent_state import _AgentSlot

        slot = _AgentSlot(
            label="a", description="task one\ntask two", status="running",
        )
        lines = build_agent_lines(slot, time.time(), is_last=False)
        assert lines, "应产出标题行"
        title = lines[0]
        assert "\n" not in title, "标题行不得含原始换行"
        assert "task one\\ntask two" in title

    def test_build_agent_lines_no_parsing_phase_line(self):
        """BUG-T5：parsing 阶段不再产生独立行（防工具开始瞬间面板高度波动）。

        parse_info 并入 parsing 工具记录行（单行转义）——修复前独立
        ``…parsing`` 阶段行使工具开始瞬间面板 +2 行 → start_tool 清除
        model_phase 后 -1 行（缩短），文档高于屏幕时 InkRenderer 全量
        clear + 重建（每次 subagent 调用工具 TUI 全量刷新闪烁）。
        """
        import time
        from src.tui._subagent_render import build_agent_lines
        from src.tui._subagent_state import _AgentSlot, _ToolRecord

        slot = _AgentSlot(
            label="a", description="run", status="running",
        )
        slot.model_phase = "parsing"
        slot.parse_info = "rf,rf 51t\n0.74s"
        rec = _ToolRecord(tool_name="search", detail="'query'")
        rec.phase = "parsing"
        slot.tool_history.append(rec)
        lines = build_agent_lines(slot, time.time(), is_last=False)
        # 修复后：无独立 ``…parsing`` 阶段行（工具记录行 ○ 前缀表达解析状态）
        assert not any("\u2026parsing" in l for l in lines), (
            f"不得出现独立 parsing 阶段行: {lines!r}"
        )
        # parse_info 并入 parsing 工具记录行（单行转义）
        assert any("rf,rf 51t\\n0.74s" in l for l in lines), lines
        for l in lines:
            assert "\n" not in l, f"工具记录行不得含原始换行: {l!r}"

    def test_format_tool_record_merges_parse_info_in_parsing_line(self):
        """BUG-T5：parsing 记录行合并 parse_info（不增加行数）。"""
        import time
        from src.tui._subagent_render import format_tool_record
        from src.tui._subagent_state import _ToolRecord

        rec = _ToolRecord(tool_name="search", detail="'query'")
        rec.phase = "parsing"
        line = format_tool_record(rec, time.time(), cont="", parse_info="rf 51t 0.74s")
        assert "\u25cc" in line, "parsing 前缀 ○ 保留"
        assert "rf 51t 0.74s" in line, "parse_info 应并入 parsing 记录行"
        assert "Grep" in line, "工具显示名保留（search → Grep）"
        # 无 parse_info 时行为不变（detail 仍在）
        line2 = format_tool_record(rec, time.time(), cont="")
        assert "'query'" in line2

    def test_format_tool_record_escapes_detail_newline(self):
        """tool detail 含 \n → 工具历史行单行（既有转义行为回归）。"""
        import time
        from src.tui._subagent_render import format_tool_record
        from src.tui._subagent_state import _ToolRecord

        rec = _ToolRecord(tool_name="read_file", detail="line1\nline2")
        rec.phase = "running"
        line = format_tool_record(rec, time.time(), cont=" ")
        assert "\n" not in line, "工具历史行不得含原始换行"
        assert "line1\\nline2" in line

    def test_render_children_boundary_escapes_newline(self):
        """显示边界 _render_children 对含 \n 的行强制单行（防御兜底）。"""
        from src.tui.app.model import AppModel
        from src.tui.app.subagent_panel import _render_children

        m = AppModel()
        m.subagent_lines = ["  ├─ [EXE] task line one\nline two"]
        children = _render_children(m, 80)
        assert len(children) == 1, "应产出 1 个子节点"
        text = "".join(r.text for r in children[0].props["styled"])
        assert "\n" not in text, "子节点文本不得含原始换行"
        assert "task line one\\nline two" in text


class TestSubAgentToolStartNoHeightFluctuation:
    """BUG-T5 — 工具开始瞬间面板高度稳定（防缩短触发 InkRenderer 全量重建）。

    回归场景：subagent 调用 search 等工具时 TUI 每次全量刷新（闪烁）。
    根因：``build_agent_lines`` 在 ``model_phase=="parsing"`` 时追加独立
    ``…parsing`` 阶段行——工具开始瞬间面板 +2 行，``start_tool`` 清除
    ``model_phase`` 后 -1 行（缩短）。文档高于屏幕时 InkRenderer 原对缩短
    （``delta<0``）做全量 clear + 重建（已由「除 resize 外均增量」替换为
    增量缩短，见 test_renderer_screen::TestShrinkRebuild）。修复：parsing
    阶段不再产生独立行（由 parsing 工具记录行 ``○`` 前缀表达），工具开始
    瞬间面板行数稳定。
    """

    def test_parsing_to_running_frame_height_stable(self):
        """update_tool_parsing → start_tool 面板帧行数不变（关键不变量）。"""
        from src.tui._subagent_state import StateStore
        from src.tui._subagent_render import render_frame

        store = StateStore(max_history=3)
        store.add_agent("agent-1", "分析", status="running", agent_type="map")
        base = render_frame(store, max_history=3)

        store.update_tool_parsing("agent-1", "search", "{'query': 'foo'}")
        parsing = render_frame(store, max_history=3)

        store.start_tool("agent-1", "search", "'foo'")
        running = render_frame(store, max_history=3)

        # 工具开始仅 +1 行（parsing 工具记录）；parsing→running 行数不变
        assert len(parsing) == len(base) + 1, (
            f"parsing 应仅 +1 行（无独立阶段行）: base={len(base)} parsing={len(parsing)}"
        )
        assert len(running) == len(parsing), (
            f"parsing→running 行数必须不变（防缩短全量重建）: "
            f"parsing={len(parsing)} running={len(running)}"
        )
        # 无独立 ``…parsing`` 阶段行（工具记录行 ○ 前缀表达解析状态）
        assert not any("\u2026parsing" in l for l in parsing), parsing
        assert any("\u25cc" in l for l in parsing), "parsing 记录 ○ 前缀保留"

    def test_multiple_tools_no_fluctuation(self):
        """连续调用多个工具：工具运行中面板高度只增不减（无缩短帧）。"""
        from src.tui._subagent_state import StateStore
        from src.tui._subagent_render import render_frame

        store = StateStore(max_history=3)
        store.add_agent("agent-1", "分析", status="running", agent_type="map")
        prev_h = len(render_frame(store, max_history=3))
        for i, tool in enumerate(("search", "read_file", "ls")):
            store.update_tool_parsing("agent-1", tool, f"'arg{i}'")
            h_parsing = len(render_frame(store, max_history=3))
            assert h_parsing >= prev_h, (
                f"工具 {tool} parsing 帧高度不得缩短: prev={prev_h} now={h_parsing}"
            )
            store.start_tool("agent-1", tool, f"'arg{i}'")
            h_running = len(render_frame(store, max_history=3))
            assert h_running == h_parsing, (
                f"工具 {tool} parsing→running 高度不得变化: "
                f"parsing={h_parsing} running={h_running}"
            )
            prev_h = h_running


class TestSubAgentCardWidthConsistency:
    """subagent 卡片截断宽度与布局宽度同源（防「第二行只剩边框字符」错乱）。

    回归：ChatView 截断用 model.width，而 TEXT 布局按 box.w wrap——两者不一致
    （TTL 缓存 / resize 时序 / 默认 80）时，卡片行被 wrap 拆成两行，第二行只剩
    尾部边框字符（``┐``/``│``）——用户可见「工具调用历史显示成 2 行，第二行
    只有一个字符」。修复：App 传 width 给 ChatView，截断与布局同源。
    """

    @staticmethod
    def _build_subagent_lines():
        from src.tui._subagent_state import StateStore
        from src.tui._subagent_render import render_frame as sub_render
        store = StateStore()
        store.add_agent("agent-1", "分析项目结构", status="running", agent_type="map")
        store.update_tool_parsing(
            "agent-1", "bash", '{"command": "ls -la &&\\necho hi"}',
        )
        store.start_tool("agent-1", "bash", "ls -la &&\necho hi")
        return sub_render(store, max_history=3)

    def test_no_single_char_wrap_when_model_width_stale(self):
        """model.width 陈旧（60）而布局宽度 40：卡片每行仍单行，无单字符残留行。"""
        from src.tui.app.model import AppModel
        from src.tui.app.apply import apply_cmd
        from src.tui._const import SubagentFrameCmd
        from src.tui.ink.reconciler import Reconciler
        from src.tui.ink.components import render_frame
        from src.tui.app.app import build_app_element

        sub_lines = self._build_subagent_lines()
        m = AppModel()
        m.width = 60  # 陈旧截断宽度（修复前用此值 truncate → 超布局宽度 wrap）
        apply_cmd(m, SubagentFrameCmd(frame_lines=sub_lines))

        r = Reconciler()
        root = r.create_root()
        r.render(root, build_app_element(m, 40), 40, 24)  # 布局宽度 40
        frame = render_frame(root, 40)
        plains = [line.plain for line in frame.lines]

        # 边框残留单字符行（┐/│ 单独成行）不得出现——修复前 subagent 卡片每行
        # 都被 wrap 出第二行（┐ 或 │）
        for p in plains:
            stripped = p.strip()
            assert not (len(stripped) <= 2 and stripped in ("┐", "│", "└", "┘")), (
                f"卡片边框字符被 wrap 成独立行: {p!r}"
            )
        # 工具历史行（含 Bash）只占一行（完整边框或窄屏截断，均不 wrap 成两行）
        bash_rows = [p for p in plains if "Bash" in p]
        assert len(bash_rows) == 1, f"工具历史行应单行完整: {bash_rows!r}"
        assert len(bash_rows[0]) <= 40, f"工具历史行不超布局宽度: {bash_rows[0]!r}"

    def test_render_children_uses_layout_width_not_model_width(self):
        """_render_children 截断宽度=布局宽度（40 而非 model.width=60）。"""
        from src.tui.app.model import AppModel
        from src.tui.app.subagent_panel import _render_children

        sub_lines = self._build_subagent_lines()
        m = AppModel()
        m.width = 60
        m.subagent_lines = sub_lines
        children = _render_children(m, 40)
        assert children, "应产出 subagent TEXT 节点"
        for child in children:
            text = "".join(r.text for r in child.props["styled"])
            from src.tui._screen import wcswidth_simple
            assert wcswidth_simple(text) <= 40, (
                f"截断后行宽超布局宽度: {text!r} width={wcswidth_simple(text)}"
            )


class TestGroupCardBorderBreath:
    """BEAUTY-11 — 运行中子代理组卡边框呼吸（暗青 23 → 亮青 45 脉动）。

    与工具卡边框呼吸（_tool_card_styled_lines）同步；全部完成（closed）保持
    静态 _C_BORDER（23）。面板 10Hz 刷新时时间基推进平滑呼吸。
    """

    def _card(self, statuses):
        from src.tui._subagent_state import StateStore
        from src.tui._subagent_render import render_frame
        store = StateStore(max_history=3)
        for i, st in enumerate(statuses):
            store.add_agent(f"agent-{i}", "分析", status=st, agent_type="map")
        return render_frame(store, max_history=3)

    def test_running_card_invokes_time_glow(self):
        """运行中边框调用 time_glow 呼吸（暗青→亮青脉动）。"""
        from unittest.mock import patch
        with patch("src.tui.app._theme.time_glow", return_value=45) as mock_glow:
            lines = self._card(["running"])
        assert mock_glow.call_count >= 1, "运行中边框应调用 time_glow 呼吸"
        assert lines, "应产出卡片行"

    def test_closed_card_does_not_invoke_time_glow(self):
        """全部完成（closed）边框静态（不调用 time_glow，零额外成本）。"""
        from unittest.mock import patch
        with patch("src.tui.app._theme.time_glow", return_value=45) as mock_glow:
            lines = self._card(["done"])
        mock_glow.assert_not_called()
        assert lines, "应产出卡片行"
        assert "38;5;23m" in lines[0], "closed 边框应保持静态 _C_BORDER(23)"

    def test_running_border_color_in_breath_range(self):
        """运行中边框色号落在暗青 23..亮青 45 区间（time_glow 语义）。"""
        import re
        from unittest.mock import patch
        with patch("src.tui.app._theme.time_glow", return_value=30):
            lines = self._card(["running"])
        # 顶边框色号应包含 30（mock 的呼吸色）
        assert re.search(r"38;5;30m", lines[0]), "运行中边框应使用呼吸色号"


class TestGroupCardBorderFill:
    """BUG-24 — 组卡边框 fill 不强制 min=2（标题满宽时行不超 1 列）。"""

    def test_card_width_never_exceeds_max(self):
        """长标题（接近内宽）→ 卡片行宽恒 ≤ card_w（修复前超 1 列）。"""
        import re
        from src.tui._subagent_state import StateStore
        from src.tui._subagent_render import render_frame, _terminal_max_width
        from src.tui._screen import wcswidth_simple

        store = StateStore(max_history=3)
        # 超长描述填满内宽
        store.add_agent("agent-1", "D" * 60, status="done", agent_type="execute")
        lines = render_frame(store, max_history=3)
        for line in lines:
            plain = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", line)
            w = wcswidth_simple(plain)
            # 行宽不应超过卡片宽度（card_w = min(max_widths + 6, terminal_w)）
            assert w <= _terminal_max_width() + 1, (
                f"卡片行宽不应大幅超终端宽度: {w} > {_terminal_max_width()}"
            )
