"""src/tui/_stdout_tracker — _StdoutLineTracker 单元测试。

覆盖：
  - File-object 协议（encoding/errors/buffer/fileno/isatty/writable）透传
  - write/flush 透传 real_stdout
  - 行跟踪：完整行入环形缓冲、ANSI 剥离、CRLF \r 剥除、部分行延迟
  - 底部栏过滤（光标定位 > scroll_end → 不跟踪；光标恢复退出底部栏）
  - scroll_end<1 禁用跟踪；close 后停止跟踪
  - 长行截断（_PARTIAL_LINE_MAX）；环形缓冲上限
  - 输出历史：缓冲→刷盘写文件（含放回重试语义）
  - 压缩：冷却、去重截断、锁失败跳过
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

import src.tui._stdout_tracker as st
from src.tui._stdout_tracker import _StdoutLineTracker


@pytest.fixture(autouse=True)
def isolate_history(tmp_path: Path, monkeypatch):
    """所有测试将输出历史文件隔离到临时目录，避免污染真实配置目录。"""
    monkeypatch.setattr(st, "OUTPUT_HISTORY_FILE", tmp_path / "output_history")
    yield


@pytest.fixture
def tracker(tmp_path: Path, monkeypatch):
    """构造指向临时历史文件的 tracker，并停止后台定时器。"""
    monkeypatch.setattr(st, "OUTPUT_HISTORY_FILE", tmp_path / "output_history")
    t = _StdoutLineTracker(io.StringIO())
    t._stop_flush_timer()
    yield t
    try:
        t.close()
    except Exception:
        pass


def _mk(fake_stdout=None, scroll_end=0):
    t = _StdoutLineTracker(fake_stdout if fake_stdout is not None else io.StringIO())
    t._stop_flush_timer()
    if scroll_end:
        t.set_scroll_end(scroll_end)
    return t


class _FakeStdout:
    """可设置属性、带 fileno 的假 stdout（协议测试用）。"""

    encoding = "utf-8"
    errors = "replace"

    def __init__(self):
        self.written = ""

    @property
    def buffer(self):
        return self

    def write(self, data: str) -> int:
        self.written += data
        return len(data)

    def flush(self):
        pass

    def isatty(self):
        return False

    def fileno(self):
        return 7


# ── File-object 协议 ─────────────────────────────────────

def test_file_protocol_passthrough():
    real = _FakeStdout()
    t = _StdoutLineTracker(real)
    t._stop_flush_timer()
    assert t.encoding == "utf-8"
    assert t.errors == "replace"
    assert t.writable() is True
    assert t.isatty() is False
    t.close()


def test_buffer_falls_back_to_real_stdout():
    real = io.StringIO()
    t = _StdoutLineTracker(real)
    t._stop_flush_timer()
    assert t.buffer is real  # StringIO 无 buffer → 返回自身
    t.close()


def test_fileno_passthrough():
    t = _StdoutLineTracker(_FakeStdout())
    t._stop_flush_timer()
    assert t.fileno() == 7
    t.close()


def test_write_flush_pass_through():
    real = _FakeStdout()
    t = _StdoutLineTracker(real)
    t._stop_flush_timer()
    t.write("hello\n")
    t.flush()
    assert real.written == "hello\n"
    assert t.write("x") == 1  # 返回写入长度
    t.close()


# ── 行跟踪 ───────────────────────────────────────────────

def test_track_complete_lines_into_ring():
    t = _mk(scroll_end=10)
    t.write("line1\nline2\n")
    assert list(t._ring) == ["line1", "line2"]
    t.close()


def test_track_partial_line_delayed():
    t = _mk(scroll_end=10)
    t.write("partial")
    assert list(t._ring) == []
    t.write(" complete\n")
    assert list(t._ring) == ["partial complete"]
    t.close()


def test_track_strips_ansi():
    t = _mk(scroll_end=10)
    t.write("\x1b[31mred\x1b[0m\n")
    assert list(t._ring) == ["red"]
    t.close()


def test_track_strips_trailing_cr():
    t = _mk(scroll_end=10)
    t.write("line\r\n")
    assert list(t._ring) == ["line"]
    t.close()


def test_track_disabled_when_scroll_end_zero():
    t = _mk(scroll_end=0)
    t.write("line\n")
    assert list(t._ring) == []
    t.close()


def test_track_bottom_bar_filtered():
    """光标定位到 scroll_end 之下（底部栏）的内容不进入环形缓冲。"""
    t = _mk(scroll_end=5)
    t.write("visible\n")
    t.write("\x1b[10;1Hbottom bar content\n")  # row 10 > scroll_end 5
    assert list(t._ring) == ["visible"]
    t.close()


def test_track_exits_bottom_bar_on_cursor_restore():
    t = _mk(scroll_end=5)
    t.write("\x1b[10;1Hhidden\n")  # 进入底部栏
    t.write("\x1b8")               # 恢复光标 → 退出底部栏
    t.write("after\n")
    assert list(t._ring) == ["after"]
    t.close()


def test_track_mixed_bottom_bar_segments():
    t = _mk(scroll_end=5)
    t.write("a\n")
    t.write("\x1b[9;1Hhidden part")
    t.write("\x1b[u")  # SCRC 恢复
    t.write(" b\n")
    assert list(t._ring) == ["a", " b"]
    t.close()


def test_ring_capacity_limit():
    t = _mk(scroll_end=1)
    for i in range(_StdoutLineTracker._MAX_LINES + 50):
        t.write(f"line{i}\n")
    assert len(t._ring) == _StdoutLineTracker._MAX_LINES
    assert t._ring[-1] == f"line{_StdoutLineTracker._MAX_LINES + 49}"
    t.close()


def test_partial_line_max_truncation(monkeypatch):
    monkeypatch.setattr(st, "_PARTIAL_LINE_MAX", 16)
    t = _mk(scroll_end=1)
    t.write("a" * 40)
    assert len(t._partial_line) == 16
    t.write("\n")
    # 截断保留尾部最新 16 字符；换行到来时尾 16 字符为 'a'*15+'\n' → 行内 15 个 a
    assert list(t._ring) == ["a" * 15]
    t.close()


def test_close_stops_tracking():
    t = _mk(scroll_end=10)
    t.write("before\n")
    t.close()
    t.write("after\n")
    assert list(t._ring) == ["before"]
    assert t._closed is True


def test_close_idempotent():
    t = _mk(scroll_end=10)
    t.close()
    t.close()  # 二次 close 不抛异常


def test_track_public_api_equivalent():
    t = _mk(scroll_end=10)
    t.track("hello\n")  # 公开入口
    assert list(t._ring) == ["hello"]
    t.close()


# ── 输出历史刷盘 ─────────────────────────────────────────

def test_flush_buffered_lines_writes_file(tracker, tmp_path):
    tracker.set_scroll_end(10)
    tracker.write("alpha\nbeta\n")
    assert tracker._flush_buffered_lines() is True
    content = (tmp_path / "output_history").read_text(encoding="utf-8")
    assert content.splitlines() == ["alpha", "beta"]


def test_flush_empty_buffer_returns_true(tracker):
    assert tracker._flush_buffered_lines() is True


def test_close_flushes_pending_lines(tracker, tmp_path):
    tracker.set_scroll_end(10)
    tracker.write("one\ntwo\n")
    tracker.close()
    content = (tmp_path / "output_history").read_text(encoding="utf-8")
    assert content.splitlines() == ["one", "two"]


def test_flush_lock_failure_returns_lines(monkeypatch, tracker):
    """加锁失败时行放回缓冲（防丢行语义），返回 False。"""
    monkeypatch.setattr(st, "_lock_history_file", lambda fd, shared: False)
    tracker.set_scroll_end(10)
    tracker.write("x\n")
    assert tracker._flush_buffered_lines() is False
    assert tracker._output_buffer == ["x"]  # 放回


def test_flush_oserror_returns_lines(monkeypatch, tracker):
    """OSError 时行放回缓冲（BUG-19 防丢行）。"""
    tracker.set_scroll_end(10)
    tracker.write("x\n")
    monkeypatch.setattr(
        st.Path, "mkdir",
        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")),
    )
    assert tracker._flush_buffered_lines() is False
    assert tracker._output_buffer == ["x"]


def test_buffer_to_output_spawns_worker_at_threshold(tracker, tmp_path):
    """缓冲达 50 行触发异步刷盘 worker（单飞），最终落盘。"""
    tracker.set_scroll_end(10)
    for i in range(50):
        tracker.write(f"l{i}\n")
    # 等待 worker 完成
    tracker._flush_history()
    content = (tmp_path / "output_history").read_text(encoding="utf-8")
    assert len(content.splitlines()) == 50


def test_output_buffer_max_caps(monkeypatch, tracker):
    """缓冲超上限裁剪最旧行（P2-4 内存保护）。"""
    monkeypatch.setattr(st, "_OUTPUT_BUFFER_MAX", 10)
    monkeypatch.setattr(st, "_lock_history_file", lambda fd, shared: False)  # 阻止刷盘
    tracker.set_scroll_end(10)
    for i in range(15):
        tracker.write(f"l{i}\n")
    assert len(tracker._output_buffer) == 10
    assert tracker._output_buffer[0] == "l5"  # 最旧 5 行被丢弃


# ── 输出历史加载与压缩 ───────────────────────────────────

def test_load_output_history_restores_ring(tmp_path, monkeypatch):
    hist = tmp_path / "output_history"
    hist.parent.mkdir(parents=True, exist_ok=True)
    hist.write_text("old1\nold2\n", encoding="utf-8")
    monkeypatch.setattr(st, "OUTPUT_HISTORY_FILE", hist)
    t = _StdoutLineTracker(io.StringIO())
    t._stop_flush_timer()
    assert "old1" in t._ring
    assert "old2" in t._ring
    t.close()


def test_load_output_history_missing_is_silent(tracker):
    assert list(tracker._ring) == []


def test_compact_cooldown_blocks_repeat(monkeypatch, tracker, tmp_path):
    hist = tmp_path / "output_history"
    hist.write_text("a\nb\n" * 3000, encoding="utf-8")  # 6000 行 > 5000
    monkeypatch.setattr(st, "_COMPACT_COOLDOWN", 3600.0)
    assert tracker._maybe_compact_output_history() is True
    # 冷却期内再次调用 → 不压缩
    assert tracker._maybe_compact_output_history() is False


def test_compact_dedupes_and_truncates(monkeypatch, tracker, tmp_path):
    hist = tmp_path / "output_history"
    # 6000 行：大量重复 + 超过 5000 行
    lines = ["dup"] * 4000 + [f"u{i}" for i in range(2000)]
    hist.write_text("\n".join(lines) + "\n", encoding="utf-8")
    monkeypatch.setattr(st, "_COMPACT_COOLDOWN", 0.0)
    assert tracker._maybe_compact_output_history() is True
    content = hist.read_text(encoding="utf-8").splitlines()
    # 去重后 = 1 dup + 2000 唯一 = 2001 行 > 2000 → 保留最后 2000 行（dup 被裁掉）
    assert len(content) == 2000
    assert content == [f"u{i}" for i in range(2000)]


def test_compact_small_file_noop(tracker, tmp_path):
    hist = tmp_path / "output_history"
    hist.write_text("a\nb\nc\n", encoding="utf-8")
    assert tracker._maybe_compact_output_history() is False


def test_compact_missing_file_noop(tracker, tmp_path):
    assert tracker._maybe_compact_output_history() is False


def test_compact_lock_failure_noop(monkeypatch, tracker, tmp_path):
    hist = tmp_path / "output_history"
    hist.write_text("x\n" * 6000, encoding="utf-8")
    monkeypatch.setattr(st, "_lock_history_file", lambda fd, shared: False)
    assert tracker._maybe_compact_output_history() is False


# ── 定时器生命周期 ───────────────────────────────────────

def test_stop_flush_timer_prevents_restart(tracker):
    tracker._flush_timer_stop.set()
    tracker._start_flush_timer()  # 已停止 → 不创建新 timer
    assert tracker._flush_timer is None


def test_timer_callback_sets_pending_when_worker_inflight(tracker):
    tracker._flush_in_progress = True
    tracker._output_buffer = ["x"]
    tracker._timer_flush_callback()
    assert tracker._pending_flush is True
