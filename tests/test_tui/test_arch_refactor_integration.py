"""跨模块架构重构集成测试（方向A-F · 2026-07-31 步骤14 集成代码）。

覆盖四大调用链锚点：
  1. 装配全管线（TuiAssembly.assemble → 全部组件 + rs 无 captured 绑定）
  2. 生命周期批处理注册 + 订阅/退订（ChatUIConsumer start/stop）
  3. 输出路径委托（CommandUiAdapter.display_messages → 路径 A ChatUIConsumer）
  4. 输入薄外观接线（Input 全公开方法委托不抛）

避免真实终端 I/O——统一 mock ``sys.__stdout__``（autouse fixture），
终端尺寸 / select 不做真实读取。
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _mock_terminal():
    """mock 终端输出：替换 sys.__stdout__/sys.__stderr__ 为可写 mock，隔离真实输出。

    P3-19：追加 patch sys.__stderr__（装配/引擎紧急路径可能写 stderr，
    避免测试输出污染真实终端）。
    同时替换 sys.stdin 为提供 fileno() 的伪对象——pytest 的 stdin 是
    pseudofile（无 fileno），而 TuiAssembly._create_chat_domain 构造
    Input 时调用 ``sys.stdin.fileno()``；mock 后装配路径可用。
    """
    out = MagicMock()
    out.closed = False
    out.write.return_value = None
    out.flush.return_value = None
    fake_stdin = MagicMock()
    fake_stdin.fileno.return_value = 0
    with patch.object(sys, "__stdout__", out), \
         patch.object(sys, "__stderr__", MagicMock()), \
         patch.object(sys, "stdin", fake_stdin):
        yield out


@pytest.fixture(autouse=True)
def _tracker_cleanup():
    """收集装配产生的 _StdoutLineTracker 实例，测试结束后清理 2s daemon Timer。

    P1-3 根因：_StdoutLineTracker.__init__ 无条件启动 2s daemon Timer；
    ``_lifecycle.stop() → bb.teardown() → _do_teardown`` 因 ``bb._active``
    从未 setup 而 early return，tracker 定时器不停止 → Timer 线程泄漏。
    本 fixture 在测试期间记录所有创建的 tracker 实例，teardown 时统一
    ``_stop_flush_timer()`` + ``_flush_history()`` 清理（不改产品代码）。
    """
    from src.tui._stdout_tracker import _StdoutLineTracker

    created: list = []
    orig_init = _StdoutLineTracker.__init__

    def _recording_init(self, *args, **kwargs):
        orig_init(self, *args, **kwargs)
        created.append(self)

    with patch.object(_StdoutLineTracker, "__init__", _recording_init):
        yield
    for tracker in created:
        try:
            tracker._stop_flush_timer()
            tracker._flush_history()
        except Exception:
            pass


@pytest.fixture(autouse=True)
def _reset_bus():
    """每个测试前后重置 DisplayEventBus 进程级单例，保证订阅计数可预测。

    P3-19：追加 _active_consumer / 引用计数快照恢复——start() 会注册
    消费者（_register_consumer），stop() 注销；若测试中途异常未 stop，
    全局 _active_consumer 残留会污染后续测试（get_active_chat_ui 返回
    陈旧实例）。teardown 时恢复快照。
    """
    from src.tui.events.event_bus import DisplayEventBus
    from src.tui.state import consumer_registry as _cr

    DisplayEventBus.reset_default()
    prev_active = _cr._active_consumer
    prev_refcount = _cr._active_consumer_refcount
    yield
    DisplayEventBus.reset_default()
    _cr._active_consumer = prev_active
    _cr._active_consumer_refcount = prev_refcount


# ═══════════════════════════════════════════════════════════
# 1. 装配全管线
# ═══════════════════════════════════════════════════════════

class TestAssemblyFullPipeline:
    """步骤14 子步骤3-①：装配全管线回归。"""

    def test_assembly_full_pipeline_regression(self):
        """assemble() 返回全部 9 个 slot；rs 绑定 render_output 后 get_content 可用且无 captured 绑定。"""
        from src.tui._assembly import TuiAssembly

        result = TuiAssembly.assemble()

        # 全部 9 个 slot 非 None
        for slot in (
            "rs", "engine", "bb", "dispatcher", "renderer",
            "cmpl_handler", "input_instance", "subagent_controller",
            "components",
        ):
            assert getattr(result, slot) is not None, f"{slot} 应为非 None"

        # _components 命名空间与 input_instance 一致
        assert result.components.input is result.input_instance
        # ★ Input 已注入 InkSession（render 循环依赖 _phase_process_input 读取 stdin，
        #   未注入则用户无法输入）
        assert result.engine._input is result.input_instance

        # AppModel 通道流式写入（apply_cmd 路径替代 get_content/get_reasoning）
        rs = result.rs
        from src.tui.app.apply import apply_cmd
        from src.tui._const import ContentCmd, PhaseDoneCmd
        apply_cmd(rs, ContentCmd(text="# Hi\n"))
        apply_cmd(rs, PhaseDoneCmd(phase="content"))
        assert any(
            "Hi" in line.plain
            for block in rs.blocks for line in block.lines
        )

        # 方向C 步骤5：captured_* 机制已删除
        assert not hasattr(rs, "captured_reasoning_output")
        assert not hasattr(rs, "captured_content_output")

    def test_assembly_no_cycle_import_regression(self):
        """_consumer ↔ _assembly 循环已消除：任意顺序导入均成功。"""
        import src.tui._consumer
        import src.tui._assembly
        import src.tui._components
        from src.tui._consumer import _ComponentsNamespace as A
        from src.tui._components import _ComponentsNamespace as B
        assert A is B


# ═══════════════════════════════════════════════════════════
# 2. 生命周期批处理注册 + 订阅/退订
# ═══════════════════════════════════════════════════════════

class TestLifecycleBatchedAndSubscriptions:
    """步骤14 子步骤3-②：生命周期批处理决策（P0-3）+ 订阅计数回归。"""

    def test_lifecycle_batched_and_subscriptions_regression(self):
        """ChatUIConsumer() + start() 后总线无批处理注册 + 订阅绑定；stop() 后清理。"""
        from src.tui.consumer import ChatUIConsumer
        from src.tui.events.event_bus import DisplayEventBus
        from src.tui.events.event_types import (
            ContentChunkEvent, ReasoningChunkEvent, PhaseDoneEvent, ToolSummaryEvent,
        )

        bus = DisplayEventBus.get_default()
        assert bus.subscriber_count == 0

        ui = ChatUIConsumer()
        ui.start()
        try:
            # 批处理不启用（P0-3 重估：33ms 窗口无合并收益且放大顺序竞态）
            assert bus._batched_events == set()
            assert ContentChunkEvent not in bus._batched_events
            assert ReasoningChunkEvent not in bus._batched_events
            # 订阅绑定：lifecycle 订阅 dispatcher.list_handlers()（>=12 类）
            # P3-19：不依赖精确计数，改显式断言关键事件已订阅
            assert bus.subscriber_count >= 12
            for ev_type in (ContentChunkEvent, ReasoningChunkEvent,
                            PhaseDoneEvent, ToolSummaryEvent):
                assert ev_type in bus._handlers, (
                    f"关键事件 {ev_type.__name__} 应已订阅"
                )
        finally:
            ui.stop()

        # stop() 后订阅全部解除（批处理未注册，_batched_events 保持为空）
        assert bus.subscriber_count == 0
        assert bus._batched_events == set()

    def test_lifecycle_restart_keeps_no_batched_regression(self):
        """重复 start()/stop() 幂等：批处理保持不启用（P0-3），订阅可重绑定。"""
        from src.tui.consumer import ChatUIConsumer
        from src.tui.events.event_bus import DisplayEventBus

        bus = DisplayEventBus.get_default()
        ui = ChatUIConsumer()
        ui.start()
        ui.stop()
        ui.start()
        try:
            assert bus._batched_events == set()
            assert bus.subscriber_count >= 12
        finally:
            ui.stop()


# ═══════════════════════════════════════════════════════════
# 3. 输出路径委托（路径 B/C → 路径 A）
# ═══════════════════════════════════════════════════════════

class TestOutputPathDelegation:
    """步骤14 子步骤3-③：CommandUiAdapter.display_messages 委托路径 A。"""

    def test_display_messages_delegates_to_chat_ui_regression(self):
        """ChatUI 活跃时 display_messages 委托 ChatUIConsumer.display_messages（路径 A）。"""
        from src.core.commands._ui_adapter import CommandUiAdapter

        consumer = MagicMock()
        data = [{"role": "user", "content": "hi"}]

        with patch("src.tui.consumer.get_active_chat_ui", return_value=consumer):
            CommandUiAdapter().display_messages(data)

        consumer.display_messages.assert_called_once_with(data, speed=0)

    def test_display_messages_fallback_no_chat_ui_regression(self, _mock_terminal):
        """ChatUI 不活跃时回退 pipeline 直写兜底（P2-16：断言直写实际发生）。"""
        from src.core.commands._ui_adapter import CommandUiAdapter

        data = [{"role": "user", "content": "hi"}]

        with patch("src.tui.consumer.get_active_chat_ui", return_value=None):
            CommandUiAdapter().display_messages(data)

        # P2-16：断言 fallback 直写实际发生（message_display 写 sys.__stdout__
        # 被 _mock_terminal 拦截，写入内容含 "[user]"）
        writes = [
            c.args[0] for c in _mock_terminal.write.call_args_list
            if c.args and isinstance(c.args[0], str)
        ]
        assert any("[user]" in w for w in writes), (
            "fallback 直写应实际写入终端（含 [user] 标记），当前无写入"
        )


# ═══════════════════════════════════════════════════════════
# 4. 输入薄外观接线
# ═══════════════════════════════════════════════════════════

class TestInputFacadeWiring:
    """步骤14 子步骤3-④：Input 薄外观全公开方法存在且委托不抛。"""

    def _make_input(self, tmp_path):
        from src.tui._input import Input

        twc = MagicMock()
        twc.get_width.return_value = 80
        twc.get_height.return_value = 24
        # P3-19：固定 /tmp 路径改用 pytest tmp_path（避免跨测试共享/污染）
        inp = Input(
            fd=0,
            history_file=tmp_path / "nonexistent_history_test.txt",
            term_width_cache=twc,
        )
        inp.set_echo_callback(lambda text, cursor_pos=-1: None)
        return inp

    def test_input_facade_wiring_regression(self, tmp_path):
        """全公开方法存在且委托不抛（mock echo 回调）。"""
        inp = self._make_input(tmp_path)

        # 公开方法/属性存在
        for name in (
            "fd", "width", "height", "is_io_running", "interrupted",
            "start_io", "stop_io", "pause_io", "resume_io",
            "read_stdin_once", "process_events",
            "read_byte", "read_with_timeout", "try_read_paste", "read_utf8_char",
            "handle_char", "handle_chars", "get_queued_input", "has_queued_input",
            "get_current_text", "reset", "drain_all", "set_buffer",
            "get_history_indicator", "load_history",
            "compute_cursor", "echo", "reset_and_echo",
            "capture_bytes", "drain_captured", "flush_stdin_buffer",
            "set_echo_callback", "set_special_key_callback",
            "set_completion_callback", "set_dismiss_completion_callback",
            "set_completion_navigate_callback", "set_auto_completion_callback",
            "set_interrupt_callback", "set_suppress_enter", "get_suppress_enter",
            "wait_until_ready",
        ):
            assert hasattr(inp, name), f"Input 缺少公开成员 {name}"

        # 缓冲/队列委托行为
        inp.handle_chars("hello")
        assert inp.get_current_text() == "hello"
        inp.set_buffer("world")
        assert inp.get_current_text() == "world"
        assert inp.wait_until_ready(0.01) is False  # 未就绪超时返回 False

        # 回调与抑制标志委托
        inp.set_suppress_enter(True)
        assert inp.get_suppress_enter() is True
        inp.reset_and_echo()
        assert inp.get_current_text() == ""

    def test_input_interrupt_callback_none_short_circuit_regression(self, tmp_path):
        """未注入 interrupt 回调时 _do_interrupt 不抛异常（None 短路）。"""
        inp = self._make_input(tmp_path)
        inp._do_interrupt()  # 未注入回调 → debug 日志 + 跳过，不抛异常

    def test_input_interrupt_callback_injected_regression(self, tmp_path):
        """注入 interrupt 回调后被调用（_loop.py 注入点验证）。"""
        inp = self._make_input(tmp_path)
        cb = MagicMock()
        inp.set_interrupt_callback(cb)
        inp._do_interrupt()
        cb.assert_called_once()


# ═══════════════════════════════════════════════════════════
# 步骤5.4 — 装配层注入 useInput router 与 useApp control
# ═══════════════════════════════════════════════════════════

class TestAssemblyHooksWiring:
    """步骤5 — assemble 后 input hook router / useApp control 已接线。"""

    def test_assembly_wires_input_router_regression(self):
        """assemble 后 input_instance 已注入 router；无 handler 时输入放行。"""
        from src.tui._assembly import TuiAssembly
        from src.tui.ink import hooks as _hooks

        result = TuiAssembly.assemble()
        try:
            # router 已注入 InputDispatcher
            router = result.input_instance._dispatcher._input_hook_router
            assert router is not None
            assert callable(router)
            # 无 handler 注册时 router 放行（输入旧路径零行为变化）
            assert router(object()) is False
            # useApp control 已注入（exit = session.stop）
            assert _hooks._app_control is not None
            assert callable(_hooks._app_control["exit"])
        finally:
            _hooks.set_app_control(None)

    def test_assembly_wires_app_control_exit_regression(self):
        """注入的 useApp control exit 可调用（session.stop 幂等）。"""
        from src.tui._assembly import TuiAssembly
        from src.tui.ink import hooks as _hooks

        result = TuiAssembly.assemble()
        try:
            exit_fn = _hooks._app_control["exit"]
            exit_fn()  # 调用不抛（session.stop 幂等）
        finally:
            _hooks.set_app_control(None)
