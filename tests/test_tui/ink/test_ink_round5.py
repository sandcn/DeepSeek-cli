"""方向4 — 完善 react ink 测试：Transform / memo children 比较 / 增量性能。

覆盖：
  - Transform 组件：字符串变换 + 嵌套 Element 递归变换 + 无 transform 透传。
  - memo children 比较修复：props 未变但 children 变化 → memo 不短路（重渲染）。
  - _should_render force 标记保留（底部重绘请求不丢失）。
  - input-area Line 快路径（canvas 行直接存 Line 对象）。
"""

from __future__ import annotations

import io
import time

from src.tui.ink import h, TEXT, BOX, Transform, Static, Newline, Fragment, memo, useStdin, useStdout, useStderr, Line, StyledRun
from src.tui.core.style import Style
from src.tui.ink.reconciler import Reconciler
from src.tui.ink.components import render_frame


class TestTransform:
    """React Ink Transform 组件（完善 react ink）。"""

    def _render(self, element):
        r = Reconciler()
        root = r.create_root()
        r.render(root, element, 80, 24)
        return render_frame(root, 80)

    def test_transform_uppercase_string_child(self):
        """字符串子级应用 uppercase 变换。"""
        el = h(Transform, {"transform": lambda s: s.upper()}, "hello")
        f = self._render(el)
        assert len(f.lines) == 1
        assert f.lines[0].plain == "HELLO"

    def test_transform_lowercase(self):
        """lowercase 变换。"""
        el = h(Transform, {"transform": lambda s: s.lower()}, "ABC")
        f = self._render(el)
        assert f.lines[0].plain == "abc"

    def test_transform_children_prop(self):
        """children 显式 prop 也支持（既有函数组件契约）。"""
        el = h(Transform, {"transform": lambda s: s.replace("x", "y"), "children": "xoxo"})
        f = self._render(el)
        assert f.lines[0].plain == "yoyo"

    def test_transform_nested_element(self):
        """嵌套 Element 递归应用到 TEXT 叶子。"""
        inner = h(BOX, None, [h(TEXT, {"children": "hi"}), h(TEXT, {"children": "there"})])
        el = h(Transform, {"transform": lambda s: s.upper()}, inner)
        f = self._render(el)
        plains = [l.plain for l in f.lines]
        assert "HI" in plains and "THERE" in plains

    def test_transform_none_passthrough(self):
        """无 transform 时原样透传。"""
        el = h(Transform, {}, "plain")
        f = self._render(el)
        assert f.lines[0].plain == "plain"

    def test_transform_children_injected_by_reconciler(self):
        """reconciler 将变参子级注入 props.children（函数组件 React 语义）。"""
        captured = {}

        def Probe(props):
            captured["children"] = props.get("children")
            return h(TEXT, {"children": "ok"})

        r = Reconciler()
        root = r.create_root()
        r.render(root, h(Probe, {}, "kid"), 80, 24)
        assert captured.get("children") is not None
        assert captured["children"][0].props["children"] == "kid"


class TestMemoChildrenComparison:
    """memo 组件 children 变化 → 不短路（重渲染子树）。"""

    def test_memo_skips_unchanged_children(self):
        """props/children 均未变 → memo 短路（组件函数不重复调用）。"""
        calls = {"n": 0}

        def Inner(props):
            calls["n"] += 1
            return h(TEXT, {"children": props.get("label", "inner")})

        MemoInner = memo(Inner)
        r = Reconciler()
        root = r.create_root()
        r.render(root, h(MemoInner, {"label": "a"}, "child"), 80, 24)
        first_calls = calls["n"]
        # 同 props + 同 children → 短路（不重调用）
        r.render(root, h(MemoInner, {"label": "a"}, "child"), 80, 24)
        assert calls["n"] == first_calls, (
            f"memo 应短路（props/children 未变），实际调用 {calls['n']} 次"
        )

    def test_memo_children_change_forces_render(self):
        """children 变化（props 相同）→ memo 不短路（修复前被误跳过）。"""
        calls = {"n": 0}

        def Inner(props):
            calls["n"] += 1
            return h(TEXT, {"children": props.get("label", "inner")})

        MemoInner = memo(Inner)
        r = Reconciler()
        root = r.create_root()
        r.render(root, h(MemoInner, {"label": "a"}, "first"), 80, 24)
        first_calls = calls["n"]
        # children 变化（props 相同）→ 必须重渲染
        r.render(root, h(MemoInner, {"label": "a"}, "second"), 80, 24)
        assert calls["n"] == first_calls + 1, (
            f"children 变化应重渲染，实际调用 {calls['n']} 次（修复前误跳过）"
        )


class TestShouldRenderForcePreserved:
    """_should_render force 标记保留（底部重绘请求不丢失）。"""

    def _session(self):
        from src.tui.ink.session import InkSession
        from src.tui.app.model import AppModel
        s = object.__new__(InkSession)
        s._config = type("C", (), {"render_interval": 0.1})()
        s._bottom_redraw_requested = type("E", (), {
            "is_set": lambda self: False,
            "set": lambda self: None,
            "clear": lambda self: None,
        })()
        s._dirty = False
        s._last_bottom_redraw = 0.0
        return s

    def test_force_request_within_window_not_lost(self):
        """force 请求在间隔窗口内 → dirty 保留，下一拍渲染（不丢失）。"""
        s = self._session()
        s._last_bottom_redraw = time.monotonic()  # 窗口内
        # 仅 force（dirty 未置位）→ 本拍不渲染
        s._bottom_redraw_requested = type("E", (), {
            "is_set": lambda self: True,
            "set": lambda self: None,
            "clear": lambda self: None,
        })()
        assert s._should_render(False) is False
        # dirty 已由 force 置位 → 下一拍（间隔到期）渲染
        s._bottom_redraw_requested = type("E", (), {
            "is_set": lambda self: False,
            "set": lambda self: None,
            "clear": lambda self: None,
        })()
        time.sleep(0.12)
        assert s._should_render(False) is True


class TestNeedsAnimation:
    """_needs_animation 活跃动画状态判定（主 agent 侧动画保持）。"""

    def _session(self, model):
        from src.tui.ink.session import InkSession
        s = object.__new__(InkSession)
        s._model = model
        return s

    def test_idle_false(self):
        """空闲（无流式/无工具/无解析）→ 不需要动画渲染。"""
        from src.tui.app.model import AppModel
        s = self._session(AppModel())
        assert s._needs_animation() is False

    def test_status_active_true(self):
        """流式生成（status_active）→ 需要动画渲染。"""
        from src.tui.app.model import AppModel
        model = AppModel()
        model.status.status_active = True
        assert self._session(model)._needs_animation() is True

    def test_tool_running_true(self):
        """开放工具卡（工具执行中）→ 需要动画渲染（边框/● 呼吸）。"""
        from src.tui.app.model import AppModel
        model = AppModel()
        model.open_tool_box("t1", "bash", "ls")
        assert self._session(model)._needs_animation() is True

    def test_parse_line_true(self):
        """实时解析进度行存在 → 需要动画渲染（spinner 推进）。"""
        from src.tui.app.model import AppModel
        from src.renderer.ansi.helpers import AnsiLine
        model = AppModel()
        model.parse_line = AnsiLine.of("  ~ rf 51t 0.5s")
        assert self._session(model)._needs_animation() is True

    def test_no_model_false(self):
        """模型缺失（防御）→ 不需要动画渲染。"""
        assert self._session(None)._needs_animation() is False


class TestShouldRenderAnimationKeepAlive:
    """_should_render 动画保持：活跃动画状态持续 10Hz 渲染，空闲回退跳过。"""

    def _session(self, model):
        from src.tui.ink.session import InkSession
        s = object.__new__(InkSession)
        s._model = model
        s._config = type("C", (), {"render_interval": 0.1})()
        s._bottom_redraw_requested = type("E", (), {
            "is_set": lambda self: False,
            "set": lambda self: None,
            "clear": lambda self: None,
        })()
        s._dirty = False
        s._last_bottom_redraw = 0.0
        return s

    def test_tool_running_renders_without_events(self):
        """工具执行中（无命令无输入）→ 间隔到期仍渲染（修复冻结）。"""
        from src.tui.app.model import AppModel
        model = AppModel()
        model.open_tool_box("t1", "bash", "sleep 1")
        s = self._session(model)
        # 无命令（changed=False）无 force，但动画活跃 → 本拍渲染
        assert s._should_render(False) is True
        assert s._dirty is False  # 渲染后清除脏
        # 动画仍活跃 → 下一拍（间隔到期）继续渲染（10Hz 推进）
        time.sleep(0.12)
        assert s._should_render(False) is True

    def test_animation_within_window_waits_tick(self):
        """活跃动画但 render_interval 未到期 → 等待拍（dirty 保留）。"""
        from src.tui.app.model import AppModel
        model = AppModel()
        model.status.status_active = True
        s = self._session(model)
        s._last_bottom_redraw = time.monotonic()  # 窗口内
        assert s._should_render(False) is False  # 窗口内等待
        time.sleep(0.12)
        assert s._should_render(False) is True  # 下一拍渲染

    def test_idle_still_skips(self):
        """空闲（无动画）→ 保持跳过渲染（CPU ~0 不回归）。"""
        from src.tui.app.model import AppModel
        s = self._session(AppModel())
        assert s._should_render(False) is False

    def test_animation_stops_after_tool_closed(self):
        """工具关闭后（无其他动画）→ 回退空闲跳过。"""
        from src.tui.app.model import AppModel
        model = AppModel()
        model.open_tool_box("t1", "bash", "sleep 1")
        s = self._session(model)
        assert s._should_render(False) is True
        # 工具关闭 → 动画结束 → 空闲跳过
        model.close_tool_box("t1", True)
        time.sleep(0.12)
        assert s._should_render(False) is False


class TestInputAreaLineFastPath:
    """input-area canvas 行 Line 快路径（性能 + 增量身份短路）。"""

    def test_canvas_row_stores_line_directly(self):
        """box.x==0 且行未命中 → canvas 行直接存 Line 对象（非 dict）。"""
        from src.tui.ink.fiber import Fiber
        from src.tui.ink.layout import LayoutBox
        from src.tui.app import input_area

        fiber = Fiber("host", "input-area", {
            "text": "hi", "cursor_pos": 2, "prompt": "> ",
            "completion": None, "status_active": False,
            "cpu": 1, "mem": 2, "history_search": None,
            "width": 20,
        })
        fiber.layout_box = LayoutBox(x=0, y=0, w=20, h=3)
        canvas = [None] * 3
        input_area._paint(fiber, canvas)
        # 所有行应为 Line（box.x==0 快路径），非 dict
        assert all(isinstance(r, input_area.Line) for r in canvas), (
            f"input-area canvas 行应为 Line 对象，实际: {[type(r).__name__ for r in canvas]}"
        )
        # 内容正确
        plains = [r.plain for r in canvas]
        assert any("hi" in p for p in plains), f"输入文本缺失: {plains}"

    def test_canvas_row_falls_back_to_merge_when_overlapping(self):
        """box.x!=0 或行已命中 → 回退 dict 合并（既有行为）。"""
        from src.tui.ink.fiber import Fiber
        from src.tui.ink.layout import LayoutBox
        from src.tui.ink.output import Line
        from src.tui.app import input_area

        fiber = Fiber("host", "input-area", {
            "text": "hi", "cursor_pos": 2, "prompt": "> ",
            "completion": None, "status_active": False,
            "cpu": 1, "mem": 2, "history_search": None,
            "width": 20,
        })
        fiber.layout_box = LayoutBox(x=2, y=0, w=20, h=3)
        canvas = [None] * 3
        input_area._paint(fiber, canvas)
        # box.x!=0 → 行应合并为 dict
        assert all(isinstance(r, dict) for r in canvas), (
            f"box.x!=0 时应合并为 dict，实际: {[type(r).__name__ for r in canvas]}"
        )
        # 重叠行回退合并（目标行已为 Line → dict 归一化）
        fiber.layout_box = LayoutBox(x=0, y=0, w=20, h=3)
        canvas2 = [Line.of("existing")] + [None] * 2
        input_area._paint(fiber, canvas2)
        assert isinstance(canvas2[0], dict), (
            "目标行已为 Line 时应归一并合并为 dict"
        )


class TestStaticComponent:
    """React Ink Static 组件（冻结子内容，完善 react ink）。"""

    def test_static_freezes_children(self):
        """Static children 首帧冻结——后续帧父级传入不同 children 仍渲染首帧值。"""
        r = Reconciler()
        root = r.create_root()

        def Render(val):
            return h(BOX, None, [
                h(Static, None, [h(TEXT, {"children": "FROZEN"})]),
                h(TEXT, {"children": f"dyn-{val}"}),
            ])

        r.render(root, Render(1), 80, 24)
        f1 = render_frame(root, 80)
        assert f1.lines[0].plain == "FROZEN"
        assert f1.lines[1].plain == "dyn-1"

        # 父级 children 变化 → Static 仍渲染冻结内容（首帧）
        r.render(root, Render(2), 80, 24)
        f2 = render_frame(root, 80)
        assert f2.lines[0].plain == "FROZEN", "Static 内容应冻结"
        assert f2.lines[1].plain == "dyn-2", "非 Static 内容应更新"

    def test_static_string_child(self):
        """Static 字符串子级冻结。"""
        r = Reconciler()
        root = r.create_root()
        r.render(root, h(Static, {}, "hello"), 80, 24)
        f = render_frame(root, 80)
        assert f.lines[0].plain == "hello"


class TestNewlineAndFragment:
    """Newline / Fragment 组件（完善 react ink）。"""

    def test_newline_renders_blank_line(self):
        """Newline 渲染换行（空行）。"""
        r = Reconciler()
        root = r.create_root()
        el = h(BOX, None, [
            h(TEXT, {"children": "a"}),
            h(Newline),
            h(TEXT, {"children": "b"}),
        ])
        r.render(root, el, 80, 24)
        f = render_frame(root, 80)
        assert [l.plain for l in f.lines] == ["a", "", "b"]

    def test_newline_count(self):
        """Newline count=N 渲染 N 个空行。"""
        r = Reconciler()
        root = r.create_root()
        el = h(BOX, None, [
            h(TEXT, {"children": "a"}),
            h(Newline, {"count": 3}),
            h(TEXT, {"children": "b"}),
        ])
        r.render(root, el, 80, 24)
        f = render_frame(root, 80)
        assert [l.plain for l in f.lines] == ["a", "", "", "", "b"]

    def test_fragment_flat_children(self):
        """Fragment 不引入独立布局盒（子节点直接流入父容器）。"""
        r = Reconciler()
        root = r.create_root()
        el = h(BOX, None, [
            h(TEXT, {"children": "pre"}),
            h(Fragment, {}, h(TEXT, {"children": "x"}), h(TEXT, {"children": "y"})),
            h(TEXT, {"children": "post"}),
        ])
        r.render(root, el, 80, 24)
        f = render_frame(root, 80)
        assert [l.plain for l in f.lines] == ["pre", "x", "y", "post"]


class TestToolBorderBreathing:
    """运行中工具卡边框呼吸（BEAUTY-10，完善动效）。"""

    def _make_block(self, status, closed):
        from src.tui.app.model import AppModel, StatusState, _tool_card_styled_lines
        from src.renderer.ansi.helpers import AnsiLine
        from src.tui.core.style import Style as _Style
        model = AppModel()
        model.width = 60
        model.status = StatusState(model_name="m", status_active=False)
        b = model.append_block("tool")
        b.extra["tool_name"] = "bash"
        b.extra["tool_status"] = status
        b.lines.append(AnsiLine.of("  \u00b7 Bash", _Style(fg=23, bold=True)))
        b.closed = closed
        return b, _tool_card_styled_lines

    def test_running_tool_border_breathes(self):
        """运行中工具卡顶边框色在呼吸区间内（23-45），非静态 23。"""
        b, fn = self._make_block("running", False)
        runs = fn(b, 60, 0, None)
        top = runs[0]
        border_fg = top[0].style.fg
        assert isinstance(border_fg, int), f"边框色应为 256 色号: {border_fg!r}"
        assert 23 <= border_fg <= 45, f"运行中边框应呼吸于 [23,45]: {border_fg}"

    def test_closed_tool_border_static(self):
        """已关闭工具卡边框保持静态（frozen，不呼吸）。"""
        b, fn = self._make_block("done", True)
        runs = fn(b, 60, 0, None)
        top = runs[0]
        assert top[0].style.fg == 23, f"关闭工具卡边框应静态 23: {top[0].style.fg}"


class TestIncrementalRendering:
    """增量渲染（需求 #8）：非 resize 变化只重写 live 区，committed 前缀零重写。"""

    def _make_app(self):
        from src.tui.app.model import AppModel, StatusState
        from src.tui.app.app import App
        model = AppModel()
        model.width = 80
        model.status = StatusState(model_name="deepseek-v3", status_active=False, cpu=10, mem=20)
        # 提交 50 行历史
        committed = [Line.of(f"history line {i}", Style(fg=244)) for i in range(50)]
        model.committed_lines = committed
        return model, App

    def test_live_change_only_rewrites_live_rows(self):
        """输入文本变化 → 输出流仅含 live 区重写（committed 历史零重写）。"""
        import io
        from src.tui.ink.renderer import InkRenderer
        from src.tui.ink.reconciler import Reconciler
        from src.tui.ink.components import render_frame
        from src.tui.ink import h

        model, App = self._make_app()
        model.input_text = "a"
        model.input_cursor = 1

        r = Reconciler()
        root = r.create_root()
        renderer = InkRenderer(stream=io.StringIO(), height=40)
        el = h(App, {"model": model, "width": 80})
        r.render(root, el, 80, 40)
        frame = render_frame(root, 80)
        renderer.render(frame)
        # 清空输出，记录后续写入
        renderer._stream.seek(0)
        renderer._stream.truncate()

        # 仅输入文本变化（其余不变）→ 只重写输入行
        model.input_text = "ab"
        model.input_cursor = 2
        el = h(App, {"model": model, "width": 80})
        r.render(root, el, 80, 40)
        frame2 = render_frame(root, 80)
        renderer.render(frame2)
        val = renderer._stream.getvalue()
        # 输出不含历史行文本（committed 前缀零重写）
        assert "history line" not in val, (
            f"增量渲染不应重写 committed 历史，实际输出含历史文本: {val[:120]!r}"
        )
        # 输出包含输入行更新（ab）
        assert "> ab" in val or "ab" in val, f"live 输入行应被重写: {val!r}"

    def test_committed_growth_appends_new_rows(self):
        """历史增长（追加新行）→ 平移快路径仅写新增行，不重写既有历史。"""
        import io
        from src.tui.ink.renderer import InkRenderer
        from src.tui.ink.reconciler import Reconciler
        from src.tui.ink.components import render_frame
        from src.tui.ink import h

        model, App = self._make_app()
        r = Reconciler()
        root = r.create_root()
        renderer = InkRenderer(stream=io.StringIO(), height=40)
        el = h(App, {"model": model, "width": 80})
        r.render(root, el, 80, 40)
        renderer.render(render_frame(root, 80))
        renderer._stream.seek(0)
        renderer._stream.truncate()

        # 追加 2 行历史 → 平移快路径（仅写新增行 + live 区）
        model.committed_lines.append(Line.of("new history A", Style(fg=244)))
        model.committed_lines.append(Line.of("new history B", Style(fg=244)))
        el = h(App, {"model": model, "width": 80})
        r.render(root, el, 80, 40)
        frame3 = render_frame(root, 80)
        renderer.render(frame3)
        val = renderer._stream.getvalue()
        # 新增行被写入
        assert "new history A" in val, f"新增历史行应被写入: {val!r}"
        assert "new history B" in val, f"新增历史行应被写入: {val!r}"
        # 既有历史行（history line 0..49）零重写
        assert "history line 49" not in val, (
            f"平移快路径不应重写既有历史行: {val!r}"
        )

    def test_resize_triggers_full_refresh(self):
        """终端尺寸变化 → 全量刷新（需求 #8：除 resize 外均为增量）。"""
        import io
        from src.tui.ink.renderer import InkRenderer
        from src.tui.ink.reconciler import Reconciler
        from src.tui.ink.components import render_frame
        from src.tui.ink import h

        model, App = self._make_app()
        model.committed_lines = model.committed_lines[:5]  # 短历史
        r = Reconciler()
        root = r.create_root()
        renderer = InkRenderer(stream=io.StringIO(), height=40)
        el = h(App, {"model": model, "width": 80})
        r.render(root, el, 80, 40)
        renderer.render(render_frame(root, 80))
        renderer._stream.seek(0)
        renderer._stream.truncate()

        # 模拟 resize：session 重置 renderer → 全量重写
        renderer.reset()
        el = h(App, {"model": model, "width": 80})
        r.render(root, el, 80, 40)
        renderer.render(render_frame(root, 80))
        val = renderer._stream.getvalue()
        assert "history line 0" in val, f"resize 全量刷新应重写历史: {val!r}"


class TestStdHooks:
    """useStdin / useStdout / useStderr（完善 react ink）。"""

    def test_use_stdin_not_injected_returns_none(self):
        """未注入访问器 → stdin None、setRawMode no-op。"""
        from src.tui.ink import hooks as _hooks
        _hooks.set_std_accessors(None, None, None)
        try:
            out = useStdin()
            assert out["stdin"] is None
            assert out["isRawModeSupported"] is False
            assert out["setRawMode"]() is None
        finally:
            _hooks.set_std_accessors(None, None, None)

    def test_use_stdout_injected(self):
        """注入 stdout 访问器 → write 写流。"""
        from src.tui.ink import hooks as _hooks
        buf = []

        class FakeStream:
            def write(self, data):
                buf.append(data)

        _hooks.set_std_accessors(lambda: object(), lambda: FakeStream(), lambda: None)
        try:
            out = useStdout()
            out["write"]("hello")
            assert buf == ["hello"]
            out2 = useStderr()
            assert out2["stderr"] is None
        finally:
            _hooks.set_std_accessors(None, None, None)
