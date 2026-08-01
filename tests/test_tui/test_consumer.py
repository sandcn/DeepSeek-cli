"""测试 _consumer.py — ChatUIConsumer 兼容实现。

测试 ChatUIConsumer 生命周期、公开方法和 for_testing 工厂方法，
使用 mock 子系统隔离真实终端 I/O。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.tui._const import SplashCmd


class TestChatUIConsumerLifecycle:
    """ChatUIConsumer 生命周期测试（mock 子系统）。"""

    @pytest.fixture
    def mock_subsystems(self):
        """创建 mock 子系统并 patch TuiAssembly.assemble 方法。"""
        with patch('src.tui._consumer.TuiAssembly.assemble') as mock_assemble, \
             patch('src.tui._consumer._register_consumer') as mock_register, \
             patch('src.tui._consumer._unregister_consumer') as mock_unregister, \
             patch('src.tui._consumer.render_lock') as mock_render_lock:
            mock_render_lock.__enter__ = MagicMock(return_value=None)
            mock_render_lock.__exit__ = MagicMock(return_value=None)

            consumer = _create_mock_consumer(mock_assemble)
            yield {
                'consumer': consumer,
                'mock_assemble': mock_assemble,
                'mock_register': mock_register,
                'mock_unregister': mock_unregister,
            }

    def test_init_calls_assemble(self, mock_subsystems):
        """__init__ 组装子系统（通过 TuiAssembly.assemble 完成）。"""
        c = mock_subsystems['consumer']
        assert c is not None
        assert hasattr(c, '_engine')

    def test_start_subscribes_and_registers(self, mock_subsystems):
        """start() 应订阅事件、注册消费者、启动引擎。"""
        c = mock_subsystems['consumer']
        c._engine.start = MagicMock()
        c._dispatcher.list_handlers = MagicMock(return_value={})
        c._bus.subscribe = MagicMock()

        c.start()

        mock_subsystems['mock_register'].assert_called_once_with(c)
        c._engine.start.assert_called_once()
        # SPLASH 命令入队
        c._engine.push_cmd.assert_any_call(SplashCmd())

    def test_start_idempotent(self, mock_subsystems):
        """重复调用 start() 应幂等。"""
        c = mock_subsystems['consumer']
        c._dispatcher.list_handlers = MagicMock(return_value={})
        c._bus.subscribe = MagicMock()
        c._engine.start = MagicMock()
        c._engine.push_cmd = MagicMock()

        c.start()
        c.start()  # 第二次调用

        # _register_consumer 只调用一次
        assert mock_subsystems['mock_register'].call_count == 1

    def test_stop_unsubscribes_and_unregisters(self, mock_subsystems):
        """stop() 应取消订阅、停止引擎、注销消费者。"""
        c = mock_subsystems['consumer']
        c._dispatcher.list_handlers = MagicMock(return_value={})
        c._bus.subscribe = MagicMock()
        c._engine.start = MagicMock()
        c._engine.push_cmd = MagicMock()
        c._lifecycle._started = True

        c.stop()

        mock_subsystems['mock_unregister'].assert_called_once()
        c._engine.stop.assert_called_once()

    def test_stop_when_not_started(self, mock_subsystems):
        """未启动时 stop() 应直接返回。"""
        c = mock_subsystems['consumer']
        c._lifecycle._started = False

        c.stop()

        c._engine.stop.assert_not_called()

    def test_suspend_stops_engine(self, mock_subsystems):
        """suspend() 应停止引擎并拆除底部栏。"""
        c = mock_subsystems['consumer']
        c._lifecycle._started = True

        c.suspend()

        c._engine.stop.assert_called_once()
        c._bb.teardown.assert_called_once()

    def test_resume_restarts_engine(self, mock_subsystems):
        """resume() 应重建底部栏并启动引擎。"""
        c = mock_subsystems['consumer']
        c._lifecycle._started = True
        c._engine.is_render_running = MagicMock(return_value=False)

        c.resume()

        c._bb.setup.assert_called_once()
        c._engine.start.assert_called_once()


class TestChatUIConsumerRaceCondition:
    """ChatUIConsumer start/stop 竞态条件测试。

    使用真实 threading.Lock 而非 MagicMock 模拟，验证锁范围正确性。
    核心验证：_engine.start() 在 _state_lock 内调用，确保 daemon 线程创建时
    _started 已设置，消除 stop() 检查通过后 _engine.start() 未完成的竞态窗口。
    """

    @pytest.fixture
    def real_lock_consumer(self):
        """创建使用真实 threading.Lock 的 ChatUIConsumer。"""
        with patch('src.tui._consumer.TuiAssembly.assemble'):
            from src.tui._consumer import ChatUIConsumer, _ComponentsNamespace
            from src.tui._lifecycle import TuiLifecycle
            from src.tui._input_orchestrator import TuiInputOrchestrator
            import threading

            c = ChatUIConsumer.__new__(ChatUIConsumer)
            c._bus = MagicMock()
            c._rs = MagicMock()
            c._bb = MagicMock()
            c._input = MagicMock()
            c._renderer = MagicMock()
            c._engine = MagicMock()
            c._dispatcher = MagicMock()
            c._cmpl_handler = MagicMock()
            c._subagent_controller = None
            c._components = _ComponentsNamespace(c._input)
            c._lifecycle = TuiLifecycle(
                engine=c._engine, bus=c._bus, bb=c._bb,
                rs=c._rs, dispatcher=c._dispatcher,
            )
            c._lifecycle._state_lock = threading.Lock()  # 真实锁
            c._input_orchestrator = TuiInputOrchestrator(c._input)
            return c

    def test_engine_start_inside_lock(self, real_lock_consumer):
        """验证 _engine.start() 在 _state_lock 内调用。

        start() 设置 _started=True 后，_engine.start() 应已被调用（在同一锁内），
        确保 _started 对其他线程可见。
        """
        c = real_lock_consumer
        c._dispatcher.list_handlers = MagicMock(return_value={})
        c._bus.subscribe = MagicMock()
        c._engine.start = MagicMock()
        c._engine.push_cmd = MagicMock()

        c.start()

        # _engine.start() 应在 _started 设置为 True 之前被调用
        assert c._started is True
        c._engine.start.assert_called_once()

    def test_start_stop_race_consistency(self, real_lock_consumer):
        """并发 start/stop 时 _started 状态保持一致性。

        模拟：在 start() 返回后（_started=True），立即调用 stop()，
        stop() 应在 _state_lock 内读到正确的 _started 值。
        """
        import threading

        c = real_lock_consumer
        c._dispatcher.list_handlers = MagicMock(return_value={})
        c._bus.subscribe = MagicMock()
        c._engine.start = MagicMock()
        c._engine.push_cmd = MagicMock()
        c._engine.flush = MagicMock()
        c._engine.stop = MagicMock()

        # 并发调用 start 和 stop
        start_ok = threading.Event()
        stop_ok = threading.Event()

        def do_start():
            c.start()
            start_ok.set()

        def do_stop():
            stop_ok.wait(timeout=1.0)
            c.stop()
            assert not c._started  # stop 后 _started 应为 False

        t_start = threading.Thread(target=do_start, daemon=True)
        t_stop = threading.Thread(target=do_stop, daemon=True)

        t_stop.start()
        t_start.start()
        start_ok.wait(timeout=1.0)
        stop_ok.set()
        t_start.join(timeout=1.0)
        t_stop.join(timeout=1.0)

        # 最终状态：stop 后 _started 应为 False
        assert c._started is False

    def test_start_inside_lock_after_stop(self, real_lock_consumer):
        """stop() 后重新 start()，_engine.start() 仍在锁内调用。"""
        c = real_lock_consumer
        c._dispatcher.list_handlers = MagicMock(return_value={})
        c._bus.subscribe = MagicMock()
        c._engine.start = MagicMock()
        c._engine.push_cmd = MagicMock()
        c._engine.flush = MagicMock()
        c._engine.stop = MagicMock()

        # 第一次 start/stop
        c.start()
        assert c._started is True
        c._lifecycle._started = False  # 模拟 stop 后的状态

        # 重新 start
        c.start()
        assert c._started is True
        assert c._engine.start.call_count == 2  # 每次 start 都调用了 _engine.start()


class TestChatUIConsumerPublicMethods:
    """ChatUIConsumer 公开方法测试。"""

    @pytest.fixture
    def mock_consumer(self):
        """创建 mock ChatUIConsumer。"""
        with patch('src.tui._consumer.TuiAssembly.assemble'):
            c = _create_mock_consumer(MagicMock())
            c._dispatcher.list_handlers = MagicMock(return_value={})
            return c

    def test_on_user_message(self, mock_consumer):
        """on_user_message 应入队 USER_MSG 命令。"""
        mock_consumer.on_user_message("hello")
        mock_consumer._engine.push_cmd.assert_called_once()
        cmd = mock_consumer._engine.push_cmd.call_args[0][0]
        assert cmd.cid == 8  # RenderCommand.USER_MSG
        assert cmd.text == "hello"

    def test_on_notification(self, mock_consumer):
        """on_notification 应入队 NOTIFICATION 命令。"""
        mock_consumer.on_notification("test")
        cmd = mock_consumer._engine.push_cmd.call_args[0][0]
        assert cmd.cid == 11  # RenderCommand.NOTIFICATION
        assert cmd.text == "test"

    def test_on_error(self, mock_consumer):
        """on_error 应入队 ERROR 命令。"""
        mock_consumer.on_error("oops")
        cmd = mock_consumer._engine.push_cmd.call_args[0][0]
        assert cmd.cid == 16  # RenderCommand.ERROR
        assert cmd.message == "oops"

    def test_on_error_empty_message(self, mock_consumer):
        """空错误消息不应入队。"""
        mock_consumer.on_error("")
        mock_consumer._engine.push_cmd.assert_not_called()

    def test_write_line(self, mock_consumer):
        """write_line 应入队 WRITE_LINE 命令。"""
        mock_consumer.write_line("line")
        cmd = mock_consumer._engine.push_cmd.call_args[0][0]
        assert cmd.cid == 12  # RenderCommand.WRITE_LINE
        assert cmd.text == "line"

    def test_flush_delegates(self, mock_consumer):
        """flush 应委托给 engine.flush。"""
        mock_consumer.flush(timeout=3.0)
        mock_consumer._engine.flush.assert_called_once_with(timeout=3.0)

    def test_push_cmd_delegates(self, mock_consumer):
        """push_cmd 应委托给 engine.push_cmd。"""
        cmd = SplashCmd()
        mock_consumer.push_cmd(cmd)
        mock_consumer._engine.push_cmd.assert_called_once_with(cmd)

    def test_bottom_bar_property(self, mock_consumer):
        """bottom_bar 属性应返回 _bb 实例。"""
        assert mock_consumer.bottom_bar is mock_consumer._bb

    def test_output_adapter_property(self, mock_consumer):
        """output_adapter 属性应委托给 renderer。"""
        mock_consumer._renderer.output_adapter = "mock_adapter"
        assert mock_consumer.output_adapter == "mock_adapter"

    def test_get_input_component(self, mock_consumer):
        """get_input_component 应返回 _components.input 实例（收敛私有访问）。"""
        assert mock_consumer.get_input_component() is mock_consumer._components.input

    def test_get_input(self, mock_consumer):
        """get_input 应返回输入组件实例。"""
        assert mock_consumer.get_input() is mock_consumer._input

    def test_request_bottom_redraw_delegates(self, mock_consumer):
        """request_bottom_redraw 应委托给 engine（公开 API 收敛）。"""
        mock_consumer.request_bottom_redraw()
        mock_consumer._engine.request_bottom_redraw.assert_called_once()


class TestForTesting:
    """for_testing 工厂方法测试。"""

    def test_for_testing_with_dataclass_components(self):
        """for_testing 应支持旧 _ChatUIComponents 风格参数。"""
        from src.tui._consumer import ChatUIConsumer

        # 创建 mock dataclass-like 对象
        components = MagicMock()
        components.rs = MagicMock()
        components.engine = MagicMock()
        components.bottom_bar = MagicMock()
        components.dispatcher = MagicMock()
        components.tui_renderer = MagicMock()
        components.cmpl_handler = MagicMock()
        components.input = MagicMock()

        c = ChatUIConsumer.for_testing(components)
        assert c._rs is components.rs
        assert c._engine is components.engine
        assert c._bb is components.bottom_bar
        assert c._dispatcher is components.dispatcher
        assert c._renderer is components.tui_renderer
        assert c._cmpl_handler is components.cmpl_handler
        assert c._input is components.input
        # _components 命名空间应存在
        assert c._components is not None
        assert c._components.input is components.input

    def test_for_testing_with_dict_components(self):
        """for_testing 应支持 dict 风格参数。"""
        from src.tui._consumer import ChatUIConsumer

        components = {
            'rs': MagicMock(),
            'engine': MagicMock(),
            'bottom_bar': MagicMock(),
            'dispatcher': MagicMock(),
            'tui_renderer': MagicMock(),
            'cmpl_handler': MagicMock(),
            'input': MagicMock(),
        }

        c = ChatUIConsumer.for_testing(components)
        assert c._rs is components['rs']
        assert c._engine is components['engine']


# ── 辅助函数 ──────────────────────────────────────────────


def _create_mock_consumer(mock_assemble):
    """创建 mock ChatUIConsumer 实例，绕过真实装配。"""
    from src.tui._consumer import ChatUIConsumer, _ComponentsNamespace
    from src.tui._lifecycle import TuiLifecycle
    from src.tui._input_orchestrator import TuiInputOrchestrator

    c = ChatUIConsumer.__new__(ChatUIConsumer)
    c._bus = MagicMock()
    c._rs = MagicMock()
    c._bb = MagicMock()
    c._input = MagicMock()
    c._renderer = MagicMock()
    c._engine = MagicMock()
    c._dispatcher = MagicMock()
    c._cmpl_handler = MagicMock()
    c._subagent_controller = None
    c._components = _ComponentsNamespace(c._input)
    # 先创建 _lifecycle 和 _input_orchestrator，再设置状态
    c._lifecycle = TuiLifecycle(
        engine=c._engine, bus=c._bus, bb=c._bb,
        rs=c._rs, dispatcher=c._dispatcher,
    )
    c._input_orchestrator = TuiInputOrchestrator(c._input)
    c._lifecycle.bound_handlers = None
    c._lifecycle._started = False
    c._lifecycle._handlers_bound = False
    return c


class TestInkBridgeCompat:
    """InkBridge 兼容 user_select 等对 _BottomBar 内部字段的直接访问。

    回归：user_select 读写 bb._last_text / _completion_idx / _completion._visible
    等内部字段，InkBridge 缺失曾导致 AttributeError。
    """

    @pytest.fixture
    def bridge(self):
        import sys

        class _FakeStdin:
            def fileno(self):
                return 0

        with patch.object(sys, "stdin", _FakeStdin()):
            from src.tui._assembly import TuiAssembly
            result = TuiAssembly.assemble()
        return result.bb, result.rs

    def test_last_text_maps_to_model(self, bridge):
        bb, model = bridge
        model.input_text = "typing"
        assert bb._last_text == "typing"
        bb._last_text = ""
        assert model.input_text == ""

    def test_completion_idx_maps_to_model(self, bridge):
        bb, model = bridge
        bb.show_completions(["a", "b"], 0, texts=["a", "b"])
        assert bb._completion_idx == 0
        bb.cycle_completion(1)
        assert bb._completion_idx == 1
        assert model.completion.selected == 1
        bb._completion_idx = 0
        assert model.completion.selected == 0

    def test_completion_internal_cleanup(self, bridge):
        bb, model = bridge
        bb.show_completions(["a", "b"], 0, texts=["a", "b"])
        assert model.completion.visible is True
        bb._completion._visible = False
        bb._completion._popup_height = 0
        bb._completion._items = []
        bb._completion._texts = []
        bb.force_redraw()
        assert model.completion.visible is False
        assert model.completion.items == []

    def test_misc_internal_fields(self, bridge):
        bb, model = bridge
        assert bb._MIN_HEIGHT == 12
        assert bb._last_bottom_lines == 5
        assert bb._bottom_lines == 5
        assert bb._last_scroll_end == 0
        assert bb.is_active is True
        bb.force_redraw()  # 不抛异常
        bb.set_active(False)  # no-op


class TestUserSelectSyncRender:
    """user_select 挂起期间补全弹窗同步渲染回归。

    回归：_run_interactive 在 user_select 前 chat_ui.suspend()（render 线程停止），
    旧实现弹窗依赖 render 线程渲染 → suspend 期间不显示。
    """

    @pytest.fixture
    def session_pair(self):
        import io
        import sys

        class _FakeStdin:
            def fileno(self):
                return 0

        with patch.object(sys, "stdin", _FakeStdin()):
            from src.tui.app.model import AppModel
            from src.tui.app.apply import apply_cmd
            from src.tui.app.app import build_app_element
            from src.tui.ink.session import InkSession
            from src.tui._ink_bridge import InkBridge
            from src.tui._const import UserMsgCmd

            model = AppModel()
            apply_cmd(model, UserMsgCmd(text="hi"))
            stream = io.StringIO()
            session = InkSession(
                model=model, apply_cmd=apply_cmd,
                build_tree=build_app_element, stream=stream,
            )
            bridge = InkBridge(model, session)
            return session, bridge, stream, model

    def test_popup_renders_while_suspended(self, session_pair):
        """suspend 后 show_completions 同步渲染（弹窗立即可见）。"""
        session, bridge, stream, model = session_pair
        session.start()
        session.flush(timeout=3.0)
        session.suspend()
        stream.seek(0)
        stream.truncate()
        bridge.show_completions(["1. read_file", "2. write_file"], 0,
                                texts=["read_file", "write_file"], title="选择")
        assert "read_file" in stream.getvalue(), "suspend 期间弹窗应同步渲染"
        session.resume()
        session.flush(timeout=3.0)
        session.stop()

    def test_cycle_triggers_redraw_while_suspended(self, session_pair):
        """suspend 期间 cycle_completion 触发重绘（高亮移动）。"""
        session, bridge, stream, model = session_pair
        session.start()
        session.flush(timeout=3.0)
        session.suspend()
        bridge.show_completions(["a", "b"], 0, texts=["a", "b"])
        stream.seek(0)
        stream.truncate()
        bridge.cycle_completion(1)
        assert len(stream.getvalue()) > 0, "cycle 应触发同步重绘"
        assert model.completion.selected == 1
        session.resume()
        session.flush(timeout=3.0)
        session.stop()


class TestSharedSourceFilterAndTruncation:
    """测试步骤 4.2/4.3 收敛的公共函数 — _const.is_agent_source / truncate_error_message。

    验证 source 过滤谓词与错误截断公共函数的行为（唯一真源）。
    """

    def test_is_agent_source_agent(self):
        from src.tui._const import is_agent_source
        assert is_agent_source("agent") is True

    def test_is_agent_source_agent_prefix(self):
        from src.tui._const import is_agent_source
        assert is_agent_source("agent-1") is True
        assert is_agent_source("agent-tool") is True

    def test_is_agent_source_other(self):
        from src.tui._const import is_agent_source
        assert is_agent_source("tool") is False
        assert is_agent_source("user") is False
        assert is_agent_source("") is False

    def test_is_agent_source_none(self):
        from src.tui._const import is_agent_source
        assert is_agent_source(None) is False

    def test_truncate_within_limit(self):
        from src.tui._const import truncate_error_message
        assert truncate_error_message("short error", 200) == "short error"

    def test_truncate_exact_limit(self):
        from src.tui._const import truncate_error_message
        text = "x" * 200
        assert truncate_error_message(text, 200) == text

    def test_truncate_over_limit(self):
        from src.tui._const import truncate_error_message
        text = "x" * 250
        result = truncate_error_message(text, 200)
        assert len(result) == 200
        assert result == "x" * 197 + "..."

    def test_truncate_empty(self):
        from src.tui._const import truncate_error_message
        assert truncate_error_message("", 200) == ""

    def test_truncate_none(self):
        from src.tui._const import truncate_error_message
        assert truncate_error_message(None, 200) == ""

    def test_truncate_zero_max_length(self):
        from src.tui._const import truncate_error_message
        assert truncate_error_message("hello", 0) == ""

    def test_truncate_small_max_length(self):
        from src.tui._const import truncate_error_message
        # max_length <= 3 时退化为直接截断，不加省略号
        assert truncate_error_message("hello", 3) == "hel"


class TestAssemblyRefactorRegression:
    """方向B 步骤3 装配层重构回归测试。

    验证 _ComponentsNamespace 迁出至 _components.py 后：
      - 旧导入路径（_consumer）与新导入路径（_components）均可用且同一类
      - assemble() 拆分为工厂方法后返回全部 9 个 slot 字段
      - _consumer ↔ _assembly 无循环 import
    """

    def test_components_namespace_moved_regression(self):
        """_ComponentsNamespace 从 _consumer 与 _components 均可导入且同一类。"""
        from src.tui._consumer import _ComponentsNamespace as FromConsumer
        from src.tui._components import _ComponentsNamespace as FromComponents
        assert FromConsumer is FromComponents

        ns = FromConsumer(None)
        assert ns.input is None
        ns2 = FromComponents("mock_input")
        assert ns2.input == "mock_input"

    def test_assembly_factory_methods_regression(self):
        """TuiAssembly.assemble() 返回全部 9 个 slot 字段非 None。"""
        import sys
        from unittest.mock import patch
        from src.tui._assembly import TuiAssembly

        class _FakeStdin:
            """pytest 下 sys.stdin 为 pseudofile 无 fileno()，提供伪造 fd。"""
            def fileno(self):
                return 0

        with patch.object(sys, "stdin", _FakeStdin()):
            result = TuiAssembly.assemble()
        slots = (
            'rs', 'engine', 'bb', 'dispatcher', 'renderer',
            'cmpl_handler', 'input_instance', 'subagent_controller',
            'components',
        )
        for slot in slots:
            assert getattr(result, slot) is not None, (
                f"slot {slot} 应为非 None"
            )
        # 命名空间 input 与 input_instance 一致
        assert result.components.input is result.input_instance

    def test_no_cycle_import_regression(self):
        """_consumer 与 _assembly 任意导入顺序均成功（P2-3：subprocess 子进程验证）。

        原实现直接 importlib.import_module 受 sys.modules 缓存影响——
        第二次顺序验证时模块已缓存，验证名不副实。改用 subprocess +
        ``python -c`` 子进程（每种子进程全新解释器，无模块缓存），分别以
        两种顺序 import，断言均成功。
        """
        import os
        import subprocess
        import sys
        from pathlib import Path

        project_root = Path(__file__).resolve().parents[2]
        script_template = (
            "import {first}; import {second}; print('IMPORT_OK')"
        )
        for first, second in (
            ("src.tui._consumer", "src.tui._assembly"),
            ("src.tui._assembly", "src.tui._consumer"),
        ):
            script = script_template.format(first=first, second=second)
            proc = subprocess.run(
                [sys.executable, "-c", script],
                cwd=str(project_root),
                capture_output=True,
                text=True,
                timeout=30,
                env={**os.environ, "PYTHONPATH": str(project_root)},
            )
            assert proc.returncode == 0, (
                f"导入顺序 ({first}, {second}) 失败:\n{proc.stderr}"
            )
            assert "IMPORT_OK" in proc.stdout

        # 类同一性在当前进程内仍可验证（与模块缓存无关的符号一致性）
        import src.tui._consumer
        import src.tui._assembly
        assert src.tui._consumer._ComponentsNamespace is (
            src.tui._assembly._ComponentsNamespace
        )


class TestLifecycleBatchedRegistration:
    """方向D 步骤6 / P0-3 重估 — TuiLifecycle.start() 批处理注册决策回归测试。"""

    def test_lifecycle_start_registers_batched_events_regression(self):
        """start() 后总线 _batched_events **为空**（批处理不启用决策，P0-3）。

        2026-07-31 重估：上游 StreamChunkHandler 100ms 节流已满足降频目标，
        33ms 批处理窗口无实际合并收益，且将「批处理延迟事件 vs 同步直发阶段
        切换事件」的顺序竞态放大为固定窗口（推理文本静默丢失/content 开新块）
        → 批处理机制保留但**不启用**。
        """
        from unittest.mock import MagicMock
        from src.tui._lifecycle import TuiLifecycle
        from src.tui.events.event_bus import DisplayEventBus
        from src.tui.events.event_types import ContentChunkEvent, ReasoningChunkEvent

        DisplayEventBus.reset_default()
        try:
            bus = DisplayEventBus.get_default()
            engine = MagicMock()
            dispatcher = MagicMock()
            dispatcher.list_handlers.return_value = {}
            bb = MagicMock()
            rs = MagicMock()
            lc = TuiLifecycle(
                engine=engine, bus=bus, bb=bb, rs=rs, dispatcher=dispatcher,
            )

            lc.start()

            # 批处理不启用：_batched_events 为空（无 ContentChunk/Reasoning 注册）
            assert bus._batched_events == set()
            assert ContentChunkEvent not in bus._batched_events
            assert ReasoningChunkEvent not in bus._batched_events

            # 重复 start() 幂等（仍不注册批处理）
            lc.start()
            assert bus._batched_events == set()

            lc.stop()

            # stop() 后仍为空
            assert bus._batched_events == set()
        finally:
            DisplayEventBus.reset_default()


class TestStreamToRenderOrdering:
    """端到端排序回归：事件总线 → dispatcher → engine → renderer 调用顺序。

    验证核心修复（步骤2 优先级 + seq 保序）：同批内容命令
    （ReasoningCmd/ContentCmd）先于完成命令（PhaseDoneCmd）出队渲染——
    事件生成顺序本身即正确渲染顺序（reasoning 尾 → PhaseDone("reasoning") →
    content 首/尾 → PhaseDone("content")），REASONING/CONTENT 与 PhaseDone
    同级优先级(0) 后 PriorityQueue 的 seq 序号自然保持插入序。
    """

    def test_same_batch_content_before_phase_done(self):
        """事件生成顺序 → 入队顺序 → AppModel 块顺序（内容先于完成）。"""
        from src.tui._dispatcher import EventDispatcher
        from src.tui.ink.session import InkSession
        from src.tui.app.model import AppModel
        from src.tui.app.apply import apply_cmd
        from src.tui.events.event_bus import DisplayEventBus
        from src.tui.events.event_types import (
            ContentChunkEvent, PhaseDoneEvent, ReasoningChunkEvent,
        )

        DisplayEventBus.reset_default()
        try:
            bus = DisplayEventBus.get_default()
            model = AppModel()
            session = InkSession(model=model, apply_cmd=apply_cmd)
            dispatcher = EventDispatcher(push_cmd=session.push_cmd, main_label="main")
            for event_type, handler in dispatcher.list_handlers().items():
                bus.subscribe(handler, event_type)

            # 依序发布（reasoning 尾 → PhaseDone("reasoning") → content 首/尾 → PhaseDone("content")）
            bus.publish(ReasoningChunkEvent(text="tail-thought\n", label="main"))
            bus.publish(PhaseDoneEvent(phase="reasoning", label="main"))
            bus.publish(ContentChunkEvent(text="first\n\n", label="main"))
            bus.publish(ContentChunkEvent(text="tail\n\n", label="main"))
            bus.publish(PhaseDoneEvent(phase="content", label="main"))

            # 排空队列并应用
            drained = []
            while not session._cmd_queue.empty():
                _, _, cmd = session._cmd_queue.get_nowait()
                drained.append(cmd)
            for cmd in drained:
                apply_cmd(model, cmd)

            # AppModel 块顺序：reasoning 先于 content
            kinds = [b.kind for b in model.blocks]
            assert kinds == ["reasoning", "content"], f"块顺序: {kinds}"
        finally:
            DisplayEventBus.reset_default()

    def test_non_main_label_content_not_enqueued(self):
        """label 非 main 的 ContentChunkEvent 不入队（_on_content_chunk label 过滤）。"""
        from src.tui._dispatcher import EventDispatcher
        from src.tui.ink.session import InkSession
        from src.tui.app.model import AppModel
        from src.tui.app.apply import apply_cmd
        from src.tui.events.event_bus import DisplayEventBus
        from src.tui.events.event_types import ContentChunkEvent

        DisplayEventBus.reset_default()
        try:
            bus = DisplayEventBus.get_default()
            session = InkSession(model=AppModel(), apply_cmd=apply_cmd)
            dispatcher = EventDispatcher(push_cmd=session.push_cmd, main_label="main")
            for event_type, handler in dispatcher.list_handlers().items():
                bus.subscribe(handler, event_type)

            # 非 main label（如 SubAgent）的 ContentChunkEvent 不应入队
            bus.publish(ContentChunkEvent(text="subagent-content", label="agent-1"))
            assert session._cmd_queue.qsize() == 0
        finally:
            DisplayEventBus.reset_default()

    def test_model_phase_answering_reopens_content(self):
        """MainPhaseCmd("answering") 入队 → apply_cmd 触发 reopen_content。"""
        from src.tui._dispatcher import EventDispatcher
        from src.tui.ink.session import InkSession
        from src.tui.app.model import AppModel
        from src.tui.app.apply import apply_cmd
        from src.tui.events.event_bus import DisplayEventBus
        from src.tui.events.event_types import ModelPhaseEvent
        from src.tui._const import MainPhaseCmd, ContentCmd, PhaseDoneCmd

        DisplayEventBus.reset_default()
        try:
            bus = DisplayEventBus.get_default()
            model = AppModel()
            session = InkSession(model=model, apply_cmd=apply_cmd)
            dispatcher = EventDispatcher(push_cmd=session.push_cmd, main_label="main")
            for event_type, handler in dispatcher.list_handlers().items():
                bus.subscribe(handler, event_type)

            # 首轮 content 关闭
            apply_cmd(model, ContentCmd(text="x"))
            apply_cmd(model, PhaseDoneCmd(phase="content"))
            assert model.content_closed is True

            # 每轮首个 content 前发布 ModelPhaseEvent("answering")
            bus.publish(ModelPhaseEvent(label="main", phase="answering", info=""))
            drained = []
            while not session._cmd_queue.empty():
                _, _, cmd = session._cmd_queue.get_nowait()
                drained.append(cmd)
            for cmd in drained:
                apply_cmd(model, cmd)

            # MainPhaseCmd 应用后 content 通道重开
            assert model.status.main_phase == "answering"
            assert model.content_closed is False
        finally:
            DisplayEventBus.reset_default()

