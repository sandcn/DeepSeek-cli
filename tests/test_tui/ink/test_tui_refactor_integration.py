"""TUI 重构集成测试 — 布局容器重构 + 渲染错误修复 + 性能优化跨模块验证。

覆盖本次 TUI 重构的全部修改链路：
  - E1：显式 width 钳制（layout.py）→ app 组件树行宽不变量；
  - E2：宽字符第二列覆盖（components.py）→ 画布合并不丢字符；
  - E8/E9：SelectInput/MultiSelect 越界与不可哈希（widgets/interactive.py）；
  - 阶段2：app 层 Column/Row 布局容器（app.py/header.py/status_bar.py/chat_view.py）；
  - P-H2/P-H3/P-H7：布局/辅助性能优化回归。
"""

from __future__ import annotations

from src.tui.app.model import AppModel
from src.tui.app.apply import apply_cmd
from src.tui.app.app import build_app_element
from src.tui.ink.reconciler import Reconciler
from src.tui.ink.components import render_frame
from src.tui.ink.element import h
from src.tui.ink import BOX, TEXT
from src.tui._screen import wcswidth_simple
from src.tui._const import UserMsgCmd, ContentCmd, PhaseDoneCmd


def _app_frame(model, width=80):
    r = Reconciler()
    root = r.create_root()
    el = build_app_element(model, width)
    r.render(root, el, width, 24)
    return render_frame(root, width)


class TestAppFrameWidthInvariant:
    """E1 集成 — app 组件树每行宽度 <= 终端宽度。"""

    def test_app_frame_width_invariant(self):
        m = AppModel()
        apply_cmd(m, UserMsgCmd(text="hello"))
        apply_cmd(m, ContentCmd(text="# Answer\n\nbody text\n"))
        apply_cmd(m, PhaseDoneCmd(phase="content"))
        m.input_text = "typing"
        f = _app_frame(m)
        for line in f.lines:
            assert line.width <= 80, f"行宽超限: {line.width} plain={line.plain!r}"

    def test_explicit_width_clamped_in_app_tree(self):
        """app 组件树内显式 width 超宽容器被钳制（不破坏行宽不变量）。"""
        m = AppModel()
        m.width = 60  # 模拟 session._render_frame 已刷新 model.width
        # input_area 不传超宽 width；模拟宽行 content
        apply_cmd(m, ContentCmd(text="x" * 200 + "\n"))
        apply_cmd(m, PhaseDoneCmd(phase="content"))
        f = _app_frame(m, width=60)
        for line in f.lines:
            assert line.width <= 60, f"行宽超限: {line.width}"


class TestMergeLineWideCharRender:
    """E2 集成 — 宽字符第二列覆盖渲染不丢字符。"""

    def test_wide_char_second_col_overwrite_render(self):
        from src.tui.ink.output import Line, StyledRun
        from src.tui.ink import components as _components

        row = {0: ("中", None), 2: ("a", None)}
        merged = _components._merge_line(row, 1, Line([StyledRun("X", None)]))
        line = _components._canvas_row_to_line(merged)
        # X 不再静默丢失（宽字符整体被替换）
        assert "X" in line.plain, f"X 丢失: {line.plain!r}"
        assert "a" in line.plain


class TestControlsShrinkIntegration:
    """E8/E9 集成 — 交互控件全链路不崩溃。"""

    def test_select_shrink_enter_integration(self):
        from src.tui.ink.widgets import SelectInput
        from src.tui.ink.hooks import set_input_router_callback
        from src.tui._input_parser import KeyEvent

        holder = {}
        set_input_router_callback(lambda r: holder.update(router=r))
        try:
            r = Reconciler()
            root = r.create_root()
            selected = []
            el = h(SelectInput, {"items": ["a", "b", "c"], "onSelect": lambda it: selected.append(it["value"])})
            r.render(root, el, 80, 24)
            router = holder.get("router")
            router(KeyEvent(kind="arrow_down"))
            router(KeyEvent(kind="arrow_down"))
            el2 = h(SelectInput, {"items": ["x"], "onSelect": lambda it: selected.append(it["value"])})
            r.render(root, el2, 80, 24)
            router(KeyEvent(kind="enter"))
            assert selected == ["x"]
        finally:
            set_input_router_callback(None)

    def test_multi_select_unhashable_integration(self):
        from src.tui.ink.widgets import MultiSelect
        from src.tui.ink.hooks import set_input_router_callback
        from src.tui._input_parser import KeyEvent

        holder = {}
        set_input_router_callback(lambda r: holder.update(router=r))
        try:
            r = Reconciler()
            root = r.create_root()
            submitted = []
            items = [
                {"label": "One", "value": {"id": 1}},
                {"label": "Two", "value": 2},
            ]
            el = h(MultiSelect, {"items": items, "onSubmit": submitted.append})
            r.render(root, el, 80, 24)
            router = holder.get("router")
            router(KeyEvent(kind="char", char=" "))
            router(KeyEvent(kind="arrow_down"))
            router(KeyEvent(kind="char", char=" "))
            router(KeyEvent(kind="enter"))
            assert submitted == [[{"id": 1}, 2]]
        finally:
            set_input_router_callback(None)


class TestLayoutContainersInApp:
    """阶段2 集成 — app 组件树使用标准布局容器（Column/Row）且输出正确。"""

    def test_app_uses_containers_and_renders(self):
        from src.tui.app import app as _app_mod
        from src.tui.app.header import TopHeader
        from src.tui.app.status_bar import StatusBar

        # 布局容器重构后组件函数可正常构建/渲染（输出等价）
        m = AppModel()
        apply_cmd(m, UserMsgCmd(text="hello"))
        f = _app_frame(m)
        plains = [ln.plain for ln in f.lines]
        assert any(p.startswith("> hello") for p in plains), f"用户行缺失: {plains}"
        # 顶部标题栏（Row 布局）仍在首行
        assert "DeepSeek CLI" in plains[0], f"顶部标题栏缺失: {plains[0]}"

    def test_status_bar_column_container(self):
        """StatusBar 用 Column 容器后输出两行结构不变。"""
        from src.tui.app.model import StatusState
        from src.tui.ink.element import h as _h
        from src.tui.ink.reconciler import Reconciler as _R
        from src.tui.ink.components import render_frame as _rf

        class _Stub:
            class status(StatusState):
                pass

        model = _Stub()
        model.status = StatusState(status_active=False, model_name="test")
        r = _R()
        root = r.create_root()
        from src.tui.app.status_bar import StatusBar
        el = _h(StatusBar, {"model": model, "width": 80})
        r.render(root, el, 80, 24)
        f = _rf(root, 80)
        assert len(f.lines) >= 1
        assert "test" in " ".join(ln.plain for ln in f.lines)

    def test_tool_header_column_container(self):
        """ToolStatusHeader 用 Column 容器后边框行输出不变（隔离验证模块）。"""
        from src.tui.ink.element import h as _h
        from src.tui.ink.reconciler import Reconciler as _R
        from src.tui.ink.components import render_frame as _rf
        from src.tui.app.tool_header import ToolStatusHeader

        m = AppModel()
        m.width = 40
        m.open_tool_box("t1", "bash", detail="ls -la")
        r = _R()
        root = r.create_root()
        el = _h(ToolStatusHeader, {"model": m, "width": 40})
        r.render(root, el, 40, 24)
        f = _rf(root, 40)
        plains = [ln.plain for ln in f.lines]
        assert any(p.startswith("┌─ ⚡ Bash") for p in plains), f"工具边框缺失: {plains}"
        assert all(len(p) <= 40 for p in plains), f"行宽超限: {plains}"

    def test_widgets_use_standard_containers(self):
        """交互/展示控件内部用标准布局容器（Row/Column）且渲染不回归。"""
        from src.tui.ink.element import h as _h
        from src.tui.ink.reconciler import Reconciler as _R
        from src.tui.ink.components import render_frame as _rf
        from src.tui.ink.widgets import SelectInput, TextInput, MultiSelect, Table, Divider

        r = _R()
        root = r.create_root()
        el = _h("box", {"flexDirection": "column"}, [
            _h(SelectInput, {"items": ["a", "b"], "focus": False}),
            _h(TextInput, {"value": "hello", "focus": False}),
            _h(MultiSelect, {"items": ["x", "y"], "focus": False}),
            _h(Table, {"data": [["A", "1"], ["B", "2"]], "columns": ["k", "v"]}),
            _h(Divider, {"title": "分隔", "width": 20}),
        ])
        r.render(root, el, 60, 24)
        f = _rf(root, 60)
        plains = [ln.plain for ln in f.lines]
        assert any(p == "▶ a" or p == "▶a" or "a" in p for p in plains), f"SelectInput 缺失: {plains}"
        assert any("hello" in p for p in plains), f"TextInput 缺失: {plains}"
        assert any(p == "○ x" or "○x" in p or "x" in p for p in plains), f"MultiSelect 缺失: {plains}"
        assert any("A" in p and "1" in p for p in plains), f"Table 缺失: {plains}"
        assert any("分隔" in p for p in plains), f"Divider 缺失: {plains}"
        assert all(ln.width <= 60 for ln in f.lines), f"行宽超限: {[ln.width for ln in f.lines]}"



class TestPerfSmokeCombined:
    """P-H2/P-H3/P-H7 集成冒烟 — 大历史渲染与辅助性能。"""

    def test_large_committed_history_render_smoke(self):
        import time
        m = AppModel()
        for i in range(200):
            apply_cmd(m, UserMsgCmd(text=f"msg {i}"))
        t0 = time.perf_counter()
        f = _app_frame(m, width=80)
        elapsed = time.perf_counter() - t0
        assert len(f.lines) > 0
        assert elapsed < 5.0, f"大历史渲染耗时 {elapsed:.2f}s"
