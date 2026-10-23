"""流式高度锁定回归测试（2026-10-23 用户报障：内容整体跳动一行）。

覆盖：
  1. ``StreamHeightLock`` 纯状态机：高水位跟随增长、**任意**缩短都补白
     （流式期间行数只增不减、无容忍上限）、非活跃/禁用时重置；
  2. ``pad_frame``：补白插在下屏之前、行对象复用、稳定前缀安全；
  3. ``find_bottom_area``：返回**host** fiber（不是无 layout_box 的
     ``Box`` 函数 fiber）；
  4. InkSession 集成：解析进度行出现→消失时，文档高度保持、下屏位置不变、
     上方内容不移动，仅解析行变为空行；流式结束后释放锁定。
"""

from __future__ import annotations

import io

import pytest

from src.tui.ink._stream_height import (
    StreamHeightLock,
    find_bottom_area,
    pad_frame,
)
from src.tui.ink.output import Frame, Line


def _line(text: str) -> Line:
    line = Line()
    line.append(text, None)
    return line


# ── 1. 纯状态机 ───────────────────────────────────────


def test_floor_tracks_growth_and_pads_transient_shrink() -> None:
    lock = StreamHeightLock()
    assert lock.update(active=True, height=10, bottom_y=5) == 0
    assert lock.floor == 10 and lock.locked
    # 增长：高水位跟随，不补白
    assert lock.update(active=True, height=13, bottom_y=5) == 0
    assert lock.floor == 13
    # 瞬时缩短 1 行：补 1 行空白（高度保持 13）
    assert lock.update(active=True, height=12, bottom_y=5) == 1
    # 持续缩短 3 行：补 3 行
    assert lock.update(active=True, height=10, bottom_y=5) == 3
    # 恢复增长：高水位重新跟随
    assert lock.update(active=True, height=14, bottom_y=5) == 0
    assert lock.floor == 14


def test_large_shrink_also_pads_never_drops_lines() -> None:
    """流式期间行数严格只增不减：大幅缩短同样补白（无容忍上限）。"""
    lock = StreamHeightLock()
    assert lock.update(active=True, height=10, bottom_y=5) == 0
    # 大幅缩短 3 行：仍然补 3 行空白，高水位不变（绝不删除行）
    assert lock.update(active=True, height=7, bottom_y=5) == 3
    assert lock.floor == 10
    # 继续缩短 4 行：继续补 4 行
    assert lock.update(active=True, height=6, bottom_y=5) == 4
    assert lock.floor == 10
    # 恢复增长：高水位跟随
    assert lock.update(active=True, height=11, bottom_y=5) == 0
    assert lock.floor == 11


def test_inactive_or_disabled_resets_lock() -> None:
    lock = StreamHeightLock()
    assert lock.update(active=True, height=10, bottom_y=5) == 0
    assert lock.update(active=False, height=9, bottom_y=5) == 0
    assert not lock.locked and lock.floor == 0
    # 重新进入活跃期：从当前高度重新开始
    assert lock.update(active=True, height=9, bottom_y=5) == 0
    assert lock.floor == 9
    # 禁用（浮窗/全屏/模态/未知屏幕高度）释放锁定
    assert lock.update(active=True, height=8, bottom_y=5, disabled=True) == 0
    assert not lock.locked and lock.floor == 0


def test_missing_bottom_area_does_not_lock() -> None:
    lock = StreamHeightLock()
    assert lock.update(active=True, height=10, bottom_y=0) == 0
    assert not lock.locked


# ── 2. pad_frame ─────────────────────────────────────


def test_pad_frame_inserts_blank_rows_before_bottom_area() -> None:
    top = [_line(f"L{i}") for i in range(5)]
    bottom = [_line("status"), _line("input")]
    frame = Frame(top + bottom)
    padded = pad_frame(frame, bottom_y=5, pad=2)
    assert padded.height == 9
    # 上方内容与下屏行对象身份不变（行级 diff 身份短路）
    assert padded.lines[:5] == top
    assert padded.lines[7:] == bottom
    assert all(not line.runs for line in padded.lines[5:7])
    # 原帧不被修改（Frame 构造后 lines 只读契约）
    assert frame.height == 7


def test_pad_frame_drops_prefix_only_when_crossing_pad_point() -> None:
    top = [_line("0"), _line("1")]
    bottom = [_line("status")]
    frame = Frame(
        top + bottom,
        stable_prefix=top,
        stable_prefix_offset=0,
        stable_prefix_len=2,
    )
    padded = pad_frame(frame, bottom_y=2, pad=1)
    # 前缀（0..1）在补白点之前：保留
    assert padded._stable_prefix is top
    assert padded._stable_prefix_offset == 0
    assert padded._stable_prefix_len == 2
    # 异常布局：前缀跨越补白点 → 放弃前缀标记（正确性优先）
    crossing = Frame(
        top + bottom,
        stable_prefix=top + bottom,
        stable_prefix_offset=0,
        stable_prefix_len=3,
    )
    padded2 = pad_frame(crossing, bottom_y=2, pad=1)
    assert padded2._stable_prefix is None
    assert padded2._stable_prefix_len == 0


# ── 3. 下屏 fiber 查找 ────────────────────────────────


def test_find_bottom_area_returns_host_fiber_with_layout_box() -> None:
    from src.tui.app.app import build_app_element
    from src.tui.app.model import AppModel
    from src.tui.ink import components
    from src.tui.ink.reconciler import Reconciler

    model = AppModel()
    reconciler = Reconciler()
    root = Reconciler.create_root()
    reconciler.render(root, build_app_element(model, 60), 60, 20)
    components.render_frame(root, 60, viewport_height=20)
    fiber = find_bottom_area(root)
    assert fiber is not None
    assert fiber.is_host
    assert fiber.props.get("dataFloatAnchor") == "bottom_area"
    assert fiber.layout_box is not None


# ── 4. InkSession 集成 ────────────────────────────────


def _make_session(width: int = 60, height: int = 20, *, active: bool = True):
    pyte = pytest.importorskip("pyte")
    from src.tui._config import TuiConfig
    from src.tui._screen import TerminalWidthCache
    from src.tui.app.app import build_app_element
    from src.tui.app.apply import apply_cmd
    from src.tui.app.model import AppModel
    from src.tui.ink.session import InkSession

    cache = TerminalWidthCache(ttl=60)
    cache.set_dimensions(width, height)
    model = AppModel()
    model.status.status_active = active
    stream = io.StringIO()
    session = InkSession(
        model=model,
        apply_cmd=apply_cmd,
        build_tree=build_app_element,
        width_cache=cache,
        config=TuiConfig.defaults(),
        stream=stream,
    )
    screen = pyte.Screen(width, height)
    parser = pyte.Stream(screen)

    def step(*cmds):
        for cmd in cmds:
            apply_cmd(model, cmd)
        session._dirty = True
        session._idle_ok = False
        session._render_frame()
        parser.feed(stream.getvalue())
        stream.seek(0)
        stream.truncate(0)
        return [line.rstrip() for line in screen.display]

    return model, session, step


def test_large_shrink_is_fully_padded_no_line_drop() -> None:
    """超过旧上限（8 行）的缩短同样全部补白：流式期间行数严格只增不减。

    构造：子代理面板 12 行摘要出现后清空（模拟子代理/内容块大幅缩短）。
    旧实现（补白上限 8）会留下 4 行净删除 → 闪烁；新实现补满 12 行空白，
    文档总高度保持不变。
    """
    from src.tui._const import SubagentFrameCmd, UserMsgCmd

    model, session, step = _make_session()
    step(UserMsgCmd(text="测试"))
    subagent_lines = [_line(f"subagent line {i}") for i in range(12)]
    step(SubagentFrameCmd(frame_lines=subagent_lines))
    height_with = session._last_frame.height
    assert session._height_lock.locked

    step(SubagentFrameCmd(frame_lines=[]))
    assert session._last_frame.height == height_with
    # 缩短 12 行全部补白（> 旧上限 8，证明无容忍上限）
    assert session._frame_bottom_pad >= 9
    assert session._height_lock.floor == height_with


def test_parse_line_disappear_keeps_height_and_bottom_position() -> None:
    from src.tui._const import ParseInfoCmd

    model, session, step = _make_session()
    step()
    with_parse = step(
        ParseInfoCmd(tool_names="Edit", tokens=100, elapsed=1.0),
    )
    height_with_parse = session._last_frame.height
    assert session._height_lock.locked
    assert session._height_lock.floor == height_with_parse
    bottom_with = session._find_bottom_area_fiber().layout_box.y
    # 文档短于屏幕时底部对齐：屏幕首行是空行，屏幕行号 = 文档行号 + offset
    offset = len(with_parse) - height_with_parse
    parse_row = bottom_with - 1 + offset

    without_parse = step(
        ParseInfoCmd(tool_names="", tokens=-1, elapsed=0.0),
    )
    # 文档高度保持（补 1 行空白），不是回到 height-1
    assert session._last_frame.height == height_with_parse
    assert session._frame_bottom_pad == 1
    # 解析行本身变为空行；其上方内容逐行不变（没有任何整体位移）
    assert without_parse[parse_row] == ""
    assert with_parse[:parse_row] == without_parse[:parse_row]
    # 下屏起始行不变（补白恰好补在它之前）
    assert session._find_bottom_area_fiber().layout_box.y == bottom_with
    # 下屏静态行位置/内容不变（输入区占位动画点与时钟行允许逐帧变化）
    for row in (bottom_with + offset, bottom_with + offset + 1,
                bottom_with + offset + 2, len(with_parse) - 1):
        assert with_parse[row] == without_parse[row], row

    # 流式结束：释放锁定，高度回到真实值
    model.status.status_active = False
    step()
    assert session._frame_bottom_pad == 0
    assert not session._height_lock.locked
    assert session._last_frame.height == height_with_parse - 1
