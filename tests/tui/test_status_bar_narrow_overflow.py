"""状态栏窄屏溢出：显示不完整的项上移到顶部分隔线最右侧（2026-10-21 用户需求）。

需求：终端过窄、状态项放不下时，**不截断**状态项——状态行只保留能完整显示的
项，显示不完整的项（含只差一点就放不下的）整体显示在**最顶那条分隔线的最右端**
（左侧仍以 ``━`` 填充）。

覆盖：
  - 宽终端：行为不变（分隔线纯 ``━``，全部项在状态行）；
  - 窄终端：状态行仅完整项，溢出项整项出现在分隔线右端；
  - 边界：项恰好占满宽度（仍完整显示）、宽度差 1 列（该项整体上移）；
  - 极窄屏：状态行无完整项 → 只渲染分隔线（溢出内容截断 …）；
  - 行宽不变量：任意宽度下每行显示宽度 <= width。
"""

from __future__ import annotations

import time

import pytest

from src.tui.app.model import AppModel
from src.tui.app import status_bar as sb
from src.tui.app.status_bar import (
    _build_status_items,
    _build_status_runs,
    _join_status_items,
    _layout_status_items,
    _overflow_trailing_runs,
)


@pytest.fixture(autouse=True)
def _no_workspace_segment(monkeypatch):
    """屏蔽 workspace 段（分支/目录随运行环境变化，会破坏固定宽度断言）。"""
    monkeypatch.setattr(sb, "_workspace_segment", lambda ctx: [])


def _model(active: bool = True) -> AppModel:
    """状态栏测试模型（模型名 + 工具 + 耗时 + 消息数 + token + 速度）。"""
    model = AppModel()
    model.status.model_name = "deepseek-chat"
    model.status.status_active = active
    model.status.tool_total = 3
    model.status.tool_count = 1
    model.message_source = lambda: [
        {"role": "user"}, {"role": "assistant"},
    ]
    # 快照 TTL 缓存在 status_bar 模块（WeakKeyDictionary 键控 model）
    sb._snapshot_cache[model] = (
        time.monotonic(),
        {"elapsed_seconds": 65.0, "total_tokens": 1500,
         "per_second_speed": 42.0},
    )
    return model


def _lines(model, width: int) -> list[str]:
    """渲染 StatusBar 为纯文本行（去 ANSI）。"""
    from src.tui.ink import h, renderToString, strip_ansi

    out = renderToString(
        h(sb.StatusBar, {"model": model, "width": width}),
        {"columns": width},
    )
    return [strip_ansi(line) for line in out.split("\n")]


def _items(model):
    return _build_status_items(model, 0.0, "\u00b7", "")


def _text(runs) -> str:
    """runs / 项列表 → 纯文本（项列表按嵌套展平）。"""
    parts: list[str] = []
    for run in runs:
        if isinstance(run, (list, tuple)):
            parts.append(_text(run))
        else:
            parts.append(run.text)
    return "".join(parts)


# ── 布局分流（纯函数） ──────────────────────────────────────


def test_layout_wide_keeps_everything_on_status_line():
    """宽终端：无溢出，全部项在状态行（单行逻辑不变）。"""
    built = _items(_model())
    layout = _layout_status_items(built.items, 80, built.model_first)
    assert layout.overflow_runs == []
    text = _text(layout.line_runs)
    assert text.startswith("\u00b7 deepseek-chat")
    for piece in ("\u2699 1/3", "\u23f1 1:05", "\u2709 2",
                  "\u25c6 1.5kt", "\u00bb 42.0t/s"):
        assert piece in text


def test_layout_narrow_moves_unfittable_items_to_overflow():
    """窄终端：放不下的 token/速度段整项进入溢出片段。"""
    built = _items(_model())
    layout = _layout_status_items(built.items, 40, built.model_first)
    line = _text(layout.line_runs)
    overflow = _text(layout.overflow_runs)
    # 恰好占满 40 列的「✉ 2」完整保留在状态行
    assert "\u2709 2" in line
    assert "\u25c6" not in line and "\u00bb" not in line
    assert overflow.startswith("\u25c6 1.5kt")
    assert "\u00bb 42.0t/s" in overflow


def test_layout_boundary_exact_fit_keeps_item_and_minus_one_moves_it():
    """边界：项恰好占满宽度 → 完整显示；再窄 1 列 → 该项整体上移。"""
    from src.tui.ink.helpers import _runs_total_width

    built = _items(_model())
    items = built.items
    # 前 3 项（模型/工具/耗时）恰好占满的宽度——模型段后为双空格间距（2 列）
    exact = (2 + _runs_total_width(items[0])
             + 2 + _runs_total_width(items[1])
             + 3 + _runs_total_width(items[2]))
    fits = _layout_status_items(items, exact, built.model_first)
    assert _text(fits.line_runs) == _text(
        _join_status_items(items[:3], built.model_first))
    assert _text(fits.overflow_runs).startswith("\u2709 2")

    narrower = _layout_status_items(items, exact - 1, built.model_first)
    assert _text(narrower.line_runs) == _text(
        _join_status_items(items[:2], built.model_first))
    assert _text(narrower.overflow_runs) == _text(
        _join_status_items(items[2:], built.model_first, 2))


def test_layout_tiny_width_all_overflow():
    """极窄屏：状态行放不下任何项 → 全部项进入溢出片段。"""
    built = _items(_model())
    layout = _layout_status_items(built.items, 10, built.model_first)
    assert layout.line_runs == []
    assert _text(layout.overflow_runs).startswith("\u00b7 deepseek-chat")


# ── 渲染（组件级） ──────────────────────────────────────────


def test_render_wide_unchanged():
    """宽终端：两行（纯分隔线 + 完整状态行），行为与修复前一致。"""
    lines = _lines(_model(), 80)
    assert len(lines) == 2
    assert lines[0].rstrip() == "\u2501" * 80
    assert "\u25c6 1.5kt" in lines[1] and "42.0t/s" in lines[1]


def test_render_narrow_overflow_on_divider_right():
    """窄终端：溢出项出现在顶部分隔线最右端，状态行无半个项。"""
    lines = _lines(_model(), 40)
    assert len(lines) == 2
    divider, status = lines[0].rstrip(), lines[1].rstrip()
    assert divider.startswith("\u2501")
    assert divider.endswith("42.0t/s")          # 最右端
    assert "\u25c6 1.5kt" in divider
    assert "\u25c6" not in status and "\u00bb" not in status
    assert "deepseek-chat" in status


def test_render_narrow_divider_keeps_fill():
    """溢出内容左端仍有分隔线填充（该行仍看得出是分隔线）。"""
    lines = _lines(_model(), 40)
    divider = lines[0].rstrip()
    assert divider.startswith("\u2501\u2501\u2501")
    assert "\u25c6 1.5kt" in divider


def test_render_tiny_terminal_only_divider_with_ellipsis():
    """极窄屏：状态行无完整项时只渲染一行（分隔线 + 截断的溢出内容）。"""
    lines = _lines(_model(), 12)
    assert len(lines) == 1
    assert lines[0].startswith("\u2501")
    assert lines[0].endswith("\u2026")


def test_render_line_width_invariant():
    """任意宽度下每行显示宽度 <= width（行宽不变量）。"""
    from src.tui._width import wcswidth_simple

    for width in (12, 20, 24, 30, 39, 40, 60, 80):
        for line in _lines(_model(), width):
            assert wcswidth_simple(line.rstrip()) <= width


# ── 溢出内容自身的截断 ────────────────────────────────────


def test_overflow_trailing_reserves_fill_and_truncates():
    """溢出内容超宽：保留 2 列分隔线填充 + 尾部 ``…``。"""
    runs = [sb.StyledRun("\u25c6 " + "x" * 40, None)]
    trailing = _overflow_trailing_runs(runs, 20)
    text = _text(trailing)
    assert text.startswith(" ")
    assert text.endswith("\u2026")
    assert len(text) == 18            # 20 - _STATUS_OVERFLOW_MIN_FILL


def test_overflow_trailing_empty_when_nothing_overflows():
    assert _overflow_trailing_runs([], 80) == []


# ── 兼容：扁平 runs 接口不变 ───────────────────────────────


def test_build_status_runs_matches_item_join():
    """``_build_status_runs``（扁平单行）与项连接结果一致。"""
    model = _model()
    flat = _text(_build_status_runs(model, 0.0, "\u00b7", ""))
    built = _items(model)
    joined = _text(_join_status_items(built.items, built.model_first))
    assert flat == joined
    # 模型段后用双空格、其余段间用 `` · ``（既有视觉保持）
    assert "deepseek-chat  \u2699 1/3" in flat
    assert "\u23f1 1:05 \u00b7 \u2709 2" in flat
