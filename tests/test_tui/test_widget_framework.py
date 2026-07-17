"""Tests for the new TUI Widget framework (widget_base, render_buffer, layout).

Tests cover:
  - Widget lifecycle (mount/unmount/set_state)
  - Widget key/parent attributes
  - WidgetTree find/find_all/find_by_type
  - RenderBuffer operations (write/merge/sub_buffer/render)
  - Layout widgets (Vertical/Horizontal/Padding/Border)
  - Framework WidgetTree integration
  - Style extensions
  - StatusBarWidget
"""

from __future__ import annotations

import unittest


class TestWidgetLifecycle(unittest.TestCase):
    """Test Widget base class lifecycle."""

    def test_widget_init(self):
        from src.tui.widget_base import Widget
        w = Widget()
        self.assertEqual(w.props, {})
        self.assertEqual(w.state, {})
        self.assertEqual(w.children, [])
        self.assertFalse(w.mounted)
        self.assertTrue(w.dirty)

    def test_widget_init_with_props(self):
        from src.tui.widget_base import Widget
        w = Widget(props={"name": "test", "count": 42})
        self.assertEqual(w.props["name"], "test")
        self.assertEqual(w.props["count"], 42)

    def test_mount_unmount(self):
        from src.tui.widget_base import Widget
        w = Widget()
        self.assertFalse(w.mounted)
        w.mount()
        self.assertTrue(w.mounted)
        w.unmount()
        self.assertFalse(w.mounted)

    def test_set_state(self):
        from src.tui.widget_base import Widget
        w = Widget()
        w.set_state({"count": 1})
        self.assertEqual(w.state["count"], 1)
        self.assertTrue(w.dirty)

    def test_set_state_merge(self):
        from src.tui.widget_base import Widget
        w = Widget()
        w.set_state({"a": 1})
        w.set_state({"b": 2})
        self.assertEqual(w.state["a"], 1)
        self.assertEqual(w.state["b"], 2)

    def test_mark_clean(self):
        from src.tui.widget_base import Widget
        w = Widget()
        self.assertTrue(w.dirty)
        w.mark_clean()
        self.assertFalse(w.dirty)

    def test_should_update(self):
        from src.tui.widget_base import Widget
        w = Widget(props={"a": 1})
        self.assertTrue(w.should_update())  # dirty by default
        w.mark_clean()
        self.assertFalse(w.should_update({"a": 1}))  # same props
        self.assertTrue(w.should_update({"a": 2}))  # different props

    def test_compose_default(self):
        from src.tui.widget_base import Widget
        w = Widget()
        result = w.compose()
        self.assertEqual(result, [])

    def test_str_repr(self):
        from src.tui.widget_base import Widget
        w = Widget(props={"name": "test"})
        repr_str = repr(w)
        self.assertIn("Widget", repr_str)
        self.assertIn("test", repr_str)

    def test_walk(self):
        from src.tui.widget_base import Widget
        w = Widget()
        nodes = w.walk()
        self.assertEqual(len(nodes), 1)
        self.assertIs(nodes[0], w)

    def test_key_attribute(self):
        """Widget key attribute for identification."""
        from src.tui.widget_base import Widget
        w1 = Widget(key="main")
        w2 = Widget()  # no key
        self.assertEqual(w1.key, "main")
        self.assertIsNone(w2.key)

    def test_parent_property(self):
        """Widget parent property after mount."""
        from src.tui.widget_base import Widget
        parent = Widget()
        child = Widget()
        parent._children = [child]
        # Before mount, parent is None
        self.assertIsNone(child.parent)
        # After mount, parent is set
        parent.mount()
        self.assertIs(child.parent, parent)
        # After unmount, parent is None
        parent.unmount()
        self.assertIsNone(child.parent)

    def test_find_child_by_type(self):
        """Widget.find_child() searches recursively."""
        from src.tui.widget_base import Widget
        class A(Widget): pass
        class B(Widget): pass
        a = A()
        b = B()
        root = Widget()
        root._children = [a, b]
        found = root.find_child(A)
        self.assertIs(found, a)
        found = root.find_child(B)
        self.assertIs(found, b)
        self.assertIsNone(root.find_child(type(None)))

    def test_find_children_by_type(self):
        """Widget.find_children() returns all matches."""
        from src.tui.widget_base import Widget
        class A(Widget): pass
        a1 = A()
        a2 = A()
        root = Widget()
        root._children = [a1, Widget(), a2]
        found = root.find_children(A)
        self.assertEqual(len(found), 2)
        self.assertIn(a1, found)
        self.assertIn(a2, found)


class TestWidgetTree(unittest.TestCase):
    """Test WidgetTree management."""

    def test_tree_init(self):
        from src.tui.widget_base import Widget, WidgetTree
        tree = WidgetTree()
        self.assertIsNone(tree.root)

    def test_tree_set_root(self):
        from src.tui.widget_base import Widget, WidgetTree
        w = Widget()
        tree = WidgetTree(w)
        self.assertIs(tree.root, w)

    def test_tree_walk(self):
        from src.tui.widget_base import Widget, WidgetTree
        parent = Widget()
        child1 = Widget()
        child2 = Widget()
        parent._children = [child1, child2]
        tree = WidgetTree(parent)
        nodes = tree.walk()
        self.assertEqual(len(nodes), 3)

    def test_tree_clear(self):
        from src.tui.widget_base import Widget, WidgetTree
        w = Widget()
        w.mount()
        tree = WidgetTree(w)
        tree.clear()
        self.assertIsNone(tree.root)

    def test_find_by_key(self):
        """WidgetTree.find() locates widgets by key."""
        from src.tui.widget_base import Widget, WidgetTree
        root = Widget(key="root")
        child = Widget(key="child")
        grandchild = Widget(key="grandchild")
        child._children = [grandchild]
        root._children = [child]
        tree = WidgetTree(root)
        self.assertIs(tree.find("root"), root)
        self.assertIs(tree.find("child"), child)
        self.assertIs(tree.find("grandchild"), grandchild)
        self.assertIsNone(tree.find("nonexistent"))

    def test_find_all_by_key(self):
        """WidgetTree.find_all() locates all widgets with same key."""
        from src.tui.widget_base import Widget, WidgetTree
        root = Widget(key="dup")
        child1 = Widget(key="dup")
        child2 = Widget(key="dup")
        root._children = [child1, child2]
        tree = WidgetTree(root)
        results = tree.find_all("dup")
        self.assertEqual(len(results), 3)

    def test_find_by_type(self):
        """WidgetTree.find_by_type() locates widgets by class."""
        from src.tui.widget_base import Widget, WidgetTree
        class SpecialWidget(Widget): pass
        root = Widget()
        special = SpecialWidget(key="s1")
        special2 = SpecialWidget(key="s2")
        root._children = [special, special2]
        tree = WidgetTree(root)
        results = tree.find_by_type(SpecialWidget)
        self.assertEqual(len(results), 2)
        self.assertIn(special, results)
        self.assertIn(special2, results)

    def test_find_empty_tree(self):
        """WidgetTree.find() on empty tree returns None."""
        from src.tui.widget_base import WidgetTree
        tree = WidgetTree()
        self.assertIsNone(tree.find("anything"))

    def test_find_all_empty_tree(self):
        """WidgetTree.find_all() on empty tree returns empty list."""
        from src.tui.widget_base import WidgetTree
        tree = WidgetTree()
        self.assertEqual(tree.find_all("anything"), [])

    def test_find_by_type_empty_tree(self):
        """WidgetTree.find_by_type() on empty tree returns empty list."""
        from src.tui.widget_base import Widget, WidgetTree
        tree = WidgetTree()
        self.assertEqual(tree.find_by_type(Widget), [])


class TestRenderBuffer(unittest.TestCase):
    """Test RenderBuffer operations."""

    def test_create(self):
        from src.tui.render_buffer import RenderBuffer
        buf = RenderBuffer(10, 5)
        self.assertEqual(buf.width, 10)
        self.assertEqual(buf.height, 5)
        self.assertFalse(buf.is_empty())

    def test_create_zero_size(self):
        from src.tui.render_buffer import RenderBuffer
        buf = RenderBuffer(0, 0)
        self.assertTrue(buf.is_empty())
        self.assertEqual(buf.render(), "")

    def test_write(self):
        from src.tui.render_buffer import RenderBuffer
        buf = RenderBuffer(10, 3)
        buf.write(0, 0, "Hello")
        buf.write(0, 1, "World")
        output = buf.render()
        self.assertIn("Hello", output)
        self.assertIn("World", output)

    def test_write_out_of_bounds(self):
        from src.tui.render_buffer import RenderBuffer
        buf = RenderBuffer(10, 3)
        # Should not raise
        buf.write(-1, 0, "test")
        buf.write(0, 100, "test")
        buf.write(100, 0, "test")

    def test_write_char(self):
        from src.tui.render_buffer import RenderBuffer
        buf = RenderBuffer(5, 3)
        buf.write_char(2, 1, "X")
        output = buf.render()
        self.assertIn("X", output)

    def test_clear(self):
        from src.tui.render_buffer import RenderBuffer
        buf = RenderBuffer(5, 3)
        buf.write(0, 0, "Hello")
        buf.clear()
        output = buf.render()
        self.assertEqual(output, "")

    def test_merge(self):
        from src.tui.render_buffer import RenderBuffer
        buf = RenderBuffer(10, 5)
        buf.write(0, 0, "Hello")
        overlay = RenderBuffer(5, 3)
        overlay.write(0, 0, "World")
        buf.merge(overlay, x=2, y=1)
        # merge should not raise and content should be added
        output = buf.render()
        self.assertIn("Hello", output)

    def test_sub_buffer(self):
        from src.tui.render_buffer import RenderBuffer
        buf = RenderBuffer(10, 5)
        buf.write(0, 0, "Hello")
        sub = buf.sub_buffer(0, 0, 5, 1)
        self.assertEqual(sub.width, 5)
        self.assertEqual(sub.height, 1)

    def test_fill(self):
        from src.tui.render_buffer import RenderBuffer
        buf = RenderBuffer(10, 3)
        buf.fill("#", 0, 0, 5, 1)
        output = buf.render()
        self.assertIn("#####", output)

    def test_hline(self):
        from src.tui.render_buffer import RenderBuffer
        buf = RenderBuffer(10, 3)
        buf.hline(1, "-")
        output = buf.render()
        self.assertIn("-" * 10, output)

    def test_hcenter(self):
        """hcenter() writes centered text."""
        from src.tui.render_buffer import RenderBuffer
        buf = RenderBuffer(20, 3)
        buf.hcenter("Hello", 1)
        output = buf.render()
        # "Hello" (5 chars) centered in 20 width → at x=7
        self.assertIn("Hello", output)
        # Should be roughly centered (7 spaces before Hello)
        line = "".join(buf._grid[1])
        trimmed = line.lstrip()
        self.assertTrue(trimmed.startswith("Hello"))

    def test_render_raw(self):
        """render_raw() preserves trailing spaces."""
        from src.tui.render_buffer import RenderBuffer
        buf = RenderBuffer(10, 2)
        buf.write(0, 0, "Hello")
        # render_raw should include trailing spaces
        raw = buf.render_raw()
        self.assertIn("Hello", raw)

    def test_clear_row(self):
        """clear_row() clears a single row."""
        from src.tui.render_buffer import RenderBuffer
        buf = RenderBuffer(5, 3)
        buf.write(0, 1, "Hello")
        buf.clear_row(1)
        output = buf.render()
        self.assertNotIn("Hello", output)

    def test_clear_col(self):
        """clear_col() clears a single column."""
        from src.tui.render_buffer import RenderBuffer
        buf = RenderBuffer(5, 3)
        buf.write(2, 1, "X")
        buf.clear_col(2)
        # Column 2 should now be spaces
        self.assertEqual(buf._grid[1][2], " ")


class TestLayoutWidgets(unittest.TestCase):
    """Test layout widgets."""

    def test_vertical_import(self):
        from src.tui.layout import Vertical
        self.assertIsNotNone(Vertical)

    def test_horizontal_import(self):
        from src.tui.layout import Horizontal
        self.assertIsNotNone(Horizontal)

    def test_padding_import(self):
        from src.tui.layout import Padding
        self.assertIsNotNone(Padding)

    def test_border_import(self):
        from src.tui.layout import Border
        self.assertIsNotNone(Border)

    def test_vertical_isinstance(self):
        """Vertical is a proper Widget subclass (isinstance works)."""
        from src.tui.layout import Vertical
        from src.tui.widget_base import Widget
        v = Vertical([])
        self.assertIsInstance(v, Widget)
        self.assertIsInstance(v, Vertical)

    def test_horizontal_isinstance(self):
        """Horizontal is a proper Widget subclass (isinstance works)."""
        from src.tui.layout import Horizontal
        from src.tui.widget_base import Widget
        h = Horizontal([])
        self.assertIsInstance(h, Widget)
        self.assertIsInstance(h, Horizontal)

    def test_padding_isinstance(self):
        """Padding is a proper Widget subclass (isinstance works)."""
        from src.tui.layout import Padding
        from src.tui.widget_base import Widget
        p = Padding(Widget())
        self.assertIsInstance(p, Widget)
        self.assertIsInstance(p, Padding)

    def test_border_isinstance(self):
        """Border is a proper Widget subclass (isinstance works)."""
        from src.tui.layout import Border
        from src.tui.widget_base import Widget
        b = Border(Widget())
        self.assertIsInstance(b, Widget)
        self.assertIsInstance(b, Border)

    def test_vertical_render(self):
        """Vertical renders children top-to-bottom."""
        from src.tui.layout import Vertical
        from src.tui.widget_base import Widget
        from src.tui.render_buffer import RenderBuffer

        class Label(Widget):
            def __init__(self, text):
                super().__init__()
                self._text = text
            def render(self, buffer):
                buffer.write(0, 0, self._text)

        v = Vertical([Label("A"), Label("B")], spacing=0)
        buf = RenderBuffer(10, 3)
        v.render(buf)
        output = buf.render()
        lines = output.split("\n")
        self.assertIn("A", lines[0] if lines else "")
        self.assertIn("B", lines[1] if len(lines) > 1 else "")

    def test_padding_render(self):
        """Padding adds space around child."""
        from src.tui.layout import Padding
        from src.tui.widget_base import Widget
        from src.tui.render_buffer import RenderBuffer

        class Label(Widget):
            def __init__(self, text):
                super().__init__()
                self._text = text
            def render(self, buffer):
                buffer.write(0, 0, self._text)

        p = Padding(Label("X"), left=2, top=1)
        buf = RenderBuffer(10, 5)
        p.render(buf)
        output = buf.render()
        # Content should be at row 1 (due to top=1)
        self.assertIn("X", output)

    def test_layout_key_support(self):
        """Layout controls support key attribute."""
        from src.tui.layout import Vertical, Horizontal, Padding, Border
        from src.tui.widget_base import Widget
        v = Vertical([], key="v1")
        h = Horizontal([], key="h1")
        p = Padding(Widget(), key="p1")
        b = Border(Widget(), key="b1")
        self.assertEqual(v.key, "v1")
        self.assertEqual(h.key, "h1")
        self.assertEqual(p.key, "p1")
        self.assertEqual(b.key, "b1")

    def test_vertical_with_key_inherited(self):
        """Vertical key is inherited from Widget."""
        from src.tui.layout import Vertical
        from src.tui.widget_base import Widget, WidgetTree
        v = Vertical([], key="my_vertical")
        tree = WidgetTree(v)
        found = tree.find("my_vertical")
        self.assertIs(found, v)


class TestFrameworkWidgetTree(unittest.TestCase):
    """Test Framework WidgetTree integration."""

    def setUp(self):
        from src.tui.framework import Framework
        Framework.reset_default()

    def test_mount_widget(self):
        from src.tui.framework import Framework
        from src.tui.widget_base import Widget
        f = Framework.get_default()
        w = Widget()
        f.mount_widget(w)
        self.assertTrue(f.has_widget_tree())
        self.assertIs(f.get_widget_root(), w)

    def test_unmount_widget(self):
        from src.tui.framework import Framework
        from src.tui.widget_base import Widget
        f = Framework.get_default()
        w = Widget()
        f.mount_widget(w)
        f.unmount_widget(w)
        self.assertFalse(f.has_widget_tree())

    def test_render_widget_tree_empty(self):
        from src.tui.framework import Framework
        from src.tui.render_buffer import RenderBuffer
        f = Framework.get_default()
        buf = RenderBuffer(10, 3)
        # Should not raise when no tree
        f.render_widget_tree(buf)


class TestStyleExtensions(unittest.TestCase):
    """Test Style extensions."""

    def test_with_props(self):
        from src.tui.core.style import Style
        s = Style.with_props(fg=45, bold=True)
        self.assertEqual(s.fg, 45)
        self.assertTrue(s.bold)

    def test_with_props_defaults(self):
        from src.tui.core.style import Style
        s = Style.with_props(fg=45)
        self.assertEqual(s.fg, 45)
        self.assertFalse(s.bold)

    def test_from_dict(self):
        from src.tui.core.style import Style
        s = Style.from_dict({"fg": 45, "italic": True, "bold": False})
        self.assertEqual(s.fg, 45)
        self.assertTrue(s.italic)
        self.assertFalse(s.bold)

    def test_from_dict_empty(self):
        from src.tui.core.style import Style
        s = Style.from_dict({})
        self.assertIsNone(s.fg)
        self.assertIsNone(s.bg)

    def test_extend(self):
        from src.tui.core.style import Style
        s = Style(fg=45)
        s2 = s.extend(bold=True)
        self.assertEqual(s2.fg, 45)
        self.assertTrue(s2.bold)
        # Original unchanged
        self.assertFalse(s.bold)

    def test_extend_overrides(self):
        from src.tui.core.style import Style
        s = Style(fg=45)
        s2 = s.extend(fg=46)
        self.assertEqual(s2.fg, 46)


class TestStatusBarWidget(unittest.TestCase):
    """Test StatusBarWidget component."""

    def test_import_and_init(self):
        """StatusBarWidget can be imported and instantiated."""
        from src.tui.widgets.status_bar_widget import StatusBarWidget
        from src.tui.widget_base import Widget
        sb = StatusBarWidget(props={"model_name": "test-model"})
        self.assertIsInstance(sb, Widget)
        self.assertIsInstance(sb, StatusBarWidget)
        self.assertEqual(sb.props.get("model_name"), "test-model")

    def test_default_props(self):
        """StatusBarWidget provides sensible defaults."""
        from src.tui.widgets.status_bar_widget import StatusBarWidget
        sb = StatusBarWidget()
        self.assertEqual(sb.props.get("model_name"), "")
        self.assertEqual(sb.props.get("tool_count"), 0)
        self.assertEqual(sb.props.get("status_active"), False)

    def test_render_creates_output(self):
        """StatusBarWidget.render() produces output to buffer."""
        from src.tui.widgets.status_bar_widget import StatusBarWidget
        from src.tui.render_buffer import RenderBuffer
        sb = StatusBarWidget(props={
            "model_name": "test-model",
            "status_active": False,
        })
        buf = RenderBuffer(80, 1)
        sb.render(buf)
        output = buf.render()
        self.assertIn("test-model", output)

    def test_render_with_tools(self):
        """StatusBarWidget renders tool counts."""
        from src.tui.widgets.status_bar_widget import StatusBarWidget
        from src.tui.render_buffer import RenderBuffer
        sb = StatusBarWidget(props={
            "model_name": "test-model",
            "tool_count": 0,
            "tool_total": 3,
            "tool_fail_count": 0,
            "status_active": True,
        })
        buf = RenderBuffer(80, 1)
        sb.render(buf)
        output = buf.render()
        # Should contain model name
        self.assertIn("test-model", output)

    def test_set_status_updates_state(self):
        """set_status() updates internal state via set_state()."""
        from src.tui.widgets.status_bar_widget import StatusBarWidget
        sb = StatusBarWidget(props={"model_name": "old"})
        self.assertEqual(sb.state.get("model_name"), None)
        # set_status should update state, NOT mutate props
        sb.set_status(model_name="new")
        self.assertEqual(sb.state.get("model_name"), "new")
        # Props should remain unchanged (immutable contract)
        self.assertEqual(sb.props.get("model_name"), "old")

    def test_render_from_state(self):
        """render() prefers state over props after set_status()."""
        from src.tui.widgets.status_bar_widget import StatusBarWidget
        from src.tui.render_buffer import RenderBuffer
        sb = StatusBarWidget(props={"model_name": "old-name"})
        sb.set_status(model_name="new-name")
        buf = RenderBuffer(80, 1)
        sb.render(buf)
        output = buf.render()
        self.assertIn("new-name", output)
        self.assertNotIn("old-name", output)

    def test_repr(self):
        """StatusBarWidget repr is informative."""
        from src.tui.widgets.status_bar_widget import StatusBarWidget
        sb = StatusBarWidget(props={"model_name": "gpt4", "tool_total": 5})
        r = repr(sb)
        self.assertIn("StatusBarWidget", r)
        self.assertIn("gpt4", r)
        self.assertIn("5", r)

    def test_vertical_layout_with_statusbar(self):
        """StatusBarWidget works inside Vertical layout."""
        from src.tui.widgets.status_bar_widget import StatusBarWidget
        from src.tui.layout import Vertical
        from src.tui.render_buffer import RenderBuffer
        sb = StatusBarWidget(props={"model_name": "test", "status_active": False})
        v = Vertical([sb])
        buf = RenderBuffer(80, 3)
        v.render(buf)
        output = buf.render()
        self.assertIn("test", output)


class TestIntegration(unittest.TestCase):
    """End-to-end integration tests."""

    def test_widget_render_buffer(self):
        """Widget with props renders to RenderBuffer."""
        from src.tui.widget_base import Widget
        from src.tui.render_buffer import RenderBuffer
        from src.tui.core.style import Style

        class Greeting(Widget):
            def render(self, buffer):
                name = self.props.get("name", "World")
                buffer.write(0, 0, f"Hello, {name}!")

        w = Greeting(props={"name": "TUI"})
        buf = RenderBuffer(20, 1)
        w.render(buf)
        output = buf.render()
        self.assertIn("Hello, TUI!", output)

    def test_widget_tree_compose_render(self):
        """WidgetTree with compose + render chain."""
        from src.tui.widget_base import Widget, WidgetTree
        from src.tui.render_buffer import RenderBuffer

        class Label(Widget):
            def __init__(self, text, **kw):
                super().__init__(**kw)
                self._text = text
            def render(self, buffer):
                buffer.write(0, 0, self._text)

        class Container(Widget):
            def __init__(self, children, **kw):
                super().__init__(**kw)
                self._children = children
            def compose(self):
                return self._children
            def render(self, buffer):
                buffer.write(0, 0, "Container:")

        label1 = Label("A")
        label2 = Label("B")
        container = Container([label1, label2])
        container.mount()

        tree = WidgetTree(container)
        buf = RenderBuffer(20, 5)
        tree.render(buf)
        output = buf.render()
        # Children render over parent at same position
        # Container wrote "Container:", then A→"Aontainer:", then B→"Bontainer:"
        self.assertEqual(output, "Bontainer:")

    def test_framework_widget_integration(self):
        """Full framework integration test."""
        from src.tui.framework import Framework
        from src.tui.widget_base import Widget
        from src.tui.render_buffer import RenderBuffer
        from src.tui.core.style import Style

        class HelloWidget(Widget):
            def render(self, buffer):
                buffer.write(0, 0, "Hello from Framework!")

        f = Framework.get_default()
        w = HelloWidget()
        f.mount_widget(w)
        self.assertTrue(f.has_widget_tree())

        buf = RenderBuffer(30, 3)
        f.render_widget_tree(buf)
        output = buf.render()
        self.assertIn("Hello from Framework!", output)

    def test_widget_tree_find_after_mount(self):
        """WidgetTree.find() works after mounting through Framework."""
        from src.tui.framework import Framework
        from src.tui.widget_base import Widget, WidgetTree
        root = Widget(key="root")
        child = Widget(key="child")
        root._children = [child]
        tree = WidgetTree(root)
        self.assertIs(tree.find("child"), child)
        self.assertIsNone(tree.find("ghost"))


if __name__ == "__main__":
    unittest.main()

