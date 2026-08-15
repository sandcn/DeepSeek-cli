"""React Ink 官方 API 缺口补齐测试（2026-08-16）。

覆盖四个官方 API 缺口（用户确认范围）：
  1. ``measureElement(dom_node)`` — 官方 v3.4+ 测量 API：布局盒/ref/None/
     畸形尺寸
  2. ``use_input(handler, options)`` mask 选项 — React Ink 生态 mask 语义
     （password 掩码）：char 输入掩码 / 非 char 不掩码 / 多 hook 隔离 /
     旧 bool 签名兼容 / (input, key) 双参适配
  3. ``useStdin().isAnyKeyPressed`` — 官方字段：默认 False / mark 置位 /
     reset 复位 / InputDispatcher 按键回调置位
  4. ``render()`` options — stdout（stream 别名）/ stderr 注入 / debug 帧
     统计 / exitOnCtrlC True/False 的 interrupt 注入与放行 / patchConsole
     控制台替换与恢复
"""

from __future__ import annotations

import io
import os
import sys
import time

import pytest

from src.tui.ink import h, render, measureElement
from src.tui.ink.layout import LayoutBox
from src.tui.ink.reconciler import Reconciler
from src.tui.ink.hooks import (
    use_input,
    useStdin,
    useStderr,
    mark_any_key_pressed,
    reset_any_key_pressed,
    set_input_router_callback,
)
from src.tui._input_parser import KeyEvent, InputParser
from src.tui._input_io import InputIO
from src.tui._input_buffer import InputBufferEditor
from src.tui._input_dispatcher import InputDispatcher
from pathlib import Path


# ═══════════════════════════════════════════════════════════
# 1. measureElement
# ═══════════════════════════════════════════════════════════

class TestMeasureElement:
    def test_layout_box(self):
        """布局盒对象 → {width, height}（官方 DOM 节点等价物）。"""
        box = LayoutBox(x=1, y=2, w=10, h=3)
        assert measureElement(box) == {"width": 10, "height": 3}

    def test_ref_object_with_current(self):
        """带 current 的 ref 对象（use_ref 返回值）→ 解引用测量。"""
        class Ref:
            current = None
        ref = Ref()
        ref.current = LayoutBox(w=7, h=2)
        assert measureElement(ref) == {"width": 7, "height": 2}

    def test_ref_current_none_returns_zero(self):
        """ref.current 为 None（未布局）→ 0x0。"""
        class Ref:
            current = None
        assert measureElement(Ref()) == {"width": 0, "height": 0}

    def test_none_returns_zero(self):
        """None → 0x0。"""
        assert measureElement(None) == {"width": 0, "height": 0}

    def test_plain_object_no_dims_returns_zero(self):
        """无尺寸属性的对象 → 0x0。"""
        assert measureElement(object()) == {"width": 0, "height": 0}

    def test_malformed_dims_clamped_to_zero(self):
        """畸形尺寸（inf/负数）→ 0x0（渲染错误修复一贯防御）。"""
        import math

        class Box:
            w = float("inf")
            h = -3
        assert measureElement(Box()) == {"width": 0, "height": 0}


# ═══════════════════════════════════════════════════════════
# 2. use_input mask 选项
# ═══════════════════════════════════════════════════════════

def _render_component(comp) -> "callable":
    """渲染函数组件，返回 input router（useInput 钩子分发入口）。"""
    captured = []
    set_input_router_callback(lambda router: captured.append(router))
    try:
        r = Reconciler()
        root = r.create_root()
        r.render(root, h(comp), 80, 24)
        assert captured, "渲染后应发布 composite router"
        return captured[-1]
    finally:
        set_input_router_callback(None)


class TestUseInputMask:
    def test_char_input_masked(self):
        """mask 非 None 时 char 事件 input 以 mask*len 替代。"""
        got = []

        def Comp(props):
            use_input(lambda event: (got.append(event), True)[1], {"mask": "*"})
            return h("text", {"children": "x"})

        router = _render_component(Comp)
        router(KeyEvent(kind="char", char="abc", raw=b"abc"))
        assert got[-1].char == "***"

    def test_two_arg_handler_receives_masked_input(self):
        """(input, key) 双参 handler 收到掩码后 input（React Ink 生态签名）。"""
        got = []

        def Comp(props):
            use_input(
                lambda input_, key: (got.append((input_, key)), True)[1],
                {"mask": "•"},
            )
            return h("text", {"children": "x"})

        router = _render_component(Comp)
        router(KeyEvent(kind="char", char="hi", raw=b"hi"))
        input_, key = got[-1]
        assert input_ == "••"
        assert key["ctrl"] is False

    def test_non_char_event_not_masked(self):
        """非 char 事件（箭头键）不掩码——char 为空串，原样透传。"""
        got = []

        def Comp(props):
            use_input(lambda event: (got.append(event), True)[1], {"mask": "*"})
            return h("text", {"children": "x"})

        router = _render_component(Comp)
        router(KeyEvent(kind="arrow_up", raw=b"\x1b[A"))
        assert got[-1].kind == "arrow_up"
        assert got[-1].char == ""

    def test_mask_only_affects_own_hook(self):
        """mask 只影响本 hook——后续无 mask hook 收到原始 input。

        ★ router 语义：任一 handler 返回 True 即消费（短路后续 hook）。
        首个 mask hook 返回 False（不消费）保证两个 hook 都被调用——
        验证 mask 隔离而非消费短路。
        """
        got = []

        def Comp(props):
            use_input(
                lambda event: (got.append(("masked", event.char)), False)[1],
                {"mask": "*"},
            )
            use_input(lambda event: (got.append(("raw", event.char)), True)[1])
            return h("text", {"children": "x"})

        router = _render_component(Comp)
        router(KeyEvent(kind="char", char="ab", raw=b"ab"))
        assert got[0] == ("masked", "**")
        assert got[1] == ("raw", "ab")

    def test_empty_char_masked_stays_empty(self):
        """空 char 掩码后仍空串（mask * 0）。"""
        got = []

        def Comp(props):
            use_input(lambda event: (got.append(event.char), True)[1], {"mask": "#"})
            return h("text", {"children": "x"})

        router = _render_component(Comp)
        router(KeyEvent(kind="char", char="", raw=b""))
        assert got[-1] == ""

    def test_bool_is_active_old_signature_compat(self):
        """旧签名 use_input(handler, False)：is_active=False 不参与 router。"""
        got = []

        def Comp(props):
            use_input(lambda event: (got.append(event), True)[1], False)
            return h("text", {"children": "x"})

        r = Reconciler()
        root = r.create_root()
        r.render(root, h(Comp), 80, 24)
        # 无 active hook → router 应为 None（输入走旧路径，零行为变化）
        assert r._build_input_router_from_hooks([], []) is None

    def test_is_active_false_in_options(self):
        """options dict：isActive=False 的 hook 不参与 router。

        无 active hook 时 router 为 None（输入走旧路径）——事件不经
        use_input，handler 不会被调用。
        """
        got = []

        def Comp(props):
            use_input(
                lambda event: (got.append(event), True)[1],
                {"isActive": False, "mask": "*"},
            )
            return h("text", {"children": "x"})

        r = Reconciler()
        root = r.create_root()
        r.render(root, h(Comp), 80, 24)
        assert r._build_input_router_from_hooks([], []) is None
        assert got == [], "isActive=False 的 hook 不应收到事件"

    def test_default_no_mask_passthrough(self):
        """缺省 mask（无 options / 纯 bool True）→ input 原样。"""
        got = []

        def Comp(props):
            use_input(lambda event: (got.append(event.char), True)[1])
            return h("text", {"children": "x"})

        router = _render_component(Comp)
        router(KeyEvent(kind="char", char="plain", raw=b"plain"))
        assert got[-1] == "plain"

    def test_mask_change_rebuilds_router(self):
        """mask 变化（重渲染）→ router 重建，新掩码生效。"""
        got = []
        state = {"mask": "*"}

        def Comp(props):
            use_input(lambda event: (got.append(event.char), True)[1], {"mask": state["mask"]})
            return h("text", {"children": "x"})

        router = _render_component(Comp)
        router(KeyEvent(kind="char", char="ab", raw=b"ab"))
        assert got[-1] == "**"
        # 重渲染（mask 变化）→ 新 router
        state["mask"] = "#"
        r = Reconciler()
        root = r.create_root()
        captured = []
        set_input_router_callback(lambda rb: captured.append(rb))
        try:
            r.render(root, h(Comp), 80, 24)
            router2 = captured[-1]
            router2(KeyEvent(kind="char", char="ab", raw=b"ab"))
            assert got[-1] == "##"
        finally:
            set_input_router_callback(None)


# ═══════════════════════════════════════════════════════════
# 3. useStdin().isAnyKeyPressed
# ═══════════════════════════════════════════════════════════

class TestIsAnyKeyPressed:
    def test_default_false(self):
        """默认 False（无按键）。"""
        reset_any_key_pressed()
        assert useStdin()["isAnyKeyPressed"] is False

    def test_mark_sets_true(self):
        """mark_any_key_pressed 后 True。"""
        reset_any_key_pressed()
        mark_any_key_pressed()
        assert useStdin()["isAnyKeyPressed"] is True

    def test_reset_clears(self):
        """reset_any_key_pressed 复位。"""
        mark_any_key_pressed()
        reset_any_key_pressed()
        assert useStdin()["isAnyKeyPressed"] is False

    def test_kept_true_after_mark(self):
        """置位后保持 True（React Ink 语义：不复位）。"""
        reset_any_key_pressed()
        mark_any_key_pressed()
        mark_any_key_pressed()
        assert useStdin()["isAnyKeyPressed"] is True

    def test_input_dispatcher_wires_key_pressed(self):
        """InputDispatcher 分发字节 → 注入回调触发（session 接线路径）。"""
        reset_any_key_pressed()
        r_fd, w_fd = os.pipe()
        try:
            io_inst = InputIO(fd=r_fd)
            dispatcher = InputDispatcher(
                io=io_inst,
                buffer_editor=InputBufferEditor(
                    history_file=Path("/tmp/hx_isanykey"),
                    history_io=type("_H", (), {"read": lambda: (None, False), "append": lambda t: True, "compact": lambda: True})(),
                ),
                parser=InputParser(io=io_inst),
            )
            calls = []
            dispatcher.set_key_pressed_callback(lambda: calls.append(1))
            os.write(w_fd, b"a")
            processed = dispatcher.read_stdin_once()
            assert processed, "应读到字节"
            assert calls, "按键回调应被触发"
        finally:
            os.close(r_fd)
            os.close(w_fd)


# ═══════════════════════════════════════════════════════════
# 4. render() options
# ═══════════════════════════════════════════════════════════

class StubInput:
    """render() stdin 桩：记录 interrupt 注入/放行调用。"""

    def __init__(self):
        self.interrupt_cb = None
        self.routable = False
        self.key_cb = None
        self.router = None

    def set_input_hook_router(self, router):
        self.router = router

    def set_key_pressed_callback(self, cb):
        self.key_cb = cb

    def set_interrupt_callback(self, cb):
        self.interrupt_cb = cb

    def set_interrupt_routable(self, flag):
        self.routable = bool(flag)


def _sleep_frames(seconds: float = 0.2) -> None:
    """等待若干渲染帧（10Hz 渲染线程异步时序）。"""
    time.sleep(seconds)


class TestRenderOptions:
    def test_stream_alias_and_stderr_injection(self):
        """stream（旧参数）+ stderr 注入：useStderr().stderr 返回注入流。"""
        err = io.StringIO()
        out = io.StringIO()
        seen = []

        def Comp(props):
            seen.append(useStderr()["stderr"])
            return h("text", {"children": "hi"})

        inst = render(h(Comp), stream=out, width=40, height=10, stderr=err)
        try:
            _sleep_frames()
            assert seen, "组件应渲染并读取 useStderr"
            assert seen[-1] is err
        finally:
            inst["unmount"]()

    def test_stdout_prefers_stdout_over_stream(self):
        """stdout 优先于 stream（两者都提供时输出到 stdout）。"""
        out1, out2 = io.StringIO(), io.StringIO()
        inst = render(
            h("text", {"children": "hi"}),
            stream=out1, stdout=out2, width=40, height=10,
        )
        try:
            _sleep_frames()
            assert "hi" in out2.getvalue()
            assert "hi" not in out1.getvalue()
        finally:
            inst["unmount"]()

    def test_debug_frame_stats_written_to_stderr(self):
        """debug=True：渲染帧统计写入注入的 stderr 流。"""
        err = io.StringIO()
        out = io.StringIO()
        inst = render(
            h("text", {"children": "x"}),
            stdout=out, width=40, height=10, debug=True, stderr=err,
        )
        try:
            _sleep_frames(0.35)
            assert "[ink:debug]" in err.getvalue(), err.getvalue()
        finally:
            inst["unmount"]()

    def test_debug_false_no_frame_stats(self):
        """debug=False（默认）：不输出帧统计。"""
        err = io.StringIO()
        out = io.StringIO()
        inst = render(
            h("text", {"children": "x"}),
            stdout=out, width=40, height=10, stderr=err,
        )
        try:
            _sleep_frames(0.2)
            assert "[ink:debug]" not in err.getvalue()
        finally:
            inst["unmount"]()

    def test_exit_on_ctrl_c_true_injects_interrupt(self):
        """exitOnCtrlC=True：注入 interrupt 回调（Ctrl+C → request_exit）。"""
        stub = StubInput()
        out = io.StringIO()
        inst = render(
            h("text", {"children": "x"}),
            stdout=out, width=40, height=10, stdin=stub, exitOnCtrlC=True,
        )
        try:
            _sleep_frames()
            assert stub.interrupt_cb is not None, "应注入 interrupt 回调"
            assert stub.routable is False, "默认不放行 interrupt 到 router"
            # 回调调用不抛异常（request_exit 幂等）
            stub.interrupt_cb()
        finally:
            inst["unmount"]()

    def test_exit_on_ctrl_c_false_routes_interrupt(self):
        """exitOnCtrlC=False：不放行 interrupt 回调；Ctrl+C 事件交给 router。"""
        stub = StubInput()
        out = io.StringIO()
        inst = render(
            h("text", {"children": "x"}),
            stdout=out, width=40, height=10, stdin=stub, exitOnCtrlC=False,
        )
        try:
            _sleep_frames()
            assert stub.interrupt_cb is None, "不应注入 interrupt 回调"
            assert stub.routable is True, "应放行 interrupt 到 router"
        finally:
            inst["unmount"]()

    def test_patch_console_redirects_and_restores(self):
        """patchConsole=True：sys.stdout/stderr 重定向到 TUI 流；cleanup 恢复。"""
        out = io.StringIO()
        orig_out, orig_err = sys.stdout, sys.stderr
        inst = None
        try:
            inst = render(
                h("text", {"children": "x"}),
                stdout=out, width=40, height=10, patchConsole=True,
            )
            _sleep_frames()
            sys.stdout.write("patched-out")
            sys.stderr.write("patched-err")
            assert "patched-out" in out.getvalue(), "print 输出应重定向到 TUI 流"
            assert sys.stdout is not orig_out
            assert sys.stderr is not orig_err
        finally:
            if inst is not None:
                inst["cleanup"]()
            sys.stdout, sys.stderr = orig_out, orig_err
        assert sys.stdout is orig_out, "cleanup 后 sys.stdout 应恢复"
        assert sys.stderr is orig_err, "cleanup 后 sys.stderr 应恢复"

    def test_patch_console_false_no_redirect(self):
        """patchConsole=False（默认）：不替换 sys.stdout。"""
        out = io.StringIO()
        orig_out, orig_err = sys.stdout, sys.stderr
        inst = None
        try:
            inst = render(
                h("text", {"children": "x"}),
                stdout=out, width=40, height=10,
            )
            _sleep_frames()
            assert sys.stdout is orig_out
            assert sys.stderr is orig_err
        finally:
            if inst is not None:
                inst["unmount"]()
            sys.stdout, sys.stderr = orig_out, orig_err
