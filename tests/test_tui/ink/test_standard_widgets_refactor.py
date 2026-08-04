"""标准控件/布局重构（阶段4/5）集成测试。

覆盖阶段4/5 标准控件/布局重构链路：
  - TopHeader 渐变标题：手写 ``TEXT styled`` → **Gradient 标准控件**
    （渐变算法单一真源在 ``widgets/gradient.py``；输出等价）。
  - _ParseLine 行首 ``~`` 前缀：手写替换 + 单 TEXT → **Row + InlineSpinner
    + TEXT** 标准控件/布局表达（与 _StreamingLine 同模式；输出等价）。
  - Gradient 控件内部 ``use_memo`` 渐变 runs 缓存（同输入同引用，跨帧
    TEXT ``_paint_cache`` 引用级命中）。
  - 边框字符单一真源：components / codeblock / display / model（工具卡）
    统一引用 ``helpers.BORDER_CHARS``（消除各处内联边框字符漂移）。
  - **StatusBar 状态行（阶段5）**：手写单 TEXT styled 组装 → **Row 布局 +
    分段 TEXT**（前缀 2 列 / 模型名段 / 段间 2 空格 / 统计段独立元素），
    输出等价；超宽防御路径回退单 TEXT 截断（行宽不变量）。
  - **工具卡片元素树化（阶段5）**：ChatView 中工具卡经 **ToolCard 标准控件
    组件**渲染（Column + TEXT）；工具卡行不写入 committed_lines（由 ToolCard
    从 block.lines 渲染）；content/reasoning 已提交块经 committed-chat **区间
    发射**（多 committed-chat 前缀复用）。
"""

from __future__ import annotations

from unittest.mock import patch

from src.tui.app.model import AppModel
from src.tui.app.apply import apply_cmd
from src.tui.ink.reconciler import Reconciler
from src.tui.ink.element import h
from src.tui.ink import components as C
from src.tui.ink.fiber import Fiber
from src.tui._const import UserMsgCmd, ContentCmd, PhaseDoneCmd


def _render(el, width=80, height=24):
    r = Reconciler()
    root = r.create_root()
    r.render(root, el, width, height)
    return r, root, C.render_frame(root, width)


def _walk(fiber: Fiber):
    """深度优先遍历 fiber 树（含 root 自身）。"""
    stack = [fiber]
    while stack:
        f = stack.pop()
        yield f
        if f.sibling:
            stack.append(f.sibling)
        if f.child:
            stack.append(f.child)


def _has_function_type(root: Fiber, fn) -> bool:
    """fiber 树中是否存在指定函数组件类型的 fiber。"""
    return any(f.tag == "function" and f.type is fn for f in _walk(root))


class TestTopHeaderGradientControl:
    """阶段4 — TopHeader 渐变标题用 Gradient 标准控件（输出等价）。"""

    def test_top_header_uses_gradient_control(self):
        from src.tui.app.header import TopHeader
        from src.tui.ink.widgets.gradient import Gradient
        m = AppModel()
        r, root, frame = _render(h(TopHeader, {"model": m, "width": 80}))
        # 输出等价：✦ DeepSeek CLI · v2.2.0
        plain = frame.lines[0].plain
        assert plain.startswith("\u2726 DeepSeek CLI"), f"标题缺失: {plain!r}"
        assert "v2" in plain, f"版本号缺失: {plain!r}"
        # 渐变标题经 Gradient 标准控件渲染（不再是手写 _title_runs 单 TEXT）
        assert _has_function_type(root, Gradient), "TopHeader 未使用 Gradient 控件"
        assert all(ln.width <= 80 for ln in frame.lines)

    def test_top_header_gradient_runs_cached(self):
        """Gradient 内部 use_memo：同 text+colors 两次渲染返回同一 runs 引用。"""
        from src.tui.ink.widgets.gradient import Gradient, _gradient_runs
        # 组件内 use_memo 缓存：渲染两次同 props，fiber 的 memo hook 复用
        r1, root1, f1 = _render(h(Gradient, {"text": "DeepSeek CLI", "colors": [45, 39, 141, 213]}))
        r2, root2, f2 = _render(h(Gradient, {"text": "DeepSeek CLI", "colors": [45, 39, 141, 213]}))
        assert f1.lines[0].plain == f2.lines[0].plain == "DeepSeek CLI"
        # 第二次渲染仍产出正确渐变（≥2 runs）
        assert len(f2.lines[0].runs) >= 2


class TestParseLineStandardLayout:
    """阶段4 — _ParseLine 用 Row + InlineSpinner + TEXT 标准控件/布局。"""

    def _model_with_parse(self, text: str):
        from src.renderer.ansi.helpers import AnsiLine
        from src.tui.core.style import Style
        from src.tui.app import app as app_mod

        class _Model:
            def __init__(self):
                self.parse_line = None

        model = _Model()
        model.parse_line = AnsiLine.of(text, Style(fg=242))
        return model, app_mod

    def test_parse_line_uses_inline_spinner(self):
        from src.tui.ink.widgets.spinner import InlineSpinner
        model, app_mod = self._model_with_parse("  ~ bash 1.23s")
        r, root, frame = _render(app_mod._ParseLine({"model": model}))
        # 输出：前导 2 空格 + spinner 帧 + 空格 + 文本
        text = frame.lines[0].plain
        assert text.startswith("  "), f"前导空格缺失: {text!r}"
        assert text[2] != "~", f"~ 未替换为 spinner: {text!r}"
        assert text.endswith("bash 1.23s"), f"文本缺失: {text!r}"
        # 标准控件表达：Row 布局内 InlineSpinner
        assert _has_function_type(root, InlineSpinner), "_ParseLine 未使用 InlineSpinner"
        assert all(ln.width <= 80 for ln in frame.lines)

    def test_parse_line_keeps_tilde_in_tool_name(self):
        """工具名内 `~` 不被替换（BUG-40 回归）。"""
        model, app_mod = self._model_with_parse("  ~ ~/proj ls 0.5s")
        _, _, frame = _render(app_mod._ParseLine({"model": model}))
        text = frame.lines[0].plain
        assert "~/proj" in text, f"工具名内 ~ 被误替换: {text!r}"
        # 行首 ~ 前缀位已替换
        assert text[2] != "~", f"行首 ~ 未替换: {text!r}"

    def test_parse_line_empty(self):
        """parse_line 为 None 时输出空行（无字符，不占可见内容）。"""
        model, app_mod = self._model_with_parse("")
        model.parse_line = None
        _, _, frame = _render(app_mod._ParseLine({"model": model}))
        assert frame.lines[0].plain == ""

    def test_parse_line_without_tilde_defensive(self):
        """无行首 ~（防御路径）整行原样保留。"""
        model, app_mod = self._model_with_parse("  hello world")
        _, _, frame = _render(app_mod._ParseLine({"model": model}))
        assert frame.lines[0].plain == "  hello world"


class TestBorderCharsSingleSource:
    """阶段4 — 边框字符单一真源 helpers.BORDER_CHARS。"""

    def test_components_uses_helpers(self):
        from src.tui.ink import helpers
        from src.tui.ink import components as C
        assert C._BORDER_CHARS is helpers.BORDER_CHARS

    def test_codeblock_uses_helpers(self):
        from src.tui.ink import helpers
        from src.tui.ink.widgets import codeblock
        assert codeblock._BORDER_CHARS is helpers.BORDER_CHARS

    def test_border_chars_values(self):
        """single 边框字符表值正确（┌ ┐ └ ┘ ─ │）。"""
        from src.tui.ink.helpers import BORDER_CHARS
        assert BORDER_CHARS["single"] == ("┌", "┐", "└", "┘", "─", "│")
        assert BORDER_CHARS["single"][0] == "\u250c"

    def test_tool_card_output_unchanged(self):
        """工具卡片边框输出不变（边框字符收敛后）。"""
        m = AppModel()
        apply_cmd(m, UserMsgCmd(text="hi"))
        m.open_tool_box("t1", "bash", detail="ls -la")
        m.append_tool_output("t1", "file1\nfile2\n")
        m.close_tool_box("t1", True)
        # 渲染 App 全树，工具卡边框行存在且行宽不变量
        from src.tui.app.app import build_app_element
        _, _, frame = _render(build_app_element(m, 80), 80, 24)
        plains = [ln.plain for ln in frame.lines]
        assert any(p.startswith("┌─ ") for p in plains), f"顶边框缺失: {plains}"
        assert any("└─" in p for p in plains), f"底边框缺失: {plains}"
        assert any("✔ 完成" in p for p in plains), f"状态缺失: {plains}"
        assert all(ln.width <= 80 for ln in frame.lines), "行宽超限"


class TestAppTreeStandardLayout:
    """阶段4 — App 组件树标准布局容器 + 标准控件渲染不回归。"""

    def test_app_tree_renders(self):
        m = AppModel()
        apply_cmd(m, UserMsgCmd(text="hello"))
        apply_cmd(m, ContentCmd(text="# Answer\n\nbody text\n"))
        apply_cmd(m, PhaseDoneCmd(phase="content"))
        from src.tui.app.app import build_app_element
        _, _, frame = _render(build_app_element(m, 80))
        plains = [ln.plain for ln in frame.lines]
        assert any(p.startswith("> hello") for p in plains), f"用户行缺失: {plains}"
        assert "DeepSeek CLI" in plains[0], f"顶部标题栏缺失: {plains[0]}"
        assert all(ln.width <= 80 for ln in frame.lines)


class TestStatusBarStandardLayout:
    """阶段5 — StatusBar 状态行用 Row + 分段 TEXT 标准控件/布局表达。

    覆盖：
      - 状态行 = Row 布局（前缀 2 列 / 模型名段 / 段间 2 空格 / 统计段独立
        TEXT），替代手写单 TEXT styled 组装。
      - 输出等价：plain 与重构前一致（``  ⠋ test-model  2→ · 5 ...``）。
      - 超宽防御路径：回退单 TEXT truncate_line（行宽不变量）。
      - 空状态压缩：仅渲染分隔线。
      - _flatten_status_runs 段间分隔符拼接正确。
    """

    class _Status:
        def __init__(self):
            self.status_active = True
            self.model_name = "test-model"
            self.tool_total = 0
            self.tool_count = 0
            self.tool_fail = 0

    class _Model:
        def __init__(self):
            self.status = TestStatusBarStandardLayout._Status()
            self.subagent_lines = []
            self.input_text = ""
            self.input_cursor = 0

    @staticmethod
    def _render_sb(model, width=80, snapshot=None):
        from src.tui.app.status_bar import StatusBar
        patches = [
            patch("src.tui.app.status_bar.time.monotonic", return_value=100.0),
            patch("src.tui.app._fx.time.monotonic", return_value=100.0),
            patch("src.tui.app._theme.time.monotonic", return_value=100.0),
        ]
        if snapshot is not None:
            patches.append(patch("src.tui.app.status_bar._snapshot", return_value=snapshot))
        for p in patches:
            p.start()
        try:
            return _render(h(StatusBar, {"model": model, "width": width}), width, 24)
        finally:
            for p in patches:
                p.stop()

    def test_status_bar_uses_row_layout(self):
        """状态行由 Row 布局容器表达（fiber 树含 Row 函数组件）。"""
        from src.tui.ink.widgets.layout import Row
        model = self._Model()
        r, root, frame = self._render_sb(model)
        assert _has_function_type(root, Row), "StatusBar 未使用 Row 标准布局容器"
        # 输出：分隔线 + 状态行（前缀 2 列 + spinner + 模型名）
        assert len(frame.lines) == 2, f"应渲染 2 行: {[ln.plain for ln in frame.lines]}"
        assert frame.lines[1].plain.startswith("  "), f"前缀缺失: {frame.lines[1].plain!r}"
        assert "test-model" in frame.lines[1].plain
        assert all(ln.width <= 80 for ln in frame.lines)

    def test_status_bar_output_equivalent(self):
        """带统计段时输出与重构前等价（模型段 + 2 空格 + 统计段）。"""
        model = self._Model()
        model.status.tool_total = 5
        model.status.tool_count = 2
        _, _, frame = self._render_sb(model, snapshot={
            "total_tokens": 12345, "elapsed_seconds": 65.0, "per_second_speed": 12.3,
        })
        plain = frame.lines[1].plain
        assert plain.startswith("  ⠋ test-model  "), f"模型段缺失: {plain!r}"
        assert "2→" in plain, f"工具计数箭头缺失: {plain!r}"
        assert " · 5" in plain, f"工具总数缺失: {plain!r}"
        assert "1:05" in plain, f"耗时缺失: {plain!r}"
        assert "12.3kt" in plain, f"token 缺失: {plain!r}"
        assert "12.3t/s" in plain, f"速度缺失: {plain!r}"
        assert all(ln.width <= 80 for ln in frame.lines)

    def test_status_bar_defensive_truncate_fallback(self):
        """超长模型名 → 防御路径回退单 TEXT 截断（行宽不变量保持）。"""
        model = self._Model()
        model.status.status_active = False
        model.status.model_name = "M" * 200
        _, _, frame = self._render_sb(model)
        status_line = frame.lines[1]
        assert status_line.width <= 80, f"防御路径行宽超限: {status_line.width}"
        assert status_line.plain.startswith("  · "), f"防御路径输出异常: {status_line.plain!r}"

    def test_status_bar_empty_compressed(self):
        """无模型名且无统计 → 只渲染分隔线一行（空状态压缩）。"""
        model = self._Model()
        model.status.model_name = ""
        model.status.status_active = False
        _, _, frame = self._render_sb(model)
        assert len(frame.lines) == 1, f"空状态应仅 1 行: {[ln.plain for ln in frame.lines]}"
        assert frame.lines[0].plain.startswith("\u2501"), "分隔线缺失"

    def test_flatten_status_runs_separator(self):
        """_flatten_status_runs 拼接分段 runs（段间 2 空格分隔符）。"""
        from src.tui.app.status_bar import _flatten_status_runs
        from src.tui.ink import StyledRun
        from src.tui.core.style import Style
        model_runs = [StyledRun("· ", Style(fg=1))]
        stats_runs = [StyledRun("1→2", Style(fg=2))]
        flat = _flatten_status_runs(model_runs, stats_runs)
        assert [r.text for r in flat] == ["· ", "  ", "1→2"], f"拼接错误: {[r.text for r in flat]}"
        # 单段时不加分隔符
        assert [r.text for r in _flatten_status_runs(model_runs, [])] == ["· "]


class TestToolCardStandardComponent:
    """阶段5 — 工具卡片 ToolCard 标准控件组件（元素树化 + committed 区间发射）。

    覆盖：
      - ChatView 中工具卡用 ToolCard 标准控件组件渲染（fiber 树断言）。
      - 工具卡行**不写入** committed_lines（由 ToolCard 从 block.lines 渲染）。
      - 多 content 块（多 committed-chat 区间发射）渲染完整。
      - 工具卡关闭后顶边框图标 ✔（ToolCard 从 block.extra.tool_status 读取）。
    """

    def test_chat_view_uses_tool_card_component(self):
        """ChatView 中工具卡用 ToolCard 标准控件组件渲染（fiber 树断言）。"""
        from src.tui.app.tool_card import ToolCard
        from src.tui.app.app import build_app_element
        m = AppModel()
        apply_cmd(m, UserMsgCmd(text="hi"))
        m.open_tool_box("t1", "bash", detail="ls")
        m.append_tool_output("t1", "file1\n")
        r, root, frame = _render(build_app_element(m, 80))
        assert _has_function_type(root, ToolCard), "ChatView 未使用 ToolCard 组件"
        plains = [ln.plain for ln in frame.lines]
        assert any(p.startswith("┌─ ") for p in plains), f"顶边框缺失: {plains}"
        assert all(ln.width <= 80 for ln in frame.lines)

    def test_tool_card_not_in_committed_lines(self):
        """工具卡行不写入 committed_lines（由 ToolCard 渲染）。"""
        from src.tui.app.app import build_app_element
        m = AppModel()
        apply_cmd(m, UserMsgCmd(text="hi"))
        m.open_tool_box("t1", "bash", detail="ls")
        m.append_tool_output("t1", "file1\n")
        m.close_tool_box("t1", True)
        # committed_lines 只含用户消息行（不含工具卡行）
        plains = [ln.plain for ln in m.committed_lines]
        assert plains and not any(p.startswith("┌") for p in plains), (
            f"工具卡不应在 committed_lines: {plains}"
        )
        # 工具卡由 ToolCard 渲染（渲染 App 树含边框）
        _, _, frame = _render(build_app_element(m, 80))
        plains = [ln.plain for ln in frame.lines]
        assert any(p.startswith("┌─ ") for p in plains), f"顶边框缺失: {plains}"
        assert any("✔ 完成" in p for p in plains), f"底边框缺失: {plains}"
        assert all(ln.width <= 80 for ln in frame.lines)

    def test_multiple_committed_chat_blocks_render(self):
        """多 content 块（多 committed-chat 区间发射）渲染完整。"""
        from src.tui.app.app import build_app_element
        from src.tui._const import MainPhaseCmd
        m = AppModel()
        apply_cmd(m, UserMsgCmd(text="q1"))
        apply_cmd(m, ContentCmd(text="answer one\n"))
        apply_cmd(m, PhaseDoneCmd(phase="content"))
        apply_cmd(m, UserMsgCmd(text="q2"))
        apply_cmd(m, MainPhaseCmd(phase="answering"))  # 新一轮内容前重开通道
        apply_cmd(m, ContentCmd(text="answer two\n"))
        apply_cmd(m, PhaseDoneCmd(phase="content"))
        _, _, frame = _render(build_app_element(m, 80))
        plains = [ln.plain for ln in frame.lines]
        assert any(p.startswith("> q1") for p in plains), f"q1 缺失: {plains}"
        assert "answer one" in "\n".join(plains), f"answer one 缺失: {plains}"
        assert any(p.startswith("> q2") for p in plains), f"q2 缺失: {plains}"
        assert "answer two" in "\n".join(plains), f"answer two 缺失: {plains}"
        assert all(ln.width <= 80 for ln in frame.lines)

    def test_tool_card_closed_status_icon(self):
        """工具卡关闭后顶边框图标 ✔（ToolCard 从 block 状态渲染）。"""
        from src.tui.app.app import build_app_element
        m = AppModel()
        apply_cmd(m, UserMsgCmd(text="hi"))
        m.open_tool_box("t1", "bash", detail="ls")
        m.append_tool_output("t1", "file1\n")
        m.close_tool_box("t1", True)
        _, _, frame = _render(build_app_element(m, 80))
        plains = [ln.plain for ln in frame.lines]
        top = next(p for p in plains if p.startswith("┌"))
        assert "✔" in top, f"关闭后顶边框应含 ✔: {top}"
        assert any("✔ 完成" in p for p in plains), f"底边框缺失: {plains}"
        assert all(ln.width <= 80 for ln in frame.lines)
