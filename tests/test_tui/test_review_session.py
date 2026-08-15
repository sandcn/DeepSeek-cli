"""InkSession/InkRenderer Code Review 问题修复测试（2026-08-15）。

覆盖修复点（全部为 Code Review 发现）：
  - P1-1 [session.flush] flush() 泄漏非 daemon 线程，进程退出挂起 →
    创建的 task_done 线程须为 daemon=True；
  - P2-1 [session.clear_screen] 渲染失败被吞且不补 _dirty →
    失败后补置 _dirty（下一 10Hz 拍重试，Ctrl+L 后屏幕不永久空白）；
  - P2-2 [session._put_no_drop] 背压无上限 → 超时（_PUT_NO_DROP_TIMEOUT）
    回退丢弃并记 warning，不无限阻塞调用方；
  - P2-3 [session.resume] 无条件覆盖 _render_thread → 旧线程仍存活时先
    join(timeout=...) 再决定是否启动新线程；
  - P2-4 [session.stop] join 超时后仍重置渲染器 → 线程仍存活时跳过
    _ink_renderer.suspend()（渲染器状态保留至线程真正退出）；
  - P2-5 [session._update_system_stats] int(cpu)/int(mem) 在 try 之外 →
    _safe_int() 防御（非数字输入不崩溃渲染线程）；
  - P3-1 [renderer.place_cursor] col 无下限钳制 → col=max(1, col) 防御
    （col<=0 时不再输出非法 ANSI）。

测试在纯 Python 环境运行（不依赖真实终端）；用 unittest.mock 打桩
threading.Thread / queue / _render_frame / _SystemMonitor 等。
"""

from __future__ import annotations

import io
import queue
import time
from unittest.mock import Mock, patch

from src.tui.app.app import build_app_element
from src.tui.app.apply import apply_cmd
from src.tui.app.model import AppModel
from src.tui._config import TuiConfig
from src.tui._screen import TerminalWidthCache
from src.tui.ink.session import InkSession, _safe_int
from src.tui.ink.renderer import InkRenderer
from src.tui._const import ContentCmd
from src.tui.ink._cmd_priority import _get_cmd_priority


# ── 测试辅助 ─────────────────────────────────────────

def _make_session() -> tuple[InkSession, AppModel, io.StringIO]:
    """构造 InkSession（沿用 ink 测试既有模式：真实 AppModel + StringIO）。"""
    cache = TerminalWidthCache.get_default()
    cache._width = 80
    cache._height = 24
    model = AppModel()
    stream = io.StringIO()
    session = InkSession(
        model=model,
        apply_cmd=apply_cmd,
        build_tree=build_app_element,
        config=TuiConfig.defaults(),
        stream=stream,
    )
    session.set_line_tracker(None)
    return session, model, stream


class _FakeThread:
    """模拟 threading.Thread：记录 daemon 参数；start 后 alive、join 后结束。"""

    def __init__(self, daemon=None, **kwargs):
        self.daemon = daemon
        self._alive = False
        self.join_calls: list = []

    def start(self) -> None:
        self._alive = True

    def join(self, timeout=None) -> None:
        self.join_calls.append(timeout)
        self._alive = False  # 模拟 join 后线程已退出

    def is_alive(self) -> bool:
        return self._alive


class _FakeStuckThread:
    """模拟卡死的渲染线程：is_alive 恒 True，join 不改变状态（join 超时场景）。"""

    def __init__(self):
        self.join_calls: list = []

    def is_alive(self) -> bool:
        return True

    def join(self, timeout=None) -> None:
        self.join_calls.append(timeout)


def _patch_thread_factory(created: list):
    """patch threading.Thread 的 side_effect 工厂：记录创建、返回 _FakeThread。"""
    def factory(*args, **kwargs):
        t = _FakeThread(**kwargs)
        created.append(t)
        return t
    return factory


# ── P1-1 flush() task_done 线程须为 daemon ──────────────

def test_flush_task_done_thread_is_daemon():
    """flush() 创建的等待队列排空线程应为 daemon（修复前 daemon=False 泄漏
    非 daemon 线程 → 进程退出挂起）。"""
    session, _, _ = _make_session()
    # 模拟渲染线程存活（flush 才会进入创建 task_done 线程的分支）
    session._render_thread = _FakeStuckThread()
    created: list = []
    with patch(
        "src.tui.ink.session.threading.Thread",
        side_effect=_patch_thread_factory(created),
    ):
        session.flush(timeout=0.01)
    assert created, "flush() 应创建 task_done 线程"
    assert created[0].daemon is True, (
        f"flush 创建的线程应为 daemon=True，实际 daemon={created[0].daemon}"
    )


def test_flush_no_thread_when_render_thread_none():
    """flush() 渲染线程不存在时走排空分支，不创建线程（无泄漏）。"""
    session, _, _ = _make_session()
    session._render_thread = None
    created: list = []
    with patch(
        "src.tui.ink.session.threading.Thread",
        side_effect=_patch_thread_factory(created),
    ):
        session.flush(timeout=0.01)
    assert created == [], "渲染线程不存在时 flush 不应创建线程"


# ── P2-1 clear_screen() 渲染失败补 _dirty ───────────────

def test_clear_screen_sets_dirty_on_render_failure():
    """clear_screen() 渲染失败后应补置 _dirty（修复前失败被吞且不补 →
    Ctrl+L 后屏幕空白且空闲时永不重绘）。"""
    session, _, _ = _make_session()
    session._dirty = False
    with patch.object(session, "_render_frame", side_effect=RuntimeError("boom")):
        session.clear_screen()
    assert session._dirty is True, "clear_screen 渲染失败应补置 _dirty"


def test_clear_screen_success_keeps_dirty_state():
    """clear_screen() 成功时（渲染正常）不额外置脏（原语义不回归）。"""
    session, _, _ = _make_session()
    session._dirty = False
    session.clear_screen()  # _render_frame 正常（build_tree 已注入）
    assert session._dirty is False, "clear_screen 成功不应置 _dirty"


# ── P2-2 _put_no_drop() 背压超时回退 ────────────────────

def test_put_no_drop_timeout_falls_back_without_blocking():
    """_put_no_drop() 背压超时（_PUT_NO_DROP_TIMEOUT 后）应回退返回 False，
    不无限阻塞调用方（修复前 while 循环无限重试）。"""
    session, _, _ = _make_session()
    session._render_running = True
    cmd = ContentCmd(text="内容")
    priority = _get_cmd_priority(cmd)
    # ★ 架构改进方向 A（2026-08-16）：_PUT_NO_DROP_TIMEOUT 常量已随 _put_no_drop
    #   迁移至 _session_queue_mixin（唯一使用方）——patch 目标同步更新
    #   （session 模块仅 re-export，patch 旧路径不生效）。
    with patch("src.tui.ink._session_queue_mixin._PUT_NO_DROP_TIMEOUT", 0.05), \
            patch.object(session._cmd_queue, "put", side_effect=queue.Full):
        start = time.monotonic()
        result = session._put_no_drop(priority, cmd)
        elapsed = time.monotonic() - start
    assert result is False, "背压超时应回退返回 False（调用方走丢弃告警路径）"
    assert elapsed < 2.0, f"背压超时应快速返回，实际耗时 {elapsed:.2f}s"


def test_put_no_drop_success_when_queue_available():
    """_put_no_drop() 队列有空间时正常入队返回 True（语义兼容）。"""
    session, _, _ = _make_session()
    session._render_running = True
    cmd = ContentCmd(text="内容")
    priority = _get_cmd_priority(cmd)
    result = session._put_no_drop(priority, cmd)
    assert result is True, "队列可用时应入队成功"
    assert session._cmd_queue.qsize() == 1, "命令应已入队"


# ── P2-3 resume() 前检查旧线程 ─────────────────────────

def test_resume_joins_old_thread_when_alive():
    """resume() 旧渲染线程仍存活时应先 join(timeout=...) 再启动新线程
    （修复前无条件覆盖 → 两个渲染线程并发双写终端）。"""
    session, _, _ = _make_session()
    old = _FakeStuckThread()
    session._render_thread = old
    session._render_running = False
    created: list = []
    with patch(
        "src.tui.ink.session.threading.Thread",
        side_effect=_patch_thread_factory(created),
    ), patch.object(session, "_render_frame"):
        session.resume()
    assert old.join_calls, "resume() 旧线程仍存活时应先 join"
    assert created, "resume() 应启动新渲染线程"
    assert created[0].daemon is True, "新渲染线程应为 daemon"


def test_resume_skips_join_when_old_thread_exited():
    """resume() 旧线程已退出时不 join（直接启动新线程，零延迟）。"""
    session, _, _ = _make_session()
    old = _FakeThread()
    session._render_thread = old
    session._render_running = False
    created: list = []
    with patch(
        "src.tui.ink.session.threading.Thread",
        side_effect=_patch_thread_factory(created),
    ), patch.object(session, "_render_frame"):
        session.resume()
    assert old.join_calls == [], "旧线程已退出时不应 join"
    assert created, "resume() 应启动新渲染线程"


# ── P2-4 stop() join 超时后不重置渲染器 ─────────────────

def test_stop_skips_renderer_suspend_when_thread_stuck():
    """stop() join 超时（渲染线程仍存活）时跳过 _ink_renderer.suspend()
    （修复前超时后仍重置渲染器 → 渲染线程仍在写 stream 时并发清理撕裂）。"""
    session, _, _ = _make_session()
    session._render_thread = _FakeStuckThread()
    with patch.object(session._ink_renderer, "suspend") as m_suspend:
        session.stop()
    m_suspend.assert_not_called(), "join 超时线程仍存活时不应重置渲染器状态"


def test_stop_suspends_renderer_when_thread_exited():
    """stop() 渲染线程已退出（或未启动）时仍正常 suspend（原语义不回归）。"""
    session, _, _ = _make_session()
    session._render_thread = None
    with patch.object(session._ink_renderer, "suspend") as m_suspend:
        session.stop()
    m_suspend.assert_called_once(), "线程已退出时 stop 应正常 suspend 渲染器"


# ── P2-5 _update_system_stats() 非数字输入不崩溃 ─────────

def test_update_system_stats_handles_non_numeric():
    """_update_system_stats() 对非数字输入（"N/A"）不崩溃（修复前
    int("N/A") 抛 ValueError 使渲染线程崩溃）。"""
    session, model, _ = _make_session()
    session._last_sys_stats_time = 0.0  # 保证 interval 检查通过
    monitor = Mock()
    monitor.get_cpu_and_mem.return_value = ("N/A", "N/A")
    session._system_monitor = monitor
    status = model.status
    before_cpu, before_mem = status.cpu, status.mem
    session._update_system_stats()  # 不应抛异常
    assert status.cpu == before_cpu, "非数字输入应保持原值"
    assert status.mem == before_mem, "非数字输入应保持原值"


def test_update_system_stats_numeric():
    """_update_system_stats() 正常数字输入更新 status 并置脏（原语义保持）。"""
    session, model, _ = _make_session()
    session._last_sys_stats_time = 0.0
    monitor = Mock()
    monitor.get_cpu_and_mem.return_value = ("12", "34")
    session._system_monitor = monitor
    status = model.status
    status.cpu = 0
    status.mem = 0
    session._update_system_stats()
    assert status.cpu == 12
    assert status.mem == 34
    assert session._dirty is True, "数值变化应置脏触发渲染"


def test_safe_int_fallback():
    """_safe_int() 防御转换：非数字/None/非法格式回退默认值。"""
    assert _safe_int("12") == 12
    assert _safe_int(7.9) == 7
    assert _safe_int("N/A") == 0
    assert _safe_int(None) == 0
    assert _safe_int("3.5") == 0  # int("3.5") 抛 ValueError → 回退
    assert _safe_int("12", default=-1) == 12
    assert _safe_int("N/A", default=-1) == -1


# ── P3-1 place_cursor() col 防御钳制 ────────────────────

def test_place_cursor_clamps_col_lower_bound():
    """place_cursor() col<=0 时钳制到 1（修复前输出非法 ANSI 污染终端）。"""
    r = InkRenderer(stream=io.StringIO())
    r.place_cursor(1, 0)
    r.place_cursor(1, -5)
    out = r._stream.getvalue()
    assert "\033[0C" not in out, "col<=0 不应输出 0 列前进序列"
    assert "\033[-" not in out, "col<=0 不应输出负数列前进序列"


def test_place_cursor_col_normal_behavior():
    """place_cursor() 正常 col 行为不变（col=5 → 4 列前进）。"""
    r = InkRenderer(stream=io.StringIO())
    r.place_cursor(1, 5)
    out = r._stream.getvalue()
    assert "\033[4C" in out, "col=5 应输出 4 列前进序列"
