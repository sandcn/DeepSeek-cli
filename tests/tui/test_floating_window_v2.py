"""通用浮动窗口 — 2026-10-21 第二轮完善回归。

覆盖本轮修复/完善项：

  - **输出历史**：浮窗覆盖 committed 行时，内容行回调必须取底层文本
    （``Frame.base_lines``），不能把窗口边框写进 scrollback；
  - **尺寸约束**：内容高于可用空间时**裁剪**窗口（底行锚定），绝不进入
    底部框；``max_height`` / ``scroll_top`` 裁剪与滚动；
  - **默认锚点**：不再把底边框压在输入区顶部分隔线（CPU/MEM 行）上；
  - **交互**：``geometry`` 运行时矩形 + ``window_hit_test`` + ``close_on_escape``；
  - **全屏模态**：默认隐藏浮窗，``over_fullscreen`` 显式覆盖；
  - **嵌套浮窗**：内层被忽略（不逃出父窗口、不双重绘制）；
  - **性能**：``idle_static`` 允许空闲帧复用；稳定前缀只失效被覆盖行之后部分。

测试走生产渲染路径（``Reconciler`` + ``render_frame`` + 合成层），不是简化桩。
"""

from __future__ import annotations

import pytest

from src.tui._input_parser import KeyEvent
from src.tui.app.app import App
from src.tui.app.floating import (
    FloatingWindowSpec,
    loop_window_spec,
    window_hit_test,
)
from src.tui.app.model import AppModel
from src.tui.ink import BOX, TEXT, h, strip_ansi
from src.tui.ink import components as ink_components
from src.tui.ink._floating import (
    _bottom_area_top,
    _collect_float_fibers,
)
from src.tui.ink._paint_canvas import _line_as_dict
from src.tui.ink.output import Line
from src.tui.ink.reconciler import Reconciler
from tests.tui.ink._harness import Harness


def _plain_lines(frame) -> list[str]:
    return [strip_ansi(line.render()) for line in frame.lines]


def _cell(line, col: int) -> str:
    entry = _line_as_dict(line).get(col)
    return entry[0] if entry else ""


def _render(model, width: int = 70, height: int = 40):
    """渲染真实 App 整帧（含浮窗定位与合成），返回（纯文本行, frame, root）。"""
    rec = Reconciler()
    rec.hook_context.install_headless(width, height)
    root = Reconciler.create_root()
    rec.render(root, h(App, {"model": model, "width": width}), width, height)
    frame = ink_components.render_frame(root, width, viewport_height=height)
    return _plain_lines(frame), frame, root


def _render_twice(model, width: int = 70, height: int = 40):
    """同一 fiber 树连续渲染两帧（第二帧命中 committed 前缀复用）。"""
    rec = Reconciler()
    rec.hook_context.install_headless(width, height)
    root = Reconciler.create_root()
    element = h(App, {"model": model, "width": width})
    rec.render(root, element, width, height)
    ink_components.render_frame(root, width)
    rec.render(root, element, width, height)
    frame = ink_components.render_frame(root, width)
    return _plain_lines(frame), frame


def _tall_content(rows: int = 12):
    """构造 N 行内容组件（窗口高度 = N + 上下边框）。"""
    def _render_content(props):
        return h(BOX, {"flexDirection": "column"}, [
            h(TEXT, {"children": f"ROW{i:02d}"}) for i in range(rows)
        ])
    return _render_content


def _loop_model(total: int = 3) -> AppModel:
    model = AppModel()
    st = model.loop_status
    st.active = True
    st.total = total
    st.round = 1
    st.run = 1
    st.runs_per_round = 2
    st.prompt = "继续完成所有"
    model.floating_windows = [loop_window_spec()]
    return model


@pytest.fixture(autouse=True)
def _isolate_workspace(monkeypatch):
    """屏蔽 workspace 段（分支/目录随运行环境变化）。"""
    import src.tui.app.status_bar as sb

    monkeypatch.setattr(sb, "_workspace_segment", lambda ctx: [])


# ── 尺寸约束 / 底部框保护 ────────────────────────────────

def test_tall_bottom_window_clips_and_never_covers_bottom_area():
    """内容高于可用空间：裁剪顶部（底边锚定），绝不进入底部框。"""
    width, height = 70, 14
    model = AppModel()
    model.committed_lines = [Line.of(f"hist {i}") for i in range(40)]
    model.floating_windows = [FloatingWindowSpec(
        id="tall", render=_tall_content(12), width=20,
    )]
    lines, frame, root = _render(model, width, height)
    ba = _bottom_area_top(root)
    x = width - 20
    bottoms = [i for i, line in enumerate(frame.lines) if _cell(line, x) == "\u2514"]
    assert bottoms, "浮窗底边框缺失"
    assert max(bottoms) == ba - 1
    # 底部框区域（含状态栏/输入区）没有任何窗口边框
    assert not any(
        _cell(frame.lines[r], x) in ("\u250c", "\u2502", "\u2514")
        for r in range(ba, len(lines))
    )
    # 顶部被裁：窗口顶边框不可见（内容 + 边框高于可用行数）
    assert not any(_cell(frame.lines[r], x) == "\u250c" for r in range(ba))


def test_absolute_window_clipped_above_bottom_area_without_opt_in():
    """绝对坐标窗口越界时裁到底部框上方（默认不允许覆盖底部框）。"""
    width = 70
    model = AppModel()
    model.committed_lines = [Line.of(f"hist {i}") for i in range(20)]
    spec = FloatingWindowSpec(
        id="abs", render=_tall_content(10), width=20, x=0, top=0, bottom=None,
    )
    model.floating_windows = [spec]
    _, _, root0 = _render(model, width)
    ba = _bottom_area_top(root0)
    spec.top = max(0, ba - 6)  # 12 行窗口从底部框上方 6 行开始 → 必须裁剪
    lines, frame, root = _render(model, width)
    ba2 = _bottom_area_top(root)
    assert ba2 > 0
    assert not any(
        _cell(frame.lines[r], 0) in ("\u250c", "\u2502", "\u2514")
        for r in range(ba2, len(lines))
    )
    # 窗口仍可见（被裁到 ba2 上方）
    assert any(_cell(frame.lines[r], 0) in ("\u250c", "\u2502") for r in range(ba2))


def test_allow_cover_bottom_opt_in_permits_overlap():
    """``allow_cover_bottom=True`` 显式允许覆盖底部框。"""
    width = 70
    model = AppModel()
    model.committed_lines = [Line.of(f"hist {i}") for i in range(20)]
    spec = FloatingWindowSpec(
        id="cover", render=_tall_content(10), width=20, x=0, top=0, bottom=None,
        allow_cover_bottom=True,
    )
    model.floating_windows = [spec]
    _, _, root0 = _render(model, width)
    ba = _bottom_area_top(root0)
    spec.top = max(0, ba - 2)
    lines, frame, root = _render(model, width)
    ba2 = _bottom_area_top(root)
    assert any(
        _cell(frame.lines[r], 0) in ("\u2502", "\u2514")
        for r in range(ba2, len(lines))
    )


def test_max_height_clips_and_scroll_top_reaches_later_rows():
    """``max_height`` 裁剪可见高度；``scroll_top`` 查看被裁掉的后续内容。"""
    width = 70
    model = AppModel()
    spec = FloatingWindowSpec(
        id="mh", render=_tall_content(8), width=14, x=0, top=1, bottom=None,
        max_height=4,
    )
    model.floating_windows = [spec]
    lines, _, _ = _render(model, width)
    text = "\n".join(lines)
    assert "ROW00" in text and "ROW01" in text
    assert "ROW03" not in text  # 4 行可见（上下边框 + 2 行内容）

    spec.scroll_top = 4
    lines2, _, _ = _render(model, width)
    text2 = "\n".join(lines2)
    assert "ROW00" not in text2
    assert "ROW03" in text2 or "ROW04" in text2


def test_default_anchor_keeps_input_separator_visible():
    """默认锚点（bottom_area）不再把窗口底边框压在输入区分隔线上。"""
    width = 70
    model = AppModel()
    model.floating_windows = [FloatingWindowSpec(
        id="d", render=lambda props: h(TEXT, {"children": "DEF"}), width=30,
    )]
    lines, frame, root = _render(model, width)
    ba = _bottom_area_top(root)
    assert "CPU:" in "\n".join(lines)  # 分隔线完整
    x = width - 30
    bottoms = [i for i, line in enumerate(frame.lines) if _cell(line, x) == "\u2514"]
    assert bottoms and max(bottoms) == ba - 1


# ── 交互：geometry / hit test / Esc ─────────────────────

def test_content_reads_live_geometry_and_hit_test():
    """内容组件经 ``props["geometry"]`` 读到运行时矩形；hit test 用屏幕坐标。"""
    def content(props):
        geometry = props.get("geometry") or {}
        return h(TEXT, {"children": f"X{geometry.get('x', '?')}"})

    spec = FloatingWindowSpec(
        id="geo", render=content, width=10, x=3, top=2, bottom=None,
    )
    model = AppModel()
    model.floating_windows = [spec]
    _render(model, 70)  # 首帧回填 geometry
    lines, _, _ = _render(model, 70)
    assert "X3" in "\n".join(lines)

    assert window_hit_test(spec, 3, 2) == (0, 0)
    assert window_hit_test(spec, 2, 2) is None
    assert window_hit_test(spec.geometry, 3, 5) is None  # 越界（窗口高 3 行）


def test_close_on_escape_consumes_only_escape():
    """``close_on_escape`` 只消费 Esc；未开启时不改变按键语义。"""
    closed: list = []
    spec = FloatingWindowSpec(
        id="esc", render=lambda props: h(TEXT, {"children": "ESCWIN"}),
        width=12, x=0, top=1, bottom=None,
        close_on_escape=True, on_close=lambda: closed.append(1),
    )
    model = AppModel()
    model.floating_windows = [spec]
    harness = Harness(70)
    harness.render(h(App, {"model": model, "width": 70}))
    router = harness.reconciler._input_router_cache[1]
    assert router is not None
    assert router(KeyEvent(kind="char", char="a")) is False
    assert router(KeyEvent(kind="escape")) is True
    assert closed == [1]

    spec.close_on_escape = False
    harness2 = Harness(70)
    harness2.render(h(App, {"model": model, "width": 70}))
    cache = harness2.reconciler._input_router_cache
    router2 = cache[1] if cache else None
    assert router2 is None or router2(KeyEvent(kind="escape")) is False


# ── 全屏模态 / 嵌套 / 输出历史 / 性能 ──────────────────

def test_fullscreen_hides_floats_by_default():
    """模态全屏视图下浮窗默认隐藏；``over_fullscreen=True`` 才显示。"""
    model = AppModel()
    model.fullscreen = "trace"
    model.floating_windows = [FloatingWindowSpec(
        id="f", render=lambda props: h(TEXT, {"children": "FLOATABS"}),
        width=14, x="left", top=1, bottom=None,
    )]
    lines, _, _ = _render(model, 70)
    assert "FLOATABS" not in "\n".join(lines)

    model.floating_windows[0].over_fullscreen = True
    lines2, _, _ = _render(model, 70)
    assert "FLOATABS" in "\n".join(lines2)


def test_nested_float_is_ignored():
    """浮窗内容里再声明的浮窗被忽略（不脱离父窗口、不双重绘制）。"""
    def outer(props):
        return h(BOX, {"flexDirection": "column"}, [
            h(TEXT, {"children": "OUTER"}),
            h(BOX, {
                "position": "absolute", "dataFloatWindow": "inner",
                "floatX": "left", "floatTop": 2, "width": 10, "border": 1,
                "flexDirection": "column",
            }, [h(TEXT, {"children": "INNER"})]),
        ])

    model = AppModel()
    model.floating_windows = [FloatingWindowSpec(
        id="outer", render=outer, width=24, x="right", top=2, bottom=None,
    )]
    lines, _, root = _render(model, 70)
    text = "\n".join(lines)
    assert "OUTER" in text
    assert "INNER" not in text
    assert [
        f.props.get("dataFloatWindow") for f in _collect_float_fibers(root)
    ] == ["outer"]


def test_output_history_uses_base_lines_not_overlay():
    """浮窗覆盖 committed 行时，内容行回调取到底层消息文本而非窗口边框。"""
    width = 70
    model = _loop_model(total=3)
    model.committed_lines = [Line.of(f"历史内容 {i:02d}") for i in range(20)]
    lines, frame, root = _render(model, width)
    committed = ink_components._find_committed_chat(root)
    start = committed.layout_box.y
    covered = [
        r for r in range(start, start + 20)
        if "\u2502" in lines[r] or "\u250c" in lines[r]
    ]
    assert covered, "浮窗应覆盖部分 committed 行"
    row = covered[0]
    # 合成帧：窗口边框（列 = width-30）+ 底层消息文本同处一行
    assert _cell(frame.lines[row], width - 30) == "\u250c"
    assert "历史内容" in frame.render_content_line(row)
    assert "\u250c" not in frame.render_content_line(row)


def test_stable_prefix_partially_preserved_when_float_covers_prefix():
    """浮窗只让被覆盖行退出稳定前缀（保留其前面的复用区间）。"""
    width = 70
    model = AppModel()
    model.committed_lines = [Line.of(f"历史内容 {i:02d}") for i in range(30)]
    model.floating_windows = [FloatingWindowSpec(
        id="w", render=lambda props: h(TEXT, {"children": "WIN"}),
        width=20, x=0, top=5, bottom=None,
    )]
    _, frame = _render_twice(model, width)
    assert frame._stable_prefix is not None
    assert 0 < frame._stable_prefix_len < 30


def test_idle_static_window_allows_idle_reuse():
    """``idle_static=True`` 的常驻浮窗不再永久关闭空闲帧复用。"""
    from src.tui._assembly_steps import _make_idle_frame_predicate

    model = AppModel()
    assert _make_idle_frame_predicate(model, None)() is True
    model.floating_windows = [FloatingWindowSpec(id="st", idle_static=True)]
    assert _make_idle_frame_predicate(model, None)() is True
    model.floating_windows = [FloatingWindowSpec(id="st2")]
    assert _make_idle_frame_predicate(model, None)() is False
