"""测试新增标准控件 — RadioList / CodeBlock / InlineSpinner。

覆盖：
  - RadioList：单选指示符 / 键盘导航 / 确认回调 / 空 items 安全 / limit 窗口；
  - CodeBlock：边框完整性 / 语言标签 / 行号 / 行宽不变量（宽字符+超宽截断）/
    空代码 / 内容自适应宽度 / 边框变体；
  - InlineSpinner：帧字符 / 帧序列推进 / 畸形参数防御。
"""

from __future__ import annotations

from src.tui.core.style import Style
from src.tui.ink import h, RadioList, CodeBlock, InlineSpinner
from src.tui.ink.reconciler import Reconciler
from src.tui.ink.components import render_frame
from src.tui.ink.widgets.radio import _CHECKED, _UNCHECKED


def _render(element, width=80, height=24):
    """渲染元素树，返回 Frame。"""
    r = Reconciler()
    root = r.create_root()
    r.render(root, element, width, height)
    return render_frame(root, width)


def _key(kind: str, char: str | None = None):
    ev = type("KeyEvent", (), {"kind": kind, "char": char})()
    return ev


def _render_with_router(element, event, rerender=False) -> tuple[list[str], list]:
    """渲染 + 注入 input router + 分发单个按键（同一 reconciler 支持 rerender）。

    与 test_widgets_extended.py 的 _render_with_router 同模式：router(event)
    后 rerender=True 时重新调和（应用 state queue——测试状态更新后的渲染）。
    """
    captured = []
    from src.tui.ink.hooks import set_input_router_callback

    def _capture(router):
        captured.append(router)

    set_input_router_callback(_capture)
    try:
        r = Reconciler()
        root = r.create_root()
        r.render(root, element, 80, 24)
        router = captured[-1] if captured else None
        if router is not None:
            router(event)
        if rerender and router is not None:
            r.render(root, element, 80, 24)
        frame = render_frame(root, 80)
        return [ln.plain for ln in frame.lines], captured
    finally:
        from src.tui.ink.hooks import set_input_router_callback
        set_input_router_callback(None)


# ═══════════════════════════════════════════════════════════
# RadioList
# ═══════════════════════════════════════════════════════════


class TestRadioList:
    def test_render_indicator(self):
        """选中项显示实心圆点，未选中显示空心圆点。"""
        frame = _render(h(RadioList, {
            "items": ["苹果", "香蕉", "樱桃"],
            "initialIndex": 1,
        }))
        plains = [ln.plain for ln in frame.lines]
        assert len(plains) == 3, plains
        assert plains[0].startswith(_UNCHECKED), plains[0]
        assert plains[1].startswith(_CHECKED), plains[1]
        assert plains[2].startswith(_UNCHECKED), plains[2]

    def test_dict_items(self):
        """dict items（label/value）渲染与回调 value 原样传递。"""
        items = [{"label": "选项A", "value": 10}, {"label": "选项B", "value": 20}]
        calls = []
        el = h(RadioList, {"items": items, "onSelect": lambda item: calls.append(item)})
        lines, _ = _render_with_router(el, _key("enter"), rerender=True)
        assert len(calls) == 1
        assert calls[0]["value"] == 10  # 初始选中 0

    def test_keyboard_navigation(self):
        """arrow_down 移动选中，space/enter 触发 onSelect。"""
        calls = []
        el = h(RadioList, {
            "items": ["a", "b", "c"],
            "onSelect": lambda item: calls.append(item),
        })
        # 同一 reconciler：down → 重渲染 → space（选中索引 1 → value 'b'）
        captured = []
        from src.tui.ink.hooks import set_input_router_callback
        set_input_router_callback(lambda router: captured.append(router))
        try:
            r = Reconciler()
            root = r.create_root()
            r.render(root, el, 80, 24)
            router = captured[-1]
            router(_key("arrow_down"))
            r.render(root, el, 80, 24)
            router = captured[-1]
            router(_key("space"))
            r.render(root, el, 80, 24)
            frame = render_frame(root, 80)
        finally:
            set_input_router_callback(None)
        assert calls and calls[-1]["value"] == "b", calls
        plains = [ln.plain for ln in frame.lines]
        assert plains[1].startswith(_CHECKED), plains

    def test_empty_items_safe(self):
        """空 items 渲染安全（不崩溃）。"""
        frame = _render(h(RadioList, {"items": []}))
        assert frame.height >= 0

    def test_limit_window(self):
        """limit 窗口：只显示视口内项。"""
        items = [f"item{i}" for i in range(10)]
        frame = _render(h(RadioList, {"items": items, "initialIndex": 5, "limit": 3}))
        plains = [ln.plain for ln in frame.lines]
        assert len(plains) == 3, plains
        assert "item5" in plains[0], plains

    def test_items_shrink_clamp(self):
        """items 动态缩小后 selected 越界钳制（不崩溃、高亮不消失）。"""
        # 初始 5 项选中 4 → 缩小到 2 项
        el = h(RadioList, {"items": ["a", "b"], "initialIndex": 4})
        frame = _render(el)
        # 不崩溃即可；渲染期钳制到 1
        plains = [ln.plain for ln in frame.lines]
        assert any(p.startswith(_CHECKED) for p in plains), plains


# ═══════════════════════════════════════════════════════════
# CodeBlock
# ═══════════════════════════════════════════════════════════


class TestCodeBlock:
    def test_borders_with_label(self):
        """语言标签标题栏 + 边框完整性 + 行宽不变量。"""
        frame = _render(h(CodeBlock, {
            "code": "def f():\n    return 1",
            "language": "python",
            "width": 30,
        }))
        plains = [ln.plain for ln in frame.lines]
        assert any(p.startswith("┌─ python") for p in plains), plains
        assert any(p.startswith("│") and p.endswith("│") for p in plains), plains
        assert any(p.startswith("└") for p in plains), plains
        for ln in frame.lines:
            assert ln.width <= 30, f"行宽 {ln.width} > 30"

    def test_line_numbers(self):
        """行号栏显示 + 对齐。"""
        frame = _render(h(CodeBlock, {
            "code": "a\nb\nc",
            "lineNumbers": True,
            "width": 20,
        }))
        plains = [ln.plain for ln in frame.lines]
        assert any(p.startswith(" 1│") for p in plains), plains
        assert any(p.startswith(" 2│") for p in plains), plains
        assert any(p.startswith(" 3│") for p in plains), plains

    def test_truncate_wide(self):
        """超宽行截断（含宽字符）——行宽不变量。"""
        code = "中文内容" * 20
        frame = _render(h(CodeBlock, {"code": code, "width": 30}))
        for ln in frame.lines:
            assert ln.width <= 30, f"行宽 {ln.width} > 30: {ln.plain!r}"

    def test_empty_code(self):
        """空代码保留占位行（不崩溃）。"""
        frame = _render(h(CodeBlock, {"code": "", "width": 20}))
        assert frame.height >= 3, frame.height  # 顶边框+空行+底边框

    def test_auto_width(self):
        """无显式宽度：内容自适应（行宽不变量）。"""
        frame = _render(h(CodeBlock, {"code": "hello\nworld"}))
        for ln in frame.lines:
            assert ln.width > 0, ln.plain
            # 所有行宽一致（边框对齐）
            widths = {ln.width for ln in frame.lines}
            assert len(widths) == 1, f"行宽不一致: {widths}"

    def test_border_variants(self):
        """边框变体渲染安全。"""
        for variant in ("single", "double", "round", "bold", "classic", "unknown"):
            frame = _render(h(CodeBlock, {
                "code": "x", "borderStyle": variant, "width": 20,
            }))
            assert frame.height >= 3

    def test_multiline_nl(self):
        """含 \\n 代码按行拆分（不嵌进单行）。"""
        frame = _render(h(CodeBlock, {"code": "第一行\n第二行", "width": 30}))
        plains = [ln.plain for ln in frame.lines]
        assert any(p.startswith("│ 第一行") for p in plains), plains
        assert any(p.startswith("│ 第二行") for p in plains), plains
        # 无单行同时含两段
        assert not any("第一行" in p and "第二行" in p for p in plains), plains


# ═══════════════════════════════════════════════════════════
# InlineSpinner
# ═══════════════════════════════════════════════════════════


class TestInlineSpinner:
    def test_render_char(self):
        """渲染单个帧字符（单字符宽度）。"""
        frame = _render(h(InlineSpinner, {"tickHz": 10}))
        assert frame.height == 1
        plain = frame.lines[0].plain
        assert len(plain) == 1, plain

    def test_custom_frames(self):
        """自定义帧序列。"""
        frame = _render(h(InlineSpinner, {"frames": "abc", "tickHz": 10}))
        assert frame.lines[0].plain in "abc"

    def test_malformed(self):
        """畸形参数防御（不崩溃）。"""
        frame = _render(h(InlineSpinner, {"tickHz": "abc", "frames": None}))
        assert frame.height == 1
        frame = _render(h(InlineSpinner, {"tickHz": -5, "frames": ""}))
        assert frame.height == 1


# ═══════════════════════════════════════════════════════════
# Gradient
# ═══════════════════════════════════════════════════════════


class TestGradient:
    def test_gradient_runs(self):
        """逐字符渐变：多字符文本产生多色 runs（行宽不变量）。"""
        from src.tui.ink import Gradient
        frame = _render(h(Gradient, {"text": "DeepSeek CLI", "colors": [45, 39, 141, 213]}))
        assert frame.height == 1
        line = frame.lines[0]
        assert line.plain == "DeepSeek CLI"
        # 至少产生多个颜色 run（渐变生效）
        assert len(line.runs) >= 2, line.runs
        for ln in frame.lines:
            assert ln.width <= 80

    def test_single_color(self):
        """单色标回退纯色。"""
        from src.tui.ink import Gradient
        frame = _render(h(Gradient, {"text": "abc", "colors": [45]}))
        assert frame.lines[0].plain == "abc"
        assert frame.lines[0].runs[0].style.fg == 45

    def test_empty(self):
        """空文本/空色标不崩溃。"""
        from src.tui.ink import Gradient
        frame = _render(h(Gradient, {"text": "", "colors": [45, 39]}))
        assert frame.height >= 0
        frame = _render(h(Gradient, {"text": "x", "colors": []}))
        assert frame.lines[0].plain == "x"

    def test_style_merge(self):
        """基础样式与渐变 fg 合并（style 其他属性保留）。"""
        from src.tui.ink import Gradient
        frame = _render(h(Gradient, {
            "text": "ab", "colors": [45, 39], "style": Style(bold=True),
        }))
        run = frame.lines[0].runs[0]
        assert run.style.bold is True
        assert run.style.fg is not None

    def test_header_gradient_single_source(self):
        """TopHeader 渐变委托 Gradient 单一真源（输出等价）。"""
        from src.tui.app.header import _gradient_runs
        from src.tui.ink.widgets.gradient import _gradient_runs as _g
        runs = _gradient_runs("DeepSeek")
        runs2 = _g("DeepSeek", [45, 39, 141, 213])
        assert [r.text for r in runs] == [r.text for r in runs2]
        assert [r.style.fg for r in runs] == [r.style.fg for r in runs2]
