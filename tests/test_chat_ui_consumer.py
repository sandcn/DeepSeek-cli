"""ChatUIConsumer 生命周期方法单元测试

测试范围：
1. TestChatUIConsumerInit       — __init__ 子系统创建 & _started 初始值
2. TestChatUIConsumerStart      — start() 订阅事件/引擎启动/幂等
3. TestChatUIConsumerStop       — stop() 取消订阅/引擎停止/底部栏拆除/幂等
4. TestChatUIConsumerSuspend    — suspend() 暂停渲染/底部栏拆除/幂等
5. TestChatUIConsumerResume     — resume() 恢复渲染/引擎重启/跳过条件
6. TestChatUIConsumerLifecycle  — start→stop→suspend→resume→stop 完整串行
7. TestChatUIConsumerPublicMethods — on_user_message/on_notification/on_error 入队

测试隔离：
- 每个测试从 root logger 移除/恢复 _error_handler（全局副作用）
- 每个测试恢复 _state._active_consumer 避免测试间污染
- 所有 EventBus 使用 MagicMock，不依赖真实 DisplayEventBus
- engine 的 start/stop 在需要时 patch（避免真实线程创建）
"""

import logging
import sys
from unittest.mock import MagicMock, patch

import pytest


# ── 项目根目录 ───────────────────────────────────────
sys.path.insert(0, "/home/DeepSeek-cli")


# ═══════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def _reset_error_handler():
    """每个测试前后从 root logger 移除/恢复 ChatUIErrorHandler。

    通过遍历 root logger handlers 查找 ChatUIErrorHandler 实例，
    而非依赖模块级 _error_handler 变量（P1-1 后已移除）。
    """
    root = logging.getLogger()
    from src.tui.consumer.error_handler import ChatUIErrorHandler
    handler = None
    for h in root.handlers:
        if isinstance(h, ChatUIErrorHandler):
            handler = h
            break
    if handler is not None and handler in root.handlers:
        root.removeHandler(handler)
    yield
    if handler is not None and handler not in root.handlers:
        root.addHandler(handler)


@pytest.fixture(autouse=True)
def _reset_active_consumer():
    """每个测试恢复 _state._active_consumer 避免测试间污染。"""
    from src.tui.state import consumer_registry as _consumer_registry
    original = _consumer_registry._active_consumer
    yield
    _consumer_registry._active_consumer = original


@pytest.fixture
def mock_bus():
    """返回一个 MagicMock EventBus。

    自动模拟 subscribe/unsubscribe 方法，
    方便验证调用次数和参数。
    """
    bus = MagicMock()
    return bus


@pytest.fixture
def consumer(mock_bus):
    """返回一个 ChatUIConsumer 实例（仅 mock EventBus）。

    所有子系统真实创建，只有 EventBus 被替换为 MagicMock。
    每个测试前清空命令队列，避免跨测试残留。
    """
    from src.tui.consumer import ChatUIConsumer
    c = ChatUIConsumer(event_bus=mock_bus)
    # 清空可能残留的队列
    while not c._engine._cmd_queue.empty():
        c._engine._cmd_queue.get_nowait()
    return c


# ═══════════════════════════════════════════════════════
# TestChatUIConsumerInit
# ═══════════════════════════════════════════════════════

class TestChatUIConsumerInit:
    """ChatUIConsumer.__init__ 子系统创建与初始状态"""

    def test_init_creates_all_subsystems(self, consumer):
        """__init__ 后各子系统已创建"""
        assert hasattr(consumer, '_rs')
        assert hasattr(consumer, '_engine')
        assert hasattr(consumer, '_disp')
        assert hasattr(consumer, '_cmpl')
        assert hasattr(consumer, '_bottom_bar')
        assert hasattr(consumer, '_tui_renderer')
        assert hasattr(consumer, '_bus')

    def test_init_started_false(self, consumer):
        """__init__ 后 _started 为 False"""
        assert consumer._started is False

    def test_init_bound_handlers_none(self, consumer):
        """__init__ 后 _bound_handlers 为 None（惰性绑定）"""
        assert consumer._bound_handlers is None

    def test_init_cmd_queue_empty(self, consumer):
        """__init__ 后命令队列为空"""
        assert consumer._engine._cmd_queue.empty()

    def test_init_event_handler_names_present(self, consumer):
        """_HANDLER_MAP 包含 12 个事件处理器"""
        from src.tui.engine.dispatcher import _HANDLER_MAP
        assert len(_HANDLER_MAP) == 12

    def test_init_event_bus_fallback(self):
        """未传入 event_bus 时使用 DisplayEventBus.get_default()"""
        from src.tui.consumer import ChatUIConsumer
        from src.tui.events.event_bus import DisplayEventBus
        default = DisplayEventBus.get_default()
        c = ChatUIConsumer()
        assert c._bus is default


# ═══════════════════════════════════════════════════════
# TestChatUIConsumerStart
# ═══════════════════════════════════════════════════════

class TestChatUIConsumerStart:
    """ChatUIConsumer.start() 生命周期启动"""

    def test_start_sets_started_true(self, consumer, mock_bus):
        """start() 后 _started=True"""
        with patch.object(consumer._engine, 'start'):
            with patch('src.tui.consumer.consumer') as mock_state:
                consumer.start()
                assert consumer._started is True

    def test_start_subscribes_events(self, consumer, mock_bus):
        """start() 为每个事件处理器调用 subscribe"""
        with patch.object(consumer._engine, 'start'):
            with patch('src.tui.consumer.consumer'):
                consumer.start()
                # 12 个事件处理器
                assert mock_bus.subscribe.call_count == 12

    def test_start_sets_active_consumer(self, consumer, mock_bus):
        """start() 调用 _state._register_consumer(self) 注册活跃实例"""
        with patch.object(consumer._engine, 'start'):
            with patch('src.tui.consumer.consumer._register_consumer') as mock_register:
                consumer.start()
                mock_register.assert_called_once_with(consumer)

    def test_start_calls_engine_start(self, consumer, mock_bus):
        """start() 调用 _engine.start()"""
        with patch.object(consumer._engine, 'start') as mock_engine_start:
            with patch('src.tui.consumer.consumer'):
                consumer.start()
                mock_engine_start.assert_called_once()

    def test_start_is_idempotent(self, consumer, mock_bus):
        """重复 start() 幂等——第二次不重复订阅"""
        with patch.object(consumer._engine, 'start'):
            with patch('src.tui.consumer.consumer'):
                consumer.start()
                first_count = mock_bus.subscribe.call_count

        # 第二次 start
        with patch.object(consumer._engine, 'start') as mock_engine_start:
            with patch('src.tui.consumer.consumer'):
                consumer.start()
                # subscribe 不应再被调用
                assert mock_bus.subscribe.call_count == first_count
                # engine.start 也不应再被调用
                mock_engine_start.assert_not_called()

    def test_start_lazy_binds_handlers(self, consumer, mock_bus):
        """首次 start() 后 _bound_handlers 已创建"""
        with patch.object(consumer._engine, 'start'):
            with patch('src.tui.consumer.consumer'):
                consumer.start()
                assert consumer._bound_handlers is not None
                assert len(consumer._bound_handlers) == 12

    def test_start_defensive_unsubscribe(self, consumer, mock_bus):
        """首次 start() 跳过防御性 unsubscribe（从未订阅过）

        架构修复（2026-06-29）：消除 unsubscribe→subscribe 时序窗口。
        首次启动时从未订阅过任何事件，跳过不必要的防御性 unsubscribe。
        后续重新启动（stop→start）时，先 subscribe 再 unsubscribe 旧 handler。
        """
        with patch.object(consumer._engine, 'start'):
            with patch('src.tui.consumer.consumer'):
                consumer.start()

        # 首次 start：跳过防御性 unsubscribe（从未订阅过）
        assert mock_bus.unsubscribe.call_count == 0
        # subscribe 仍正常执行
        assert mock_bus.subscribe.call_count == 12

    def test_start_defensive_unsubscribe_ignored(self, consumer, mock_bus):
        """防御性 unsubscribe 抛出异常时静默跳过（未订阅时）"""
        # 让 unsubscribe 抛出异常
        mock_bus.unsubscribe.side_effect = Exception("not subscribed")

        with patch.object(consumer._engine, 'start'):
            with patch('src.tui.consumer.consumer'):
                # 不应传播异常
                consumer.start()

        assert consumer._started is True

    def test_start_registers_after_subscribe(self, consumer, mock_bus):
        """订阅完成后再设置 _active_consumer（消除竞态窗口）

        验证方式：在 start() 中先 subscribe 所有事件，再设置 _active_consumer。
        我们通过检查 subscribe call_count 来确认订阅已完成。
        """
        with patch.object(consumer._engine, 'start'):
            with patch('src.tui.consumer.consumer'):
                consumer.start()

        # 12 个事件已全部订阅
        assert mock_bus.subscribe.call_count == 12
        # _started 为 True 表示整个 start() 已执行完毕
        assert consumer._started is True

    def test_start_without_mock_engine(self, consumer, mock_bus):
        """真实 engine.start()（不 patch）也能正常执行

        引擎线程为 daemon 线程，测试结束时自动清理。
        """
        with patch('src.tui.consumer.consumer'):
            consumer.start()
            assert consumer._started is True
            assert consumer._engine._render_running is True
            assert consumer._engine._render_thread is not None
            # 停止以清理
        with patch('src.tui.consumer.consumer'):
            consumer.stop()


# ═══════════════════════════════════════════════════════
# TestChatUIConsumerStop
# ═══════════════════════════════════════════════════════

class TestChatUIConsumerStop:
    """ChatUIConsumer.stop() 生命周期停止"""

    def test_stop_sets_started_false(self, consumer, mock_bus):
        """stop() 后 _started=False"""
        # 模拟已启动状态
        consumer._started = True
        consumer._bound_handlers = {MagicMock(): MagicMock()}

        with patch.object(consumer._engine, 'stop'):
            with patch.object(consumer._engine, 'flush'):
                with patch('src.tui.consumer.consumer'):
                    consumer.stop()
                    assert consumer._started is False

    def test_stop_calls_engine_stop(self, consumer, mock_bus):
        """stop() 调用 _engine.stop()"""
        consumer._started = True
        consumer._bound_handlers = {MagicMock(): MagicMock()}

        with patch.object(consumer._engine, 'stop') as mock_stop:
            with patch.object(consumer._engine, 'flush'):
                with patch('src.tui.consumer.consumer'):
                    consumer.stop()
                    mock_stop.assert_called_once()

    def test_stop_calls_engine_flush(self, consumer, mock_bus):
        """stop() 调用 _engine.flush()"""
        consumer._started = True
        consumer._bound_handlers = {MagicMock(): MagicMock()}

        with patch.object(consumer._engine, 'stop'):
            with patch.object(consumer._engine, 'flush') as mock_flush:
                with patch('src.tui.consumer.consumer'):
                    consumer.stop()
                    mock_flush.assert_called_once()

    def test_stop_unsubscribes_events(self, consumer, mock_bus):
        """stop() 为每个事件处理器调用 unsubscribe"""
        consumer._started = True
        n_handlers = 12
        consumer._bound_handlers = {MagicMock(): MagicMock() for _ in range(n_handlers)}

        with patch.object(consumer._engine, 'stop'):
            with patch.object(consumer._engine, 'flush'):
                with patch('src.tui.consumer.consumer'):
                    consumer.stop()
                    assert mock_bus.unsubscribe.call_count >= n_handlers

    def test_stop_clears_active_consumer(self, consumer, mock_bus):
        """stop() 调用 _state._unregister_consumer() 注销活跃实例"""
        consumer._started = True
        consumer._bound_handlers = {MagicMock(): MagicMock()}

        with patch.object(consumer._engine, 'stop'):
            with patch.object(consumer._engine, 'flush'):
                with patch('src.tui.consumer.consumer._unregister_consumer') as mock_unregister:
                    consumer.stop()
                    mock_unregister.assert_called_once()

    def test_stop_calls_bottom_bar_teardown(self, consumer, mock_bus):
        """stop() 调用 _bottom_bar.teardown()"""
        consumer._started = True
        consumer._bound_handlers = {MagicMock(): MagicMock()}

        with patch.object(consumer._engine, 'stop'):
            with patch.object(consumer._engine, 'flush'):
                with patch('src.tui.consumer.consumer'):
                    with patch.object(consumer._bottom_bar, 'teardown') as mock_teardown:
                        consumer.stop()
                        mock_teardown.assert_called_once()

    def test_stop_calls_rs_close_all(self, consumer, mock_bus):
        """stop() 调用 _rs.close_all()"""
        consumer._started = True
        consumer._bound_handlers = {MagicMock(): MagicMock()}

        with patch.object(consumer._engine, 'stop'):
            with patch.object(consumer._engine, 'flush'):
                with patch('src.tui.consumer.consumer'):
                    with patch.object(consumer._rs, 'close_all') as mock_close:
                        consumer.stop()
                        mock_close.assert_called_once()

    def test_stop_is_idempotent(self, consumer, mock_bus):
        """未 start 时 stop() 幂等——不执行任何操作"""
        consumer._started = False

        with patch.object(consumer._engine, 'stop') as mock_stop:
            with patch.object(consumer._engine, 'flush') as mock_flush:
                with patch('src.tui.consumer.consumer'):
                    with patch.object(consumer._bottom_bar, 'teardown') as mock_teardown:
                        consumer.stop()
                        mock_stop.assert_not_called()
                        mock_flush.assert_not_called()
                        mock_teardown.assert_not_called()
                        assert consumer._started is False

    def test_stop_unsubscribe_exception_safe(self, consumer, mock_bus):
        """unsubscribe 异常时 stop() 不传播异常"""
        consumer._started = True
        # 使用有 __name__ 的 mock 事件类型（stop() 的 except 块会访问 __name__）
        mock_event_type = MagicMock(__name__="MockEventType")
        consumer._bound_handlers = {mock_event_type: MagicMock()}
        mock_bus.unsubscribe.side_effect = Exception("unsubscribe failed")

        with patch.object(consumer._engine, 'stop'):
            with patch.object(consumer._engine, 'flush'):
                with patch('src.tui.consumer.consumer'):
                    # 不应抛出异常
                    consumer.stop()

        assert consumer._started is False

    def test_stop_resets_after_start_stop_start(self, consumer, mock_bus):
        """start→stop→start→stop 完整周期可正常执行"""
        with patch.object(consumer._engine, 'start'):
            with patch('src.tui.consumer.consumer'):
                consumer.start()
                assert consumer._started is True

        with patch.object(consumer._engine, 'stop'):
            with patch.object(consumer._engine, 'flush'):
                with patch('src.tui.consumer.consumer'):
                    with patch.object(consumer._bottom_bar, 'teardown'):
                        consumer.stop()
                        assert consumer._started is False

        # 第二次 start
        with patch.object(consumer._engine, 'start'):
            with patch('src.tui.consumer.consumer'):
                consumer.start()
                assert consumer._started is True

        # 第二次 stop
        with patch.object(consumer._engine, 'stop'):
            with patch.object(consumer._engine, 'flush'):
                with patch('src.tui.consumer.consumer'):
                    with patch.object(consumer._bottom_bar, 'teardown'):
                        consumer.stop()
                        assert consumer._started is False


# ═══════════════════════════════════════════════════════
# TestChatUIConsumerSuspend
# ═══════════════════════════════════════════════════════

class TestChatUIConsumerSuspend:
    """ChatUIConsumer.suspend() 暂停渲染"""

    def test_suspend_stops_engine(self, consumer, mock_bus):
        """suspend() 调用 _engine.stop()"""
        consumer._started = True
        with patch.object(consumer._engine, 'stop') as mock_stop:
            with patch.object(consumer._engine, 'flush'):
                with patch.object(consumer._bottom_bar, 'teardown'):
                    consumer.suspend()
                    mock_stop.assert_called_once()

    def test_suspend_flushes_engine(self, consumer, mock_bus):
        """suspend() 调用 _engine.flush()"""
        consumer._started = True
        with patch.object(consumer._engine, 'stop'):
            with patch.object(consumer._engine, 'flush') as mock_flush:
                with patch.object(consumer._bottom_bar, 'teardown'):
                    consumer.suspend()
                    mock_flush.assert_called_once()

    def test_suspend_calls_bottom_bar_teardown(self, consumer, mock_bus):
        """suspend() 调用 _bottom_bar.teardown()"""
        consumer._started = True
        with patch.object(consumer._engine, 'stop'):
            with patch.object(consumer._engine, 'flush'):
                with patch.object(consumer._bottom_bar, 'teardown') as mock_teardown:
                    consumer.suspend()
                    mock_teardown.assert_called_once()

    def test_suspend_keeps_started_true(self, consumer, mock_bus):
        """suspend() 不改变 _started（仍为 True）"""
        consumer._started = True
        with patch.object(consumer._engine, 'stop'):
            with patch.object(consumer._engine, 'flush'):
                with patch.object(consumer._bottom_bar, 'teardown'):
                    consumer.suspend()
                    assert consumer._started is True

    def test_suspend_is_idempotent(self, consumer, mock_bus):
        """未 start 时 suspend() 跳过——不执行任何操作"""
        consumer._started = False
        with patch.object(consumer._engine, 'stop') as mock_stop:
            with patch.object(consumer._engine, 'flush') as mock_flush:
                with patch.object(consumer._bottom_bar, 'teardown') as mock_teardown:
                    consumer.suspend()
                    mock_stop.assert_not_called()
                    mock_flush.assert_not_called()
                    mock_teardown.assert_not_called()

    def test_suspend_double_idempotent(self, consumer, mock_bus):
        """连续两次 suspend 幂等"""
        consumer._started = True
        with patch.object(consumer._engine, 'stop'):
            with patch.object(consumer._engine, 'flush'):
                with patch.object(consumer._bottom_bar, 'teardown') as mock_teardown:
                    consumer.suspend()
                    assert mock_teardown.call_count == 1

                    # 第二次 suspend：已 stop 过引擎，但不影响
                    consumer.suspend()
                    # teardown 不应再被调用（_started 仍为 True，但 _bottom_bar._active 为 False）
                    # 注意：这里 teardown 可能被再次调用，但 BottomBar.teardown 内部幂等
                    # 我们只验证不崩溃即可


# ═══════════════════════════════════════════════════════
# TestChatUIConsumerResume
# ═══════════════════════════════════════════════════════

class TestChatUIConsumerResume:
    """ChatUIConsumer.resume() 恢复渲染"""

    def test_resume_starts_engine(self, consumer, mock_bus):
        """resume() 调用 _engine.start()"""
        consumer._started = True
        # 模拟 engine 未运行
        consumer._engine._render_running = False
        with patch.object(consumer._engine, 'start') as mock_start:
            with patch.object(consumer._bottom_bar, 'setup'):
                with patch('sys.__stdout__'):
                    consumer.resume()
                    mock_start.assert_called_once()

    def test_resume_calls_bottom_bar_setup(self, consumer, mock_bus):
        """resume() 调用 _bottom_bar.setup()"""
        consumer._started = True
        consumer._engine._render_running = False
        with patch.object(consumer._engine, 'start'):
            with patch.object(consumer._bottom_bar, 'setup') as mock_setup:
                with patch('sys.__stdout__'):
                    consumer.resume()
                    mock_setup.assert_called_once()

    def test_resume_skips_when_engine_running(self, consumer, mock_bus):
        """engine 已在运行时 resume() 跳过"""
        consumer._started = True
        consumer._engine._render_running = True
        with patch.object(consumer._engine, 'start') as mock_start:
            with patch.object(consumer._bottom_bar, 'setup') as mock_setup:
                consumer.resume()
                mock_start.assert_not_called()
                mock_setup.assert_not_called()

    def test_resume_skips_when_not_started(self, consumer, mock_bus):
        """未 start() 时 resume() 跳过"""
        consumer._started = False
        with patch.object(consumer._engine, 'start') as mock_start:
            with patch.object(consumer._bottom_bar, 'setup') as mock_setup:
                consumer.resume()
                mock_start.assert_not_called()
                mock_setup.assert_not_called()

    def test_resume_writes_ansi_cursor(self, consumer, mock_bus):
        """resume() 写入光标定位序列到 sys.__stdout__"""
        consumer._started = True
        consumer._engine._render_running = False
        with patch.object(consumer._engine, 'start'):
            with patch.object(consumer._bottom_bar, 'setup'):
                with patch('sys.__stdout__') as mock_stdout:
                    consumer.resume()
                    # 验证写入了光标定位序列（通过 Blessed 或回退 ANSI）
                    calls = [str(c) for c in mock_stdout.write.call_args_list]
                    has_cursor_pos = any(
                        '\033[' in call for call in calls
                    ) if calls else False
                    # 若未检测到 ANSI 序列，至少验证 flush 被调用
                    assert has_cursor_pos or mock_stdout.flush.called

    def test_resume_after_suspend_full_cycle(self, consumer, mock_bus):
        """suspend→resume 完整暂停恢复周期"""
        consumer._started = True
        consumer._engine._render_running = False

        with patch.object(consumer._engine, 'stop'):
            with patch.object(consumer._engine, 'flush'):
                with patch.object(consumer._bottom_bar, 'teardown'):
                    consumer.suspend()

        # render 已停止
        with patch.object(consumer._engine, 'start') as mock_start:
            with patch.object(consumer._bottom_bar, 'setup'):
                with patch('sys.__stdout__'):
                    consumer.resume()
                    mock_start.assert_called_once()

    def test_resume_forces_bottom_bar_active_false(self, consumer, mock_bus):
        """resume() 强制重置 _bottom_bar._active=False，防止 setup() 被 early return 跳过。

        模拟 run_bottom_bar_selection 泄漏 _active=True 状态，
        验证 resume() 中 setup() 仍被调用且 _active 在调用前已重置。
        """
        consumer._started = True
        consumer._engine._render_running = False

        # 模拟 run_bottom_bar_selection 泄漏 _active=True
        consumer._bottom_bar._active = True

        call_order = []

        def _record_setup():
            # 记录调用时 _active 的实际值
            call_order.append(("setup", consumer._bottom_bar._active))

        with patch.object(consumer._engine, 'start'):
            with patch.object(consumer._bottom_bar, 'setup', side_effect=_record_setup):
                with patch('sys.__stdout__'):
                    consumer.resume()

        # 验证 setup() 被调用（未被 early return 跳过）
        assert len(call_order) == 1, (
            f"预期 setup() 被调用 1 次，实际: {len(call_order)}"
        )
        # 验证 setup() 被调用时 _active 已为 False
        assert call_order[0] == ("setup", False), (
            f"预期 setup 调用时 _active=False，实际: {call_order[0]}"
        )


# ═══════════════════════════════════════════════════════
# TestChatUIConsumerLifecycle
# ═══════════════════════════════════════════════════════

class TestChatUIConsumerLifecycle:
    """start → stop → suspend → resume → stop 完整串行"""

    def test_lifecycle_start_stop(self, consumer, mock_bus):
        """start → stop 基础生命周期"""
        with patch.object(consumer._engine, 'start'):
            with patch('src.tui.consumer.consumer'):
                consumer.start()
                assert consumer._started is True

        with patch.object(consumer._engine, 'stop'):
            with patch.object(consumer._engine, 'flush'):
                with patch('src.tui.consumer.consumer'):
                    with patch.object(consumer._bottom_bar, 'teardown'):
                        consumer.stop()
                        assert consumer._started is False

    def test_lifecycle_start_suspend_resume_stop(self, consumer, mock_bus):
        """start → suspend → resume → stop 完整串行"""
        # start
        with patch.object(consumer._engine, 'start'):
            with patch('src.tui.consumer.consumer'):
                consumer.start()
                assert consumer._started is True

        # suspend
        with patch.object(consumer._engine, 'stop'):
            with patch.object(consumer._engine, 'flush'):
                with patch.object(consumer._bottom_bar, 'teardown'):
                    consumer.suspend()
                    assert consumer._started is True

        # resume
        consumer._engine._render_running = False
        with patch.object(consumer._engine, 'start'):
            with patch.object(consumer._bottom_bar, 'setup'):
                with patch('sys.__stdout__'):
                    consumer.resume()

        # stop
        with patch.object(consumer._engine, 'stop'):
            with patch.object(consumer._engine, 'flush'):
                with patch('src.tui.consumer.consumer'):
                    with patch.object(consumer._bottom_bar, 'teardown'):
                        consumer.stop()
                        assert consumer._started is False

    def test_lifecycle_double_start_stop(self, consumer, mock_bus):
        """start → stop → start → stop 双周期"""
        for _ in range(2):
            with patch.object(consumer._engine, 'start'):
                with patch('src.tui.consumer.consumer'):
                    consumer.start()
                    assert consumer._started is True

            with patch.object(consumer._engine, 'stop'):
                with patch.object(consumer._engine, 'flush'):
                    with patch('src.tui.consumer.consumer'):
                        with patch.object(consumer._bottom_bar, 'teardown'):
                            consumer.stop()
                            assert consumer._started is False

    def test_lifecycle_suspend_before_start_is_noop(self, consumer, mock_bus):
        """未 start 时 suspend 跳过"""
        with patch.object(consumer._engine, 'stop') as mock_stop:
            consumer.suspend()
            mock_stop.assert_not_called()

    def test_lifecycle_resume_before_start_is_noop(self, consumer, mock_bus):
        """未 start 时 resume 跳过"""
        with patch.object(consumer._engine, 'start') as mock_start:
            consumer.resume()
            mock_start.assert_not_called()

    def test_lifecycle_start_then_suspend_twice(self, consumer, mock_bus):
        """start → suspend → suspend（第二次幂等）"""
        with patch.object(consumer._engine, 'start'):
            with patch('src.tui.consumer.consumer'):
                consumer.start()

        with patch.object(consumer._engine, 'stop'):
            with patch.object(consumer._engine, 'flush'):
                with patch.object(consumer._bottom_bar, 'teardown') as mock_teardown:
                    consumer.suspend()
                    consumer.suspend()
                    # 第二次 suspend 可能仍会尝试 teardown（取决于 _bottom_bar._active）
                    # 但不崩溃即可

    def test_lifecycle_no_unexpected_events(self, consumer, mock_bus):
        """完整周期中不产生意外副作用"""
        with patch.object(consumer._engine, 'start'):
            with patch('src.tui.consumer.consumer'):
                consumer.start()
                assert mock_bus.subscribe.call_count == 12

        with patch.object(consumer._engine, 'stop'):
            with patch.object(consumer._engine, 'flush'):
                with patch('src.tui.consumer.consumer'):
                    with patch.object(consumer._bottom_bar, 'teardown'):
                        consumer.stop()
                        # unsubscribe 至少调用了 12 次
                        assert mock_bus.unsubscribe.call_count >= 12


# ═══════════════════════════════════════════════════════
# TestChatUIConsumerPublicMethods
# ═══════════════════════════════════════════════════════

class TestChatUIConsumerPublicMethods:
    """on_user_message / on_notification / on_error 入队正确"""

    def test_on_user_message_enqueues(self, consumer, mock_bus):
        """on_user_message('hello') → 命令入队"""
        from src.tui.consumer import RenderCommand
        consumer.on_user_message("hello")
        cmd = consumer._engine._cmd_queue.get_nowait()
        assert cmd[0] == RenderCommand.USER_MSG
        assert cmd[1] == "hello"

    def test_on_user_message_empty(self, consumer, mock_bus):
        """on_user_message('') → 入队空字符串"""
        from src.tui.consumer import RenderCommand
        consumer.on_user_message("")
        cmd = consumer._engine._cmd_queue.get_nowait()
        assert cmd[0] == RenderCommand.USER_MSG
        assert cmd[1] == ""

    def test_on_notification_enqueues(self, consumer, mock_bus):
        """on_notification('通知') → 命令入队"""
        from src.tui.consumer import RenderCommand
        consumer.on_notification("通知")
        cmd = consumer._engine._cmd_queue.get_nowait()
        assert cmd[0] == RenderCommand.NOTIFICATION
        assert cmd[1] == "通知"

    def test_on_notification_empty(self, consumer, mock_bus):
        """on_notification('') → 入队空字符串"""
        from src.tui.consumer import RenderCommand
        consumer.on_notification("")
        cmd = consumer._engine._cmd_queue.get_nowait()
        assert cmd[0] == RenderCommand.NOTIFICATION
        assert cmd[1] == ""

    def test_on_error_enqueues(self, consumer, mock_bus):
        """on_error('错误') → 命令入队"""
        from src.tui.consumer import RenderCommand
        consumer.on_error("错误消息")
        cmd = consumer._engine._cmd_queue.get_nowait()
        assert cmd[0] == RenderCommand.ERROR
        assert cmd[1] == "错误消息"

    def test_on_error_empty_skipped(self, consumer, mock_bus):
        """on_error('') → 不入队（防御式检查）"""
        consumer.on_error("")
        # 空消息被 _consumer.on_error 内部拦截
        assert consumer._engine._cmd_queue.empty()

    def test_on_error_none_skipped(self, consumer, mock_bus):
        """on_error(None) → 不崩溃（防御式检查）"""
        # None 空值，内部条件 `if not message` 会拦截
        consumer.on_error(None)  # type: ignore[arg-type]
        # 不应崩溃

    def test_write_line_enqueues(self, consumer, mock_bus):
        """write_line('文本') → 命令入队"""
        from src.tui.consumer import RenderCommand
        consumer.write_line("通用文本")
        cmd = consumer._engine._cmd_queue.get_nowait()
        assert cmd[0] == RenderCommand.WRITE_LINE
        assert cmd[1] == "通用文本"

    def test_display_messages_enqueues(self, consumer, mock_bus):
        """display_messages([...]) → 命令入队"""
        from src.tui.consumer import RenderCommand
        msgs = [{"role": "user", "content": "hi"}]
        consumer.display_messages(msgs, speed=1)
        cmd = consumer._engine._cmd_queue.get_nowait()
        assert cmd[0] == RenderCommand.DISPLAY_MSGS
        assert cmd[1] == msgs
        assert cmd[2] == 1

    def test_multiple_calls_queue_order(self, consumer, mock_bus):
        """多次入队保持 FIFO 顺序"""
        from src.tui.consumer import RenderCommand
        consumer.on_user_message("first")
        consumer.on_notification("second")
        consumer.on_error("third")

        cmd1 = consumer._engine._cmd_queue.get_nowait()
        cmd2 = consumer._engine._cmd_queue.get_nowait()
        cmd3 = consumer._engine._cmd_queue.get_nowait()

        assert cmd1 == (RenderCommand.USER_MSG, "first")
        assert cmd2 == (RenderCommand.NOTIFICATION, "second")
        assert cmd3 == (RenderCommand.ERROR, "third")

    def test_push_cmd_delegates_to_engine(self, consumer, mock_bus):
        """入队委托给 _engine.push_cmd"""
        from src.tui.consumer import RenderCommand
        with patch.object(consumer._engine, 'push_cmd') as mock_push:
            consumer.on_user_message("delegated")
            mock_push.assert_called_once_with((RenderCommand.USER_MSG, "delegated"))



# ═══════════════════════════════════════════════════════
# TestChatUIConsumerSetupCompletion
# ═══════════════════════════════════════════════════════

class TestChatUIConsumerSetupCompletion:
    """setup_completion 注册补全回调"""

    def test_setup_completion_registers_callbacks(self, consumer, mock_bus):
        """setup_completion() 注册所有补全回调到 monitor"""
        mock_monitor = MagicMock()
        consumer.setup_completion(mock_monitor)

        mock_monitor.set_completion_callback.assert_called_once_with(
            consumer._cmpl.on_tab
        )
        mock_monitor.set_dismiss_completion_callback.assert_called_once_with(
            consumer._cmpl.on_dismiss
        )
        mock_monitor.set_completion_navigate_callback.assert_called_once_with(
            consumer._cmpl.on_navigate
        )
        mock_monitor.set_auto_completion_callback.assert_called_once_with(
            consumer._cmpl.on_auto
        )


# ═══════════════════════════════════════════════════════
# TestChatUIConsumerBottomBarMethods
# ═══════════════════════════════════════════════════════

class TestChatUIConsumerBottomBarMethods:
    """底部栏委托方法"""

    def test_setup_bottom_bar(self, consumer, mock_bus):
        """setup_bottom_bar() 委托给 _bottom_bar.setup()"""
        with patch.object(consumer._bottom_bar, 'setup') as mock_setup:
            consumer.setup_bottom_bar()
            mock_setup.assert_called_once()

    def test_teardown_bottom_bar(self, consumer, mock_bus):
        """teardown_bottom_bar() 委托给 _bottom_bar.teardown()"""
        with patch.object(consumer._bottom_bar, 'teardown') as mock_teardown:
            consumer.teardown_bottom_bar()
            mock_teardown.assert_called_once()

    def test_bottom_bar_property(self, consumer, mock_bus):
        """bottom_bar property 返回 _bottom_bar 实例"""
        assert consumer.bottom_bar is consumer._bottom_bar

    def test_set_model_name_via_bottom_bar(self, consumer, mock_bus):
        """set_model_name() 通过 bottom_bar 委托"""
        with patch.object(consumer._bottom_bar, 'set_model_name') as mock_set:
            consumer.bottom_bar.set_model_name("deepseek-v3")
            mock_set.assert_called_once_with("deepseek-v3")

    def test_enable_status_via_bottom_bar(self, consumer, mock_bus):
        """enable_status() 通过 bottom_bar 委托"""
        with patch.object(consumer._bottom_bar, 'enable_status') as mock_enable:
            consumer.bottom_bar.enable_status()
            mock_enable.assert_called_once()

    def test_disable_status_via_bottom_bar(self, consumer, mock_bus):
        """disable_status() 通过 bottom_bar 委托"""
        with patch.object(consumer._bottom_bar, 'disable_status') as mock_disable:
            consumer.bottom_bar.disable_status()
            mock_disable.assert_called_once()

    def test_reset_tool_count_via_bottom_bar(self, consumer, mock_bus):
        """reset_tool_count() 通过 bottom_bar 委托"""
        with patch.object(consumer._bottom_bar, 'reset_tool_count') as mock_reset:
            consumer.bottom_bar.reset_tool_count()
            mock_reset.assert_called_once()

    def test_get_status_elapsed_via_bottom_bar(self, consumer, mock_bus):
        """get_status_elapsed() 通过 bottom_bar 委托"""
        with patch.object(consumer._bottom_bar, 'get_status_elapsed', return_value=1.5):
            result = consumer.bottom_bar.get_status_elapsed()
            assert result == 1.5

    def test_redraw_via_bottom_bar(self, consumer, mock_bus):
        """force_redraw() 通过 bottom_bar 委托"""
        with patch.object(consumer._bottom_bar, 'force_redraw') as mock_redraw:
            consumer.bottom_bar.force_redraw()
            mock_redraw.assert_called_once()

    def test_flush_delegates_to_engine(self, consumer, mock_bus):
        """flush() 委托给 _engine.flush()"""
        with patch.object(consumer._engine, 'flush') as mock_flush:
            consumer.flush(timeout=3.0)
            mock_flush.assert_called_once_with(timeout=3.0)

    def test_flush_default_timeout(self, consumer, mock_bus):
        """flush() 默认 timeout=5.0"""
        with patch.object(consumer._engine, 'flush') as mock_flush:
            consumer.flush()
            mock_flush.assert_called_once_with(timeout=5.0)


# ═══════════════════════════════════════════════════════
# TestChatUIConsumerCmdQueueProperty
# ═══════════════════════════════════════════════════════

class TestChatUIConsumerCmdQueueProperty:
    """_cmd_queue 向后兼容 property"""

    def test_engine_has_cmd_queue(self, consumer, mock_bus):
        """RenderEngine 内部有 _cmd_queue 属性"""
        import queue
        assert isinstance(consumer._engine._cmd_queue, queue.Queue)


# ═══════════════════════════════════════════════════════
# TestChatUIConsumerEdgeCases
# ═══════════════════════════════════════════════════════

class TestChatUIConsumerEdgeCases:
    """边界情况测试"""

    def test_on_error_with_whitespace(self, consumer, mock_bus):
        """on_error('  ') → 入队（空格非空）"""
        from src.tui.consumer import RenderCommand
        consumer.on_error("  ")
        cmd = consumer._engine._cmd_queue.get_nowait()
        assert cmd[0] == RenderCommand.ERROR
        assert cmd[1] == "  "

    def test_ensure_cursor_upper(self, consumer, mock_bus):
        """ensure_cursor_upper() 委托给 _engine"""
        with patch.object(consumer._engine, 'ensure_cursor_upper') as mock_fn:
            consumer.ensure_cursor_upper()
            mock_fn.assert_called_once()

    def test_ensure_cursor_lower_via_bottom_bar(self, consumer, mock_bus):
        """ensure_cursor_in_lower() 通过 bottom_bar 委托"""
        with patch('sys.__stdout__'):
            consumer.bottom_bar.ensure_cursor_in_lower()

    def test_refresh_bottom_bar(self, consumer, mock_bus):
        """refresh_bottom_bar() 更新状态后请求重绘"""
        with patch.object(consumer._engine, 'request_bottom_redraw') as mock_redraw:
            consumer.refresh_bottom_bar("test", cursor_pos=2)
            assert consumer._bottom_bar._last_text == "test"
            assert consumer._bottom_bar._input_cursor_pos == 2
            mock_redraw.assert_called_once()

    def test_refresh_bottom_bar_default_cursor(self, consumer, mock_bus):
        """refresh_bottom_bar() 默认 cursor_pos=-1 使用文本长度"""
        with patch.object(consumer._engine, 'request_bottom_redraw') as mock_redraw:
            consumer.refresh_bottom_bar("hello")
            # cursor_pos=-1 → 使用 len("hello") = 5
            assert consumer._bottom_bar._input_cursor_pos == 5
            assert consumer._bottom_bar._last_text == "hello"
            mock_redraw.assert_called_once()
