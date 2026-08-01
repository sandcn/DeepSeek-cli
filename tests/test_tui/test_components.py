"""测试 src/tui/app 组件 — 组件树渲染输出断言。

构建 AppModel → apply 命令 → build_app_element → render_frame，
断言各组件输出行。
"""

from __future__ import annotations

from src.tui.app.model import AppModel, CompletionState
from src.tui.app.apply import apply_cmd
from src.tui.app.app import build_app_element
from src.tui.ink.reconciler import Reconciler
from src.tui.ink.components import render_frame
from src.tui._const import (
    UserMsgCmd,
    ContentCmd,
    PhaseDoneCmd,
    ToolOutputCmd,
    ToolSummaryCmd,
    ToolCountIncCmd,
)


def _frame(model, width=80):
    r = Reconciler()
    root = r.create_root()
    el = build_app_element(model, width)
    r.render(root, el, width, 24)
    return render_frame(root, width)


class TestChatView:
    def test_renders_user_and_content(self):
        m = AppModel()
        apply_cmd(m, UserMsgCmd(text="question"))
        apply_cmd(m, ContentCmd(text="# Answer\n\nbody text\n"))
        apply_cmd(m, PhaseDoneCmd(phase="content"))
        f = _frame(m)
        plains = [l.plain for l in f.lines]
        assert "  > question" in plains
        assert "Answer" in plains
        assert "body text" in plains

    def test_content_bold_renders(self):
        m = AppModel()
        apply_cmd(m, ContentCmd(text="a **bold** c\n"))
        apply_cmd(m, PhaseDoneCmd(phase="content"))
        f = _frame(m)
        line = next(l for l in f.lines if "bold" in l.plain)
        assert any(r.style is not None and r.style.bold for r in line.runs)


class TestInputArea:
    def test_input_line_renders(self):
        m = AppModel()
        m.input_text = "typing here"
        m.input_cursor = 6
        f = _frame(m)
        plains = [l.plain for l in f.lines]
        assert any("> typing here" in p for p in plains)

    def test_input_multiline(self):
        m = AppModel()
        m.input_text = "line one\nline two"
        m.input_cursor = 5
        f = _frame(m)
        plains = [l.plain for l in f.lines]
        assert any("> line one" in p for p in plains)
        assert any("\u00b7 line two" in p for p in plains)

    def test_placeholder_when_empty(self):
        m = AppModel()
        f = _frame(m)
        plains = [l.plain for l in f.lines]
        assert any("输入消息" in p for p in plains)

    def test_timestamp_separator(self):
        m = AppModel()
        f = _frame(m)
        plains = [l.plain for l in f.lines]
        assert any("2026-" in p or "20" in p and "━━" in p for p in plains)


class TestCompletionPopup:
    def test_completion_renders(self):
        m = AppModel()
        comp = CompletionState()
        comp.visible = True
        comp.items = ["read_file", "write_file"]
        comp.texts = ["read_file", "write_file"]
        comp.selected = 0
        comp.title = "补全"
        m.completion = comp
        f = _frame(m)
        plains = [l.plain for l in f.lines]
        assert any("read_file" in p for p in plains)
        assert any("write_file" in p for p in plains)


class TestStatusBar:
    def test_status_line_present(self):
        m = AppModel()
        m.status.model_name = "deepseek"
        m.status.status_active = True
        m.status.tool_total = 1
        apply_cmd(m, ToolCountIncCmd())
        f = _frame(m)
        plains = [l.plain for l in f.lines]
        assert any("deepseek" in p for p in plains)


class TestSubAgentPanel:
    def test_subagent_lines_render(self):
        m = AppModel()
        m.subagent_lines = ["  ● 3 agents [████] 2/3 done", "  ├─ running tool"]
        f = _frame(m)
        plains = [l.plain for l in f.lines]
        assert any("3 agents" in p for p in plains)
        assert any("running tool" in p for p in plains)

    def test_subagent_long_line_truncated_not_wrapped(self):
        """超宽 subagent 行截断为单行（不换行，避免破坏树形结构）。"""
        m = AppModel()
        m.subagent_lines = ["  ● [EXE] " + "超长描述内容" * 10]
        r = Reconciler()
        root = r.create_root()
        el = build_app_element(m, 40)  # 窄终端 40 列
        r.render(root, el, 40, 24)
        f = render_frame(root, 40)
        sub_lines = [l for l in f.lines if "EXE" in l.plain]
        assert len(sub_lines) == 1, f"超宽行应截断为单行，实际 {len(sub_lines)} 行"
        assert all(l.width <= 40 for l in sub_lines)


class TestFullDocument:
    def test_document_structure(self):
        """文档 = 静态聊天历史 + 尾部 live 区（状态栏 + 输入）。"""
        m = AppModel()
        apply_cmd(m, UserMsgCmd(text="hi"))
        apply_cmd(m, ContentCmd(text="answer\n"))
        apply_cmd(m, PhaseDoneCmd(phase="content"))
        m.status.status_active = True
        m.status.model_name = "m"
        f = _frame(m)
        # 静态内容在上，输入区在下（输入行在文档底部）
        last_plains = [l.plain for l in f.lines]
        chat_idx = next(i for i, p in enumerate(last_plains) if "answer" in p)
        input_idx = next(i for i, p in enumerate(last_plains) if p.startswith("> "))
        assert chat_idx < input_idx

    def test_cursor_fiber_has_layout(self):
        from src.tui.ink.reconciler import Reconciler
        m = AppModel()
        m.input_text = "abc"
        m.input_cursor = 2
        r = Reconciler()
        root = r.create_root()
        el = build_app_element(m, 80)
        r.render(root, el, 80, 24)
        # 找到 input-area fiber
        def find(f):
            from src.tui.ink.fiber import Fiber
            f2 = f
            while f2 is not None:
                if f2.is_host and f2.type == "input-area":
                    return f2
                r = find(f2.child)
                if r is not None:
                    return r
                f2 = f2.sibling
            return None

        fiber = find(root)
        assert fiber is not None
        assert fiber.layout_box is not None
        assert fiber.layout_box.h >= 3  # 上分隔 + 输入 + 下分隔
