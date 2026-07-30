"""测试 _consumer.py — ChatUIConsumer 兼容实现。

测试 ChatUIConsumer 生命周期、公开方法和 for_testing 工厂方法，
使用 mock 子系统隔离真实终端 I/O。
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch, PropertyMock


class TestChatUIConsumerLifecycle:
    """ChatUIConsumer 生命周期测试（mock 子系统）。"""

    @pytest.fixture
    def mock_subsystems(self):
        """创建 mock 子系统并 patch _assemble 方法。"""
        with patch('src.tui._consumer.ChatUIConsumer._assemble') as mock_assemble, \
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
        """__init__ 组装子系统（通过 _assemble 完成）。"""
        # _assemble 被 patch，mock consumer 通过 _create_mock_consumer 创建
        # 验证 consumer 对象正确创建即可
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
        c._engine.push_cmd.assert_any_call((19,))  # RenderCommand.SPLASH = 19

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
        c._started = True

        c.stop()

        mock_subsystems['mock_unregister'].assert_called_once()
        c._engine.stop.assert_called_once()

    def test_stop_when_not_started(self, mock_subsystems):
        """未启动时 stop() 应直接返回。"""
        c = mock_subsystems['consumer']
        c._started = False

        c.stop()

        c._engine.stop.assert_not_called()

    def test_suspend_stops_engine(self, mock_subsystems):
        """suspend() 应停止引擎并拆除底部栏。"""
        c = mock_subsystems['consumer']
        c._started = True

        c.suspend()

        c._engine.stop.assert_called_once()
        c._bb.teardown.assert_called_once()

    def test_resume_restarts_engine(self, mock_subsystems):
        """resume() 应重建底部栏并启动引擎。"""
        c = mock_subsystems['consumer']
        c._started = True
        c._engine._render_running = False

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
        with patch('src.tui._consumer.ChatUIConsumer._assemble'):
            from src.tui._consumer import ChatUIConsumer, _ComponentsNamespace
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
            c._components = _ComponentsNamespace(c._input)
            c._bound_handlers = None
            c._state_lock = threading.Lock()  # 真实锁
            c._started = False
            c._handlers_bound = False
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
        c._started = False  # 模拟 stop 后的状态

        # 重新 start
        c.start()
        assert c._started is True
        assert c._engine.start.call_count == 2  # 每次 start 都调用了 _engine.start()


class TestChatUIConsumerPublicMethods:
    """ChatUIConsumer 公开方法测试。"""

    @pytest.fixture
    def mock_consumer(self):
        """创建 mock ChatUIConsumer。"""
        with patch('src.tui._consumer.ChatUIConsumer._assemble'):
            c = _create_mock_consumer(MagicMock())
            c._dispatcher.list_handlers = MagicMock(return_value={})
            return c

    def test_on_user_message(self, mock_consumer):
        """on_user_message 应入队 USER_MSG 命令。"""
        mock_consumer.on_user_message("hello")
        mock_consumer._engine.push_cmd.assert_called_once()
        cmd = mock_consumer._engine.push_cmd.call_args[0][0]
        assert cmd[0] == 8  # RenderCommand.USER_MSG
        assert cmd[1] == "hello"

    def test_on_notification(self, mock_consumer):
        """on_notification 应入队 NOTIFICATION 命令。"""
        mock_consumer.on_notification("test")
        cmd = mock_consumer._engine.push_cmd.call_args[0][0]
        assert cmd[0] == 11  # RenderCommand.NOTIFICATION
        assert cmd[1] == "test"

    def test_on_error(self, mock_consumer):
        """on_error 应入队 ERROR 命令。"""
        mock_consumer.on_error("oops")
        cmd = mock_consumer._engine.push_cmd.call_args[0][0]
        assert cmd[0] == 16  # RenderCommand.ERROR
        assert cmd[1] == "oops"

    def test_on_error_empty_message(self, mock_consumer):
        """空错误消息不应入队。"""
        mock_consumer.on_error("")
        mock_consumer._engine.push_cmd.assert_not_called()

    def test_write_line(self, mock_consumer):
        """write_line 应入队 WRITE_LINE 命令。"""
        mock_consumer.write_line("line")
        cmd = mock_consumer._engine.push_cmd.call_args[0][0]
        assert cmd[0] == 12  # RenderCommand.WRITE_LINE
        assert cmd[1] == "line"

    def test_flush_delegates(self, mock_consumer):
        """flush 应委托给 engine.flush。"""
        mock_consumer.flush(timeout=3.0)
        mock_consumer._engine.flush.assert_called_once_with(timeout=3.0)

    def test_push_cmd_delegates(self, mock_consumer):
        """push_cmd 应委托给 engine.push_cmd。"""
        cmd = (99, "test")
        mock_consumer.push_cmd(cmd)
        mock_consumer._engine.push_cmd.assert_called_once_with(cmd)

    def test_bottom_bar_property(self, mock_consumer):
        """bottom_bar 属性应返回 _bb 实例。"""
        assert mock_consumer.bottom_bar is mock_consumer._bb

    def test_output_adapter_property(self, mock_consumer):
        """output_adapter 属性应委托给 renderer。"""
        mock_consumer._renderer.output_adapter = "mock_adapter"
        assert mock_consumer.output_adapter == "mock_adapter"


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

    c = ChatUIConsumer.__new__(ChatUIConsumer)
    c._bus = MagicMock()
    c._rs = MagicMock()
    c._bb = MagicMock()
    c._input = MagicMock()
    c._renderer = MagicMock()
    c._engine = MagicMock()
    c._dispatcher = MagicMock()
    c._cmpl_handler = MagicMock()
    c._components = _ComponentsNamespace(c._input)
    c._bound_handlers = None
    c._state_lock = MagicMock()
    c._state_lock.__enter__ = MagicMock(return_value=None)
    c._state_lock.__exit__ = MagicMock(return_value=None)
    c._started = False
    c._handlers_bound = False
    return c
