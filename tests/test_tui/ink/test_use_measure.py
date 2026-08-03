"""useMeasure hook 测试（方向8 完善 react ink——host 元素尺寸测量）。

React Ink 语义：``useMeasure()`` 返回 ``{ref, width, height}``，ref 绑定到
host 元素；布局完成后尺寸经 layout effect 读取并触发重渲染（首帧 0x0）。
"""

from __future__ import annotations

from src.tui.ink import (
    BOX,
    TEXT,
    h,
    useMeasure,
    use_state,
)
from src.tui.ink.reconciler import Reconciler
from src.tui.ink.components import render_frame


class TestUseMeasure:
    def test_initial_zero_then_measured(self):
        """首帧返回 (0,0)，layout effect 后触发重渲染返回实际尺寸。"""
        measurements = []

        def Comp(props):
            m = useMeasure()
            measurements.append((m["width"], m["height"]))
            return h(BOX, {"ref": m["ref"], "width": 30, "height": 4},
                     h(TEXT, {"children": "x"}))

        r = Reconciler()
        root = r.create_root()
        # 首次渲染：布局未完成 → (0,0)
        r.render(root, h(Comp), 80, 24)
        assert measurements[-1] == (0, 0)
        # layout effect 提交后 set_state 触发第二次渲染 → 实际尺寸
        r.render(root, h(Comp), 80, 24)
        assert measurements[-1] == (30, 4)

    def test_measure_wrap_box(self):
        """测量内容自适应 BOX 的尺寸（宽度由父容器约束）。"""
        measurements = []

        def Comp(props):
            m = useMeasure()
            measurements.append((m["width"], m["height"]))
            return h(BOX, {"ref": m["ref"], "width": 40},
                     h(TEXT, {"children": "hello"}))

        r = Reconciler()
        root = r.create_root()
        r.render(root, h(Comp), 80, 24)
        r.render(root, h(Comp), 80, 24)
        # BOX width=40 → 尺寸稳定 (40, 1)
        assert measurements[-1] == (40, 1)

    def test_ref_receives_layout_box(self):
        """ref.current 在 layout 后为布局盒（含 x/y/w/h 字段）。"""
        captured = []

        def Comp(props):
            m = useMeasure()
            captured.append(m["ref"])
            return h(BOX, {"ref": m["ref"], "width": 20, "height": 2},
                     h(TEXT, {"children": "hi"}))

        r = Reconciler()
        root = r.create_root()
        r.render(root, h(Comp), 80, 24)
        r.render(root, h(Comp), 80, 24)
        box = captured[-1].current
        assert box is not None
        assert box.w == 20
        assert box.h == 2

    def test_function_ref_callback(self):
        """函数 ref 回调在 layout 后收到布局盒。"""
        calls = []

        def Comp(props):
            def ref_cb(box):
                calls.append(box)

            return h(BOX, {"ref": ref_cb, "width": 10, "height": 3},
                     h(TEXT, {"children": "x"}))

        r = Reconciler()
        root = r.create_root()
        r.render(root, h(Comp), 80, 24)
        assert len(calls) >= 1
        assert calls[-1].w == 10
        assert calls[-1].h == 3

    def test_measure_conditional_render(self):
        """useMeasure 常见用法：尺寸 > 0 时条件渲染内容。"""
        measurements = []

        def Comp(props):
            m = useMeasure()
            measurements.append((m["width"], m["height"]))
            children = "已测量" if m["width"] > 0 else "等待布局"
            return h(BOX, {"ref": m["ref"], "width": 15, "height": 2},
                     h(TEXT, {"children": children}))

        r = Reconciler()
        root = r.create_root()
        r.render(root, h(Comp), 80, 24)
        f1 = render_frame(root, 80)
        assert f1.lines[0].plain == "等待布局"
        r.render(root, h(Comp), 80, 24)
        f2 = render_frame(root, 80)
        assert f2.lines[0].plain == "已测量"
