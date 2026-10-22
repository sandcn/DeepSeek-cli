"""渲染器"文档上方空行区"残留清理回归测试（2026-10-22 用户报障）。

用户报障：``python chat.py`` 启动后立即按回车，TUI 会出现渲染错误——屏幕
顶部残留上一版 banner/边框（两份 UI 叠加），或文档整体下移一行、框顶被
覆盖。

根因（启动期偏移变化）：
  - 启动首帧在终端光标行查询失败（DSR 超时/启动瞬间按键抢占）时回退
    「文档底部贴屏幕底部」锚定（``_resolve_start_row``），``_buf_h`` 被置为
    屏幕高度；文档随后增长/缩短时 ``drift = _buf_h - doc_h`` 变化，物理行与
    文档行的映射整体位移；
  - ``_grow_drifted`` / ``_rewrite_drifted`` 清理「文档上方空行区」时，只在
    ``old_line is not None or old_idx >= prev_h`` 时清行——``old_idx < 0``
    （prev 帧同样覆盖不到）的物理行被视为"用户历史"而不清，但其中可能残留
    **本会话更早帧**绘制的 UI → 屏幕上留下旧 banner/边框。

修复：
  - 新增 ``InkRenderer._painted_top``（本会话写过的最高物理行，0-based）：
    首帧全量写入按 ``max(0, _buf_h - n)`` 初始化，全量增长/缩短按
    ``max(0, drift)`` 收缩，清屏路径重置为 0；
  - 偏移变化（``drift0 != drift1`` / ``old_drift != drift``）时，对
    ``q >= _painted_top`` 的未知行保守清空——既不残留旧 UI，也不误清用户
    终端里首帧之前的历史输出。

覆盖：
  1. ``_painted_top`` 初始化 / 增长收缩 / 清屏重置；
  2. 偏移变化时清理 ``_painted_top`` 范围内的未知残留行（pyte 屏幕断言）；
  3. 用户历史（``_painted_top`` 之上）不被清除。
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


def _frame(*texts: str) -> Frame:
    return Frame([_line(t) for t in texts])


def _drain(stream: io.StringIO, parser, screen) -> str:
    """把渲染器新写入的字节喂给终端模拟器，并清空缓冲区。"""
    data = stream.getvalue()
    stream.seek(0)
    stream.truncate(0)
    if data:
        parser.feed(data)
    return data


def _screen_lines(screen) -> list[str]:
    return [line.rstrip() for line in screen.display]


# ── 1. _painted_top 记账 ──────────────────────────────


def test_painted_top_initialised_from_bottom_anchor() -> None:
    """首帧回退底部锚定时：写入范围顶部 = 文档起始物理行。"""
    renderer = InkRenderer(stream=io.StringIO(), height=10)
    renderer.render(_frame(*[f"L{i}" for i in range(6)]))
    # 首帧 6 行、屏幕 10 行 → 文档从物理行 4（0-based）开始。
    assert renderer._painted_top == 4
    assert renderer._buf_h == 10


def test_painted_top_shrinks_when_document_grows_to_fill_screen() -> None:
    """文档增长到填满屏幕：写入范围顶部收缩到 0（整屏都属会话）。"""
    renderer = InkRenderer(stream=io.StringIO(), height=10)
    renderer.render(_frame(*[f"L{i}" for i in range(6)]))
    assert renderer._painted_top == 4
    renderer.render(_frame(*[f"L{i}" for i in range(10)]))
    assert renderer._painted_top == 0


def test_painted_top_reset_on_full_clear() -> None:
    """清屏后屏幕干净：写入范围重置，允许下一帧清理整屏。"""
    renderer = InkRenderer(stream=io.StringIO(), height=10)
    renderer.render(_frame(*[f"L{i}" for i in range(6)]))
    assert renderer._painted_top == 4
    renderer.full_clear()
    assert renderer._painted_top == 0


# ── 2. 偏移变化时清理未知残留行 ───────────────────────


def test_offset_change_clears_stale_rows_inside_painted_range() -> None:
    """文档缩短（偏移变化）后，会话写入范围内 prev 覆盖不到的残留行被清空。

    构造：
      - 首帧 10 行填满屏幕（``_painted_top == 0``）；
      - 缩短到 3 行（偏移 0 → 7，文档贴底，上方 7 行为空）；
      - 模拟屏幕上更早帧/外部写入的残留（pyte 直接写行 1..3）；
      - 再缩短到 1 行（偏移 7 → 9）：行 1..8 属于"文档上方空行区"，
        其中行 1..3 在渲染器记账里 ``old_idx < 0``（prev 只有 3 行）。

    修复前：这类行不被清理 → STALE 残留（用户看到的旧 banner/边框）。
    修复后：偏移变化且 ``q >= _painted_top`` → 清空。
    """
    pyte = pytest.importorskip("pyte")
    stream = io.StringIO()
    screen = pyte.Screen(40, 10)
    parser = pyte.Stream(screen)
    renderer = InkRenderer(stream=stream, height=10)

    renderer.render(_frame(*[f"L{i}" for i in range(10)]))
    _drain(stream, parser, screen)
    renderer.render(_frame("A", "B", "C"))
    _drain(stream, parser, screen)
    assert renderer._painted_top == 0

    # 模拟屏幕上更早帧的残留（渲染器 prev 里只有 3 行，覆盖不到这几行）。
    # 写完把模拟终端光标移回渲染器记账所在行（行 10）——真实场景的残留由
    # 渲染器自身写入，光标记账与终端一致。
    parser.feed(
        "\x1b[1;1HSTALE-1\x1b[2;1HSTALE-2\x1b[3;1HSTALE-3\x1b[10;1H",
    )
    assert _screen_lines(screen)[:3] == ["STALE-1", "STALE-2", "STALE-3"]

    renderer.render(_frame("Z"))
    _drain(stream, parser, screen)
    lines = _screen_lines(screen)
    assert lines[:9] == [""] * 9, lines
    assert lines[9] == "Z"


def test_offset_change_keeps_user_history_above_painted_range() -> None:
    """偏移变化只清会话写入范围：首帧之前的用户历史输出不被清除。

    构造：屏幕上先有 4 行用户历史，首帧 6 行从第 5 行开始追加
    （``_painted_top == 4``）；文档缩短到 3 行后，行 1..4 仍属用户历史，
    必须原样保留。
    """
    pyte = pytest.importorskip("pyte")
    stream = io.StringIO()
    screen = pyte.Screen(40, 10)
    parser = pyte.Stream(screen)
    parser.feed(
        "\x1b[1;1HHIST-1\x1b[2;1HHIST-2\x1b[3;1HHIST-3\x1b[4;1HHIST-4",
    )
    renderer = InkRenderer(stream=stream, height=10)
    renderer.render(_frame(*[f"L{i}" for i in range(6)]))
    _drain(stream, parser, screen)
    assert renderer._painted_top == 4
    lines = _screen_lines(screen)
    assert lines[:4] == ["HIST-1", "HIST-2", "HIST-3", "HIST-4"]
    assert lines[4:10] == [f"L{i}" for i in range(6)]

    renderer.render(_frame("A", "B", "C"))
    _drain(stream, parser, screen)
    lines = _screen_lines(screen)
    # 用户历史保留；文档贴底（行 8..10）。
    assert lines[:4] == ["HIST-1", "HIST-2", "HIST-3", "HIST-4"], lines
    assert lines[7:10] == ["A", "B", "C"], lines
    # 中间让出的行（会话写过、现为空行区）被清空。
    assert lines[4:7] == ["", "", ""], lines
