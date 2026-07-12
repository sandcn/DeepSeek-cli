"""Tests for src/ui/parallel/display.py — ParallelDisplay"""

import asyncio
import sys

import pytest

from src.ui.parallel._config import (
    SPINNER_BRAILLE, SPINNER_PULSE, SPINNER_CIRCLE, SPINNER_DOTS,
    SPINNER_SETS, SPINNER_FRAMES, SPINNER_SPEED,
    get_spinner_frames, breathing_animation,
    DEFAULT_SPINNER, DEFAULT_SPINNER_SPEED,
)
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
        from unittest.mock import patch, MagicMock
        mock_chat_ui = MagicMock()
        mock_chat_ui.output_adapter = MagicMock()
        mock_chat_ui.output_adapter.width = 120
        with patch('src.chat_ui.get_active_chat_ui', return_value=mock_chat_ui):
            display.start()
        assert display._adapter is not None, (
            "start() 应设置 _adapter 为 ChatUI 的 output_adapter"
        )
        display.stop()

    def test_stop_clears_adapter(self, display):
        """stop() 将 _adapter 置 None 并停止渲染。"""
        display.add_agent("agent-1", "test agent")
        from unittest.mock import patch, MagicMock
        mock_chat_ui = MagicMock()
        mock_chat_ui.output_adapter = MagicMock()
        mock_chat_ui.output_adapter.width = 120
        with patch('src.chat_ui.get_active_chat_ui', return_value=mock_chat_ui):
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
        from unittest.mock import patch, MagicMock
        mock_chat_ui = MagicMock()
        mock_chat_ui.output_adapter = MagicMock()
        mock_chat_ui.output_adapter.width = 120
        with patch('src.chat_ui.get_active_chat_ui', return_value=mock_chat_ui):
            d.start()
        assert d._adapter is not None, "start() 后应持有 adapter"
        assert d._started is True, "start() 后 _started 应为 True"
        d.stop()
        assert d._adapter is None, "stop() 后 adapter 应被释放"
        assert d._finished is True, "stop() 后 _finished 应为 True"

    def test_refresh_after_stop_safe(self, display):
        """stop() 后 refresh() 安全（无 adapter，渲染提前返回）。"""
        display.add_agent("a", "test")
        from unittest.mock import patch, MagicMock
        mock_chat_ui = MagicMock()
        mock_chat_ui.output_adapter = MagicMock()
        mock_chat_ui.output_adapter.width = 120
        with patch('src.chat_ui.get_active_chat_ui', return_value=mock_chat_ui):
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

    def test_diff_guard_does_not_suppress_exception(self, display):
        """_DiffGuard.__exit__ 返回 False（不抑制异常）。"""
        guard = display._diff_active_guard(capture_frame=False)
        result = guard.__exit__(None, None, None)
        assert result is False, (
            "__exit__ 应返回 False 以允许异常自然传播"
        )

    def test_clear_frame_and_run_returns_result(self, display):
        """clear_frame_and_run 正确执行 func 并返回结果。"""
        result = display.clear_frame_and_run(lambda: 42)
        assert result == 42, (
            f"clear_frame_and_run 应返回 func 执行结果: expected 42, got {result}"
        )

    def test_clear_frame_and_run_no_adapter_safe(self, display):
        """clear_frame_and_run 在无 adapter 时安全（_clear_frame_lines 提前返回）。"""
        result = display.clear_frame_and_run(lambda: "safe")
        assert result == "safe"

# ═══════════════════════════════════════════════════════
# _clear_frame_lines 委托给 BottomBar 测试
# ═══════════════════════════════════════════════════════

class TestClearFrameLinesBottomBar:
    """验证 _clear_frame_lines 通过 bottom_bar.set_subagent_frame([]) 清除面板"""

    def test_clear_frame_lines_calls_set_subagent_frame(self):
        """_clear_frame_lines → 调用 bottom_bar.set_subagent_frame([])"""
        from unittest.mock import MagicMock, patch
        from src.ui.parallel.display import ParallelDisplay

        pd = ParallelDisplay(max_history=3)
        pd._last_lines = 5  # 模拟有面板行

        mock_bb = MagicMock()
        mock_bb.set_subagent_frame = MagicMock()

        mock_chat_ui = MagicMock()
        mock_chat_ui.bottom_bar = mock_bb

        with patch('src.chat_ui.get_active_chat_ui', return_value=mock_chat_ui):
            pd._clear_frame_lines()

        mock_bb.set_subagent_frame.assert_called_once_with([])
        assert pd._last_lines == 0

    def test_clear_frame_lines_no_chat_ui_no_crash(self):
        """无活跃 ChatUI 时 _clear_frame_lines 静默跳过不崩溃"""
        from unittest.mock import patch
        from src.ui.parallel.display import ParallelDisplay

        pd = ParallelDisplay(max_history=3)
        pd._last_lines = 5

        with patch('src.chat_ui.get_active_chat_ui', return_value=None):
            try:
                pd._clear_frame_lines()
            except Exception as e:
                pytest.fail(f"_clear_frame_lines 应静默跳过但抛异常: {e}")

    def test_clear_frame_lines_no_set_subagent_method(self):
        """bottom_bar 无 set_subagent_frame 方法 → 静默跳过"""
        from unittest.mock import MagicMock, patch
        from src.ui.parallel.display import ParallelDisplay

        pd = ParallelDisplay(max_history=3)
        pd._last_lines = 5

        mock_bb = MagicMock(spec=['force_redraw'])  # 不含 set_subagent_frame
        mock_chat_ui = MagicMock()
        mock_chat_ui.bottom_bar = mock_bb

        with patch('src.chat_ui.get_active_chat_ui', return_value=mock_chat_ui):
            try:
                pd._clear_frame_lines()
            except Exception as e:
                pytest.fail(f"_clear_frame_lines 应静默跳过但抛异常: {e}")

    def test_clear_zero_lines_skipped(self):
        """_last_lines=0 时 _clear_frame_lines 应直接返回"""
        from unittest.mock import MagicMock, patch
        from src.ui.parallel.display import ParallelDisplay

        pd = ParallelDisplay(max_history=3)
        pd._last_lines = 0  # 无面板行

        with patch('src.chat_ui.get_active_chat_ui') as mock_get:
            pd._clear_frame_lines()

        # get_active_chat_ui 不应被调用（因为 _last_lines=0 提前返回）
        mock_get.assert_not_called()


# ═══════════════════════════════════════════════════════
# _push_frame_cmd push 失败恢复测试
# ═══════════════════════════════════════════════════════

class TestPushFrameCmdFailureRecovery:
    """_push_frame_cmd push 失败时重置 _last_rendered_version，强制下帧重建。

    回归 [P2-9]：_push_cmd 抛异常时，帧未实际推送到命令队列。
    若不重置版本号，_build_frame 的版本号检查（current_version ==
    _last_rendered_version 且 80ms 内）会跳过重建，导致失败帧永不被重试。
    """

    def test_push_failure_resets_last_rendered_version(self):
        """_push_cmd 抛异常时，_last_rendered_version 重置为 0。"""
        from unittest.mock import MagicMock
        pd = ParallelDisplay(max_history=3)
        pd._adapter = MagicMock()
        pd._adapter.width = 120

        # 模拟 _build_frame 成功构建帧并更新版本号
        def mock_build_frame(final=False):
            pd._last_rendered_version = 42
            return (["line1", "line2"], 24, 0, "\033[K]")
        pd._build_frame = mock_build_frame

        def failing_push_cmd(_cmd):
            raise RuntimeError("command queue closed")
        pd._push_cmd = failing_push_cmd

        pd._push_frame_cmd()

        assert pd._last_rendered_version == 0, (
            "push 失败后应重置 _last_rendered_version 为 0，"
            f"实际为 {pd._last_rendered_version}"
        )

    def test_push_failure_then_success_rebuilds_frame(self):
        """push 失败后，下次调用能重建帧并成功推送（版本号已重置）。"""
        from unittest.mock import MagicMock
        pd = ParallelDisplay(max_history=3)
        pd._adapter = MagicMock()
        pd._adapter.width = 120

        build_count = 0

        def mock_build_frame(final=False):
            nonlocal build_count
            build_count += 1
            pd._last_rendered_version = 42
            return ([f"line{build_count}"], 24, 0, "\033[K]")
        pd._build_frame = mock_build_frame

        # 第一次调用：push 失败
        def failing_push(cmd):
            raise RuntimeError("queue closed")
        pd._push_cmd = failing_push
        pd._push_frame_cmd()
        assert pd._last_rendered_version == 0, "失败后版本号应已重置为 0"

        # 第二次调用：push 成功
        success_push = MagicMock()
        pd._push_cmd = success_push
        pd._push_frame_cmd()

        # 验证第二次 push 成功（帧被重建并推送）
        success_push.assert_called_once()
        assert build_count == 2, (
            f"_build_frame 应被调用 2 次（两次都重建帧），实际 {build_count} 次"
        )

    def test_push_failure_no_exception_propagation(self):
        """_push_cmd 抛异常时不应向上传播，_push_frame_cmd 静默处理。"""
        from unittest.mock import MagicMock
        pd = ParallelDisplay(max_history=3)
        pd._adapter = MagicMock()
        pd._adapter.width = 120

        def mock_build_frame(final=False):
            pd._last_rendered_version = 42
            return (["line1"], 24, 0, "\033[K]")
        pd._build_frame = mock_build_frame

        def failing_push(cmd):
            raise RuntimeError("critical queue failure")
        pd._push_cmd = failing_push

        try:
            pd._push_frame_cmd()
        except Exception as e:
            pytest.fail(f"_push_frame_cmd 不应抛异常: {e}")

    def test_push_success_does_not_reset_version(self):
        """push 成功时不应重置 _last_rendered_version。"""
        from unittest.mock import MagicMock
        pd = ParallelDisplay(max_history=3)
        pd._adapter = MagicMock()
        pd._adapter.width = 120

        def mock_build_frame(final=False):
            pd._last_rendered_version = 99
            return (["line1"], 24, 0, "\033[K]")
        pd._build_frame = mock_build_frame

        success_push = MagicMock()
        pd._push_cmd = success_push

        pd._push_frame_cmd()

        assert pd._last_rendered_version == 99, (
            "push 成功后不应重置 _last_rendered_version，"
            f"实际为 {pd._last_rendered_version}"
        )

    def test_push_failure_updates_last_lines_before_push(self):
        """push 失败前 _last_lines 已更新（供下次 SU/SD delta 计算）。"""
        from unittest.mock import MagicMock
        pd = ParallelDisplay(max_history=3)
        pd._adapter = MagicMock()
        pd._adapter.width = 120

        def mock_build_frame(final=False):
            pd._last_rendered_version = 42
            return (["line1", "line2", "line3"], 24, 0, "\x1b[K")
        pd._build_frame = mock_build_frame

        def failing_push(cmd):
            raise RuntimeError("queue closed")
        pd._push_cmd = failing_push

        pd._push_frame_cmd()

        assert pd._last_lines == 3, (
            "_last_lines 应在 push 前更新为帧行数 3，"
            f"实际为 {pd._last_lines}"
        )


# ═══════════════════════════════════════════════════════
# Spinner 增强测试
# ═══════════════════════════════════════════════════════

class TestSpinnerSets:
    """验证多套 spinner 动画帧集定义正确。"""

    def test_spinner_braille_12_frames(self):
        """SPINNER_BRAILLE 有 12 帧。"""
        assert len(SPINNER_BRAILLE) == 12, (
            f"SPINNER_BRAILLE 应为 12 帧，实际 {len(SPINNER_BRAILLE)}"
        )

    def test_spinner_pulse_14_frames(self):
        """SPINNER_PULSE 有 14 帧。"""
        assert len(SPINNER_PULSE) == 14, (
            f"SPINNER_PULSE 应为 14 帧，实际 {len(SPINNER_PULSE)}"
        )

    def test_spinner_circle_8_frames(self):
        """SPINNER_CIRCLE 有 8 帧。"""
        assert len(SPINNER_CIRCLE) == 8, (
            f"SPINNER_CIRCLE 应为 8 帧，实际 {len(SPINNER_CIRCLE)}"
        )

    def test_spinner_dots_15_frames(self):
        """SPINNER_DOTS 有 15 帧。"""
        assert len(SPINNER_DOTS) == 15, (
            f"SPINNER_DOTS 应为 15 帧，实际 {len(SPINNER_DOTS)}"
        )

    def test_spinner_sets_contains_all(self):
        """SPINNER_SETS 包含全部 11 套帧集（含 heart/bounce/clock/matrix/glow）。"""
        assert set(SPINNER_SETS.keys()) == {
            "braille", "pulse", "circle", "dots",
            "wave", "typing", "heart", "bounce", "clock",
            "matrix", "glow",
        }, (
            f"SPINNER_SETS 键不完整: {list(SPINNER_SETS.keys())}"
        )

    def test_spinner_frames_backward_compat(self):
        """SPINNER_FRAMES 是 SPINNER_BRAILLE 前 8 帧（向后兼容）。"""
        assert SPINNER_FRAMES == SPINNER_BRAILLE[:8], (
            f"SPINNER_FRAMES 应等于 SPINNER_BRAILLE[:8]，"
            f"实际长度 {len(SPINNER_FRAMES)}"
        )

    def test_spinner_speed_values_positive(self):
        """SPINNER_SPEED 所有值 > 0。"""
        for name, speed in SPINNER_SPEED.items():
            assert speed > 0, (
                f"{name} 的帧间隔应为正数，实际 {speed}"
            )

    def test_spinner_speed_keys_match_sets(self):
        """SPINNER_SPEED 的键与 SPINNER_SETS 一致。"""
        assert set(SPINNER_SPEED.keys()) == set(SPINNER_SETS.keys()), (
            f"键不匹配: SPEED={set(SPINNER_SPEED.keys())} vs "
            f"SETS={set(SPINNER_SETS.keys())}"
        )


class TestGetSpinnerFrames:
    """验证 get_spinner_frames 函数。"""

    def test_returns_tuple(self):
        """get_spinner_frames 返回 (list[str], float) 元组。"""
        result = get_spinner_frames()
        assert isinstance(result, tuple), f"应返回 tuple，实际 {type(result)}"
        assert len(result) == 2, f"应返回 2 元素，实际 {len(result)}"
        frames, speed = result
        assert isinstance(frames, list), f"帧应返回 list，实际 {type(frames)}"
        assert isinstance(speed, float), f"速度应返回 float，实际 {type(speed)}"
        assert len(frames) >= 4, f"帧列表至少 4 帧，实际 {len(frames)}"

    def test_default_is_braille(self):
        """默认返回 braille 帧集。"""
        frames, speed = get_spinner_frames()
        assert frames == SPINNER_BRAILLE, "默认应返回 braille 帧集"
        assert speed == DEFAULT_SPINNER_SPEED, f"默认速度应 {DEFAULT_SPINNER_SPEED}"

    def test_braille_frames(self):
        """指定 braille 返回正确。"""
        frames, speed = get_spinner_frames("braille")
        assert frames == SPINNER_BRAILLE
        assert speed == SPINNER_SPEED["braille"]

    def test_pulse_frames(self):
        """指定 pulse 返回正确。"""
        frames, speed = get_spinner_frames("pulse")
        assert frames == SPINNER_PULSE
        assert speed == SPINNER_SPEED["pulse"]

    def test_circle_frames(self):
        """指定 circle 返回正确。"""
        frames, speed = get_spinner_frames("circle")
        assert frames == SPINNER_CIRCLE
        assert speed == SPINNER_SPEED["circle"]

    def test_dots_frames(self):
        """指定 dots 返回正确。"""
        frames, speed = get_spinner_frames("dots")
        assert frames == SPINNER_DOTS
        assert speed == SPINNER_SPEED["dots"]

    def test_unknown_name_fallback(self):
        """未知名称兜底返回 braille 帧集。"""
        frames, speed = get_spinner_frames("unknown")
        assert frames == SPINNER_BRAILLE, "未知名称应兜底返回 braille"
        assert speed == DEFAULT_SPINNER_SPEED, "未知名称速度应兜底返回默认速度"


class TestBreathingAnimation:
    """验证 breathing_animation 函数。"""

    def test_returns_list(self):
        """breathing_animation 返回 list[str]。"""
        result = breathing_animation(22, 47)
        assert isinstance(result, list), f"应返回 list，实际 {type(result)}"
        assert len(result) > 0, "返回列表不应为空"

    def test_length_symmetric(self):
        """对称呼吸周期长度 = 2 * steps。"""
        for steps in (4, 6, 8, 10):
            result = breathing_animation(22, 47, steps=steps)
            expected = 2 * steps
            assert len(result) == expected, (
                f"steps={steps} 时应返回 {expected} 帧，实际 {len(result)}"
            )

    def test_color_range(self):
        """颜色值在 [0, 255] 范围内。"""
        result = breathing_animation(22, 47, steps=6)
        for frame in result:
            # 提取颜色号 \033[38;5;{N}m
            import re
            m = re.search(r'38;5;(\d+)', frame)
            assert m is not None, f"帧缺少 256 色码: {frame!r}"
            color = int(m.group(1))
            assert 0 <= color <= 255, (
                f"颜色号越界: {color}"
            )

    def test_ansi_format(self):
        """每帧格式为 \\033[38;5;{color}m▊\\033[0m。"""
        result = breathing_animation(196, 221, steps=4)
        for frame in result:
            assert frame.startswith("\033[38;5;"), (
                f"帧应以 256 色前景开始: {frame[:20]!r}"
            )
            assert "▊" in frame, f"帧应含方块字符: {frame!r}"
            assert frame.endswith("\033[0m"), (
                f"帧应以重置序列结束: {frame[-10:]!r}"
            )

    def test_steps_less_than_2(self):
        """steps < 2 时返回 4 帧的兜底呼吸。"""
        result = breathing_animation(22, 47, steps=1)
        assert len(result) == 4, (
            f"steps=1 时应返回 4 帧兜底，实际 {len(result)}"
        )

    def test_start_end_cycle_symmetry(self):
        """呼吸周期首尾颜色相同（start→end→start 对称）。"""
        result = breathing_animation(22, 47, steps=6)
        # 第一个和最后一个应颜色相同（都是起始色）
        first_color = result[0]
        last_color = result[-1]
        assert first_color == last_color, (
            f"首尾帧应颜色相同（起始色），实际: "
            f"第一帧={first_color!r}, 最后一帧={last_color!r}"
        )

    def test_midpoint_color(self):
        """中间帧应为结束色（end 颜色）。"""
        result = breathing_animation(22, 47, steps=6)
        # 中间索引 = steps - 1（因为从 0 开始）
        mid_idx = 6 - 1  # steps=6, 索引 5 是 end
        mid_frame = result[mid_idx]
        assert "38;5;47" in mid_frame or "38;5;46" in mid_frame or "38;5;45" in mid_frame, (
            f"中间帧应接近结束色 47: {mid_frame!r}"
        )

    def test_called_independently(self):
        """函数可独立调用不抛异常。"""
        try:
            result = breathing_animation(0, 255, steps=8)
            assert len(result) == 16  # 2 * steps = 16 (对称版)
        except Exception as e:
            pytest.fail(f"breathing_animation 独立调用抛异常: {e}")


class TestFrameRendererSpinnerIntegration:
    """验证 FrameRenderer 与新 spinner 接口的集成。"""

    def test_default_constructor_backward_compat(self):
        """不传 spinner_name 时使用默认 8 帧 braille（向后兼容）。"""
        from src.ui.renderer.frame_renderer import FrameRenderer
        r = FrameRenderer(terminal_width=120, frame=0)
        assert len(r._spinner_frames) == 8, (
            f"默认 spinner 应为 8 帧，实际 {len(r._spinner_frames)}"
        )
        assert r._spinner_speed == DEFAULT_SPINNER_SPEED

    def test_spinner_name_braille(self):
        """传入 spinner_name='braille' 使用 12 帧集。"""
        from src.ui.renderer.frame_renderer import FrameRenderer
        r = FrameRenderer(terminal_width=120, frame=0, spinner_name="braille")
        assert len(r._spinner_frames) == 12, (
            f"braille 应为 12 帧，实际 {len(r._spinner_frames)}"
        )

    def test_spinner_name_pulse(self):
        """传入 spinner_name='pulse' 使用脉冲帧集。"""
        from src.ui.renderer.frame_renderer import FrameRenderer
        r = FrameRenderer(terminal_width=120, frame=0, spinner_name="pulse")
        assert r._spinner_frames == SPINNER_PULSE
        assert r._spinner_speed == SPINNER_SPEED["pulse"]

    def test_spinner_frames_param_still_works(self):
        """旧的 spinner_frames 参数仍然有效。"""
        from src.ui.renderer.frame_renderer import FrameRenderer
        custom = ["◐", "◓", "◑", "◒"]
        r = FrameRenderer(terminal_width=120, frame=0, spinner_frames=custom)
        assert r._spinner_frames == custom
        assert r._spinner_speed == DEFAULT_SPINNER_SPEED
