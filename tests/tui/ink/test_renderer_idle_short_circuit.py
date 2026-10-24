"""空闲帧 O(1) 短路 + 基础写屏 smoke（2026-10-24）。

会话空闲复用路径会把**同一个 Frame 对象**再次传给 ``InkRenderer.render``。
修复前仍会进入 ``first_diff_line`` 逐行扫描整篇文档（5000 行约 0.7ms/帧，
60Hz 下 42ms/s）；修复后同一对象直接返回，且不改变渲染器记账。
"""

from __future__ import annotations

import io

from src.tui.ink.output import Frame, Line
from src.tui.ink.renderer import InkRenderer


def _line(text: str) -> Line:
    line = Line()
    line.append(text, None)
    return line


def _frame(texts: list[str]) -> Frame:
    return Frame([_line(text) for text in texts])


def _drain(stream: io.StringIO) -> str:
    data = stream.getvalue()
    stream.seek(0)
    stream.truncate(0)
    return data


def test_same_frame_object_is_noop() -> None:
    stream = io.StringIO()
    renderer = InkRenderer(stream=stream, height=10)
    renderer.set_width(40)
    frame = _frame(["alpha", "beta"])
    renderer.render(frame)
    _drain(stream)
    prev = renderer.prev_frame
    # 同一对象重复渲染：零字节输出、记账不变。
    renderer.render(frame)
    assert _drain(stream) == ""
    assert renderer.prev_frame is prev


def test_first_frame_contains_all_lines() -> None:
    stream = io.StringIO()
    renderer = InkRenderer(stream=stream, height=10)
    renderer.set_width(40)
    renderer.render(_frame(["alpha", "beta"]))
    out = _drain(stream)
    assert "alpha" in out and "beta" in out


def test_growth_writes_new_line() -> None:
    stream = io.StringIO()
    renderer = InkRenderer(stream=stream, height=10)
    renderer.set_width(40)
    renderer.render(_frame(["alpha"]))
    _drain(stream)
    renderer.render(_frame(["alpha", "beta"]))
    out = _drain(stream)
    assert "beta" in out
    assert renderer.prev_frame is not None
    assert renderer.prev_frame.height == 2
