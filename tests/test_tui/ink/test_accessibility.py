"""accessibility — aria-* 属性提取测试。"""

from __future__ import annotations

from src.tui.ink import (
    get_accessibility,
    screen_reader_text,
    ARIA_ROLES,
    ARIA_STATE_KEYS,
)
from src.tui.ink.accessibility import get_accessibility as ga


def test_defaults_when_missing():
    assert get_accessibility({}) == {"label": None, "hidden": False, "role": None, "state": {}}


def test_non_mapping_is_safe():
    assert get_accessibility(None)["label"] is None
    assert get_accessibility("x")["hidden"] is False


def test_extracts_valid_fields():
    acc = get_accessibility({
        "aria-label": "提交",
        "aria-hidden": True,
        "aria-role": "button",
        "aria-state": {"checked": True, "bogus": 1},
    })
    assert acc["label"] == "提交"
    assert acc["hidden"] is True
    assert acc["role"] == "button"
    assert acc["state"] == {"checked": True}


def test_invalid_role_dropped():
    assert get_accessibility({"aria-role": "nope"})["role"] is None


def test_role_and_state_key_sets():
    assert "button" in ARIA_ROLES
    assert "checked" in ARIA_STATE_KEYS


def test_screen_reader_text_prefers_label():
    assert screen_reader_text({"aria-label": "L"}, "fallback") == "L"
    assert screen_reader_text({}, "fallback") == "fallback"
    assert screen_reader_text({"aria-hidden": True, "aria-label": "L"}, "f") == ""


def test_module_alias_same():
    assert ga is get_accessibility


def test_transform_accessibility_label_in_screen_reader_mode():
    # ★ 多会话隔离：屏幕阅读器开关经 renderToString options 注入**本会话**，
    #   不再写模块级全局（见 _render_api / _hooks_env 修复）。
    from src.tui.ink import h, Transform, renderToString

    out = renderToString(
        h(Transform, {"accessibilityLabel": "图片：一只猫", "transform": str.upper}, "cat"),
        {"isScreenReaderEnabled": True},
    )
    assert out == "图片：一只猫"


def test_transform_normal_mode_uses_children():
    from src.tui.ink import h, Transform, renderToString

    out = renderToString(h(Transform, {"accessibilityLabel": "L", "transform": str.upper}, "cat"))
    assert out == "CAT"
