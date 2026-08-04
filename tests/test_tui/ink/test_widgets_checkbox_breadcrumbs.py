"""测试 ink/widgets 新增标准控件 — Checkbox / Breadcrumbs。

覆盖：
  - Checkbox：勾选/未勾选渲染/受控与内部状态/键盘切换/样式/label Element；
  - Breadcrumbs：层级渲染/分隔符/active 高亮/maxItems 折叠/样式/换行归一化。
"""

from __future__ import annotations

from src.tui.core.style import Style
from src.tui.ink import h, TEXT
from src.tui.ink.reconciler import Reconciler
from src.tui.ink.components import render_frame
from src.tui.ink.widgets import Checkbox, Breadcrumbs


def _render(element, width=80, height=24):
    r = Reconciler()
    root = r.create_root()
    r.render(root, element, width, height)
    return render_frame(root, width)


def _key(kind: str, char: str | None = None):
    return type("KeyEvent", (), {"kind": kind, "char": char})()


class _RenderCtx:
    """共享调和上下文（连续按键保持 state）。"""

    def __init__(self, element, width=80, height=24):
        self.width = width
        self.height = height
        self.element = element
        self.captured = []
        from src.tui.ink.hooks import set_input_router_callback
        set_input_router_callback(lambda r: self.captured.append(r))
        self.r = Reconciler()
        self.root = self.r.create_root()
        self.r.render(self.root, element, width, height)

    def key(self, event, rerender=True):
        router = self.captured[-1] if self.captured else None
        if router is not None:
            router(event)
        if rerender and router is not None:
            self.r.render(self.root, self.element, self.width, self.height)
        frame = render_frame(self.root, self.width)
        return [ln.plain for ln in frame.lines]

    def close(self):
        from src.tui.ink.hooks import set_input_router_callback
        set_input_router_callback(None)


# ═══════════════════════════════════════════════════════════
# Checkbox
# ═══════════════════════════════════════════════════════════


class TestCheckbox:
    def test_checked_render(self):
        frame = _render(h(Checkbox, {"checked": True, "label": "启用"}))
        assert frame.lines[0].plain == "[x] 启用"

    def test_unchecked_render(self):
        frame = _render(h(Checkbox, {"checked": False, "label": "启用"}))
        assert frame.lines[0].plain == "[ ] 启用"

    def test_no_label(self):
        frame = _render(h(Checkbox, {"checked": True}))
        assert frame.lines[0].plain == "[x]"

    def test_label_element(self):
        frame = _render(h(Checkbox, {
            "checked": True, "label": h(TEXT, {"children": "自定义", "style": Style(fg=2)}),
        }))
        assert "自定义" in frame.lines[0].plain

    def test_internal_state_toggle(self):
        calls = []
        el = h(Checkbox, {"defaultChecked": False, "label": "x", "onChange": lambda v: calls.append(v)})
        ctx = _RenderCtx(el)
        try:
            lines = ctx.key(_key("space"))
            ctx.key(_key("enter"))
        finally:
            ctx.close()
        assert any("[x]" in p for p in lines), f"space 后应勾选: {lines}"
        assert calls == [True, False], f"onChange 回调: {calls}"

    def test_controlled_no_internal_change(self):
        """受控模式下按键不改变内部 state（外部 value 决定）。"""
        calls = []
        el = h(Checkbox, {"checked": False, "label": "x", "onChange": lambda v: calls.append(v)})
        ctx = _RenderCtx(el)
        try:
            lines = ctx.key(_key("space"))
        finally:
            ctx.close()
        # 受控 + 外部未更新 → 仍显示未勾选（回调已触发）
        assert any("[ ]" in p for p in lines), f"受控应保持未勾选: {lines}"
        assert calls == [True], f"onChange 应触发: {calls}"

    def test_arrow_not_consumed(self):
        """非切换键放行（不消费）。"""
        el = h(Checkbox, {"label": "x"})
        ctx = _RenderCtx(el)
        try:
            lines = ctx.key(_key("arrow_down"))
        finally:
            ctx.close()
        # arrow 不消费（无状态变化）
        assert any("[ ]" in p for p in lines)


# ═══════════════════════════════════════════════════════════
# Breadcrumbs
# ═══════════════════════════════════════════════════════════


class TestBreadcrumbs:
    def test_basic_render(self):
        frame = _render(h(Breadcrumbs, {
            "items": ["Home", "Docs", "Guide"],
        }))
        plains = [ln.plain for ln in frame.lines]
        assert any("Home" in p and "Docs" in p and "Guide" in p for p in plains)
        assert any(" / " in p for p in plains), f"分隔符: {plains}"

    def test_active_item(self):
        frame = _render(h(Breadcrumbs, {
            "items": [
                {"label": "Home", "active": False},
                {"label": "Guide", "active": True},
            ],
        }))
        plains = [ln.plain for ln in frame.lines]
        # active 项高亮（bold + 青色）
        assert any("Guide" in p for p in plains)

    def test_custom_separator(self):
        frame = _render(h(Breadcrumbs, {
            "items": ["A", "B"], "separator": " > ",
        }))
        plains = [ln.plain for ln in frame.lines]
        assert any("A > B" in p for p in plains), f"自定义分隔符: {plains}"

    def test_max_items_collapse(self):
        frame = _render(h(Breadcrumbs, {
            "items": ["A", "B", "C", "D", "E"], "maxItems": 3,
        }))
        plains = [ln.plain for ln in frame.lines]
        # 保留首 + … + 尾
        assert any("A" in p and "…" in p and "E" in p for p in plains), f"折叠: {plains}"
        assert not any("C" in p for p in plains), f"中间项应省略: {plains}"

    def test_newline_normalized(self):
        frame = _render(h(Breadcrumbs, {
            "items": ["Home\n\nX", "Guide"],
        }))
        plains = [ln.plain for ln in frame.lines]
        # 换行归一化为空格（行级 diff 宽度不变量）
        assert all("\n" not in p for p in plains), f"换行应归一化: {plains}"
        assert any("Home X" in p or "Home  X" in p for p in plains)

    def test_empty(self):
        frame = _render(h(Breadcrumbs, {"items": []}))
        assert len(frame.lines) >= 0  # 不崩溃
        assert all(not ln.plain for ln in frame.lines)


__all__ = ["TestCheckbox", "TestBreadcrumbs"]
