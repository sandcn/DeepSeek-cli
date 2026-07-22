"""测试 ComponentRegistry — 组件注册表。"""

from __future__ import annotations

import threading
from src.tui.core.component_registry import ComponentRegistry


class TestComponentRegistry:
    """测试 ComponentRegistry 的单例、注册、查询功能。"""

    def setup_method(self):
        ComponentRegistry.reset_default()

    def teardown_method(self):
        ComponentRegistry.reset_default()

    def test_singleton(self):
        """get_default() 返回同一实例。"""
        a = ComponentRegistry.get_default()
        b = ComponentRegistry.get_default()
        assert a is b

    def test_register_and_resolve(self):
        """register/resolve 基本功能。"""
        reg = ComponentRegistry.get_default()
        reg.register(1, "_do_content", (1,))
        result = reg.resolve(1)
        assert result is not None
        assert result[0] == "_do_content"
        assert result[1] == (1,)

    def test_register_with_no_args(self):
        """注册无参数命令。"""
        reg = ComponentRegistry.get_default()
        reg.register(14, "_do_tool_count_inc", ())
        result = reg.resolve(14)
        assert result is not None
        assert result[0] == "_do_tool_count_inc"
        assert result[1] == ()

    def test_resolve_nonexistent(self):
        """不存在的命令 ID 返回 None。"""
        reg = ComponentRegistry.get_default()
        result = reg.resolve(9999)
        assert result is None

    def test_has(self):
        """检查是否存在。"""
        reg = ComponentRegistry.get_default()
        reg.register(0, "_do_reasoning", (1,))
        assert reg.has(0)
        assert not reg.has(999)

    def test_clear(self):
        """清空后所有注册消失。"""
        reg = ComponentRegistry.get_default()
        reg.register(0, "_do_reasoning", (1,))
        reg.clear()
        assert not reg.has(0)

    def test_reset_default(self):
        """reset_default 后 get_default 返回新实例，且自动填充默认命令。"""
        a = ComponentRegistry.get_default()
        a.register(0, "test", ())
        ComponentRegistry.reset_default()
        b = ComponentRegistry.get_default()
        assert a is not b
        # 新实例自动填充全部 17 个默认命令
        assert b.has(0)
        assert b.count() == 17
        # 旧实例注册的 test 不在新实例中
        result = b.resolve(0)
        assert result is not None
        assert result[0] != "test"

    def test_all_commands(self):
        """返回所有已注册的命令 ID（含默认命令）。"""
        reg = ComponentRegistry.get_default()
        cmds = reg.all_commands()
        # 默认 17 个命令
        assert len(cmds) == 17
        assert 0 in cmds  # REASONING
        assert 11 in cmds  # NOTIFICATION

    def test_count(self):
        """正确返回注册数量（含自动填充的默认命令）。"""
        reg = ComponentRegistry.get_default()
        # 自动填充 17 个默认命令
        default_count = reg.count()
        assert default_count == 17
        reg.register(99, "a", ())
        assert reg.count() == default_count + 1
        reg.register(98, "b", ())
        assert reg.count() == default_count + 2
        reg.clear()
        # clear() 后再次 get_default() 会重新填充默认命令
        # 但当前实例的 _commands 已被清空，默认命令通过 _populate_defaults() 重建
        assert reg.count() == 0
