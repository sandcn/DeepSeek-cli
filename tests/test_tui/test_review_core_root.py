"""TUI 根目录核心模块 Code Review 修复回归测试（2026-08-15）。

覆盖修复：
  - P1-1 [_diff_renderer] diff 文件头判定过宽——删除行内容以 ``-- `` 开头
    （diff 行 ``--- comment``）/新增行内容以 ``++ `` 开头（diff 行 ``+++ i``）
    不再误判为文件头；正常文件头仍识别；fromfile/tofile 为任意字符串的标准
    difflib 输出不被误判。
  - P2-1 [_diff_renderer] 同一 diff 重复解析——render_diff 返回 parsed，
    摘要函数复用（render_diff_to_ansi/show_file_diff 各只解析一次）。
  - P2-2 [_diff_renderer] _write_diff_line 截断路径异常降级为不截断原样输出。
  - P2-3 [_stdout_tracker] _flush_history 循环耗尽兜底刷盘前检测在途 worker，
    不并发取批（行序保护）。
  - P2-4 [_stdout_tracker] _output_buffer 超限丢弃最旧行（内存有界 + 告警）。
  - P2-5 [_lifecycle] stop() 复位 _handlers_bound=False（修复前残留 True）。
  - P2-6 [_assembly] assemble() 部分失败时清理已创建的 line_tracker
    （防 daemon 定时器泄漏）。
"""

from __future__ import annotations

import difflib
import io
from unittest.mock import Mock

import pytest

from src.tui._diff_renderer import (
    _parse_diff_hunks,
    _write_diff_line,
    render_diff_to_ansi,
    show_file_diff,
)
from src.tui._stdout_tracker import (
    _OUTPUT_BUFFER_MAX,
    _StdoutLineTracker,
)


# ── 工具 ────────────────────────────────────────────────

def _unified(old, new, fromfile="a/f.sql", tofile="b/f.sql"):
    """构造 difflib.unified_diff 标准输出（fromfile/tofile 可任意指定）。"""
    return list(difflib.unified_diff(
        old, new, fromfile=fromfile, tofile=tofile, lineterm="", n=3,
    ))


class _Collector:
    """收集 write_line 调用的简单输出目标（与 render_diff_to_ansi 同型）。"""

    _target: list = []

    @classmethod
    def write_line(cls, text: str) -> None:
        cls._target.append(text)


# ── P1-1：diff 文件头判定过宽 ─────────────────────────────

def test_parse_diff_hunks_del_line_starting_with_double_dash_not_file_header():
    """P1-1：删除行内容以 ``-- `` 开头（SQL 注释 → diff 行 ``--- comment``）
    不被误判为 old_file（落入 del 分支）。"""
    old = ["keep", "-- comment", "keep2"]
    new = ["keep", "keep2"]
    parsed = _parse_diff_hunks(_unified(old, new, "a/f.sql", "b/f.sql"))
    types = [t for t, *_ in parsed]
    # 正常文件头仍识别
    assert types[0] == "old_file" and types[1] == "new_file"
    assert types[2] == "hunk"
    # 删除行 '--- comment' 落入 del（hunk 之后不再被误判为文件头）
    del_items = [p for p in parsed if p[0] == "del"]
    assert len(del_items) == 1
    assert del_items[0][1] == "--- comment"
    assert not any(p[0] == "old_file" and "comment" in p[1] for p in parsed)


def test_parse_diff_hunks_add_line_starting_with_double_plus_not_file_header():
    """P1-1：新增行内容以 ``++ `` 开头（diff 行 ``+++ i;``）不被误判为
    new_file（落入 add 分支）。"""
    old = ["int i = 0;", "return i;"]
    new = ["int i = 0;", "++ i;", "return i;"]
    parsed = _parse_diff_hunks(_unified(old, new, "a/f.c", "b/f.c"))
    types = [t for t, *_ in parsed]
    assert types[0] == "old_file" and types[1] == "new_file"
    add_items = [p for p in parsed if p[0] == "add"]
    assert len(add_items) == 1
    assert add_items[0][1] == "+++ i;"
    assert not any(p[0] == "new_file" and "i;" in p[1] for p in parsed)


def test_parse_diff_hunks_normal_file_headers_still_recognized():
    """P1-1：正常文件头（--- a/path / +++ b/path）仍被正确识别。"""
    old = ["a", "b"]
    new = ["a", "c"]
    parsed = _parse_diff_hunks(_unified(old, new, "a/f.py", "b/f.py"))
    assert parsed[0][0] == "old_file" and parsed[0][1] == "--- a/f.py"
    assert parsed[1][0] == "new_file" and parsed[1][1] == "+++ b/f.py"


def test_parse_diff_hunks_arbitrary_fromfile_tofile_not_misjudged():
    """P1-1：fromfile/tofile 为任意字符串的标准 difflib 输出不被误判
    （文件头恒在第一个 hunk 之前；hunk 后的 ---/+++ 前缀行为行内容）。"""
    old = ["line1", "-- data"]
    new = ["line1", "++ data", "line3"]
    parsed = _parse_diff_hunks(_unified(old, new, "任意文件", "目标文件"))
    # 文件头正常识别（任意字符串路径）
    assert parsed[0][0] == "old_file" and parsed[0][1] == "--- 任意文件"
    assert parsed[1][0] == "new_file" and parsed[1][1] == "+++ 目标文件"
    # hunk 之后的 ---/+++ 前缀行为 del/add 行内容，不是文件头
    del_items = [p for p in parsed if p[0] == "del"]
    add_items = [p for p in parsed if p[0] == "add"]
    assert any(p[1] == "--- data" for p in del_items)
    assert any(p[1] == "+++ data" for p in add_items)


# ── P2-1：同一 diff 重复解析 ─────────────────────────────

def test_render_diff_to_ansi_parses_once(monkeypatch):
    """P2-1：render_diff_to_ansi 对同一 diff 只解析一次（render_diff 返回
    parsed 供摘要复用，打桩计数验证）。"""
    from src.tui import _diff_renderer as dr

    calls: list = []
    orig = dr._parse_diff_hunks

    def _spy(diff_list, line_offset=0):
        calls.append(line_offset)
        return orig(diff_list, line_offset)

    monkeypatch.setattr(dr, "_parse_diff_hunks", _spy)
    dr.render_diff_to_ansi("f.py", "a\nb\n", "a\nc\n")
    assert len(calls) == 1, f"render_diff_to_ansi 应只解析一次，实际 {len(calls)} 次"


def test_show_file_diff_parses_once(monkeypatch):
    """P2-1：show_file_diff 对同一 diff 只解析一次（打桩计数验证）。"""
    from src.tui import _diff_renderer as dr

    calls: list = []
    orig = dr._parse_diff_hunks

    def _spy(diff_list, line_offset=0):
        calls.append(line_offset)
        return orig(diff_list, line_offset)

    monkeypatch.setattr(dr, "_parse_diff_hunks", _spy)
    collected: list = []
    _Collector._target = collected
    dr.show_file_diff("f.py", "a\nb\n", "a\nc\n", output_target=_Collector)
    assert len(calls) == 1, f"show_file_diff 应只解析一次，实际 {len(calls)} 次"


# ── P2-2：_write_diff_line 截断路径异常降级 ───────────────

def test_write_diff_line_truncate_exception_falls_back(monkeypatch):
    """P2-2：截断路径（ansi_to_line）抛异常时降级为不截断原样输出。"""
    import src.renderer.ansi.helpers as helpers

    def _boom(text):
        raise RuntimeError("ansi_to_line 故障")

    monkeypatch.setattr(helpers, "ansi_to_line", _boom)
    collected: list = []
    _Collector._target = collected
    _write_diff_line("some text", _Collector, width=30)
    assert collected == ["some text"], "截断异常应降级为不截断原样输出"


# ── P2-3：_flush_history 兜底不并发取批 ───────────────────

def test_flush_history_fallback_waits_for_inflight_worker():
    """P2-3：循环耗尽兜底刷盘前检测在途 worker（_flush_in_progress=True），
    改走 join 等待而非直接取批（不并发取批，行序保护）。"""
    tracker = _StdoutLineTracker(io.StringIO())
    try:
        tracker._output_buffer = ["line1", "line2"]
        # 在途 worker：Mock 线程（is_alive 恒 True、join 立即返回）→ 循环
        # 2000 次快速耗尽，进入 for-else 兜底分支
        fake_thread = Mock()
        fake_thread.is_alive.return_value = True
        tracker._flush_worker_thread = fake_thread
        tracker._flush_in_progress = True
        # 打桩刷盘：兜底分支不应并发取批（不被调用）
        tracker._flush_buffered_lines = Mock(return_value=True)

        tracker._flush_history()

        # 在途 worker 未完成（Mock 恒 True）→ 兜底刷盘被跳过，未取批
        tracker._flush_buffered_lines.assert_not_called()
        # join 等待被调用（改走 join 等待而非直接取批）
        fake_thread.join.assert_called()
    finally:
        tracker.close()


def test_flush_history_fallback_flushes_when_no_inflight_worker():
    """P2-3：兜底刷盘在无在途 worker 时正常执行（行为不回归）。"""
    tracker = _StdoutLineTracker(io.StringIO())
    try:
        tracker._output_buffer = ["line1", "line2"]
        tracker._flush_worker_thread = None
        tracker._flush_in_progress = False
        tracker._flush_buffered_lines = Mock(return_value=True)

        tracker._flush_history()

        tracker._flush_buffered_lines.assert_called()
    finally:
        tracker.close()


# ── P2-4：_output_buffer 超限丢弃最旧行 ──────────────────

def test_output_buffer_bounded_drops_oldest():
    """P2-4：_output_buffer 超限丢弃最旧行（刷盘持续失败时内存有界），
    最新行保留。"""
    tracker = _StdoutLineTracker(io.StringIO())
    try:
        # 打桩避免真实刷盘线程/写盘（本测试只验证缓冲有界语义）
        tracker._spawn_flush_worker_locked = Mock()
        tracker._flush_buffered_lines = Mock(return_value=True)
        tracker._flush_in_progress = False

        for i in range(_OUTPUT_BUFFER_MAX + 10):
            tracker._buffer_to_output(f"line-{i}")

        assert len(tracker._output_buffer) == _OUTPUT_BUFFER_MAX, (
            f"缓冲应裁剪到上限 {_OUTPUT_BUFFER_MAX}，实际 {len(tracker._output_buffer)}"
        )
        # 最旧 10 行被丢弃；最新行保留
        assert tracker._output_buffer[0] == "line-10", "最旧行应被丢弃"
        assert tracker._output_buffer[-1] == f"line-{_OUTPUT_BUFFER_MAX + 9}", "最新行应保留"
    finally:
        tracker.close()


# ── P2-5：stop() 复位 _handlers_bound ────────────────────

def test_lifecycle_stop_resets_handlers_bound(monkeypatch):
    """P2-5：stop() 复位 _handlers_bound=False（修复前残留 True，下次 start
    产生多余 unsubscribe 且外部误判为仍已绑定）。"""
    from src.tui._lifecycle import TuiLifecycle

    # 隔离全局历史写盘冲刷（不影响其他测试的单例 writer）
    monkeypatch.setattr("src.tui._lifecycle.flush_history_disk", lambda timeout=2.0: True)

    engine = Mock()
    bus = Mock()
    rs = Mock()
    dispatcher = Mock()
    dispatcher.list_handlers.return_value = {"evt": lambda e: None}
    bb = Mock()

    lc = TuiLifecycle(engine=engine, bus=bus, bb=bb, rs=rs, dispatcher=dispatcher)
    lc.start()
    assert lc.handlers_bound is True

    lc.stop()
    assert lc.handlers_bound is False, "stop() 后 _handlers_bound 应复位为 False"

    # stop 后再次 start 正常（无残留半停止状态，不产生多余 unsubscribe 异常）
    lc.start()
    assert lc.handlers_bound is True
    lc.stop()
    assert lc.handlers_bound is False


# ── P2-6：assemble 部分失败清理 ──────────────────────────

def test_assemble_partial_failure_closes_tracker(monkeypatch):
    """P2-6：assemble 中途失败时已创建的 line_tracker 被 close()
    （防 __init__ 启动的 daemon 定时器泄漏）。"""
    from src.tui import _assembly_steps
    from src.tui._assembly import TuiAssembly

    tracker = Mock()
    monkeypatch.setattr(_assembly_steps, "create_infrastructure", lambda: tracker)

    def _boom(*args, **kwargs):
        raise RuntimeError("create_shared 装配失败")

    # 在 create_infrastructure（tracker 已创建）之后、create_framework
    # （真实重量对象创建）之前失败——避免真实 InkSession 创建
    monkeypatch.setattr(_assembly_steps, "create_shared", _boom)

    with pytest.raises(RuntimeError, match="create_shared"):
        TuiAssembly.assemble()

    tracker.close.assert_called_once()


def test_assemble_success_does_not_close_tracker(monkeypatch):
    """P2-6：assemble 成功时不应误 close tracker（清理仅限失败路径）。"""
    from src.tui import _assembly_steps
    from src.tui._assembly import TuiAssembly

    tracker = Mock()
    monkeypatch.setattr(_assembly_steps, "create_infrastructure", lambda: tracker)
    # 打桩后续重量步骤为轻量假对象（避免真实 InkSession/AppModel 创建）
    monkeypatch.setattr(_assembly_steps, "create_shared", lambda: (Mock(), Mock()))
    monkeypatch.setattr(_assembly_steps, "create_chat_domain", lambda: Mock())
    monkeypatch.setattr(_assembly_steps, "create_framework", lambda *a, **k: (Mock(), Mock(), Mock()))
    monkeypatch.setattr(
        _assembly_steps, "create_chat_domain_assembly",
        lambda *a, **k: (Mock(), Mock(), Mock()),
    )

    result = TuiAssembly.assemble()
    assert result.engine is not None
    tracker.close.assert_not_called()
