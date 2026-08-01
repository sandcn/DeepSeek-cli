"""测试 ink/element.py — Element 不可变元素 + h() 工厂。"""

from __future__ import annotations

import pytest

from src.tui.ink.element import (
    BOX,
    TEXT,
    STATIC,
    SPACER,
    APP,
    Element,
    h,
)


class TestElement:
    """Element 不可变性与规范化。"""

    def test_default_props_children(self):
        el = Element(BOX, {}, ())
        assert el.type == BOX
        assert el.props == {}
        assert el.children == ()

    def test_props_children_copied(self):
        props = {"width": 10}
        children = (Element(TEXT, {"children": "hi"}, ()),)
        el = Element(BOX, props, children)
        # 修改外部容器不影响内部副本
        props["width"] = 99
        assert el.props["width"] == 10
        # props 为独立 dict
        el.props["width"] = 5
        assert el.props["width"] == 5

    def test_key_from_props(self):
        el = h(TEXT, {"key": "abc", "children": "x"})
        assert el.key == "abc"

    def test_key_fallback_type(self):
        assert h(TEXT, {"children": "x"}).key == "host:text"
        assert h(BOX).key == "host:box"

    def test_frozen(self):
        el = h(TEXT, {"children": "x"})
        with pytest.raises(Exception):
            el.props = {}  # type: ignore[misc]


class TestH:
    """h() 工厂。"""

    def test_h_without_children(self):
        el = h(BOX, {"width": 10})
        assert el.type == BOX
        assert el.props == {"width": 10}
        assert el.children == ()

    def test_h_children_elements(self):
        el = h(BOX, None, h(TEXT, {"children": "a"}), h(TEXT, {"children": "b"}))
        assert len(el.children) == 2
        assert all(isinstance(c, Element) for c in el.children)

    def test_h_string_children_to_text(self):
        el = h(TEXT, None, "hello")
        assert len(el.children) == 1
        child = el.children[0]
        assert child.type == TEXT
        assert child.props["children"] == "hello"

    def test_h_mixed_children(self):
        el = h(BOX, None, "str", h(TEXT, {"children": "el"}))
        assert el.children[0].type == TEXT
        assert el.children[1].type == TEXT

    def test_function_component_type(self):
        def Comp(props):
            return h(TEXT, {"children": "x"})

        el = h(Comp, {"k": 1})
        assert el.type is Comp
        assert el.props == {"k": 1}
        # 函数组件 key 带模块限定（方向C 步骤6：消除跨模块同名冲突）
        assert el.key == f"fn:{Comp.__module__}.{Comp.__name__}"

    def test_same_name_different_module_keys(self):
        """跨模块同名组件产生不同 key（方向C 步骤6 模块限定）。"""
        def comp_a(props):
            return h(TEXT, {"children": "a"})

        def comp_b(props):
            return h(TEXT, {"children": "b"})

        comp_a.__name__ = "SameName"
        comp_b.__name__ = "SameName"
        comp_a.__module__ = "mod_a"
        comp_b.__module__ = "mod_b"

        el_a = h(comp_a)
        el_b = h(comp_b)
        assert el_a.key != el_b.key
        assert el_a.key == "fn:mod_a.SameName"
        assert el_b.key == "fn:mod_b.SameName"

    def test_fiber_key_matches_element_key(self):
        """Fiber.key 与 Element.key 对函数组件采用同一模块限定格式。"""
        from src.tui.ink.fiber import Fiber, TAG_FUNCTION

        def Comp(props):
            return h(TEXT, {"children": "x"})

        el = h(Comp)
        fiber = Fiber(TAG_FUNCTION, Comp, {})
        assert fiber.key == el.key == f"fn:{Comp.__module__}.{Comp.__name__}"


class TestHostTags:
    """host 标签常量唯一性。"""

    def test_tags_distinct(self):
        tags = {BOX, TEXT, STATIC, SPACER, APP}
        assert len(tags) == 5

    def test_tags_are_strings(self):
        assert isinstance(BOX, str)
        assert isinstance(TEXT, str)
