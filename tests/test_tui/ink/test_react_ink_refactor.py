"""标准 React Ink 组件化回归测试（2026-08-05）。

覆盖「所有 TUI 布局/组件按标准 React Ink 重构，没有例外」的核心成果：
  1. StaticLines — committed 历史静态行批量渲染标准组件（chat_view host 迁移）；
  2. InputArea + CompletionPopup — input-area 自定义 host 迁移为函数组件
     （Column 组件树 + dataInputArea 容器标记）；
  3. _subagent_render — subagent 卡片 ANSI 字符串行迁移为 ink Line 行
     （StylRun 数据，无 ANSI 中间层）。

每项断言：标准组件导出、组件树表达、渲染输出正确。
"""

from __future__ import annotations

from src.tui.app.model import AppModel, CompletionState
from src.tui.app.app import build_app_element
from src.tui.app.apply import apply_cmd
from src.tui._const import UserMsgCmd, ContentCmd, PhaseDoneCmd
from src.tui.ink.element import h, TEXT, BOX
from src.tui.ink.output import Line, StyledRun
from src.tui.ink.reconciler import Reconciler
from src.tui.ink.components import render_frame


class TestStaticLinesStandardComponent:
    """StaticLines — 标准组件导出 + 组件树表达 + 渲染。"""

    def test_exported_from_ink(self):
        from src.tui.ink import StaticLines
        from src.tui.ink.widgets import StaticLines as _w
        assert StaticLines is _w

    def test_renders_lines(self):
        from src.tui.ink import StaticLines
        lines = [Line([StyledRun(f"历史 {i}", None)]) for i in range(3)]
        r = Reconciler()
        root = r.create_root()
        el = h(BOX, None, [h(StaticLines, {"lines": lines})])
        r.render(root, el, 80, 24)
        f = render_frame(root, 80)
        plains = [ln.plain for ln in f.lines]
        assert plains == ["历史 0", "历史 1", "历史 2"]

    def test_committed_host_alias_removed(self):
        """旧 committed-chat host 别名已彻底移除（无例外）。"""
        from src.tui.ink.registry import has_host
        assert not has_host("committed-chat"), "旧 committed-chat 别名应移除"

    def test_chat_view_uses_standard_component(self):
        """ChatView 生产路径用 StaticLines（不再直接 h("committed-chat")）。"""
        import inspect
        import src.tui.app.chat_view as cv
        src = inspect.getsource(cv.ChatView)
        assert "StaticLines" in src, "ChatView 应使用 StaticLines 标准组件"
        assert 'h("committed-chat"' not in src, "ChatView 不应直接构造旧 host"


class TestInputAreaStandardComponent:
    """InputArea — 标准函数组件（dataInputArea 容器 + CompletionPopup）。"""

    def _render_app(self, m: AppModel, width: int = 80):
        r = Reconciler()
        root = r.create_root()
        el = build_app_element(m, width)
        r.render(root, el, width, 24)
        f = render_frame(root, width)
        return r, root, f

    def _find_input(self, root):
        def find(f):
            f2 = f
            while f2 is not None:
                if f2.is_host and f2.props.get("dataInputArea"):
                    return f2
                r = find(f2.child)
                if r is not None:
                    return r
                f2 = f2.sibling
            return None
        return find(root)

    def test_input_area_renders_column_tree(self):
        """InputArea 渲染为 Column 组件树（含补全弹窗 + 输入行 + 分隔线）。"""
        m = AppModel()
        m.input_text = "你好"
        m.input_cursor = 1
        r, root, f = self._render_app(m)
        plains = [ln.plain for ln in f.lines]
        # 输入行 + 上下分隔线
        assert any("> 你好" in p or "> " in p for p in plains), plains[-3:]
        assert any("CPU:" in p for p in plains), "上分隔线 CPU/MEM"
        # dataInputArea 容器
        fiber = self._find_input(root)
        assert fiber is not None, "InputArea 应标记 dataInputArea 容器"
        assert fiber.layout_box is not None and fiber.layout_box.h >= 3

    def test_completion_popup_component(self):
        """补全弹窗经独立 CompletionPopup 组件渲染（Column + TEXT 行）。"""
        m = AppModel()
        m.completion = CompletionState(
            items=["/help", "/model"], texts=["/help", "/model"],
            selected=0, visible=True, title="命令补全",
        )
        r, root, f = self._render_app(m)
        plains = [ln.plain for ln in f.lines]
        assert any("命令补全" in p for p in plains), "弹窗标题"
        assert any("/help" in p for p in plains), "候选项"

    def test_session_cursor_locates_input(self):
        """session._position_cursor 经 dataInputArea 容器定位（不崩溃）。"""
        from src.tui.ink.session import InkSession
        m = AppModel()
        m.input_text = "abc"
        m.input_cursor = 1
        r, root, f = self._render_app(m)

        class _R:
            def place_cursor(self, row, col):
                self.row, self.col = row, col

        renderer = _R()
        s = InkSession.__new__(InkSession)
        s._root_fiber = root
        s._model = m
        s._width_cache = type("W", (), {
            "get_width": lambda self: 80, "get_height": lambda self: 24,
        })()
        s._ink_renderer = renderer
        s._input_fiber = None
        s._position_cursor()
        assert renderer.row > 0, "光标应定位在文档内行"

    def test_input_area_host_alias_removed(self):
        """旧 input-area host 别名已彻底移除（无例外）；InputArea 组件正常。"""
        from src.tui.ink.registry import has_host
        assert not has_host("input-area"), "旧 input-area 别名应移除"
        m = AppModel()
        m.input_text = "x"
        r = Reconciler()
        root = r.create_root()
        el = build_app_element(m, 80)
        r.render(root, el, 80, 24)
        f = render_frame(root, 80)
        assert any("> " in ln.plain for ln in f.lines), "输入行应渲染"


class TestSubAgentStyledRuns:
    """subagent 卡片 — ink Line 行数据（无 ANSI 中间层）。"""

    def test_render_frame_returns_lines(self):
        from src.tui._subagent_state import StateStore
        from src.tui._subagent_render import render_frame
        store = StateStore(max_history=3)
        store.add_agent("agent-1", "分析", status="running", agent_type="map")
        lines = render_frame(store, max_history=3)
        assert lines, "应产出卡片行"
        assert all(isinstance(ln, Line) for ln in lines), "行应为 ink Line"
        plains = [ln.plain for ln in lines]
        assert plains[0].startswith("┌"), "顶边框"
        assert any("分析" in p for p in plains), "agent 描述"

    def test_format_tool_record_returns_line(self):
        from src.tui._subagent_state import _ToolRecord
        from src.tui._subagent_render import format_tool_record
        rec = _ToolRecord(tool_name="search", detail="'q'")
        rec.phase = "running"
        line = format_tool_record(rec, 0.0, "")
        assert isinstance(line, Line)
        assert "Grep" in line.plain

    def test_render_children_no_ansi_parse(self):
        """subagent_panel 直接复用 Line.runs（不再 ansi_to_runs）。"""
        from src.tui.app.subagent_panel import _render_children
        from src.tui.core.style import Style
        m = AppModel()
        line = Line([
            StyledRun("●", Style(fg=214)),
            StyledRun(" 子代理", None),
        ])
        m.subagent_lines = [line]
        children = _render_children(m, 80)
        assert len(children) == 1
        runs = children[0].props["styled"]
        assert runs[0].style is not None and runs[0].style.fg == 214, "样式保留"
        assert "".join(r.text for r in runs) == "● 子代理"


__all__ = [
    "TestStaticLinesStandardComponent",
    "TestInputAreaStandardComponent",
    "TestSubAgentStyledRuns",
]
