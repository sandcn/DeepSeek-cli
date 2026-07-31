"""统一输出管线测试 — 步骤2（A 渲染输出路径统一）。

覆盖：
  1. RenderOutput.write_text/write_raw 委托正确（内容写走 OutputAdapter）
  2. write_emergency 限频（同类型 5s 内至多 1 次）
  3. 无全局 stdout 劫持（setup/teardown 前后 sys.__stdout__ 引用不变）
  4. 行跟踪回调完整行检测（内容写经 tracker.track → ring 完整行）
  5. reasoning 共享 adapter + dim 样式（IncrementalRenderer(output_adapter, style="dim")）
  6. _do_parse_info 走 write_raw（常规分支 + _CLEAR_PARSE_LINE 分支，无 _emergency_write）
"""

from __future__ import annotations

import io
import sys
from unittest.mock import MagicMock, patch

import pytest

from rich.console import Console


def _make_adapter(buf: io.StringIO):
    """创建绑定 StringIO 的 OutputAdapter。"""
    from src.renderer.output import OutputAdapter
    console = Console(file=buf, force_terminal=True, width=80, color_system="standard")
    return OutputAdapter(console)


@pytest.fixture
def render_output():
    """创建 RenderOutput（mock adapter，便于断言委托）。"""
    from src.tui._output import RenderOutput
    mock_adapter = MagicMock()
    return RenderOutput(mock_adapter)


# ============================================================
# 1. write_text / write_raw 委托正确
# ============================================================

class TestWriteDelegation:
    """内容写路径委托 OutputAdapter。"""

    def test_write_text_delegates(self, render_output):
        render_output.write_text("hello")
        render_output._adapter.write.assert_called_once_with("hello")

    def test_write_text_level_param_ignored(self, render_output):
        """level 参数为预留，不影响写入行为。"""
        render_output.write_text("hello", level="error")
        render_output._adapter.write.assert_called_once_with("hello")

    def test_write_raw_delegates(self, render_output):
        render_output.write_raw("\r\033[K  ~ tool 100t 1.23s")
        render_output._adapter.write_raw.assert_called_once_with(
            "\r\033[K  ~ tool 100t 1.23s"
        )

    def test_write_empty_noop(self, render_output):
        render_output.write_text("")
        render_output._adapter.write.assert_not_called()

    def test_width_delegates(self, render_output):
        render_output._adapter.width = 120
        assert render_output.width == 120

    def test_flush_delegates(self, render_output):
        render_output.flush()
        render_output._adapter.flush.assert_called_once_with()


# ============================================================
# 2. write_emergency 限频
# ============================================================

class TestEmergencyRateLimit:
    """紧急输出限频 — 同类型 5s 内至多 1 次。"""

    def test_emergency_rate_limited(self):
        from src.tui._output import RenderOutput
        ro = RenderOutput(MagicMock(), emergency_interval=5.0)
        with patch("src.tui._output.sys.__stderr__", new=io.StringIO()) as fake_stderr:
            ro.write_emergency("msg1")
            ro.write_emergency("msg2")   # 5s 内同类型 → 限频丢弃
            ro.write_emergency("msg3")
            assert fake_stderr.getvalue() == "msg1", (
                f"限频后仅首次写入, got: {fake_stderr.getvalue()!r}"
            )

    def test_emergency_different_stream_not_shared(self):
        """不同 stream 类型独立限频。"""
        from src.tui._output import RenderOutput
        ro = RenderOutput(MagicMock(), emergency_interval=5.0)
        with patch("src.tui._output.sys.__stderr__", new=io.StringIO()) as fake_stderr, \
             patch("src.tui._output.sys.__stdout__", new=io.StringIO()) as fake_stdout:
            ro.write_emergency("to-stderr")
            ro.write_emergency("to-stdout", stream="stdout")
            ro.write_emergency("to-stderr-2")
            assert fake_stderr.getvalue() == "to-stderr"
            assert fake_stdout.getvalue() == "to-stdout"

    def test_emergency_after_interval(self):
        """超过限频间隔后再次写入。"""
        from src.tui._output import RenderOutput
        ro = RenderOutput(MagicMock(), emergency_interval=0.05)
        with patch("src.tui._output.sys.__stderr__", new=io.StringIO()) as fake_stderr:
            ro.write_emergency("first")
            assert fake_stderr.getvalue() == "first"
            import time
            time.sleep(0.06)
            ro.write_emergency("second")
            assert fake_stderr.getvalue() == "firstsecond"


# ============================================================
# 3. 无全局 stdout 劫持
# ============================================================

class TestNoGlobalStdoutHijack:
    """setup/teardown 前后 sys.__stdout__ 引用不变。"""

    def test_setup_teardown_preserve_stdout_ref(self, tmp_path):
        from src.tui._consumer import ChatUIConsumer
        saved = sys.__stdout__
        fake_stdin = MagicMock()
        fake_stdin.fileno.return_value = 0  # pytest 捕获 stdin 无 fileno，需 mock
        output_file = tmp_path / "output_history"
        with patch("src.tui._stdout_tracker.OUTPUT_HISTORY_FILE", output_file), \
             patch("sys.stdin", fake_stdin):
            c = ChatUIConsumer()
            c.setup_bottom_bar()
            try:
                assert sys.__stdout__ is saved, (
                    "setup 后 sys.__stdout__ 引用不应改变（无全局劫持）"
                )
            finally:
                c.teardown_bottom_bar()
            assert sys.__stdout__ is saved, (
                "teardown 后 sys.__stdout__ 引用不应改变（无全局劫持）"
            )


# ============================================================
# 4. 行跟踪回调完整行检测
# ============================================================

class TestLineTrackerCallback:
    """RenderOutput 内容写 → tracker.track → ring 完整行。"""

    def _make_tracker(self, tmp_path):
        from src.tui._stdout_tracker import _StdoutLineTracker
        output_file = tmp_path / "output_history"
        return (
            output_file,
            _StdoutLineTracker,
        )

    def test_write_text_tracks_full_line(self, tmp_path):
        from src.tui._output import RenderOutput
        from src.tui._stdout_tracker import _StdoutLineTracker
        output_file = tmp_path / "output_history"
        with (
            patch("src.tui._stdout_tracker.OUTPUT_HISTORY_FILE", output_file),
            patch("src.tui._stdout_tracker._lock_history_file", return_value=True),
            patch("src.tui._stdout_tracker._unlock_history_file"),
        ):
            real_stdout = io.StringIO()
            tracker = _StdoutLineTracker(real_stdout)
            tracker._scroll_end = 10
            ro = RenderOutput(MagicMock())
            ro.set_line_tracker(tracker)

            ro.write_text("line one\n")
            ro.write_text("line two\n")
            assert list(tracker._ring) == ["line one", "line two"]

    def test_write_raw_tracks_partial_then_full(self, tmp_path):
        from src.tui._output import RenderOutput
        from src.tui._stdout_tracker import _StdoutLineTracker
        output_file = tmp_path / "output_history"
        with (
            patch("src.tui._stdout_tracker.OUTPUT_HISTORY_FILE", output_file),
            patch("src.tui._stdout_tracker._lock_history_file", return_value=True),
            patch("src.tui._stdout_tracker._unlock_history_file"),
        ):
            real_stdout = io.StringIO()
            tracker = _StdoutLineTracker(real_stdout)
            tracker._scroll_end = 10
            ro = RenderOutput(MagicMock())
            ro.set_line_tracker(tracker)

            # 流式 chunk：无 \n 累积 partial，\n 到达后入 ring
            ro.write_raw("hel")
            ro.write_raw("lo\n")
            assert list(tracker._ring) == ["hello"]


# ============================================================
# 5. reasoning 共享 adapter + dim 样式
# ============================================================

class TestReasoningSharedAdapterDim:
    """IncrementalRenderer(output_adapter, style="dim") 保留 dim 样式。"""

    def test_dim_style_preserved_with_shared_adapter(self):
        from src.renderer import IncrementalRenderer
        buf = io.StringIO()
        adapter = _make_adapter(buf)
        captured: list[str] = []
        r = IncrementalRenderer(
            output_adapter=adapter, style="dim",
            show_indicator=False, captured_output=captured,
        )
        r.write("hello **world**")
        r.close()
        out = buf.getvalue()
        assert "\x1b[2m" in out, f"共享 adapter 下 dim 样式应保留, got: {out!r}"
        assert len(captured) > 0, "captured_output 应被填充（转发底层 adapter）"

    def test_no_style_path_unaffected(self):
        from src.renderer import IncrementalRenderer
        buf = io.StringIO()
        adapter = _make_adapter(buf)
        r = IncrementalRenderer(
            output_adapter=adapter, style="", show_indicator=False,
        )
        r.write("plain **bold**")
        r.close()
        assert "bold" in buf.getvalue()


# ============================================================
# 6. _do_parse_info 走 write_raw（无 _emergency_write）
# ============================================================

class TestParseInfoWriteRaw:
    """解析进度行改走 OutputAdapter.write_raw。"""

    def _make_renderer(self):
        from src.tui._renderer._renderer import TuiRenderer
        rs = MagicMock()
        adapter = MagicMock()
        bb = MagicMock()
        return TuiRenderer(rs, adapter, bb), adapter

    def test_parse_info_normal_uses_write_raw(self):
        renderer, adapter = self._make_renderer()
        renderer._do_parse_info("tool", 100, 1.23)
        adapter.write_raw.assert_called_once()
        text = adapter.write_raw.call_args[0][0]
        assert text.startswith("\r\033[K"), f"应保持行内覆盖语义, got: {text!r}"
        assert "tool" in text and "100t" in text and "1.23s" in text

    def test_parse_info_clear_line_uses_write_raw_newline(self):
        from src.tui._const import _CLEAR_PARSE_LINE
        renderer, adapter = self._make_renderer()
        renderer._do_parse_info("tool", _CLEAR_PARSE_LINE, 1.23)
        adapter.write_raw.assert_called_once_with("\n")
