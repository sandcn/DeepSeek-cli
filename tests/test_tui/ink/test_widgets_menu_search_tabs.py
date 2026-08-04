"""测试 ink/widgets 新增标准控件 — Menu / SearchInput / Tabs。

覆盖：
  - Menu：分组标题/禁用项/快捷键右对齐/键盘导航/循环移动/选择回调；
  - SearchInput：查询过滤/键盘选择/backspace/escape/limit 窗口/无结果提示；
  - Tabs：标签行渲染/受控与内部状态/键盘切换/内容渲染/指示符。
"""

from __future__ import annotations

from src.tui.core.style import Style
from src.tui.ink import h
from src.tui.ink.reconciler import Reconciler
from src.tui.ink.components import render_frame
from src.tui.ink.widgets import Menu, SearchInput, Tabs


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


def _press(element, *events):
    """连续按键（共享调和上下文），返回最终行文本。"""
    ctx = _RenderCtx(element)
    try:
        lines = None
        for ev in events:
            lines = ctx.key(ev)
        return lines
    finally:
        ctx.close()


# ═══════════════════════════════════════════════════════════
# Menu
# ═══════════════════════════════════════════════════════════


class TestMenu:
    def test_basic_render(self):
        items = [
            {"label": "新建", "shortcut": "N"},
            {"label": "打开", "shortcut": "O"},
            {"label": "退出", "shortcut": "Q"},
        ]
        frame = _render(h(Menu, {"items": items}))
        plains = [ln.plain for ln in frame.lines]
        assert any("新建" in p for p in plains)
        assert any("N" in p for p in plains), f"快捷键标签: {plains}"

    def test_header_and_disabled(self):
        items = [
            {"label": "文件", "type": "header"},
            {"label": "新建", "shortcut": "N"},
            {"label": "打开", "disabled": True, "shortcut": "O"},
            {"label": "退出", "shortcut": "Q"},
        ]
        frame = _render(h(Menu, {"items": items}))
        plains = [ln.plain for ln in frame.lines]
        # 分组标题渲染
        assert any("文件" in p for p in plains)
        # 禁用项渲染（可见）
        assert any("打开" in p for p in plains)

    def test_arrow_navigation_skips_disabled(self):
        items = [
            {"label": "a"},
            {"label": "b", "disabled": True},
            {"label": "c"},
        ]
        selected = []
        el = h(Menu, {
            "items": items,
            "onSelect": lambda item, idx: selected.append((item["label"], idx)),
        })
        ctx = _RenderCtx(el)
        try:
            ctx.key(_key("arrow_down"))  # a → c（跳过禁用 b）
            ctx.key(_key("enter"))
        finally:
            ctx.close()
        assert selected == [("c", 2)], f"应跳过禁用项 b: {selected}"

    def test_enter_select(self):
        items = ["alpha", "beta"]
        selected = []
        el = h(Menu, {"items": items, "onSelect": lambda item, idx: selected.append(idx)})
        ctx = _RenderCtx(el)
        try:
            ctx.key(_key("arrow_down"))
            ctx.key(_key("enter"))
        finally:
            ctx.close()
        assert selected == [1]

    def test_item_onselect_preferred(self):
        calls = []
        items = [
            {"label": "x", "onSelect": lambda item, idx: calls.append("item")},
        ]
        el = h(Menu, {"items": items, "onSelect": lambda item, idx: calls.append("group")})
        ctx = _RenderCtx(el)
        try:
            ctx.key(_key("enter"))
        finally:
            ctx.close()
        assert calls == ["item"], f"item 自带 onSelect 应优先: {calls}"

    def test_highlight_callback(self):
        highlighted = []
        items = ["a", "b", "c"]
        el = h(Menu, {"items": items, "onHighlight": lambda item, idx: highlighted.append(idx)})
        ctx = _RenderCtx(el)
        try:
            ctx.key(_key("arrow_down"))
        finally:
            ctx.close()
        assert highlighted == [1]

    def test_arrow_up_cycles_to_last(self):
        items = ["a", "b", "c"]
        el = h(Menu, {"items": items})
        lines = _press(el, _key("arrow_up"))
        # 高亮循环到最后一个可选项（渲染期高亮 c）
        assert any("c" in p for p in lines)

    def test_empty_items_no_crash(self):
        """空 items 渲染安全（修复前渲染期钳制越界抛 IndexError）。"""
        for props in ({}, {"items": None}, {"items": []}, {"items": 123},
                      {"items": "not-a-list"}):
            frame = _render(h(Menu, dict(props)))
            # 空菜单不崩溃；渲染空帧/空行（根画布最小高度 1），无菜单项内容
            plains = [ln.plain for ln in frame.lines]
            assert all(p.strip() == "" for p in plains), (
                f"Menu {props} 空 items 不应渲染菜单项: {plains}"
            )

    def test_all_header_items_no_crash(self):
        """全部为 header（无可选项）时渲染安全。"""
        items = [
            {"label": "头1", "type": "header"},
            {"label": "头2", "type": "header"},
        ]
        frame = _render(h(Menu, {"items": items}))
        plains = [ln.plain for ln in frame.lines]
        assert any("头1" in p for p in plains), f"header 项应渲染: {plains}"


# ═══════════════════════════════════════════════════════════
# SearchInput
# ═══════════════════════════════════════════════════════════


class TestSearchInput:
    def test_empty_query_shows_all(self):
        items = ["apple", "banana", "cherry"]
        frame = _render(h(SearchInput, {"items": items}))
        plains = [ln.plain for ln in frame.lines]
        # 查询行 + 全部 3 项
        assert len(plains) == 4
        assert any("apple" in p for p in plains)

    def test_filter_matches(self):
        items = ["apple", "banana", "cherry"]
        el = h(SearchInput, {"items": items})
        plains = _press(el, _key("char", "a"), _key("char", "n"))
        assert any("banana" in p for p in plains)
        assert not any("cherry" in p for p in plains), f"cherry 应被过滤: {plains}"

    def test_backspace_restores(self):
        items = ["apple", "banana"]
        el = h(SearchInput, {"items": items})
        plains = _press(el, _key("char", "x"), _key("backspace"))
        # 恢复全量
        assert any("apple" in p for p in plains) and any("banana" in p for p in plains)

    def test_escape_clears_query(self):
        items = ["apple", "banana"]
        el = h(SearchInput, {"items": items})
        plains = _press(el, _key("char", "x"), _key("escape"))
        assert any("apple" in p for p in plains)

    def test_select_on_enter(self):
        items = ["apple", "banana", "cherry"]
        selected = []
        el = h(SearchInput, {"items": items, "onSelect": lambda item, idx: selected.append((item["label"], idx))})
        ctx = _RenderCtx(el)
        try:
            ctx.key(_key("char", "b"))
            ctx.key(_key("enter"))
        finally:
            ctx.close()
        assert selected == [("banana", 0)], f"过滤后索引: {selected}"

    def test_limit_window(self):
        items = [f"item{i}" for i in range(10)]
        frame = _render(h(SearchInput, {"items": items, "limit": 3}))
        plains = [ln.plain for ln in frame.lines]
        # 查询行 + 3 行窗口
        assert len(plains) == 4

    def test_no_results_hint(self):
        items = ["apple"]
        el = h(SearchInput, {"items": items})
        plains = _press(el, _key("char", "z"))
        assert any("无匹配" in p for p in plains)

    def test_on_query_change(self):
        queries = []
        items = ["apple"]
        el = h(SearchInput, {"items": items, "onQueryChange": lambda q: queries.append(q)})
        ctx = _RenderCtx(el)
        try:
            ctx.key(_key("char", "a"))
            ctx.key(_key("char", "p"))
        finally:
            ctx.close()
        assert queries == ["a", "ap"]


# ═══════════════════════════════════════════════════════════
# Tabs
# ═══════════════════════════════════════════════════════════


class TestTabs:
    def test_basic_render(self):
        tabs = [
            {"label": "对话", "key": "chat"},
            {"label": "工具", "key": "tools"},
        ]
        frame = _render(h(Tabs, {"tabs": tabs}))
        plains = [ln.plain for ln in frame.lines]
        assert any("对话" in p for p in plains)
        assert any("工具" in p for p in plains)

    def test_arrow_switch(self):
        changed = []
        tabs = [{"label": "A", "key": "a"}, {"label": "B", "key": "b"}]
        el = h(Tabs, {"tabs": tabs, "onChange": lambda tab, key, idx: changed.append(key)})
        ctx = _RenderCtx(el)
        try:
            ctx.key(_key("arrow_right"))
        finally:
            ctx.close()
        assert changed == ["b"], f"切换回调: {changed}"

    def test_internal_state_highlight(self):
        tabs = [{"label": "A", "key": "a"}, {"label": "B", "key": "b"}]
        el = h(Tabs, {"tabs": tabs})
        plains = _press(el, _key("arrow_right"))
        # B 激活（● 指示符在 B 前）
        assert any("● B" in p for p in plains), f"切换后 B 应激活: {plains}"

    def test_controlled_active_key(self):
        tabs = [{"label": "A", "key": "a"}, {"label": "B", "key": "b"}]
        frame = _render(h(Tabs, {"tabs": tabs, "activeKey": "b"}))
        plains = [ln.plain for ln in frame.lines]
        assert any("● B" in p for p in plains), f"受控 B 应激活: {plains}"

    def test_content_render(self):
        tabs = [{"label": "A", "key": "a"}, {"label": "B", "key": "b"}]
        frame = _render(h(Tabs, {
            "tabs": tabs,
            "renderContent": lambda tab, idx: h("text", {"children": f"content-{tab['key']}"}),
        }))
        plains = [ln.plain for ln in frame.lines]
        assert "content-a" in plains, f"内容渲染: {plains}"

    def test_show_content_false(self):
        tabs = [{"label": "A", "key": "a"}]
        frame = _render(h(Tabs, {"tabs": tabs, "showContent": False}))
        assert len(frame.lines) == 1, "仅标签行"

    def test_no_tabs_empty(self):
        frame = _render(h(Tabs, {"tabs": []}))
        # 空 Column 高度 0 → render_frame 返回 1 行空（画布惰性行兜底）
        assert len(frame.lines) == 1 and not frame.lines[0].plain

    def test_marks_hidden(self):
        tabs = [{"label": "A", "key": "a"}]
        frame = _render(h(Tabs, {"tabs": tabs, "showMarks": False}))
        assert frame.lines[0].plain.strip() == "A"


__all__ = ["TestMenu", "TestSearchInput", "TestTabs"]
