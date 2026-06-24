"""FocusManager 单元测试。

覆盖 FocusManager 单例的注册/注销/焦点遍历/启用禁用。
测试策略：直接实例化 FocusManager 并调用其方法，
不依赖 use_focus hook 的渲染上下文。
"""

from __future__ import annotations

import pytest

from src.chat_ui.react_ink._focus import FocusManager, _FocusableEntry


# ── 测试辅助 ────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _reset_focus_manager():
    """每个测试前重置 FocusManager 单例状态。"""
    FocusManager._instance = None
    yield
    FocusManager._instance = None


def _make_entry(is_active: bool = True, auto_focus: bool = False,
                on_focus=None, on_blur=None) -> _FocusableEntry:
    """创建测试用 _FocusableEntry。"""
    return _FocusableEntry(
        component=None,
        is_active=is_active,
        auto_focus=auto_focus,
        on_focus=on_focus,
        on_blur=on_blur,
    )


# ═══════════════════════════════════════════════════════════
# TestFocusManager
# ═══════════════════════════════════════════════════════════

class TestFocusManager:
    """FocusManager 测试。"""

    def test_register_component(self):
        """注册组件后出现在内部列表中。"""
        fm = FocusManager()
        entry = _make_entry()
        fm.register("id_a", entry)
        assert "id_a" in fm._focusables
        assert "id_a" in fm._order

    def test_register_duplicate_id_noop(self):
        """重复注册同一 ID 不覆盖。"""
        fm = FocusManager()
        entry1 = _make_entry()
        entry2 = _make_entry(is_active=False)
        fm.register("id_x", entry1)
        fm.register("id_x", entry2)
        # 仍是第一个 entry
        assert fm._focusables["id_x"] is entry1

    def test_unregister_component(self):
        """注销后组件从列表移除。"""
        fm = FocusManager()
        fm.register("id_a", _make_entry())
        fm.register("id_b", _make_entry())

        fm.unregister("id_a")
        assert "id_a" not in fm._focusables
        assert "id_a" not in fm._order
        assert "id_b" in fm._focusables

    def test_unregister_trigger_blur_when_focused(self):
        """注销当前焦点组件时触发 on_blur。"""
        blurs = []
        fm = FocusManager()
        entry = _make_entry(on_blur=lambda: blurs.append("blur"))
        fm.register("id_f", entry)
        fm.focus("id_f")
        assert fm.active_id == "id_f"

        fm.unregister("id_f")
        assert blurs == ["blur"]
        assert fm.active_id is None

    def test_focus_next_cyclic(self):
        """Tab 正向循环遍历。"""
        fm = FocusManager()
        fm.register("a", _make_entry())
        fm.register("b", _make_entry())
        fm.register("c", _make_entry())

        # 无焦点时 focus_next → 第一个
        fm.focus_next()
        assert fm.active_id == "a"
        fm.focus_next()
        assert fm.active_id == "b"
        fm.focus_next()
        assert fm.active_id == "c"
        # 循环回第一个
        fm.focus_next()
        assert fm.active_id == "a"

    def test_focus_previous_cyclic(self):
        """Shift+Tab 反向循环遍历。"""
        fm = FocusManager()
        fm.register("a", _make_entry())
        fm.register("b", _make_entry())
        fm.register("c", _make_entry())

        # 无焦点时 focus_previous → 最后一个
        fm.focus_previous()
        assert fm.active_id == "c"
        fm.focus_previous()
        assert fm.active_id == "b"
        fm.focus_previous()
        assert fm.active_id == "a"
        # 循环回最后一个
        fm.focus_previous()
        assert fm.active_id == "c"

    def test_focus_skip_inactive(self):
        """焦点遍历跳过 is_active=False 的组件。"""
        fm = FocusManager()
        fm.register("a", _make_entry(is_active=True))
        fm.register("b", _make_entry(is_active=False))  # 非活跃
        fm.register("c", _make_entry(is_active=True))

        fm.focus_next()
        assert fm.active_id == "a"
        fm.focus_next()
        assert fm.active_id == "c"  # 跳过 b

    def test_focus_by_id(self):
        """focus(id) 直接聚焦指定组件。"""
        fm = FocusManager()
        fm.register("a", _make_entry())
        fm.register("b", _make_entry())

        fm.focus("b")
        assert fm.active_id == "b"

    def test_focus_triggers_callbacks(self):
        """聚焦和失焦时触发 on_focus / on_blur 回调。"""
        events = []
        entry_a = _make_entry(
            on_focus=lambda: events.append("fa"),
            on_blur=lambda: events.append("ba"),
        )
        entry_b = _make_entry(
            on_focus=lambda: events.append("fb"),
            on_blur=lambda: events.append("bb"),
        )

        fm = FocusManager()
        fm.register("a", entry_a)
        fm.register("b", entry_b)

        fm.focus("a")
        assert events == ["fa"]

        fm.focus("b")
        assert events == ["fa", "ba", "fb"]

    def test_active_id_when_none_focused(self):
        """无焦点时 active_id 返回 None。"""
        fm = FocusManager()
        assert fm.active_id is None

    def test_disable_focus(self):
        """禁用后 focus_next 不生效。"""
        fm = FocusManager()
        fm.register("a", _make_entry())
        fm.disable()
        fm.focus_next()
        assert fm.active_id is None

    def test_enable_focus(self):
        """启用后 focus_next 恢复正常。"""
        fm = FocusManager()
        fm.register("a", _make_entry())
        fm.disable()
        fm.focus_next()
        assert fm.active_id is None

        fm.enable()
        fm.focus_next()
        assert fm.active_id == "a"

    def test_singleton(self):
        """多次调用 FocusManager() 返回同一实例。"""
        fm1 = FocusManager()
        fm2 = FocusManager()
        assert fm1 is fm2

    def test_auto_focus_on_register(self):
        """auto_focus=True 且当前无焦点时自动聚焦。"""
        fm = FocusManager()
        fm.register("x", _make_entry(auto_focus=True))
        assert fm.active_id == "x"

    def test_auto_focus_no_override(self):
        """auto_focus 不覆盖已有焦点。"""
        fm = FocusManager()
        fm.register("a", _make_entry())
        fm.focus("a")
        fm.register("b", _make_entry(auto_focus=True))
        assert fm.active_id == "a"  # 不覆盖

    def test_has_focusables(self):
        """has_focusables 属性反映注册状态。"""
        fm = FocusManager()
        assert fm.has_focusables is False
        fm.register("a", _make_entry())
        assert fm.has_focusables is True
