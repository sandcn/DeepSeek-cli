"""批量 host 绘制越界防护 + 帧行宽/前缀一致性守卫（2026-10-23 用户报障）。

用户报障：上屏流式输出时**偶发**把内容写到下屏最顶的分割线里，随后「闪一下」
（下一帧）就恢复正常。

机制（本文件锁定的防护面）：
  - 批量 host（``StaticLines`` / ``LiveLines``）的 ``_paint`` 修复前无条件把
    ``len(lines)`` 行写入画布——一旦数据与布局出现瞬时不一致（行数变化与布局
    测量时序、缓存键陈旧等），多出的行会**写穿到自身布局盒之外**，即下屏第一
    行（状态栏上方分割线），下一帧布局/帧内容修复后才恢复（视觉上的「闪一下」）。
  - ``render_frame`` 的 committed 前缀拼接假设「缓存前缀行数 == 该 host 的
    布局高度」；不一致时尾部行整体错位一行（同样是上屏内容压到分割线的表现）。
  - ``_to_lines`` 对 ``Line`` 行直通**不校验行宽**，超宽行进入帧后终端自动
    换行，同样使整屏错位。

修复：
  1. ``livelines._paint`` / ``staticlines._paint`` 绘制行数上限 = ``box.h``；
     StaticLines 前缀缓存长度随之恒等于布局高度；
  2. ``components._to_lines`` 对 ``Line`` 行执行 E-OVERFLOW-GUARD 截断；
  3. ``components.render_frame`` 校验前缀长度与 ``committed_box.h``，不一致时
     把前缀行填回画布并走全量画布路径（内容零丢失、行数与布局一致）。
"""

from __future__ import annotations

from types import SimpleNamespace

from src.tui.ink import APP, TEXT, components, h
from src.tui.ink.layout import LayoutBox
from src.tui.ink.output import Line, StyledRun
from src.tui.ink.reconciler import Reconciler
from src.tui.ink.widgets import livelines, staticlines


def _line(text: str) -> Line:
    return Line([StyledRun(text, None)])


def _text_of(line) -> str:
    if line is None:
        return ""
    return "".join(r.text for r in line.runs)


def _host_fiber(lines, height: int, y: int = 1, x: int = 0):
    """构造批量 host paint 所需的最小 fiber（SimpleNamespace）。"""
    return SimpleNamespace(
        layout_box=LayoutBox(x, y, 10, height),
        props={"lines": lines},
        return_=None,
        _merged_rows_cache=None,
        _committed_prefix=None,
    )


# ── 1. LiveLines：绘制行数不超过布局高度 ──────────────────

def test_live_lines_paint_clipped_to_layout_height() -> None:
    """lines 数（5）> box.h（2）时只绘制前 2 行，不得写穿到下屏行。"""
    fiber = _host_fiber([_line(f"L{i}") for i in range(5)], height=2)
    canvas: list = [None] * 8

    livelines._paint(fiber, canvas)

    assert _text_of(canvas[1]) == "L0"
    assert _text_of(canvas[2]) == "L1"
    # box.h 之外的行必须保持未绘制（修复前 L2/L3/L4 会覆盖 3/4/5 行——
    # 对应「上屏内容写进下屏分割线」）。
    assert canvas[3] is None
    assert canvas[4] is None
    assert canvas[5] is None


def test_live_lines_paint_full_height_unchanged() -> None:
    """正常帧（lines 数 == box.h）绘制行为不变。"""
    fiber = _host_fiber([_line(f"L{i}") for i in range(3)], height=3)
    canvas: list = [None] * 8

    livelines._paint(fiber, canvas)

    assert [_text_of(canvas[i]) for i in range(1, 4)] == ["L0", "L1", "L2"]
    assert canvas[4] is None


# ── 2. StaticLines：绘制行数 + 前缀长度都不超过布局高度 ──────

def test_static_lines_paint_clipped_to_layout_height() -> None:
    """lines 数 > box.h 时不写穿，且前缀缓存长度恒等于布局高度。"""
    fiber = _host_fiber([_line(f"S{i}") for i in range(5)], height=2)
    canvas: list = [None] * 8

    staticlines._paint(fiber, canvas)

    cached = fiber._committed_prefix
    assert cached is not None
    prefix = cached[1]
    assert len(prefix) == 2, "前缀长度必须等于布局高度（render_frame 拼接前提）"
    # 前缀路径不写画布（render_frame 复用），关键是不越界：画布 3 行之后为空。
    assert canvas[3] is None
    assert canvas[4] is None


def test_static_lines_merged_path_clipped_to_layout_height() -> None:
    """非前缀路径（box.x != 0 合并路径）同样不越过布局高度。"""
    fiber = _host_fiber([_line(f"S{i}") for i in range(5)], height=2, x=2)
    canvas: list = [None] * 8

    staticlines._paint(fiber, canvas)

    # 只合并前 2 行（L0/L1 起始列 2），后续行不得写入。
    assert _text_of(canvas[1]).strip() == "S0"
    assert _text_of(canvas[2]).strip() == "S1"
    assert canvas[3] is None
    assert canvas[4] is None


# ── 3. 帧行宽不变量（Line 行直通也要截断）────────────────

def test_to_lines_truncates_overflowing_line() -> None:
    """``_to_lines`` 对 Line 行做 E-OVERFLOW-GUARD 截断（修复前直通超宽）。"""
    wide = _line("x" * 30)
    out = components._to_lines([wide], 10)

    assert len(out) == 1
    assert out[0].width <= 10, "超宽行必须截断到文档宽度（否则终端自动换行错位）"


def test_to_lines_keeps_normal_line_identity() -> None:
    """正常行保持对象身份（跨帧 diff 身份短路，零性能回归）。"""
    line = _line("ok")
    out = components._to_lines([line], 10)

    assert out[0] is line


# ── 4. render_frame：前缀长度与布局高度不一致时回退全量 ─────

def _render_simple_tree(width: int = 20):
    reconciler = Reconciler()
    root = Reconciler.create_root()
    element = h(APP, {"width": width, "flexDirection": "column"}, [
        h(TEXT, {"children": "top"}),
        h(TEXT, {"children": "b1"}),
        h(TEXT, {"children": "b2"}),
        h(TEXT, {"children": "b3"}),
        h(TEXT, {"children": "b4"}),
    ])
    reconciler.render(root, element, width, 0)
    return root


def test_render_frame_fills_prefix_back_when_length_mismatch(monkeypatch) -> None:
    """前缀长度（3）≠ 布局高度（4）→ 前缀行填回画布并走全量路径。

    修复前：``header + prefix + tail`` 直接拼接，前缀比布局少一行 → 尾部位移
    一行（上屏内容压到下一行 = 下屏分割线），内容还会被截断丢失。
    """
    width = 20
    root = _render_simple_tree(width)
    prefix = [_line("P0"), _line("P1"), _line("P2")]

    fake_committed = SimpleNamespace(
        layout_box=LayoutBox(0, 1, width, 4),   # 布局占 4 行，前缀只有 3 行
        _committed_prefix=((None, 3, 1, width), prefix, True),
    )
    monkeypatch.setattr(
        components, "_find_committed_chat", lambda _root: fake_committed,
    )

    frame = components.render_frame(root, width)

    # 前缀行被填回画布（内容零丢失），行数与布局一致（无位移错位）。
    assert [_text_of(frame.lines[i]) for i in (1, 2, 3)] == ["P0", "P1", "P2"]
    assert len(frame.lines) == 5, "帧行数 = 布局高度（内容不得因错位被截断/丢失）"


def test_render_frame_uses_prefix_when_length_matches(monkeypatch) -> None:
    """前缀长度 == 布局高度：仍走前缀快路径（行为不变）。"""
    width = 20
    root = _render_simple_tree(width)
    prefix = [_line("Q0"), _line("Q1")]

    fake_committed = SimpleNamespace(
        layout_box=LayoutBox(0, 1, width, 2),
        _committed_prefix=((None, 2, 1, width), prefix, True),
    )
    monkeypatch.setattr(
        components, "_find_committed_chat", lambda _root: fake_committed,
    )

    frame = components.render_frame(root, width)

    assert frame._stable_prefix is prefix, "一致时应保留稳定前缀快路径"
    assert [_text_of(frame.lines[i]) for i in (1, 2)] == ["Q0", "Q1"]
