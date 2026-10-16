"""src/renderer/targets/base — RenderTarget / CompositeRenderTarget 单元测试。

覆盖：
  - RenderTargetContext 默认值与字段
  - RenderTarget 抽象性：缺少抽象方法无法实例化；默认实现行为
  - CompositeRenderTarget：多目标扇出、width 取首目标、空列表兜底 80
"""

from __future__ import annotations

import pytest

from src.renderer.targets.base import (
    CompositeRenderTarget,
    RenderTarget,
    RenderTargetContext,
)


# ── RenderTargetContext ───────────────────────────────────

def test_context_defaults():
    ctx = RenderTargetContext()
    assert ctx.indent == 0
    assert ctx.depth == 0
    assert ctx.extra == {}


def test_context_custom_values():
    ctx = RenderTargetContext(indent=2, depth=3, extra={"k": "v"})
    assert ctx.indent == 2
    assert ctx.depth == 3
    assert ctx.extra == {"k": "v"}


# ── RenderTarget 抽象基类 ────────────────────────────────

class _RecorderTarget(RenderTarget):
    """记录调用的最小实现。"""

    def __init__(self, width=100):
        self._width = width
        self.calls = []

    def write(self, renderable):
        self.calls.append(("write", renderable))

    def write_line(self, text=""):
        self.calls.append(("write_line", text))

    def clear_line(self):
        self.calls.append(("clear_line",))

    @property
    def width(self):
        return self._width

    def flush(self):
        self.calls.append(("flush",))

    def close(self):
        self.calls.append(("close",))


def test_render_target_abstract():
    """缺少任一抽象方法的子类无法实例化。"""
    class _Partial(RenderTarget):
        def write(self, renderable):
            pass

    with pytest.raises(TypeError):
        _Partial()


def test_render_target_default_helpers():
    t = _RecorderTarget()
    # write_raw 默认委托给 write
    t.write_raw("raw")
    assert t.calls[-1] == ("write", "raw")
    # render_inline 默认返回原文
    assert t.render_inline("**x**") == "**x**"
    # flush / close 默认无操作
    t.flush()
    t.close()
    # 上下文管理器调用 close
    with t as inner:
        assert inner is t


def test_render_target_enter_exit_calls_close():
    t = _RecorderTarget()
    closed = []
    t.close = lambda: closed.append(True)  # type: ignore[method-assign]
    with t:
        pass
    assert closed == [True]


# ── CompositeRenderTarget ─────────────────────────────────

def test_composite_fans_out():
    a = _RecorderTarget()
    b = _RecorderTarget()
    c = CompositeRenderTarget([a, b])
    c.write("x")
    c.write_line("line")
    c.clear_line()
    c.write_raw("raw")
    assert a.calls == [("write", "x"), ("write_line", "line"), ("clear_line",), ("write", "raw")]
    assert b.calls == a.calls


def test_composite_width_first_target():
    a = _RecorderTarget(width=80)
    b = _RecorderTarget(width=120)
    assert CompositeRenderTarget([a, b]).width == 80


def test_composite_width_empty_default_80():
    assert CompositeRenderTarget([]).width == 80


def test_composite_flush_close_fans_out():
    a = _RecorderTarget()
    b = _RecorderTarget()
    c = CompositeRenderTarget([a, b])
    c.flush()
    c.close()
    assert a.calls == [("flush",), ("close",)]
    assert b.calls == a.calls
