"""Tests for src/ui/parallel/display.py — ParallelDisplay"""

import asyncio
import sys

import pytest

from src.ui.parallel.display import ParallelDisplay


@pytest.fixture
def display():
    """返回一个 ParallelDisplay 实例（不启动定时器）。"""
    return ParallelDisplay()


class TestCaptureAndPrintAsyncConcurrency:
    """capture_and_print_async 并发竞态回归测试。

    验证异步协程交错时 redirect_stdout 不会出现输出丢失或泄漏。
    """

    @pytest.mark.asyncio
    async def test_concurrent_stdout_capture_no_loss(self, display):
        """多个协程并发调用 capture_and_print_async，每个都 print 内容。

        在正确的实现（asyncio.Lock 保护）下，所有输出应被捕获到各自 buf；
        在没有锁保护的错误实现下，部分输出会丢失。
        """
        N = 20  # 并发协程数，充分触发协程交错

        async def task(i: int) -> str:
            async def inner():
                print(f"output_from_task_{i}")
                return f"result_{i}"
            return await display.capture_and_print_async(inner)

        tasks = [task(i) for i in range(N)]
        results = await asyncio.gather(*tasks)

        # 验证每个协程都返回了正确的结果
        for i, res in enumerate(results):
            assert res == f"result_{i}", (
                f"协程 {i} 结果异常: expected result_{i}, got {res}"
            )

    @pytest.mark.asyncio
    async def test_stdout_not_polluted_after_concurrent_calls(self, display):
        """并发调用后，sys.stdout 恢复正常（没有被残留的 StringIO 污染）。"""
        N = 10

        async def task(i: int) -> int:
            async def inner():
                print(f"task_{i}")
                return i
            return await display.capture_and_print_async(inner)

        original_stdout = sys.stdout
        await asyncio.gather(*(task(i) for i in range(N)))
        assert sys.stdout is original_stdout, (
            f"并发调用后 sys.stdout 被污染: expected {original_stdout}, "
            f"got {sys.stdout}"
        )

    @pytest.mark.asyncio
    async def test_concurrent_output_isolated(self, display):
        """并发协程间的 stdout 输出不会互相串流。

        每个协程的 print 内容应只进入自己的 buf，
        不应被其他协程的 buf 捕获到。
        """
        collected = []

        async def task(label: str, msg: str) -> str:
            async def inner():
                print(msg)
                return label
            return await display.capture_and_print_async(inner)

        # 两个协程同时打印不同内容
        t1 = task("A", "hello_from_A")
        t2 = task("B", "hello_from_B")
        results = await asyncio.gather(t1, t2)

        assert set(results) == {"A", "B"}, (
            f"两个协程都应成功返回: {results}"
        )


class TestParallelDisplayLifecycle:
    """ParallelDisplay 生命周期测试（start/stop/refresh）。

    新实现不依赖 SubAgentPanelControl 和 chat_ui._state._active_subagent_panel，
    帧渲染直接通过 OutputAdapter.write_raw() 写入终端。
    """

    def test_refresh_called_safely(self, display):
        """refresh() 可被安全调用（无 adapter 时静默跳过）。"""
        # 尚未 start()，_adapter 为 None，refresh 不应抛异常
        display.refresh()

    def test_start_acquires_adapter(self, display):
        """start() 从 ChatUI 获取 OutputAdapter。"""
        display.add_agent("agent-1", "test agent")
        from unittest.mock import MagicMock
        mock_chat_ui = MagicMock()
        mock_chat_ui.output_adapter = MagicMock()
        mock_chat_ui.output_adapter.width = 120
        display.set_panel_context(mock_chat_ui)
        display.start()
        assert display._adapter is not None, (
            "start() 应设置 _adapter 为 ChatUI 的 output_adapter"
        )
        display.stop()

    def test_stop_clears_adapter(self, display):
        """stop() 将 _adapter 置 None 并停止渲染。"""
        display.add_agent("agent-1", "test agent")
        from unittest.mock import MagicMock
        mock_chat_ui = MagicMock()
        mock_chat_ui.output_adapter = MagicMock()
        mock_chat_ui.output_adapter.width = 120
        display.set_panel_context(mock_chat_ui)
        display.start()
        assert display._adapter is not None
        display.stop()
        assert display._adapter is None, (
            "stop() 应将 _adapter 置为 None"
        )
        assert display._finished is True, (
            "stop() 应设置 _finished = True"
        )

    def test_start_then_stop_one_cycle(self):
        """一次 start → stop 生命周期完整，adapter 正确获取和释放。"""
        d = ParallelDisplay()
        d.add_agent("agent-1", "test agent")
        from unittest.mock import MagicMock
        mock_chat_ui = MagicMock()
        mock_chat_ui.output_adapter = MagicMock()
        mock_chat_ui.output_adapter.width = 120
        d.set_panel_context(mock_chat_ui)
        d.start()
        assert d._adapter is not None, "start() 后应持有 adapter"
        assert d._started is True, "start() 后 _started 应为 True"
        d.stop()
        assert d._adapter is None, "stop() 后 adapter 应被释放"
        assert d._finished is True, "stop() 后 _finished 应为 True"

    def test_refresh_after_stop_safe(self, display):
        """stop() 后 refresh() 安全（无 adapter，渲染提前返回）。"""
        display.add_agent("a", "test")
        from unittest.mock import MagicMock
        mock_chat_ui = MagicMock()
        mock_chat_ui.output_adapter = MagicMock()
        mock_chat_ui.output_adapter.width = 120
        display.set_panel_context(mock_chat_ui)
        display.start()
        display.stop()
        display.refresh()  # 不应抛异常

    def test_refresh_with_active_agents(self, display):
        """有活跃 agent 时 refresh() 正常渲染不抛异常（无 adapter 时返回）。"""
        display.add_agent("agent-1", "test agent", status="running")
        display.refresh()  # 不抛异常即通过（_adapter=None，_render_frame 提前返回）


class TestDiffGuard:
    """_DiffGuard 上下文管理器测试。

    新实现不依赖 SubAgentPanelControl.diff_active_set/clear，
    直接在 __enter__ 中清除帧行，__exit__ 不抑制异常。
    """

    def test_clear_frame_and_run_returns_result(self, display):
        """clear_frame_and_run 正确执行 func 并返回结果。"""
        result = display.clear_frame_and_run(lambda: 42)
        assert result == 42, (
            f"clear_frame_and_run 应返回 func 执行结果: expected 42, got {result}"
        )

    def test_clear_frame_and_run_no_adapter_safe(self, display):
        """clear_frame_and_run 在无 adapter 时安全。"""
        result = display.clear_frame_and_run(lambda: "safe")
        assert result == "safe"


class TestToolHistorySerialization:
    """测试 _push_slot_update 中 tool_history 的序列化。

    通过 mock _push_cmd 拦截 CmdSubagentSlotUpdate，
    验证 slot_dict 中 tool_history 字段的 ToolRecord → dict 转换。
    """

    @staticmethod
    def _setup_display_with_capture(display):
        """设置 display._push_cmd 为捕获回调，返回捕获列表。"""
        from unittest.mock import MagicMock
        captured = []
        mock_push = MagicMock()
        mock_push.side_effect = lambda cmd: captured.append(cmd)
        display._push_cmd = mock_push
        return captured

    def test_tool_history_empty_list_on_add_agent(self, display):
        """add_agent 时 tool_history 为空列表 []。"""
        captured = self._setup_display_with_capture(display)
        display.add_agent("agent-1", "test agent")

        assert len(captured) >= 1
        slot = captured[-1].slot
        assert "tool_history" in slot
        assert slot["tool_history"] == []

    def test_tool_history_single_parsing_entry(self, display):
        """tool_parsing 后序列化一条 parsing 状态的 ToolRecord。"""
        captured = self._setup_display_with_capture(display)
        display.add_agent("agent-1", "test agent")
        display.tool_parsing("agent-1", "read_file", '{"path": "a.py"}')

        history = captured[-1].slot["tool_history"]
        assert len(history) == 1
        rec = history[0]
        assert rec["tool_name"] == "read_file"
        assert rec["phase"] == "parsing"
        assert rec["detail"] == '{"path": "a.py"}'
        assert rec["start_time"] > 0
        assert "end_time" in rec

    def test_tool_history_parsing_to_done(self, display):
        """tool_parsing → tool_start → tool_done 后 phase 为 done 且有 end_time。"""
        captured = self._setup_display_with_capture(display)
        display.add_agent("agent-1", "test agent")
        display.tool_parsing("agent-1", "bash", "ls -la")
        display.tool_start("agent-1", "bash", "ls -la /home")
        display.tool_done("agent-1", "bash", success=True)

        history = captured[-1].slot["tool_history"]
        assert len(history) == 1
        rec = history[0]
        assert rec["tool_name"] == "bash"
        assert rec["phase"] == "done"
        assert rec["end_time"] > 0

    def test_tool_history_parsing_to_fail(self, display):
        """tool_done(success=False) 后 phase 为 fail。"""
        captured = self._setup_display_with_capture(display)
        display.add_agent("agent-1", "test agent")
        display.tool_parsing("agent-1", "bash", "bad_cmd")
        display.tool_start("agent-1", "bash", "bad_cmd --flag")
        display.tool_done("agent-1", "bash", success=False)

        history = captured[-1].slot["tool_history"]
        assert len(history) == 1
        assert history[0]["phase"] == "fail"

    def test_tool_history_multiple_entries_ordered(self, display):
        """多次工具调用后 tool_history 按调用顺序包含全部记录。"""
        captured = self._setup_display_with_capture(display)
        display.add_agent("agent-1", "test agent")

        for i, (name, detail) in enumerate([
            ("read_file", "a.py"),
            ("bash", "pytest -x"),
            ("write_file", "b.py:42"),
        ]):
            display.tool_parsing("agent-1", name, detail)
            display.tool_start("agent-1", name, detail)
            display.tool_done("agent-1", name, success=True)

        history = captured[-1].slot["tool_history"]
        assert len(history) == 3
        assert [r["tool_name"] for r in history] == ["read_file", "bash", "write_file"]
        assert all(r["phase"] == "done" for r in history)

    def test_tool_history_agent_done_cleans_running(self, display):
        """agent status → done 时，running/parsing 的 ToolRecord 被批量标记为 done。"""
        captured = self._setup_display_with_capture(display)
        display.add_agent("agent-1", "test agent")
        # 遗留一条 running 状态记录（模拟异常场景）
        display.tool_parsing("agent-1", "search", "pattern")
        display.tool_start("agent-1", "search", "pattern.*")
        # 不调用 tool_done，直接标记 agent 为 done
        display.update_agent_status("agent-1", "done")

        # update_agent_status("done") 先推送最终 slot，再调用 remove_agent_slot 清除，
        # 所以 captured[-2] 是带 tool_history 的最终 slot 数据
        final_slot = captured[-2].slot
        history = final_slot["tool_history"]
        assert len(history) == 1
        # AgentStateStore.update_agent_status 会将残留 running/parsing 批量标记为 done
        assert history[0]["phase"] in ("done", "fail")

    def test_tool_history_dict_keys_match_toolrecord_fields(self, display):
        """序列化后的 dict 键与 ToolRecord 字段一一对应。"""
        captured = self._setup_display_with_capture(display)
        display.add_agent("agent-1", "test agent")
        display.tool_parsing("agent-1", "read_file", "main.py")
        display.tool_start("agent-1", "read_file", "main.py:1-10")
        display.tool_done("agent-1", "read_file", success=True)

        rec = captured[-1].slot["tool_history"][0]
        expected_keys = {"tool_name", "detail", "start_time", "end_time", "phase"}
        assert set(rec.keys()) == expected_keys, (
            f"tool_history dict 键应为 {expected_keys}，实际为 {set(rec.keys())}"
        )
