"""单次 render 调用内完成 layout effect 的绘制前 flush（game_loop 语义）。

``InkSession._render_frame_impl`` 现在只构建一次组件树、调用一次
``Reconciler.render``；layout effect 中 ``set_state`` 触发的更新必须在
**同一调用内**于绘制前生效（React 语义），并返回是否仍有未 flush 更新。
"""

from __future__ import annotations

from src.tui.ink import Text, h
from src.tui.ink.components import render_frame
from src.tui.ink.hooks import useLayoutEffect, use_state
from src.tui.ink.helpers import strip_ansi
from src.tui.ink.reconciler import Reconciler


def test_layout_effect_state_flushes_inside_one_render_call() -> None:
    runs: list[int] = []

    def Comp(_props) -> object:
        value, set_value = use_state(0)
        useLayoutEffect(lambda: set_value(1), [])
        runs.append(value)
        return h(Text, None, f"v{value}")

    reconciler = Reconciler(None)
    root = Reconciler.create_root()

    pending = reconciler.render(root, h(Comp, None), 20, 10)
    frame = render_frame(root, 20)
    text = strip_ansi("\n".join(line.render() for line in frame.lines))

    assert pending is False
    assert "v1" in text
    # 同一次调用内部重跑提交阶段（不是调用方多帧驱动）。
    assert runs == [0, 1]


def test_render_returns_false_when_no_pending_update() -> None:
    def Comp(_props) -> object:
        return h(Text, None, "static")

    reconciler = Reconciler(None)
    root = Reconciler.create_root()

    assert reconciler.render(root, h(Comp, None), 20, 10) is False
