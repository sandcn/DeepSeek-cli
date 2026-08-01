import pytest
import io
import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock


class TestOutputHistoryIntegration:
    """集成测试：写入→刷盘→恢复全链路。"""

    def test_write_flush_restore_cycle(self, tmp_path: Path):
        """验证写入→刷盘→重新加载恢复最后行的完整链路。

        mock 文件锁为始终成功，避免多线程并发刷盘时 flock 竞争导致丢行。
        """
        from src.tui._stdout_tracker import _StdoutLineTracker

        output_file = tmp_path / "output_history"
        with (
            patch("src.tui._stdout_tracker.OUTPUT_HISTORY_FILE", output_file),
            patch("src.tui._stdout_tracker._lock_history_file", return_value=True),
            patch("src.tui._stdout_tracker._unlock_history_file"),
        ):
            real_stdout = io.StringIO()
            tracker = _StdoutLineTracker(real_stdout)
            tracker._scroll_end = 10  # 启用追踪

            # 写入 1500 行
            for i in range(1500):
                tracker._track(f"line_{i}\n")

            # 刷盘
            tracker._flush_history()

            # 验证文件存在且有内容
            assert output_file.exists()
            content = output_file.read_text(encoding="utf-8")
            lines = content.splitlines()
            assert len(lines) > 0
            # 验证最后写入的行在文件中
            assert "line_1499" in lines

        # 重新创建 tracker，验证恢复最后 1000 行
        with (
            patch("src.tui._stdout_tracker.OUTPUT_HISTORY_FILE", output_file),
            patch("src.tui._stdout_tracker._lock_history_file", return_value=True),
            patch("src.tui._stdout_tracker._unlock_history_file"),
        ):
            tracker2 = _StdoutLineTracker(real_stdout)
            assert len(tracker2._ring) <= 1000
            # 环形缓冲最后一行应为 line_1499
            if tracker2._ring:
                assert tracker2._ring[-1] == "line_1499"

    def test_no_file_graceful_start(self, tmp_path: Path):
        """文件不存在时平滑启动。"""
        from src.tui._stdout_tracker import _StdoutLineTracker

        real_stdout = io.StringIO()
        output_file = tmp_path / "nonexistent_output_history"
        with patch("src.tui._stdout_tracker.OUTPUT_HISTORY_FILE", output_file):
            tracker = _StdoutLineTracker(real_stdout)
            assert len(tracker._ring) == 0  # 平滑启动，空缓冲

    def test_overflow_ring_capacity(self, tmp_path: Path):
        """环形缓冲超过 _MAX_LINES=1000 时自动淘汰旧行。"""
        from src.tui._stdout_tracker import _StdoutLineTracker

        real_stdout = io.StringIO()
        output_file = tmp_path / "output_history_overflow"
        with patch("src.tui._stdout_tracker.OUTPUT_HISTORY_FILE", output_file):
            tracker = _StdoutLineTracker(real_stdout)
            tracker._scroll_end = 10

            # 写入 1200 行（超过 1000）
            for i in range(1200):
                tracker._track(f"line_{i}\n")

            assert len(tracker._ring) == 1000
            assert tracker._ring[0] == "line_200"  # 第200行成为首行（0-based）
            assert tracker._ring[-1] == "line_1199"  # 最后一行

    def test_history_file_not_overwritten_on_start(self, tmp_path: Path):
        """启动时加载不覆盖现有文件内容，仅读取。"""
        from src.tui._stdout_tracker import _StdoutLineTracker

        output_file = tmp_path / "output_history_persist"
        # 预置文件内容
        output_file.write_text("\n".join(f"existing_{i}" for i in range(500)) + "\n", encoding="utf-8")

        real_stdout = io.StringIO()
        with patch("src.tui._stdout_tracker.OUTPUT_HISTORY_FILE", output_file):
            tracker = _StdoutLineTracker(real_stdout)
            # 恢复 500 行到环形缓冲
            assert len(tracker._ring) == 500
            assert tracker._ring[-1] == "existing_499"

        # 验证文件内容未被修改
        content = output_file.read_text(encoding="utf-8")
        assert "existing_499" in content
        assert len(content.splitlines()) == 500


class TestFlushTimerLeak:
    """测试 flush timer 泄露修复 — _flush_timer_stop Event（Issue 3）。"""

    @pytest.fixture
    def tracker(self, tmp_path: Path):
        """创建 mock 后的 _StdoutLineTracker 实例。"""
        from src.tui._stdout_tracker import _StdoutLineTracker
        real_stdout = io.StringIO()
        output_file = tmp_path / "test_output_history"
        with (
            patch("src.tui._stdout_tracker.OUTPUT_HISTORY_FILE", output_file),
            patch("src.tui._stdout_tracker._lock_history_file", return_value=True),
            patch("src.tui._stdout_tracker._unlock_history_file"),
        ):
            tr = _StdoutLineTracker(real_stdout)
            yield tr

    def test_flush_timer_stop_event_in_init(self, tracker):
        """验证 _flush_timer_stop 在 __init__ 中被初始化为 threading.Event。"""
        import threading
        assert isinstance(tracker._flush_timer_stop, threading.Event)
        # 初始状态应为未设置
        assert not tracker._flush_timer_stop.is_set()

    def test_stop_flush_timer_prevents_new_timer(self, tracker):
        """验证 _stop_flush_timer() 后 callback 不会创建新定时器。"""
        # 记录原始 flush timer
        original_timer = tracker._flush_timer
        assert original_timer is not None

        # 停止定时器
        tracker._stop_flush_timer()

        # 验证 flush_timer 为 None
        assert tracker._flush_timer is None

        # 验证 stop 标志已设置
        assert tracker._flush_timer_stop.is_set()

        # 模拟 callback 被触发（即使已 cancel，已触发的 callback 仍可能执行）
        tracker._timer_flush_callback()

        # 验证不会创建新定时器
        assert tracker._flush_timer is None
        assert tracker._flush_timer_stop.is_set()

    def test_start_flush_timer_defensive_check(self, tracker):
        """验证 _start_flush_timer() 在 stop 标志已设置时跳过。"""
        # 先停止
        tracker._stop_flush_timer()
        assert tracker._flush_timer is None

        # 尝试启动（应被防御性检查阻止）
        tracker._start_flush_timer()

        # 验证未创建新定时器
        assert tracker._flush_timer is None

    def test_timer_not_created_after_teardown(self, tracker):
        """模拟 teardown 场景：_flush_history → _stop_flush_timer → callback 不创建新 timer。"""
        # 模拟 teardown 路径
        tracker._flush_history()

        # 验证 timer 已停止
        assert tracker._flush_timer is None
        assert tracker._flush_timer_stop.is_set()

        # 即使 callback 被触发，也不应创建新 timer
        tracker._timer_flush_callback()
        assert tracker._flush_timer is None

    def test_multiple_stop_calls_safe(self, tracker):
        """验证多次调用 _stop_flush_timer 安全。"""
        tracker._stop_flush_timer()
        assert tracker._flush_timer is None
        assert tracker._flush_timer_stop.is_set()

        # 第二次调用不应抛异常
        tracker._stop_flush_timer()
        assert tracker._flush_timer is None
        assert tracker._flush_timer_stop.is_set()


class TestPublicTrackMethod:
    """步骤2（A 输出路径统一）：新增公开 track(data) 方法。

    语义与内部 _track 一致，供 RenderOutput 内容写回调调用。
    """

    def test_track_public_method_records_full_lines(self, tmp_path):
        from src.tui._stdout_tracker import _StdoutLineTracker

        output_file = tmp_path / "output_history_track"
        with (
            patch("src.tui._stdout_tracker.OUTPUT_HISTORY_FILE", output_file),
            patch("src.tui._stdout_tracker._lock_history_file", return_value=True),
            patch("src.tui._stdout_tracker._unlock_history_file"),
        ):
            real_stdout = io.StringIO()
            tracker = _StdoutLineTracker(real_stdout)
            tracker._scroll_end = 10

            tracker.track("alpha\n")
            tracker.track("beta\n")
            assert list(tracker._ring) == ["alpha", "beta"]

    def test_track_public_method_partial_line(self, tmp_path):
        """流式 chunk 无 \\n 时累积 partial_line，\\n 到达后入 ring。"""
        from src.tui._stdout_tracker import _StdoutLineTracker

        output_file = tmp_path / "output_history_track_partial"
        with (
            patch("src.tui._stdout_tracker.OUTPUT_HISTORY_FILE", output_file),
            patch("src.tui._stdout_tracker._lock_history_file", return_value=True),
            patch("src.tui._stdout_tracker._unlock_history_file"),
        ):
            real_stdout = io.StringIO()
            tracker = _StdoutLineTracker(real_stdout)
            tracker._scroll_end = 10

            tracker.track("hel")
            tracker.track("lo\n")
            assert list(tracker._ring) == ["hello"]

    def test_track_public_method_flush_history(self, tmp_path):
        """track 的完整行经 _flush_history 落盘到输出历史文件。"""
        from src.tui._stdout_tracker import _StdoutLineTracker

        output_file = tmp_path / "output_history_track_flush"
        with (
            patch("src.tui._stdout_tracker.OUTPUT_HISTORY_FILE", output_file),
            patch("src.tui._stdout_tracker._lock_history_file", return_value=True),
            patch("src.tui._stdout_tracker._unlock_history_file"),
        ):
            real_stdout = io.StringIO()
            tracker = _StdoutLineTracker(real_stdout)
            tracker._scroll_end = 10

            tracker.track("persisted\n")
            tracker._flush_history()

            assert output_file.exists()
            content = output_file.read_text(encoding="utf-8")
            assert "persisted" in content



class TestFlushSingleFlightAndCompactCooldown:
    """步骤6.4 — 刷盘单飞（不再每 50 行新线程）+ 压缩冷却。"""

    def test_no_thread_per_50_lines_regression(self, tmp_path: Path):
        """写入 150 行：单飞刷盘线程 ≤ 1（不再每 50 行无条件新线程）。"""
        import threading
        import time as _time
        from src.tui._stdout_tracker import _StdoutLineTracker

        output_file = tmp_path / "output_history_singleflight"
        real_stdout = io.StringIO()
        created = []
        orig_thread = threading.Thread
        with (
            patch("src.tui._stdout_tracker.OUTPUT_HISTORY_FILE", output_file),
            patch("src.tui._stdout_tracker._lock_history_file", return_value=True),
            patch("src.tui._stdout_tracker._unlock_history_file"),
        ):
            tracker = _StdoutLineTracker(real_stdout)
            tracker._scroll_end = 10
            # 刷盘 worker 慢速执行（模拟文件 I/O 在途）→ 单飞标志保持置位
            def slow_flush():
                _time.sleep(0.5)
                return True
            tracker._flush_buffered_lines = slow_flush

            def counting_thread(*a, **kw):
                t = orig_thread(*a, **kw)
                created.append(t)
                return t

            with patch("threading.Thread", side_effect=counting_thread):
                for i in range(150):
                    tracker._track(f"line_{i}\n")

            # 单飞：刷盘线程在途时不再为后续 50 行批量创建新线程
            assert len(created) <= 1

        # 等待在途线程完成，避免 daemon 残留
        for t in created:
            t.join(timeout=1.0)

    def test_compact_cooldown_regression(self, tmp_path: Path):
        """冷却内不触发压缩（即使文件 >5000 行）。"""
        import time as _time
        from src.tui._stdout_tracker import _StdoutLineTracker, _COMPACT_COOLDOWN

        output_file = tmp_path / "output_history_cooldown"
        output_file.write_text(
            "\n".join(f"l{i}" for i in range(6000)) + "\n", encoding="utf-8"
        )
        real_stdout = io.StringIO()
        with (
            patch("src.tui._stdout_tracker.OUTPUT_HISTORY_FILE", output_file),
            patch("src.tui._stdout_tracker._lock_history_file", return_value=True),
            patch("src.tui._stdout_tracker._unlock_history_file"),
        ):
            tracker = _StdoutLineTracker(real_stdout)
            tracker._scroll_end = 10
            # 冷却内：即使文件 >5000 行也不压缩
            tracker._last_compact_time = _time.monotonic()
            assert tracker._maybe_compact_output_history() is False
            content = output_file.read_text(encoding="utf-8")
            assert len(content.splitlines()) == 6000  # 未压缩

    def test_compact_allowed_after_cooldown_regression(self, tmp_path: Path):
        """冷却过后可压缩（>5000 行时去重 + 截断 2000）。"""
        import time as _time
        from src.tui._stdout_tracker import _StdoutLineTracker, _COMPACT_COOLDOWN

        output_file = tmp_path / "output_history_cooldown_ok"
        output_file.write_text(
            "\n".join(f"l{i}" for i in range(6000)) + "\n", encoding="utf-8"
        )
        real_stdout = io.StringIO()
        with (
            patch("src.tui._stdout_tracker.OUTPUT_HISTORY_FILE", output_file),
            patch("src.tui._stdout_tracker._lock_history_file", return_value=True),
            patch("src.tui._stdout_tracker._unlock_history_file"),
        ):
            tracker = _StdoutLineTracker(real_stdout)
            tracker._scroll_end = 10
            # 冷却已过（last = now - cooldown - 1）
            tracker._last_compact_time = _time.monotonic() - _COMPACT_COOLDOWN - 1.0
            assert tracker._maybe_compact_output_history() is True
            content = output_file.read_text(encoding="utf-8")
            assert len(content.splitlines()) <= 2000  # 已压缩（去重 + 截断 2000）

    def test_compact_cooldown_triggers_regression(self, tmp_path: Path):
        """方向2 — 压缩冷却修复：_flush_buffered_lines 不再刷新冷却 → 冷却过后压缩可触发。

        修复前 _flush_buffered_lines 每次刷盘后更新 _last_compact_time →
        now-last<30 恒成立 → 压缩永不触发。
        """
        import time as _time
        from src.tui._stdout_tracker import _StdoutLineTracker, _COMPACT_COOLDOWN

        output_file = tmp_path / "output_history_compact_fix"
        output_file.write_text(
            "\n".join(f"l{i}" for i in range(6000)) + "\n", encoding="utf-8"
        )
        real_stdout = io.StringIO()
        with (
            patch("src.tui._stdout_tracker.OUTPUT_HISTORY_FILE", output_file),
            patch("src.tui._stdout_tracker._lock_history_file", return_value=True),
            patch("src.tui._stdout_tracker._unlock_history_file"),
        ):
            tracker = _StdoutLineTracker(real_stdout)
            tracker._scroll_end = 10
            # 冷却已过（last = now - cooldown - 1）——修复前 _flush_buffered_lines
            # 刷盘后更新 _last_compact_time → 冷却恒不满足 → 压缩不触发
            tracker._last_compact_time = _time.monotonic() - _COMPACT_COOLDOWN - 1.0
            tracker._buffer_to_output("new_line")
            assert tracker._flush_buffered_lines() is True
            content = output_file.read_text(encoding="utf-8")
            assert len(content.splitlines()) <= 2000, (
                f"冷却过后刷盘应触发压缩，实际行数 {len(content.splitlines())}"
            )

    def test_close_flushes_history_and_stops_timer_regression(self, tmp_path: Path):
        """方向2 — close() 刷出全部缓冲行 + 停止 daemon 定时器（幂等）。"""
        from src.tui._stdout_tracker import _StdoutLineTracker

        output_file = tmp_path / "output_history_close"
        real_stdout = io.StringIO()
        with (
            patch("src.tui._stdout_tracker.OUTPUT_HISTORY_FILE", output_file),
            patch("src.tui._stdout_tracker._lock_history_file", return_value=True),
            patch("src.tui._stdout_tracker._unlock_history_file"),
        ):
            tracker = _StdoutLineTracker(real_stdout)
            tracker._scroll_end = 10
            tracker.track("line_a\n")
            tracker.track("line_b\n")
            tracker.close()
            # 缓冲行已落盘（输出历史含全部行）
            content = output_file.read_text(encoding="utf-8")
            assert "line_a" in content
            assert "line_b" in content
            # 定时器已停止（不再自重置泄漏）
            assert tracker._flush_timer is None
            assert tracker._flush_timer_stop.is_set()
            # 幂等：重复 close 安全（不抛异常）
            tracker.close()
            # close 后 _start_flush_timer 防御性跳过（不再创建新定时器）
            tracker._start_flush_timer()
            assert tracker._flush_timer is None


class TestAnsiStripOutputHistory:
    """方向1 — 输出历史 ANSI SGR 剥离（环形缓冲/历史文件存纯文本）。"""

    def test_track_strips_ansi_from_ring_and_file(self, tmp_path):
        """写入含 SGR 的行 → _ring/输出历史文件内容为纯文本 RED。"""
        from src.tui._stdout_tracker import _StdoutLineTracker

        output_file = tmp_path / "output_history_ansi"
        real_stdout = io.StringIO()
        with (
            patch("src.tui._stdout_tracker.OUTPUT_HISTORY_FILE", output_file),
            patch("src.tui._stdout_tracker._lock_history_file", return_value=True),
            patch("src.tui._stdout_tracker._unlock_history_file"),
        ):
            tracker = _StdoutLineTracker(real_stdout)
            tracker._scroll_end = 10
            tracker.track("\033[31mRED\033[0m\n")
            assert list(tracker._ring) == ["RED"]
            tracker._flush_history()
            content = output_file.read_text(encoding="utf-8")
            assert "RED" in content
            assert "\033[" not in content

    def test_load_history_strips_ansi(self, tmp_path):
        """_load_output_history 加载含 SGR 的历史文件 → 环形缓冲纯文本。"""
        from src.tui._stdout_tracker import _StdoutLineTracker

        output_file = tmp_path / "output_history_ansi_load"
        output_file.write_text("\033[32mGREEN\033[0m\n", encoding="utf-8")
        real_stdout = io.StringIO()
        with (
            patch("src.tui._stdout_tracker.OUTPUT_HISTORY_FILE", output_file),
            patch("src.tui._stdout_tracker._lock_history_file", return_value=True),
            patch("src.tui._stdout_tracker._unlock_history_file"),
        ):
            tracker = _StdoutLineTracker(real_stdout)
            assert list(tracker._ring) == ["GREEN"]

    def test_track_plain_text_unchanged(self, tmp_path):
        """纯文本行不受剥离影响（回归：无 ANSI 时内容一致）。"""
        from src.tui._stdout_tracker import _StdoutLineTracker

        output_file = tmp_path / "output_history_plain"
        real_stdout = io.StringIO()
        with (
            patch("src.tui._stdout_tracker.OUTPUT_HISTORY_FILE", output_file),
            patch("src.tui._stdout_tracker._lock_history_file", return_value=True),
            patch("src.tui._stdout_tracker._unlock_history_file"),
        ):
            tracker = _StdoutLineTracker(real_stdout)
            tracker._scroll_end = 10
            tracker.track("hello world\n")
            assert list(tracker._ring) == ["hello world"]


class TestCrlfAndUnifiedAnsi:
    """方向1 步骤2 — CRLF 行尾 \\r 剥除 + ANSI 统一工具消费。"""

    def test_track_crlf_no_carriage_regression(self, tmp_path):
        """CRLF 终端行尾残留 \\r 剥除：track("a\\r\\nb\\n") 后 ring 含 "a"、"b"（无 \\r）。"""
        from src.tui._stdout_tracker import _StdoutLineTracker

        output_file = tmp_path / "output_history_crlf"
        real_stdout = io.StringIO()
        with (
            patch("src.tui._stdout_tracker.OUTPUT_HISTORY_FILE", output_file),
            patch("src.tui._stdout_tracker._lock_history_file", return_value=True),
            patch("src.tui._stdout_tracker._unlock_history_file"),
        ):
            tracker = _StdoutLineTracker(real_stdout)
            tracker._scroll_end = 10
            tracker.track("a\r\nb\n")
            assert list(tracker._ring) == ["a", "b"], (
                f"CRLF 行尾 \\r 应剥除，实际 ring={list(tracker._ring)!r}"
            )

    def test_track_crlf_mid_carriage_kept(self, tmp_path):
        """行中段 \\r 不剥（仅行尾）："a\\rb\\n" → "a\\rb"。"""
        from src.tui._stdout_tracker import _StdoutLineTracker

        output_file = tmp_path / "output_history_crlf_mid"
        real_stdout = io.StringIO()
        with (
            patch("src.tui._stdout_tracker.OUTPUT_HISTORY_FILE", output_file),
            patch("src.tui._stdout_tracker._lock_history_file", return_value=True),
            patch("src.tui._stdout_tracker._unlock_history_file"),
        ):
            tracker = _StdoutLineTracker(real_stdout)
            tracker._scroll_end = 10
            tracker.track("a\rb\n")
            assert list(tracker._ring) == ["a\rb"], (
                f"行中段 \\r 应保留，实际 ring={list(tracker._ring)!r}"
            )

    def test_no_local_control_seq_re(self):
        """_stdout_tracker 不再定义独立 _CONTROL_SEQ_RE（收敛至 ink/helpers）。"""
        import inspect
        import src.tui._stdout_tracker as mod
        src = inspect.getsource(mod)
        assert "_CONTROL_SEQ_RE = re.compile" not in src, (
            "_stdout_tracker 不应再定义独立光标控制正则（统一工具收敛）"
        )
        from src.tui.ink.helpers import cursor_control_re
        assert mod.cursor_control_re is cursor_control_re, (
            "_stdout_tracker 应消费统一 ink.helpers.cursor_control_re"
        )
