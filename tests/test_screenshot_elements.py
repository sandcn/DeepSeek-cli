"""控件枚举层（``_screenshot.elements``）测试。

覆盖：控件类型推断、控件匹配（``#N`` / ``text:`` / ``class:`` / 裸子串 /
无匹配错误）、过滤与序列化，以及无平台后端实现时返回空清单。
"""

from __future__ import annotations

import pytest

from src.tools import _screenshot as screenshot_package
from src.tools._screenshot.elements import (
    DEFAULT_ELEMENT_LIMIT,
    ElementError,
    ElementInfo,
    classify_control,
    describe_elements,
    filter_elements,
    item_to_dict,
    list_process_elements,
    match_element,
)


def _element(handle=1, class_name="Button", text="确定", left=0, top=0,
             width=10, height=10, enabled=True, visible=True, depth=1):
    return ElementInfo(handle=handle, pid=99, class_name=class_name, text=text,
                       left=left, top=top, width=width, height=height,
                       enabled=enabled, visible=visible, depth=depth)


# ── 类型推断 ────────────────────────────────────────────

def test_classify_control_by_class_name():
    assert classify_control("Button") == "button"
    assert classify_control("Edit") == "edit"
    assert classify_control("SysListView32") == "list"
    assert classify_control("SysTreeView32") == "tree"
    assert classify_control("#32770") == "dialog"
    assert classify_control("Chrome_WidgetWin_1") == "webview"
    assert classify_control("CustomClass") == "window"
    assert classify_control("") == "window"


def test_element_properties_and_dict():
    item = _element()
    assert item.center_x == 5 and item.center_y == 5
    assert item.control_type == "button" and item.label == "确定"
    assert item.handle_hex == "0x1"
    payload = item_to_dict(item)
    assert payload["type"] == "button" and payload["label"] == "确定"
    assert payload["center_x"] == 5
    hidden = _element(handle=2, text="", visible=False)
    assert hidden.label == "按钮"
    assert "[hidden]" in hidden.summary()


# ── 匹配 ────────────────────────────────────────────────

def test_match_element_by_text_class_and_index():
    items = [
        _element(handle=1, class_name="Button", text="确定"),
        _element(handle=2, class_name="Button", text="取消", top=20),
        _element(handle=3, class_name="Edit", text="", top=40),
    ]
    assert match_element(items, "确定").handle == 1
    assert match_element(items, "text:取消").handle == 2
    assert match_element(items, "class:edit").handle == 3
    assert match_element(items, "#2").handle == 2
    assert match_element(items, "取消").handle == 2


def test_match_element_prefers_interactive_control():
    items = [
        _element(handle=1, class_name="Panel", text="确定", width=200, height=200,
                 visible=False),
        _element(handle=2, class_name="Button", text="确定", width=50, height=20),
    ]
    assert match_element(items, "确定").handle == 2


def test_match_element_by_type_or_label():
    items = [
        _element(handle=1, class_name="RICHEDIT50W", text=""),
        _element(handle=2, class_name="Button", text="确定", top=30),
    ]
    assert match_element(items, "编辑框").handle == 1
    assert match_element(items, "edit").handle == 1
    assert match_element(items, "按钮").handle == 2
    assert match_element(items, "type:edit").handle == 1
    assert match_element(items, "button").handle == 2


def test_filter_elements_matches_type_labels():
    items = [_element(handle=1, class_name="RICHEDIT50W", text=""),
             _element(handle=2, class_name="Button", text="确定", top=30)]
    assert [item.handle for item in filter_elements(items, "编辑框")] == [1]
    assert [item.handle for item in filter_elements(items, "button")] == [2]


def test_match_element_reports_errors():
    items = [_element(handle=1, text="确定")]
    with pytest.raises(ElementError) as error:
        match_element(items, "不存在")
    assert "没有匹配" in str(error.value) and "确定" in str(error.value)
    with pytest.raises(ElementError):
        match_element(items, "#5")
    with pytest.raises(ElementError):
        match_element(items, "  ")
    with pytest.raises(ElementError):
        match_element(items, "#abc")


def test_filter_elements_falls_back_to_class_and_type():
    items = [_element(handle=1, class_name="Button", text="确定"),
             _element(handle=2, class_name="Edit", text="", top=20)]
    # 类名匹配
    assert [item.handle for item in filter_elements(items, "Edit")] == [2]
    # 类型标签匹配（"编辑" 命中 edit → 编辑框）
    assert [item.handle for item in filter_elements(items, "编辑")] == [2]
    assert len(filter_elements(items, None)) == 2


def test_describe_elements_respects_limit():
    items = [_element(handle=index, top=index) for index in range(5)]
    assert len(describe_elements(items, 3)) == 3
    assert len(describe_elements(items)) == 5
    assert DEFAULT_ELEMENT_LIMIT >= 5


# ── 平台后端缺失 ────────────────────────────────────────

def test_list_process_elements_without_backend_support(monkeypatch):
    class _Backend:
        name = "stub"

    monkeypatch.setattr(screenshot_package, "resolve_backend", lambda: _Backend())
    assert list_process_elements(99) == []


def test_list_process_elements_rejects_bad_pid():
    assert list_process_elements(0) == []
    assert list_process_elements(-1) == []
    assert list_process_elements("99") == []
