"""集成测试：Input → InkSession 光标定位（ink 非全屏流动模型）。

验证 InkSession._position_cursor 经 input-area fiber 的 layout_box +
_compute_cursor_visual_pos 计算光标位置。
"""

from __future__ import annotations

import io
import os
import time
import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path

from src.tui.input import Input
from src.tui.ink.session import InkSession
from src.tui.app.model import AppModel
from src.tui.app.app import build_app_element
from src.tui._input import _compute_cursor_visual_pos


class TestInputBottomBarEngineIntegration:
    """验证 Input → InkSession 的光标定位。"""

    @pytest.fixture
    def mock_width_cache(self):
        """Mock TerminalWidthCache。"""
        cache = MagicMock()
        cache.get_width.return_value = 80
        cache.get_height.return_value = 24
        return cache

    @pytest.fixture
    def mock_input(self, mock_width_cache, tmp_path):
        """创建 Input 实例。"""
        fd = os.open("/dev/null", os.O_RDONLY)
        try:
            inp = Input(
                fd=fd,
                history_file=tmp_path / "test_history",
                term_width_cache=mock_width_cache,
            )
            yield inp
        finally:
            os.close(fd)

    def test_engine_injected_input_processes_stdin(self):
        """回归：装配后 InkSession._input 已注入，渲染循环可读取 stdin。

        旧 bug：_assembly 未调用 session.set_input(input_instance) →
        _phase_process_input() 恒空转 → 用户无法输入。
        """
        import os
        import select
        import sys as _sys

        class _FakeStdin:
            def fileno(self):
                return 0

        with patch.object(_sys, "stdin", _FakeStdin()):
            from src.tui._assembly import TuiAssembly
            result = TuiAssembly.assemble()

        assert result.engine._input is result.input_instance
        r_fd, w_fd = os.pipe()
        try:
            inp = result.input_instance
            inp._fd = r_fd
            inp.start_io()
            os.write(w_fd, b"hello")
            ready, _, _ = select.select([r_fd], [], [], 2.0)
            assert ready
            result.engine._phase_process_input()
            result.engine.flush(timeout=3.0)
            assert result.rs.input_text == "hello"
        finally:
            os.close(w_fd)
            os.close(r_fd)

    def test_engine_position_cursor_finds_input_fiber(self, mock_width_cache):
        """InkSession._position_cursor 找到 input-area fiber 并计算光标。"""
        model = AppModel()
        model.input_text = "test input"
        model.input_cursor = 5
        session = InkSession(
            model=model,
            apply_cmd=None,
            build_tree=build_app_element,
            width_cache=mock_width_cache,
            stream=io.StringIO(),
        )
        session._render_frame()
        # 渲染后光标已写入流（place_cursor）
        assert session._ink_renderer.cursor_row >= 1

    def test_cursor_visual_pos_consistency(self):
        """_compute_cursor_visual_pos 多行输入光标一致。"""
        text = "hello world"
        vis_row, vis_col = _compute_cursor_visual_pos(text, 11, 76)
        assert vis_row == 0
        assert vis_col == 11
        # 多行：第二行光标（"line one\n" 占 9 字符，光标 14 → 第二行第 5 列）
        multi = "line one\nline two"
        row2, col2 = _compute_cursor_visual_pos(multi, 14, 76)
        assert row2 == 1
        assert col2 == 5

    def test_refresh_bottom_bar_updates_input_state(self, mock_input):
        """验证 ChatUIConsumer.refresh_bottom_bar 同步更新 Input 状态。"""
        # 创建最小化的 components
        bb = MagicMock()
        engine = MagicMock()
        components = {
            'rs': MagicMock(),
            'bottom_bar': bb,
            'tui_renderer': MagicMock(),
            'engine': engine,
            'dispatcher': MagicMock(),
            'cmpl_handler': MagicMock(),
            'input': mock_input,
        }

        from src.tui._consumer import ChatUIConsumer
        consumer = ChatUIConsumer.for_testing(components)

        consumer.refresh_bottom_bar("new text", 5)

        # ink 模型：输入状态注入 engine（InkSession.update_input → AppModel）
        engine.update_input.assert_called_once_with("new text", 5)


# ═══════════════════════════════════════════════════════════
# 方向A 步骤2：TuiInputOrchestrator 事件化等待回归测试（新增，2026-07-31）
# ═══════════════════════════════════════════════════════════

class TestInputOrchestratorEventWaiting:
    """TuiInputOrchestrator.wait_for_user_input 事件化（方向A 步骤2）。"""

    @pytest.fixture
    def inp(self, tmp_path):
        """创建 Input 实例（P2-7：fixture 确保 fd 关闭）。"""
        fd = os.open("/dev/null", os.O_RDONLY)
        try:
            yield Input(fd=fd, history_file=tmp_path / "test_history")
        finally:
            os.close(fd)

    @pytest.fixture
    def monitor(self):
        m = MagicMock()
        m.is_alive = True
        return m

    def test_wait_until_ready_event_regression(self, inp, monitor, tmp_path):
        """Enter 提交后 wait_for_user_input 立即返回（<100ms，验证无 50ms 轮询延迟）。"""
        import threading
        import time

        from src.tui._input_orchestrator import TuiInputOrchestrator
        orch = TuiInputOrchestrator(inp)

        def submit():
            with patch("src.tui._input._append_to_history_file", return_value=True):
                inp.handle_chars("hello")
                inp._enter()

        t = threading.Thread(target=submit)
        start = time.monotonic()
        t.start()
        text = orch.wait_for_user_input(monitor, timeout=5.0)
        elapsed = time.monotonic() - start
        t.join()
        assert text == "hello"
        assert elapsed < 0.1  # 验证无 50ms 轮询延迟（事件化后立即唤醒）

    def test_wait_for_user_input_timeout_regression(self, inp, monitor):
        """timeout 超时返回空字符串。"""
        from src.tui._input_orchestrator import TuiInputOrchestrator
        orch = TuiInputOrchestrator(inp)
        text = orch.wait_for_user_input(monitor, timeout=0.2)
        assert text == ""

    def test_wait_for_user_input_monitor_death_regression(self, inp):
        """monitor 死亡时抛 RuntimeError（_loop.py 捕获后走恢复逻辑）。"""
        from src.tui._input_orchestrator import TuiInputOrchestrator
        orch = TuiInputOrchestrator(inp)
        dead_monitor = MagicMock()
        dead_monitor.is_alive = False
        with pytest.raises(RuntimeError, match="EscapeMonitor"):
            orch.wait_for_user_input(dead_monitor, timeout=0.5)

    def test_wait_for_user_input_prefill_regression(self, inp, monitor):
        """prefill 注入后缓冲区被预填且可正常提交。"""
        import threading
        import time

        from src.tui._input_orchestrator import TuiInputOrchestrator
        orch = TuiInputOrchestrator(inp)
        result = {}

        def wait_with_prefill():
            with patch("src.tui._input._append_to_history_file", return_value=True):
                result["text"] = orch.wait_for_user_input(
                    monitor, prefill="prefill text", timeout=5.0,
                )

        t = threading.Thread(target=wait_with_prefill)
        t.start()
        # 等待 prefill 注入完成（缓冲区被预填）
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if inp.get_current_text() == "prefill text":
                break
            time.sleep(0.01)
        assert inp.get_current_text() == "prefill text"
        # 提交（模拟用户按 Enter）——patch 上下文在 wait 线程内仍活跃，无磁盘写入
        inp._enter()
        t.join()
        assert result["text"] == "prefill text"


# ═══════════════════════════════════════════════════════════
# 2026-08-01：TuiInputOrchestrator prefill 残留提交兜底恢复回归测试
# ═══════════════════════════════════════════════════════════

class TestInputOrchestratorPrefillResidual:
    """prefill 注入后残留 Enter 提交恢复（editmsg 竞态兜底修复）。

    覆盖场景：LF 在 set_buffer(prefill) 之后才被 render 线程处理、
    _enter() 已提交 prefill 的残余窗口——编排器在 set_buffer 后再次
    get_queued_input() 检查残留提交，若非 None 则重新注入恢复。
    采用可控 mock 序列保证确定性（避免线程时序不确定性）。
    """

    def test_prefill_residual_enter_restored_regression(self, tmp_path):
        """注入 prefill 后残留提交被恢复，prefill 仍在缓冲中。"""
        fd = os.open("/dev/null", os.O_RDONLY)
        try:
            inp = Input(fd=fd, history_file=tmp_path / "history")
            from src.tui._input_orchestrator import TuiInputOrchestrator
            orch = TuiInputOrchestrator(inp)
            monitor = MagicMock()
            monitor.is_alive = True

            # 记录 set_buffer 调用；保留原方法以便真实设置缓冲
            set_buffer_calls = []
            original_set_buffer = inp.set_buffer
            with patch.object(
                inp, "set_buffer",
                side_effect=lambda text: (
                    set_buffer_calls.append(text), original_set_buffer(text),
                )[1],
            ), patch.object(
                inp, "get_queued_input",
                side_effect=iter([None, "prefill text"]).__next__,
            ), patch.object(inp, "wait_until_ready", return_value=False):
                result = orch.wait_for_user_input(
                    monitor, prefill="prefill text", timeout=0.1,
                )

            # 注入 + 恢复各调用一次 set_buffer
            assert set_buffer_calls == ["prefill text", "prefill text"]
            # prefill 仍在缓冲中（恢复后未被消费）
            assert inp.get_current_text() == "prefill text"
            # 超时返回空字符串（用户未输入）
            assert result == ""
        finally:
            os.close(fd)


# ═══════════════════════════════════════════════════════════
# 横切步骤18 — 新输入 kind 全链路（解析→分发→缓冲/补全）
# ═══════════════════════════════════════════════════════════

class TestNewInputKindsChain:
    """横切步骤18 — Alt 组合 / Shift+Tab / Ctrl+R 搜索 / Esc 取消 全链路。

    以 os.pipe 模拟 stdin，验证「原始字节 → InputParser 解析 → InputDispatcher
    分发 → 缓冲编辑/补全回调」整条输入链在新 kind 上连通。
    """

    @pytest.fixture
    def piped_input(self, tmp_path):
        """创建 (Input, 写端 fd) 对：真实 pipe fd 驱动全链路。"""
        r_fd, w_fd = os.pipe()
        try:
            inp = Input(fd=r_fd, history_file=tmp_path / "history")
            yield inp, w_fd
        finally:
            os.close(w_fd)
            os.close(r_fd)

    def test_alt_b_word_left_chain_regression(self, piped_input):
        """Alt+B（ESC b）→ alt_char → 词跳转左（缓冲光标生效）。"""
        inp, w_fd = piped_input
        inp.set_buffer("hello world")
        inp._cursor_pos = len("hello world")
        os.write(w_fd, b"\x1bb")
        time.sleep(0.05)
        inp.process_events()
        # 光标从末尾跳到 "world" 词首之前（"hello " 末尾）
        assert inp._cursor_pos == len("hello ")

    def test_alt_f_word_right_chain_regression(self, piped_input):
        """Alt+F（ESC f）→ alt_char → 词跳转右（缓冲光标生效）。"""
        inp, w_fd = piped_input
        inp.set_buffer("hello world")
        inp._cursor_pos = 0
        os.write(w_fd, b"\x1bf")
        time.sleep(0.05)
        inp.process_events()
        # 词跳转右：从词首跳到下一词首（跳过词与分隔空格，pos 0 → 6）
        assert inp._cursor_pos == 6

    def test_shift_tab_reverse_chain_regression(self, piped_input):
        """Shift+Tab（CSI u 9;2u）→ tab/modifier=2 → 补全反向循环。"""
        inp, w_fd = piped_input
        navigated = []
        inp.set_completion_navigate_callback(
            lambda delta, text: navigated.append((delta, text)) or text
        )
        inp.set_buffer("he")
        os.write(w_fd, b"\x1b[9;2u")
        time.sleep(0.05)
        inp.process_events()
        assert navigated == [(-1, "he")]

    def test_ctrl_r_search_chain_regression(self, piped_input):
        """Ctrl+R（启用配置门控）→ 反向历史搜索进入（状态同步回调触发）。"""
        inp, w_fd = piped_input
        inp.set_reverse_search_enabled(True)
        inp._history = ["hello world", "hello there", "other"]
        states = []
        inp.set_reverse_search_callback(
            lambda query, matches, index, active: states.append(
                (query, matches, index, active)
            )
        )
        inp.set_buffer("hello")
        os.write(w_fd, b"\x12")  # Ctrl+R
        time.sleep(0.05)
        inp.process_events()
        assert inp._buffer_editor.is_search_active() is True
        assert states and states[0][0] == "hello"
        assert states[0][1] == ["hello world", "hello there"]
        assert states[0][2] == 0  # 最近匹配优先

    def test_esc_cancel_input_chain_regression(self, piped_input):
        """Esc（启用配置门控 + 空闲 + 非空缓冲）→ 清空输入（非中断）。"""
        inp, w_fd = piped_input
        inp.set_esc_cancel_input(True)
        inp.set_active_status_callback(lambda: False)
        inp.set_buffer("hello")
        os.write(w_fd, b"\x1b")  # 单独 Esc
        time.sleep(0.1)
        inp.process_events()
        assert inp.get_current_text() == ""
        assert inp.interrupted is False  # 未触发中断标志

    def test_esc_cancel_input_disabled_keeps_interrupt_regression(self, piped_input):
        """Esc（未启用配置门控）→ 保持中断语义（中断标志置位）。"""
        inp, w_fd = piped_input
        inp.set_esc_cancel_input(False)
        inp.set_buffer("hello")
        os.write(w_fd, b"\x1b")
        time.sleep(0.1)
        inp.process_events()
        assert inp.interrupted is True


# ═══════════════════════════════════════════════════════════
# 方向2 — 粘贴文本先问 router（消费则跳过旧路径）
# ═══════════════════════════════════════════════════════════

class TestPasteRouterConsume:
    """方向2 — 粘贴绕过 router 修复：粘贴文本先经 input router 消费判定。"""

    @pytest.fixture
    def piped_input(self, tmp_path):
        """创建 (Input, 写端 fd) 对：真实 pipe fd 驱动粘贴检测。"""
        r_fd, w_fd = os.pipe()
        try:
            inp = Input(fd=r_fd, history_file=tmp_path / "history")
            yield inp, w_fd
        finally:
            os.close(w_fd)
            os.close(r_fd)

    def test_paste_consumed_by_router_skips_handle_chars(self, piped_input):
        """router 返回 True → 粘贴文本不被 handle_chars 处理（缓冲不变）。"""
        inp, w_fd = piped_input
        consumed = []
        inp.set_input_hook_router(lambda ev: consumed.append(ev) or True)
        inp.handle_chars("prefix")
        os.write(w_fd, b"abc")  # 一次性写入模拟粘贴
        time.sleep(0.05)
        inp.process_events()
        assert consumed  # router 收到粘贴 char 事件
        assert inp.get_current_text() == "prefix"  # 粘贴未被插入

    def test_paste_not_consumed_by_router_inserts(self, piped_input):
        """router 返回 False → 粘贴正常插入（零行为变化）。"""
        inp, w_fd = piped_input
        inp.set_input_hook_router(lambda ev: False)
        inp.handle_chars("prefix")
        os.write(w_fd, b"abc")
        time.sleep(0.05)
        inp.process_events()
        assert inp.get_current_text() == "prefixabc"

    def test_paste_router_event_kind_char(self, piped_input):
        """router 收到的粘贴事件为 char 类型且 char 为整段文本。"""
        inp, w_fd = piped_input
        events = []
        inp.set_input_hook_router(lambda ev: events.append(ev) or False)
        os.write(w_fd, b"hello")
        time.sleep(0.05)
        inp.process_events()
        assert events
        ev = events[0]
        assert ev.kind == "char"
        assert ev.char == "hello"
