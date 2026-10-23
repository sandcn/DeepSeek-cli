"""大增量帧渲染回归测试（2026-10-23 流式输出闪烁修复）。

用户报障：上屏流式输出时会「闪」/「内容整体跳动一下后恢复」。

根因：``InkRenderer`` 的单帧重写行数超过旧上限 ``_MAX_REWRITE_ROWS``（200）时，
修复前会**降级为** ``clear_screen()`` + ``_write_full(frame)``——清屏后把
**整篇文档**（含全部历史）重新写一遍。大输出帧（工具 read/bash/grep 一次
刷数百行、模型单 chunk 输出数百行）在 Termux 上因此整片瞬时重绘（闪），
并把整篇历史重复写进 scrollback。

修复：超阈值只记录限频告警，渲染继续走增量路径——只写「必须显示的新增
/变化行」，不清屏、不重写整篇文档。

覆盖：
  1. 大增量帧不清屏、不写整篇文档，屏幕内容与帧一致（pyte）；
  2. 纯尾部追加仍走平移快路径（只写新增行）；
  3. 大帧后渲染器记账（``_prev`` / ``_buf_h`` / 光标行）与增量语义一致。
"""

from __future__ import annotations

import io

import pytest

from src.tui.ink.output import Frame, Line
from src.tui.ink.renderer import InkRenderer


def _line(text: str) -> Line:
    line = Line()
    line.append(text, None)
    return line


def _frame(texts: list[str]) -> Frame:
    return Frame([_line(t) for t in texts])


def _feed(stream: io.StringIO, parser, screen) -> str:
    """把渲染器新写入的字节喂给终端模拟器，并清空缓冲区。"""
    data = stream.getvalue()
    stream.seek(0)
    stream.truncate(0)
    if data:
        parser.feed(data)
    return data


def _screen_lines(screen) -> list[str]:
    return [line.rstrip() for line in screen.display]


def test_large_growth_does_not_clear_or_rewrite_whole_document() -> None:
    """大增量帧：不清屏、不整篇重写；屏幕显示帧末尾的可见窗口。"""
    pyte = pytest.importorskip("pyte")
    height = 20
    stream = io.StringIO()
    screen = pyte.Screen(40, height)
    parser = pyte.Stream(screen)
    renderer = InkRenderer(stream=stream, height=height)

    # 首帧：450 行历史 + 2 行底部区，文档远高于屏幕。
    base = [f"L{i:03d}" for i in range(450)] + ["status", "input"]
    renderer.render(_frame(base))
    _feed(stream, parser, screen)
    assert renderer._buf_h == len(base)

    # 第二帧：中间插入 300 行（模拟工具大输出），同时改一行（让首差异行
    # 落在文档中部，绕开纯尾部追加的平移快路径）。
    grown = list(base)
    grown[5] = "changed"
    grown[400:400] = [f"N{i:03d}" for i in range(300)]
    frame = _frame(grown)
    renderer.render(frame)
    data = _feed(stream, parser, screen)

    # 核心断言：不再清屏、不再整篇重写（修复前会写 700+ 行）。
    # ★ 2026-10-23（底部滚动快路径）：大增量帧现在先滚动 delta 行、再只重写
    #   可见区变化行——写入行数上限 = 屏幕高度（20），不再与新增行数同阶。
    assert "\x1b[2J" not in data
    rows = data.count("\x1b[K")
    assert rows <= height, (
        f"大增量帧重写行数异常：{rows}（应不超过可见区 {height} 行）"
    )

    # 屏幕内容 == 新帧最后 height 行（pyte 逐行核对）。
    assert _screen_lines(screen) == [t.rstrip() for t in grown[-height:]]
    assert renderer._prev is frame
    assert renderer._buf_h == len(grown)


def test_large_tail_append_stays_incremental() -> None:
    """纯尾部大追加：滚动 + 只重写可见区（不清屏、不重复历史）。"""
    pyte = pytest.importorskip("pyte")
    height = 20
    stream = io.StringIO()
    screen = pyte.Screen(40, height)
    parser = pyte.Stream(screen)
    renderer = InkRenderer(stream=stream, height=height)

    base = [f"L{i:03d}" for i in range(300)]
    renderer.render(_frame(base))
    _feed(stream, parser, screen)

    grown = base + [f"N{i:03d}" for i in range(250)]
    frame = _frame(grown)
    renderer.render(frame)
    data = _feed(stream, parser, screen)

    assert "\x1b[2J" not in data
    # 滚动 250 行由终端原生完成；内容只重写可见区 20 行。
    assert data.count("\x1b[K") <= height
    assert _screen_lines(screen) == [t.rstrip() for t in grown[-height:]]
    assert renderer._prev is frame
    assert renderer._buf_h == len(grown)
    assert renderer.cursor_row == height
