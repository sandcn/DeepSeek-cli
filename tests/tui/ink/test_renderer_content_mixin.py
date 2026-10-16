"""渲染器内容行发射 Mixin 测试（巨型文件拆分后的行为回归）。

``_RendererContentMixin``（``ink/_renderer_content.py``）承载 committed 内容行
→ 输出历史回调；``InkRenderer`` 经继承组合。断言拆分后行为不变：仅新增内容
行回调、缩短同步基线、未注入回调时零动作。
"""

from __future__ import annotations

import io

from src.tui.ink._renderer_content import _RendererContentMixin
from src.tui.ink.output import Frame, Line
from src.tui.ink.renderer import InkRenderer


def _frame(texts):
    return Frame([Line.of(t) for t in texts])


def test_mixin_is_composed_into_renderer():
    assert issubclass(InkRenderer, _RendererContentMixin)
    renderer = InkRenderer(stream=io.StringIO())
    assert renderer._content_line_count == 0
    assert renderer._frame_content_count == 0


def test_emit_only_new_committed_lines():
    seen: list = []
    renderer = InkRenderer(stream=io.StringIO())
    renderer.set_line_callback(seen.append)
    renderer.set_content_line_count(2, start=0)
    renderer._emit_content_lines(_frame(["a", "b", "tail"]))
    assert seen == ["a\n", "b\n"]

    # 行数未变 → 无新回调（帧内重绘/动画不产生历史）
    renderer._emit_content_lines(_frame(["a", "b", "tail2"]))
    assert seen == ["a\n", "b\n"]

    # 增长 → 仅回调新增行
    renderer.set_content_line_count(3, start=0)
    renderer._emit_content_lines(_frame(["a", "b", "c", "tail"]))
    assert seen == ["a\n", "b\n", "c\n"]


def test_emit_content_start_offset():
    seen: list = []
    renderer = InkRenderer(stream=io.StringIO())
    renderer.set_line_callback(seen.append)
    renderer.set_content_line_count(1, start=1)   # 内容区自文档第 2 行起
    renderer._emit_content_lines(_frame(["header", "body"]))
    assert seen == ["body\n"]


def test_shrink_syncs_baseline_without_callback():
    seen: list = []
    renderer = InkRenderer(stream=io.StringIO())
    renderer.set_line_callback(seen.append)
    renderer.set_content_line_count(3, start=0)
    renderer._emit_content_lines(_frame(["a", "b", "c"]))
    seen.clear()
    renderer.set_content_line_count(1, start=0)   # 清屏/重放：缩短
    renderer._emit_content_lines(_frame(["x"]))
    assert seen == []                              # 缩短不补记、不重复回调
    assert renderer._content_line_count == 1


def test_resync_sets_baseline_directly():
    renderer = InkRenderer(stream=io.StringIO())
    renderer.set_content_line_count(5, start=0, resync=True)
    assert renderer._frame_content_count == 5
    assert renderer._content_line_count == 5


def test_no_callback_is_noop():
    renderer = InkRenderer(stream=io.StringIO())
    renderer.set_content_line_count(2, start=0)
    renderer._emit_content_lines(_frame(["a", "b"]))
    assert renderer._content_line_count == 2


def test_reset_content_lines():
    renderer = InkRenderer(stream=io.StringIO())
    renderer.set_content_line_count(3, start=0)
    renderer.reset_content_lines()
    assert renderer._content_line_count == 0
    assert renderer._frame_content_count == 0


def test_callback_exception_is_swallowed():
    def _boom(_text):
        raise RuntimeError("callback failure")

    renderer = InkRenderer(stream=io.StringIO())
    renderer.set_line_callback(_boom)
    renderer.set_content_line_count(1, start=0)
    renderer._emit_content_lines(_frame(["a"]))    # 不抛异常
    assert renderer._content_line_count == 1
