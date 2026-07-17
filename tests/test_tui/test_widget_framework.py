"""Tests for the new TUI Widget framework (widget_base, render_buffer, layout).

Tests cover:
  - Widget lifecycle (mount/unmount/set_state)
  - RenderBuffer operations (write/merge/sub_buffer/render)
  - Layout widgets (Vertical/Horizontal/Padding/Border)
  - Framework WidgetTree integration
  - Style extensions
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


if __name__ == "__main__":
    unittest.main()
