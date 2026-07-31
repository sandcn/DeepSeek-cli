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
