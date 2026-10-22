"""浮窗渲染性能重构回归测试（2026-10-21）。

覆盖三处性能改动（语义必须与重构前一致）：

  1. ``components._merge_window_row`` 改为**按窗口区域切片**合并（不再把整行
     转列键字典 + 全量重建）——不透明/透明、CJK 宽字符边界、窗口出屏裁剪、
     行短于窗口、底层行左右内容保留；
  2. ``render_frame`` 每帧只做**一遍**浮窗收集（定位阶段写
     ``root._float_frame_windows``，合成层复用）；
  3. ``FloatingWindowSpec.refresh_interval``：带该字段的时间驱动浮窗不再阻止
     空闲帧复用，由会话按间隔强制重建（``_float_refresh_due``）。``/loop``
     浮窗按用户确认**保持每帧刷新**（不声明该字段）。
"""

from __future__ import annotations

import time
from types import SimpleNamespace

from src.tui._assembly_steps import _make_idle_frame_predicate, _window_blocks_idle
from src.tui.app._state_types import LoopStatusState
from src.tui.app.app import build_app_element
from src.tui.app.floating import FloatingWindowSpec, loop_window_spec
from src.tui.app.model import AppModel
from src.tui.ink import _floating, components
from src.tui.ink._paint_canvas import _slice_run_text_strict, _span_at
from src.tui.ink._render_api import renderToString
from src.tui.ink._session_frame_mixin import _SessionFrameMixin
from src.tui.ink.helpers import strip_ansi
from src.tui.ink.output import Line
from src.tui.ink.reconciler import Reconciler


# ── 辅助 ─────────────────────────────────────────────


def _line(text: str) -> Line:
    line = Line()
    line.append(text, None)
    return line


def _row(*chars: str, start: int = 0) -> dict:
    """按显示宽度构造浮窗画布行（列键 = 窗口局部列）。"""
    cells: dict = {}
    col = start
    for ch in chars:
        cells[col] = (ch, None)
        col += max(1, _width(ch))
    return cells


def _width(text: str) -> int:
    from src.tui._width import wcswidth_simple
    return wcswidth_simple(text)


# ── 1. 合并语义（按窗口区域切片） ──────────────────────


def test_merge_opaque_replaces_rect_and_keeps_sides() -> None:
    merged = components._merge_window_row(
        _line("abcdefghij"), _row("W"), 3, 4, 10, True,
    )
    assert merged is not None
    # 窗口矩形 [3,7) 整段占用：'d'/'e'/'f' 被清空，'W' 落在第 3 列
    assert merged.plain == "abcW   hij"
    assert merged.width == 10


def test_merge_transparent_only_occupies_drawn_cells() -> None:
    merged = components._merge_window_row(
        _line("abcdefghij"), _row("W"), 3, 4, 10, False,
    )
    assert merged is not None
    # 透明窗口只覆盖实际绘制字符的列（第 3 列），其余底层内容保留
    assert merged.plain == "abcWefghij"


def test_merge_cjk_boundary_keeps_alignment() -> None:
    # 窗口矩形 [3,5) 落在第二个「中」（占 2-3 列）与第三个「中」（占 4-5 列）
    # 的边界上——宽字符不能被切一半，左侧留白 1 列、右侧从第 6 列继续。
    merged = components._merge_window_row(
        _line("中中中中"), _row("X", "Y"), 3, 2, 10, True,
    )
    assert merged is not None
    assert merged.plain == "中 XY 中"
    assert merged.width == 8


def test_merge_window_partially_offscreen_left() -> None:
    merged = components._merge_window_row(
        _line("abcdefghij"),
        _row("A", "B", "C", "D", "E"),
        -2, 5, 10, True,
    )
    assert merged is not None
    # 出屏的 A/B 被丢弃，C/D/E 落在第 0/1/2 列，第 3 列起底层内容保留
    assert merged.plain == "CDEdefghij"


def test_merge_window_past_line_end() -> None:
    merged = components._merge_window_row(
        _line("abc"), _row("W"), 6, 4, 10, True,
    )
    assert merged is not None
    # 底层行短于窗口：中间补齐空格，W 仍落在第 6 列
    assert merged.plain == "abc   W"
    assert merged.width == 7


def test_merge_window_at_right_edge_clips() -> None:
    merged = components._merge_window_row(
        _line("abcdefghij"), _row("W"), 8, 4, 10, True,
    )
    assert merged is not None
    assert merged.plain == "abcdefghW"
    assert merged.width <= 10


def test_merge_empty_row_returns_none() -> None:
    assert components._merge_window_row(_line("abc"), {}, 0, 4, 10, True) is None


# ── 2. 切片辅助（严格切片 / 字符跨度） ─────────────────


def test_slice_run_text_strict_drops_straddling_wide_char() -> None:
    # 「中」各占 2 列：第一个 0-1，第二个 2-3。
    assert _slice_run_text_strict("中中", 0, 2) == ("中", 0)
    assert _slice_run_text_strict("中中", 2, 4) == ("中", 0)
    # [1,3) 与两个宽字符都只部分相交 → 整字丢弃（不返回半个宽字符）
    assert _slice_run_text_strict("中中", 1, 3) == ("", 0)
    # ASCII 快路径
    assert _slice_run_text_strict("abcdef", 2, 4) == ("cd", 0)


def test_span_at_reports_char_start_and_width() -> None:
    line = _line("a中b")
    assert _span_at(line, 0) == (0, 1)
    assert _span_at(line, 1) == (1, 2)  # 「中」首列
    assert _span_at(line, 2) == (1, 2)  # 「中」第二列（跨列字符内部）
    assert _span_at(line, 3) == (3, 1)
    assert _span_at(line, 4) is None


# ── 3. 每帧只做一遍浮窗收集 ────────────────────────────


def _render_once(model, width: int = 80):
    reconciler = Reconciler(None)
    root = Reconciler.create_root()
    element = build_app_element(model, width)
    reconciler.render(root, element, width, 24)
    frame = components.render_frame(root, width, viewport_height=24)
    return root, frame


def _loop_model() -> AppModel:
    model = AppModel()
    model.loop_status = LoopStatusState(
        active=True, total=3, round=2, prompt="继续", round_started_at=time.monotonic(),
    )
    model.floating_windows = [loop_window_spec()]
    return model


def test_render_frame_collects_floating_fibers_once(monkeypatch) -> None:
    model = _loop_model()
    _render_once(model)  # 预热（首帧建树/缓存）

    calls = {"n": 0}
    original = _floating._collect_float_fibers

    def _counting(*args, **kwargs):
        calls["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(_floating, "_collect_float_fibers", _counting)
    root, frame = _render_once(model)

    assert calls["n"] == 1, "浮窗收集应每帧只做一次（定位写入、合成复用）"
    text = "\n".join(strip_ansi(line.render()) for line in frame.lines)
    assert "⟳ /loop" in text
    assert "第 2/3 批" in text


def test_render_frame_still_works_without_floating_window() -> None:
    model = AppModel()
    root, frame = _render_once(model)
    assert root._float_frame_windows is None
    assert frame.lines


# ── 4. 浮窗定时刷新（refresh_interval） ────────────────


class _DummySession(_SessionFrameMixin):
    """最小会话替身：只提供空闲帧复用判定所需字段。"""

    def __init__(self, windows, built_at: float) -> None:
        self._model = SimpleNamespace(floating_windows=windows)
        self._last_frame = object()
        self._last_frame_idle_static = True
        self._idle_ok = True
        self._dirty = False
        self._resize_pending = False
        self._cmd_queue = None
        self._bottom_redraw_requested = None
        self._hook_ctx = None
        self._last_frame_built_at = built_at


def test_loop_window_spec_is_per_frame() -> None:
    """``/loop`` 浮窗保持每帧刷新（2026-10-22 用户确认）。"""
    spec = loop_window_spec()
    assert spec.refresh_interval is None
    assert spec.idle_static is False  # 内容并非永久静态
    # 每帧刷新的浮窗阻止空闲帧复用（保守：内容不会冻结）
    assert _window_blocks_idle(spec) is True


def test_refresh_interval_normalization() -> None:
    assert FloatingWindowSpec(id="w").refresh_interval is None
    assert FloatingWindowSpec(id="w", refresh_interval=0).refresh_interval is None
    assert FloatingWindowSpec(id="w", refresh_interval=-1).refresh_interval is None
    assert FloatingWindowSpec(id="w", refresh_interval=float("nan")).refresh_interval is None
    assert FloatingWindowSpec(id="w", refresh_interval="0.5").refresh_interval == 0.5
    spec = FloatingWindowSpec(id="w", refresh_interval=0.25)
    assert spec.copy_with(refresh_interval=1.0).refresh_interval == 1.0


def test_window_blocks_idle_three_states() -> None:
    volatile = SimpleNamespace(idle_static=False, refresh_interval=None)
    static = SimpleNamespace(idle_static=True, refresh_interval=None)
    timed = SimpleNamespace(idle_static=False, refresh_interval=0.25)
    # 默认（内容未知且可能每帧变化）→ 阻止空闲复用（保守）
    assert _window_blocks_idle(volatile) is True
    # 只依赖自身 props → 复用安全
    assert _window_blocks_idle(static) is False
    # 时间驱动但按间隔采样 → 允许复用，由会话到期强制重建
    assert _window_blocks_idle(timed) is False
    # 非法间隔 → 按「每帧刷新」保守处理
    assert _window_blocks_idle(SimpleNamespace(refresh_interval=0.0)) is True
    assert _window_blocks_idle(SimpleNamespace(refresh_interval=float("nan"))) is True


def test_float_refresh_due_only_for_interval_windows() -> None:
    now = time.monotonic()
    timed = FloatingWindowSpec(id="timed", refresh_interval=0.25)
    # 未到期 → 可复用（不强制重建）
    session = _DummySession([timed], built_at=now)
    assert session._float_refresh_due() is False
    assert session._should_reuse_idle_frame(True) is True
    # 到期 → 强制重建
    session = _DummySession([timed], built_at=now - 10.0)
    assert session._float_refresh_due() is True
    assert session._should_reuse_idle_frame(True) is False
    # 无浮窗 / 无间隔窗口（含 /loop 默认每帧刷新）→ 不额外约束
    assert _DummySession([], built_at=0.0)._float_refresh_due() is False
    plain_spec = FloatingWindowSpec(id="plain", idle_static=True)
    assert _DummySession([plain_spec], built_at=0.0)._float_refresh_due() is False
    assert _DummySession([loop_window_spec()], built_at=0.0)._float_refresh_due() is False


def test_float_refresh_due_uses_min_interval() -> None:
    now = time.monotonic()
    slow = FloatingWindowSpec(id="slow", refresh_interval=5.0)
    fast = FloatingWindowSpec(id="fast", refresh_interval=0.1)
    session = _DummySession([slow, fast], built_at=now - 0.2)
    assert session._float_refresh_due() is True


def test_idle_predicate_floating_windows() -> None:
    model = AppModel()
    session = SimpleNamespace(_input_fiber=None)
    # 默认窗口（无 idle_static / refresh_interval）→ 阻止空闲复用
    model.floating_windows = [FloatingWindowSpec(id="w")]
    assert _make_idle_frame_predicate(model, session)() is False
    # /loop 窗口（每帧刷新）→ 同样阻止空闲复用（2026-10-22 用户确认）
    model.floating_windows = [loop_window_spec()]
    assert _make_idle_frame_predicate(model, session)() is False
    # 显式声明 refresh_interval 的时间驱动窗口 → 允许复用（会话到期重建）
    model.floating_windows = [FloatingWindowSpec(id="timed", refresh_interval=0.25)]
    assert _make_idle_frame_predicate(model, session)() is True
    # idle_static=True → 允许复用
    model.floating_windows = [FloatingWindowSpec(id="static", idle_static=True)]
    assert _make_idle_frame_predicate(model, session)() is True


# ── 5. 端到端渲染（renderToString 走同一合成层） ────────


def test_render_to_string_shows_loop_window_content() -> None:
    model = _loop_model()
    text = strip_ansi(
        renderToString(build_app_element(model, 90), {"columns": 90})
    )
    assert "⟳ /loop" in text
    assert "第 2/3 批" in text
    assert "继续" in text
