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
from src.tui.ink.element import h
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
        """时间戳行精确格式匹配（加固：非宽泛 `or` 匹配）。"""
        import re
        m = AppModel()
        f = _frame(m)
        plains = [l.plain for l in f.lines]
        assert any(re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", p) for p in plains)


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


class TestCompletionPopupHeightConsistency:
    """方向C 步骤4 — popup_height 单实现一致性（input_area 与 session 共享）。"""

    def test_completion_height_matches_rendered_popup_lines(self):
        """_completion_height 与渲染弹窗行数一致（标题 + 候选项 + 提示行）。"""
        from src.tui.app.input_area import _completion_height
        m = AppModel()
        comp = CompletionState()
        comp.visible = True
        comp.items = ["alpha", "beta", "gamma"]
        comp.texts = ["alpha", "beta", "gamma"]
        comp.selected = 0
        comp.title = "高度一致"
        m.completion = comp
        assert _completion_height(comp) == 5  # 3 项 + 标题 + 提示行
        f = _frame(m)
        plains = [l.plain for l in f.lines]
        popup_lines = [
            p for p in plains
            if any(name in p for name in ("alpha", "beta", "gamma"))
            or "高度一致" in p
            or "\u2191\u2193" in p
        ]
        assert len(popup_lines) == 5

    def test_completion_height_zero_when_hidden(self):
        """completion 隐藏/空/None 时高度为 0（session 光标定位不偏移）。"""
        from src.tui.app.input_area import _completion_height
        m = AppModel()
        assert _completion_height(m.completion) == 0  # 默认不可见
        comp = CompletionState()
        comp.visible = True
        comp.items = []
        assert _completion_height(comp) == 0  # 无候选项
        assert _completion_height(None) == 0


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

    def test_subagent_newline_renders_single_line(self):
        """含 \n 的 subagent 行渲染为单行（转义为字面量，不拆成两行）。"""
        m = AppModel()
        m.subagent_lines = ["  ├─ [EXE] task line one\nline two"]
        f = _frame(m)
        sub_lines = [l for l in f.lines if "task line one" in l.plain]
        assert len(sub_lines) == 1, f"含换行行应保持单行，实际 {len(sub_lines)} 行"
        # \n 转义为字面量（反斜杠 n），不产生终端换行
        assert "task line one\\nline two" in sub_lines[0].plain

    def test_single_conversion_point_matches_ansi_to_runs(self):
        """方向C 步骤8 — 子代理行经单一转换点渲染与 ansi_to_runs 直接解析一致。

        契约：ANSI 字符串为「控制器→模型→组件」互换契约，组件侧转换点收敛到
        ``subagent_panel._render_children`` 的 ``ansi_to_runs`` 一处。
        本用例断言渲染结果与 ansi_to_runs 直接解析（同参数）等值。
        """
        from src.tui.app.subagent_panel import _render_children
        from src.renderer.ansi.helpers import ansi_to_runs
        from src.tui.ink import StyledRun, truncate_runs

        line = "\033[38;5;214m●\033[0m 3 agents [████] 2/3 done"
        width = 80
        m = AppModel()
        m.subagent_lines = [line]

        children = _render_children(m, width)
        assert len(children) == 1, "单一转换点应产出 1 个子节点"
        child = children[0]
        # 与 ansi_to_runs 直接解析（同截断参数）等值
        expected_runs = truncate_runs(
            [StyledRun(r.text, r.style) for r in ansi_to_runs(line) if r.text],
            width,
        )
        assert child.props["styled"] == expected_runs
        # 文本内容拼接一致（防样式对象 __eq__ 缺失的兜底断言）
        assert "".join(r.text for r in child.props["styled"]) == \
            "".join(r.text for r in expected_runs)


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


class TestNoAnimatorDependency:
    """app 组件无 _animator 运行时依赖（步骤 5 重构回归）。"""

    def test_render_no_animator_dependency_regression(self):
        """导入 app 组件模块不应触发 src.tui._animator 加载。"""
        import sys

        # 幂等：防止其他测试进程内已引入 src.tui._animator
        sys.modules.pop("src.tui._animator", None)
        import src.tui.app.app  # noqa: F401
        import src.tui.app.input_area  # noqa: F401
        import src.tui.app.status_bar  # noqa: F401
        assert "src.tui._animator" not in sys.modules

    def test_render_deterministic_regression(self):
        """同一 model 连续两次渲染的 plain 行完全一致（确定性渲染）。"""
        m = AppModel()
        apply_cmd(m, UserMsgCmd(text="hi"))
        apply_cmd(m, ContentCmd(text="answer\n"))
        apply_cmd(m, PhaseDoneCmd(phase="content"))
        m.status.status_active = True
        m.status.model_name = "deepseek"
        f1 = _frame(m)
        f2 = _frame(m)
        assert [l.plain for l in f1.lines] == [l.plain for l in f2.lines]


class TestMergeLine:
    """方向C 步骤6 — _merge_line 画布合并快路径与旧逐字符路径一致。"""

    def test_merge_line_batch_matches_old_path(self):
        from src.tui.ink.components import _merge_line
        from src.tui.ink.output import Line, StyledRun
        from src.tui.core.style import Style

        def old_merge(row, x, line):
            col = x
            for run in line.runs:
                for ch in run.text:
                    row[col] = (ch, run.style)
                    col += 1

        style_a = Style(fg=1)
        style_b = Style(fg=2)
        line = Line([StyledRun("ab", style_a), StyledRun("cd", style_b)])

        # 无重叠：批量合并路径
        row_new = {}
        row_old = {}
        old_merge(row_old, 2, line)
        _merge_line(row_new, 2, line)
        assert row_new == row_old

        # 重叠：回退逐字符覆盖（语义一致）
        row_new = {2: ("X", style_a), 3: ("Y", style_b)}
        row_old = dict(row_new)
        old_merge(row_old, 2, line)
        _merge_line(row_new, 2, line)
        assert row_new == row_old

    def test_merge_line_empty_runs_noop(self):
        """空 runs 行合并为 no-op。"""
        from src.tui.ink.components import _merge_line
        from src.tui.ink.output import Line
        row = {0: ("x", None)}
        _merge_line(row, 5, Line([]))
        assert row == {0: ("x", None)}


class TestCustomHostPaintErrorLogging:
    """方向C 步骤5 — 自定义 host paint 异常非关键降级（记录日志不崩溃）。"""
    def test_custom_host_paint_error_logged(self, caplog):
        """注册抛异常的自定义 host → 渲染不崩溃且日志含 host 标签。"""
        import logging
        from src.tui.ink.registry import register_host, unregister_host

        tag = "paint-error-host"

        def measure(fiber, avail_w):
            return (10, 1)

        def bad_paint(fiber, canvas):
            raise RuntimeError("paint boom")

        register_host(tag, measure, bad_paint)
        try:
            r = Reconciler()
            root = r.create_root()
            el = h(tag, {"width": 10})
            with caplog.at_level(logging.DEBUG, logger="src.tui.ink.components"):
                r.render(root, el, 80, 24)  # layout 阶段调用 measure（不抛）
                render_frame(root, 80)      # paint 阶段调用 bad_paint（抛 → 捕获）
            assert any(
                rec.name == "src.tui.ink.components"
                and "paint-error-host" in rec.getMessage()
                for rec in caplog.records
            )
        finally:
            unregister_host(tag)

    def test_custom_host_paint_normal_untouched(self):
        """正常自定义 host 不受异常处理影响（仍绘制到画布）。"""
        from src.tui.ink.registry import register_host, unregister_host
        from src.tui.ink.output import Line

        tag = "paint-ok-host"

        def measure(fiber, avail_w):
            return (6, 1)

        def good_paint(fiber, canvas):
            canvas[0] = {0: ("o", None), 1: ("k", None)}

        register_host(tag, measure, good_paint)
        try:
            r = Reconciler()
            root = r.create_root()
            el = h(tag, {"width": 6})
            r.render(root, el, 80, 24)
            frame = render_frame(root, 80)
            assert frame.lines[0].plain == "ok"
        finally:
            unregister_host(tag)


class TestToolCard:
    """方向D 步骤15 — 工具调用卡片渲染（状态图标 / running 输出可见）。"""

    def test_tool_running_output_visible(self):
        """running → 输出可见（● 图标 + 标题）。"""
        m = AppModel()
        m.open_tool_box("t1", "read_file")
        m.append_tool_output("t1", "partial1")
        m.append_tool_output("t1", "partial2")
        f = _frame(m)
        plains = [l.plain for l in f.lines]
        assert any("partial1" in p for p in plains)
        assert any("partial2" in p for p in plains)
        assert any("\u25cf" in p and "rf" in p for p in plains)

    def test_tool_done_shows_output_and_status(self):
        """done → 输出完整可见 + 状态行。"""
        m = AppModel()
        m.open_tool_box("t1", "read_file")
        m.append_tool_output("t1", "brief")
        m.close_tool_box("t1", True)
        f = _frame(m)
        plains = [l.plain for l in f.lines]
        assert any("brief" in p for p in plains)
        assert any("\u2714" in p for p in plains)

    def test_tool_fail_icon(self):
        """fail → 标题前置 ✖ 图标（工具名经缩写为 bs）。"""
        m = AppModel()
        m.open_tool_box("t1", "bash")
        m.append_tool_output("t1", "boom")
        m.close_tool_box("t1", False)
        f = _frame(m)
        plains = [l.plain for l in f.lines]
        assert any("\u2716" in p and "bs" in p for p in plains)
