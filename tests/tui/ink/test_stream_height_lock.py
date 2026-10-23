"""流式高度锁定回归测试（2026-10-23 用户报障：内容整体跳动一行）。

覆盖：
  1. ``StreamHeightLock`` 纯状态机：高水位跟随增长、缩短补白、补白上限、
     非活跃/禁用时重置；
  2. ``pad_frame``：补白插在下屏之前、行对象复用、稳定前缀安全；
  3. ``find_bottom_area``：返回**host** fiber（不是无 layout_box 的
     ``Box`` 函数 fiber）；
  4. InkSession 集成：解析进度行出现→消失时，文档高度保持、下屏位置不变、
     上方内容不移动，仅解析行变为空行；流式结束后释放锁定；
  5. 2026-10-23「流式输出时浮动窗口抖动」：补白帧的浮窗可见几何必须按
     **含补白的文档总高**重新解析（修复前按补白前高度解析，窗口顶部被多裁
     一行——顶部边框/标题行在补白帧消失、下一帧又出现）。
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
    lock = StreamHeightLock(max_pad=8)
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


def test_pad_is_capped_and_floor_reset_on_large_shrink() -> None:
    lock = StreamHeightLock(max_pad=2)
    assert lock.update(active=True, height=10, bottom_y=5) == 0
    # 缩短 3 行 > 上限 2：放弃本次补白并重置高水位
    assert lock.update(active=True, height=7, bottom_y=5) == 0
    assert lock.floor == 7
    # 后续小缩短正常补白
    assert lock.update(active=True, height=6, bottom_y=5) == 1


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


def _make_session(width: int = 60, height: int = 20, *, active: bool = True,
                  window=None):
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
    if window is not None:
        model.floating_windows = [window]
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


def _make_loop_session(width: int = 80, height: int = 24, *, window=None):
    """带 ``/loop`` 浮窗的会话（浮窗每帧重建 + 动态计时行）。"""
    from src.tui.app.floating import loop_window_spec

    spec = window if window is not None else loop_window_spec()
    model, session, step = _make_session(width, height, window=spec)
    st = model.loop_status
    st.active = True
    st.total = 3
    st.round = 1
    st.run = 1
    st.prompt = "测试提词"
    return model, session, step


def _rows_with(display, needle: str) -> list:
    """屏幕中包含 needle 的行号（0-based）。"""
    return [index for index, line in enumerate(display) if needle in line]


def _assert_content_window_bottom_stable(with_parse, without_parse) -> None:
    """解析进度行出现/消失时：内容、浮窗、底部框的屏幕位置全部不变。

    唯一允许变化的是解析进度行本身（它出现/消失）。
    """
    bottom = _rows_with(with_parse, "⎇ main")
    assert bottom, "底部框未渲染"
    bottom_row = bottom[0]
    # 浮窗边框位置稳定（/loop 浮窗不得相对底部框上下跳动）
    assert _rows_with(with_parse, "╭") == _rows_with(without_parse, "╭")
    # 底部框位置稳定
    assert bottom == _rows_with(without_parse, "⎇ main")
    # 内容 + 浮窗区域逐行稳定（解析行除外）
    parse_rows = set(_rows_with(with_parse, "Edit"))
    for row in range(bottom_row):
        if row in parse_rows:
            continue
        assert with_parse[row] == without_parse[row], (
            f"row {row} 发生位移/变化: {with_parse[row]!r} -> {without_parse[row]!r}"
        )



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


# ── 5. /loop 浮窗 + 流式高度锁定（2026-10-23 用户报障：/loop 后 TUI 抖） ──


def test_loop_window_parse_toggle_keeps_content_window_bottom_stable() -> None:
    """短文档 + /loop 浮窗：解析行出现→消失，内容/浮窗/底框都不移动。

    修复前浮窗存在时高度锁定被整体禁用（浮窗会相对底框跳动）：解析行
    消失时文档高度瞬时缩短，底对齐让上方内容整体下移一行 → 视觉抖动。
    """
    from src.tui._const import ParseInfoCmd

    model, session, step = _make_loop_session()
    step()
    with_parse = step(
        ParseInfoCmd(tool_names="Edit", tokens=100, elapsed=1.0),
    )
    # 浮窗存在时锁定器必须生效（修复前 locked 为 False）
    assert session._height_lock.locked
    without_parse = step(
        ParseInfoCmd(tool_names="", tokens=-1, elapsed=0.0),
    )
    assert session._frame_bottom_pad == 1
    _assert_content_window_bottom_stable(with_parse, without_parse)


def test_loop_window_long_document_parse_toggle_stable() -> None:
    """长文档（超过屏幕）+ /loop 浮窗：底对齐滚动不再因瞬时缩短回跳。"""
    from src.tui._const import DisplayMsgsCmd, ParseInfoCmd

    model, session, step = _make_loop_session()
    messages = [
        {"role": "user", "content": f"消息 {index} " + "内容" * 20}
        for index in range(12)
    ]
    step(DisplayMsgsCmd(messages=messages, speed=0))
    with_parse = step(
        ParseInfoCmd(tool_names="Edit", tokens=100, elapsed=1.0),
    )
    assert session._height_lock.locked
    without_parse = step(
        ParseInfoCmd(tool_names="", tokens=-1, elapsed=0.0),
    )
    assert session._frame_bottom_pad == 1
    _assert_content_window_bottom_stable(with_parse, without_parse)
    # 再次出现解析行：内容/浮窗位置仍稳定（不来回跳）
    again = step(ParseInfoCmd(tool_names="Edit", tokens=200, elapsed=2.0))
    _assert_content_window_bottom_stable(with_parse, again)


def test_bottom_anchored_non_loop_window_stable() -> None:
    """同一机制覆盖其它底部锚定浮窗（/usage、/pet 共用 bottom_area 锚点）。"""
    from src.tui._const import ParseInfoCmd

    from src.tui.app.floating import loop_window_spec

    # 复制 loop 浮窗规格（不同 id / 不同水平位置），验证与浮窗身份无关。
    spec = loop_window_spec().copy_with(id="usage-test", x="left")
    model, session, step = _make_loop_session(window=spec)
    step()
    with_parse = step(ParseInfoCmd(tool_names="Edit", tokens=100, elapsed=1.0))
    without_parse = step(ParseInfoCmd(tool_names="", tokens=-1, elapsed=0.0))
    assert session._height_lock.locked
    assert session._frame_bottom_pad == 1
    _assert_content_window_bottom_stable(with_parse, without_parse)


def test_doc_anchor_window_keeps_height_lock_disabled() -> None:
    """文档锚点浮窗：补白会改变其相对文档位置 → 保持既有「不锁定」语义。"""
    from src.tui._const import ParseInfoCmd
    from src.tui.app.floating import FloatingWindowSpec
    from src.tui.ink import TEXT, h

    spec = FloatingWindowSpec(
        id="doc-test",
        render=lambda props: h(TEXT, {"children": "doc"}),
        width=20,
        bottom="doc_top",
    )
    model, session, step = _make_session(80, 24, window=spec)
    model.status.status_active = True
    step()
    step(ParseInfoCmd(tool_names="Edit", tokens=100, elapsed=1.0))
    step(ParseInfoCmd(tool_names="", tokens=-1, elapsed=0.0))
    assert not session._height_lock.locked
    assert session._frame_bottom_pad == 0


def test_loop_window_lock_released_after_streaming() -> None:
    """流式结束后释放锁定：帧高度回到真实值，浮窗仍在原位。"""
    from src.tui._const import ParseInfoCmd

    model, session, step = _make_loop_session()
    step()
    step(ParseInfoCmd(tool_names="Edit", tokens=100, elapsed=1.0))
    without_parse = step(ParseInfoCmd(tool_names="", tokens=-1, elapsed=0.0))
    locked_height = session._last_frame.height
    window_rows = _rows_with(without_parse, "⟳ /loop")
    assert window_rows, "循环浮窗未渲染"
    model.status.status_active = False
    display = step()
    assert session._frame_bottom_pad == 0
    assert not session._height_lock.locked
    # 锁定释放：高度回到真实值（解析行已消失，比锁定高度少 1 行）
    assert session._last_frame.height == locked_height - 1
    # 浮窗仍贴在底部框上方（标题行位置不变）
    assert _rows_with(display, "⟳ /loop") == window_rows



def test_multiple_bottom_windows_stable() -> None:
    """多个底部锚定浮窗同时打开（/loop + /usage）：补白随全部窗口一起下移。"""
    from src.tui._const import DisplayMsgsCmd, ParseInfoCmd
    from src.tui.app.floating import loop_window_spec

    model, session, step = _make_session(80, 24, window=None)
    model.floating_windows = [
        loop_window_spec(),
        loop_window_spec().copy_with(id="usage-test", x="left"),
    ]
    st = model.loop_status
    st.active = True
    st.total = 3
    st.round = 1
    st.run = 1
    st.prompt = "测试提词"
    step(DisplayMsgsCmd(
        messages=[
            {"role": "user", "content": f"消息 {index} " + "内容" * 20}
            for index in range(12)
        ],
        speed=0,
    ))
    with_parse = step(ParseInfoCmd(tool_names="Edit", tokens=100, elapsed=1.0))
    without_parse = step(ParseInfoCmd(tool_names="", tokens=-1, elapsed=0.0))
    assert session._height_lock.locked
    assert session._frame_bottom_pad == 1
    _assert_content_window_bottom_stable(with_parse, without_parse)


def test_small_screen_cropped_window_stable() -> None:
    """小终端下浮窗被可视区裁剪（_float_src_row）：补白仍不打断其内容。"""
    from src.tui._const import DisplayMsgsCmd, ParseInfoCmd

    model, session, step = _make_loop_session(80, 15)
    step(DisplayMsgsCmd(
        messages=[
            {"role": "user", "content": f"消息 {index} " + "内容" * 20}
            for index in range(12)
        ],
        speed=0,
    ))
    with_parse = step(ParseInfoCmd(tool_names="Edit", tokens=100, elapsed=1.0))
    without_parse = step(ParseInfoCmd(tool_names="", tokens=-1, elapsed=0.0))
    assert session._height_lock.locked
    assert session._frame_bottom_pad == 1
    # 浮窗顶部边框位置稳定（即使可见高度被裁剪）
    assert _rows_with(with_parse, "╭") == _rows_with(without_parse, "╭")
    # 非浮窗行（左侧内容区）逐行稳定；浮窗行由合成层承载，位置由上一条断言守护
    for row, (left, right) in enumerate(zip(with_parse, without_parse)):
        if "Edit" in left or "╭" in left or "│" in left or "╰" in left:
            continue
        assert left == right, row


# ── 6. 补白帧浮窗几何重算（2026-10-23 用户报障：流式输出时浮窗抖动） ──


def test_pad_frame_recomputes_window_geometry_with_padded_height() -> None:
    """短文档：浮窗顶部本就贴到文档首行（可见高度被裁 1 行）。

    解析进度行消失 → 高度锁定补 1 行空白，文档总高与上一帧相同。补白帧的
    浮窗几何必须按**含补白的总高**重新解析；修复前按补白前高度解析，窗口
    顶部被多裁一行——标题行/顶部边框在补白帧消失、下一帧又出现（抖动）。
    """
    from src.tui._const import ContentCmd, DisplayMsgsCmd, ParseInfoCmd

    model, session, step = _make_loop_session(80, 24)
    step()
    step(DisplayMsgsCmd(messages=[{"role": "user", "content": "你好"}], speed=0))
    step(ContentCmd(text="第0段流式内容"))
    with_parse = step(ParseInfoCmd(tool_names="Edit", tokens=100, elapsed=1.0))
    geometry_with = dict(model.floating_windows[0].geometry)
    assert _rows_with(with_parse, "⟳ /loop"), "前置：带解析行时浮窗应完整可见"

    without_parse = step(ParseInfoCmd(tool_names="", tokens=-1, elapsed=0.0))
    geometry_without = dict(model.floating_windows[0].geometry)
    assert session._frame_bottom_pad == 1

    # 浮窗标题行 / 底边框在补白帧与原帧完全同位置
    assert _rows_with(without_parse, "⟳ /loop") == _rows_with(with_parse, "⟳ /loop")
    assert _rows_with(without_parse, "╰") == _rows_with(with_parse, "╰")
    # 运行时几何（可见高度 / 屏幕坐标）也必须一致（鼠标命中依赖）
    assert geometry_without["height"] == geometry_with["height"]
    assert geometry_without["screen_y"] == geometry_with["screen_y"]


def test_streaming_pad_frames_keep_window_rows_stable() -> None:
    """连续流式输出 + 解析行反复出现/消失：浮窗屏幕行号逐帧稳定。"""
    from src.tui._const import ContentCmd, DisplayMsgsCmd, ParseInfoCmd

    model, session, step = _make_loop_session(80, 24)
    step()
    step(DisplayMsgsCmd(messages=[{"role": "user", "content": "你好"}], speed=0))
    step(ContentCmd(text="起始内容"))
    stable: list | None = None
    parse_on = False
    for index in range(10):
        step(ContentCmd(text=f"第{index}段流式内容，内容内容"))
        parse_on = not parse_on
        display = step(ParseInfoCmd(
            tool_names="Edit" if parse_on else "",
            tokens=index if parse_on else -1,
            elapsed=1.0 if parse_on else 0.0,
        ))
        rows = _rows_with(display, "⟳ /loop")
        assert rows, f"第 {index} 帧浮窗标题行消失"
        if stable is None:
            stable = rows
        else:
            assert rows == stable, f"第 {index} 帧浮窗抖动: {stable} -> {rows}"
