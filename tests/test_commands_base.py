"""Tests for src/core/commands/base.py — 命令插件系统"""

import pytest

from src.core.commands.base import (
    CommandMeta, CommandPlugin, CommandPluginRegistry,
    command_plugin, get_plugin_registry,
)


# ============================================================
#  辅助: Mock 输出端口（避免触发 ui.events）
# ============================================================

@pytest.fixture(autouse=True)
def _mock_output_port():
    """自动将全局输出端口替换为 Mock，防止测试中触发 UI 层导入"""
    from src.core.adapters.output import set_default_output_port, reset_default_output_port

    class _MockPort:
        def write(self, text: str, level: str = "info", source: str = "core") -> None:
            pass
        def write_with_lock(self, text: str, level: str = "info", source: str = "core") -> None:
            pass
        def locked(self):
            from contextlib import nullcontext
            return nullcontext()

    set_default_output_port(_MockPort())
    yield
    reset_default_output_port()


# ============================================================
#  辅助: 测试用 CommandPlugin 子类
# ============================================================

class HelloCommand(CommandPlugin):
    """打招呼命令"""

    def execute(self, ctx) -> bool:
        self.output("Hello!")
        return True


class GoodbyeCommand(CommandPlugin):
    """再见命令"""

    def execute(self, ctx) -> bool:
        self.output("Goodbye!")
        return False


class PingCommand(CommandPlugin):
    """Ping 命令 — 无别名、隐藏"""

    def execute(self, ctx) -> bool:
        return True


class EchoCommand(CommandPlugin):
    """Echo 命令 — 带 usage"""

    def execute(self, ctx) -> bool:
        self.output(str(ctx))
        return True


# ============================================================
#  CommandMeta
# ============================================================

class TestCommandMeta:

    def test_defaults(self):
        """name 必填，其余字段有合理默认值"""
        meta = CommandMeta(name="test")
        assert meta.name == "test"
        assert meta.aliases == []
        assert meta.group == "general"
        assert meta.description == ""
        assert meta.usage == ""
        assert meta.hidden is False

    def test_custom_values(self):
        """所有字段均可自定义"""
        meta = CommandMeta(
            name="custom",
            aliases=["c", "cst"],
            group="admin",
            description="A custom command",
            usage="[arg1] [arg2]",
            hidden=True,
        )
        assert meta.name == "custom"
        assert meta.aliases == ["c", "cst"]
        assert meta.group == "admin"
        assert meta.description == "A custom command"
        assert meta.usage == "[arg1] [arg2]"
        assert meta.hidden is True

    def test_hidden_default_false(self):
        """hidden 默认值为 False"""
        meta = CommandMeta(name="visible")
        assert meta.hidden is False

    def test_mutable_aliases_default(self):
        """缺省 aliases 是独立的空列表（每次调用都创建新实例）"""
        meta1 = CommandMeta(name="a")
        meta2 = CommandMeta(name="b")
        assert meta1.aliases is not meta2.aliases


# ============================================================
#  CommandPlugin 抽象基类
# ============================================================

class TestCommandPlugin:

    def test_cannot_instantiate_directly(self):
        """抽象基类不能直接实例化（execute 为 abstractmethod）"""
        with pytest.raises(TypeError):
            CommandPlugin()  # type: ignore[abstract]

    def test_subclass_must_implement_execute(self):
        """不实现 execute 的子类也无法实例化"""
        class Incomplete(CommandPlugin):
            pass

        with pytest.raises(TypeError):
            Incomplete()

    def test_execute_returns_true(self):
        """execute 返回 True 表示命令已处理"""
        cmd = HelloCommand()
        result = cmd.execute(None)
        assert result is True

    def test_execute_returns_false(self):
        """execute 返回 False 表示未处理"""
        cmd = GoodbyeCommand()
        result = cmd.execute(None)
        assert result is False

    def test_execute_receives_context(self):
        """execute 接收上下文参数"""
        class CaptureCtx(CommandPlugin):
            def execute(self, ctx) -> bool:
                self._captured = ctx
                return True

        cmd = CaptureCtx()
        context = {"key": "value"}
        cmd.execute(context)
        assert cmd._captured is context

    def test_help_text_with_name_only(self):
        """无别名、无描述、无用法时 help_text 的格式"""
        cmd = PingCommand()
        cmd.meta = CommandMeta(name="ping", description="", usage="")
        text = cmd.help_text()
        assert "/ping" in text
        assert "—" in text
        assert "别名" not in text

    def test_help_text_with_aliases(self):
        """有别名时 help_text 包含别名信息"""
        cmd = HelloCommand()
        cmd.meta = CommandMeta(
            name="hello",
            aliases=["hi", "hey"],
            description="Say hello",
        )
        text = cmd.help_text()
        assert "/hello" in text
        assert "Say hello" in text
        assert "/hi" in text
        assert "/hey" in text
        assert "别名" in text

    def test_help_text_with_usage(self):
        """有 usage 时 help_text 包含用法"""
        cmd = EchoCommand()
        cmd.meta = CommandMeta(
            name="echo",
            usage="<message>",
            description="Echo a message",
        )
        text = cmd.help_text()
        assert "/echo" in text
        assert "<message>" in text
        assert "Echo a message" in text

    def test_help_text_full(self):
        """完整的 help_text 格式"""
        cmd = HelloCommand()
        cmd.meta = CommandMeta(
            name="greet",
            aliases=["g"],
            description="Greet the user",
            usage="[name]",
        )
        text = cmd.help_text()
        assert "/greet [name]" in text
        assert "Greet the user" in text
        assert "/g" in text

    def test_on_register_default(self):
        """on_register 默认空实现，不抛异常"""
        cmd = HelloCommand()
        cmd.on_register()  # 不应抛异常

    def test_on_unregister_default(self):
        """on_unregister 默认空实现，不抛异常"""
        cmd = HelloCommand()
        cmd.on_unregister()  # 不应抛异常

    def test_output_called(self):
        """output() 调用不抛异常"""
        cmd = HelloCommand()
        cmd.output("test message")  # 不应抛异常

    def test_meta_default_from_class_name(self):
        """未显式设置 meta 时使用类名小写"""
        class AutoName(CommandPlugin):
            def execute(self, ctx) -> bool:
                return True

        cmd = AutoName()
        assert cmd.meta.name == "autoname"
        assert cmd.meta.aliases == []
        assert cmd.meta.group == "general"

    def test_meta_custom_set_in_init(self):
        """子类可以在 __init__ 中自定义 meta"""
        class CustomMeta(CommandPlugin):
            def __init__(self):
                self.meta = CommandMeta(
                    name="custom_meta",
                    aliases=["cm"],
                    group="special",
                    description="Custom",
                    usage="[args]",
                    hidden=True,
                )

            def execute(self, ctx) -> bool:
                return True

        cmd = CustomMeta()
        assert cmd.meta.name == "custom_meta"
        assert cmd.meta.aliases == ["cm"]
        assert cmd.meta.group == "special"
        assert cmd.meta.hidden is True


# ============================================================
#  CommandPluginRegistry — 基本功能
# ============================================================

class TestCommandPluginRegistryBasic:

    def test_create_empty(self):
        """创建空注册表，count 为 0"""
        registry = CommandPluginRegistry()
        assert registry.count() == 0
        assert registry.list() == []

    def test_register_and_get(self):
        """register 后可通过 get 获取"""
        registry = CommandPluginRegistry()
        plugin = HelloCommand()
        plugin.meta = CommandMeta(name="hello")

        registry.register(plugin)
        retrieved = registry.get("hello")
        assert retrieved is plugin

    def test_get_by_alias(self):
        """register 后可通过别名 get"""
        registry = CommandPluginRegistry()
        plugin = HelloCommand()
        plugin.meta = CommandMeta(name="hello", aliases=["hi", "hey"])

        registry.register(plugin)

        assert registry.get("hi") is plugin
        assert registry.get("hey") is plugin

    def test_get_nonexistent(self):
        """不存在的名称返回 None"""
        registry = CommandPluginRegistry()
        assert registry.get("nonexistent") is None

    def test_register_overwrite_same_name(self):
        """同名注册时覆盖旧插件"""
        registry = CommandPluginRegistry()

        plugin1 = HelloCommand()
        plugin1.meta = CommandMeta(name="duplicate")
        registry.register(plugin1)
        assert registry.get("duplicate") is plugin1

        plugin2 = GoodbyeCommand()
        plugin2.meta = CommandMeta(name="duplicate")
        registry.register(plugin2)
        assert registry.get("duplicate") is plugin2
        assert registry.count() == 1  # 数量不变

    def test_register_auto_group(self):
        """register 自动加入分组"""
        registry = CommandPluginRegistry()

        plugin = HelloCommand()
        plugin.meta = CommandMeta(name="hello", group="chat")

        registry.register(plugin)
        assert "chat" in registry.list_groups()
        assert registry.get("hello") is plugin

    def test_count(self):
        """count 返回正确数量"""
        registry = CommandPluginRegistry()

        p1 = HelloCommand()
        p1.meta = CommandMeta(name="hello")
        registry.register(p1)
        assert registry.count() == 1

        p2 = GoodbyeCommand()
        p2.meta = CommandMeta(name="goodbye")
        registry.register(p2)
        assert registry.count() == 2


# ============================================================
#  CommandPluginRegistry — unregister
# ============================================================

class TestCommandPluginRegistryUnregister:

    def test_unregister_success(self):
        """unregister 已存在的插件返回 True"""
        registry = CommandPluginRegistry()
        plugin = HelloCommand()
        plugin.meta = CommandMeta(name="hello")
        registry.register(plugin)

        assert registry.unregister("hello") is True

    def test_unregister_nonexistent(self):
        """unregister 不存在的插件返回 False"""
        registry = CommandPluginRegistry()
        assert registry.unregister("nonexistent") is False

    def test_unregister_removes_plugin(self):
        """unregister 后 get 返回 None"""
        registry = CommandPluginRegistry()
        plugin = HelloCommand()
        plugin.meta = CommandMeta(name="hello")
        registry.register(plugin)

        registry.unregister("hello")
        assert registry.get("hello") is None
        assert registry.count() == 0

    def test_unregister_removes_aliases(self):
        """unregister 清理关联的别名"""
        registry = CommandPluginRegistry()
        plugin = HelloCommand()
        plugin.meta = CommandMeta(name="hello", aliases=["hi"])
        registry.register(plugin)

        registry.unregister("hello")
        assert registry.get("hi") is None  # 别名也被清理

    def test_unregister_removes_from_group(self):
        """unregister 从分组中移除"""
        registry = CommandPluginRegistry()
        plugin = HelloCommand()
        plugin.meta = CommandMeta(name="hello", group="chat")
        registry.register(plugin)

        registry.unregister("hello")
        # 分组列表为空（或该组不存在）
        assert "hello" not in [p.meta.name for p in registry.list("chat")]

    def test_unregister_after_overwrite(self):
        """覆盖注册后 unregister 新名称，旧插件也被移除"""
        registry = CommandPluginRegistry()

        p1 = HelloCommand()
        p1.meta = CommandMeta(name="dup")
        registry.register(p1)

        p2 = GoodbyeCommand()
        p2.meta = CommandMeta(name="dup")
        registry.register(p2)

        assert registry.unregister("dup") is True
        assert registry.get("dup") is None
        assert registry.count() == 0

    def test_unregister_multiple_times(self):
        """重复 unregister 同一个名称：第一次 True，后续 False"""
        registry = CommandPluginRegistry()
        plugin = HelloCommand()
        plugin.meta = CommandMeta(name="hello")
        registry.register(plugin)

        assert registry.unregister("hello") is True
        assert registry.unregister("hello") is False
        assert registry.unregister("hello") is False


# ============================================================
#  CommandPluginRegistry — list / list_groups
# ============================================================

class TestCommandPluginRegistryList:

    def test_list_all(self):
        """list() 列出所有已注册插件"""
        registry = CommandPluginRegistry()

        p1 = HelloCommand()
        p1.meta = CommandMeta(name="hello")
        registry.register(p1)

        p2 = GoodbyeCommand()
        p2.meta = CommandMeta(name="goodbye")
        registry.register(p2)

        all_plugins = registry.list()
        assert len(all_plugins) == 2
        names = {p.meta.name for p in all_plugins}
        assert names == {"hello", "goodbye"}

    def test_list_by_group(self):
        """list(group) 按分组过滤"""
        registry = CommandPluginRegistry()

        p1 = HelloCommand()
        p1.meta = CommandMeta(name="hello", group="chat")
        registry.register(p1)

        p2 = GoodbyeCommand()
        p2.meta = CommandMeta(name="goodbye", group="admin")
        registry.register(p2)

        p3 = PingCommand()
        p3.meta = CommandMeta(name="ping", group="chat")
        registry.register(p3)

        chat_plugins = registry.list("chat")
        assert len(chat_plugins) == 2
        chat_names = {p.meta.name for p in chat_plugins}
        assert chat_names == {"hello", "ping"}

        admin_plugins = registry.list("admin")
        assert len(admin_plugins) == 1
        assert admin_plugins[0].meta.name == "goodbye"

    def test_list_nonexistent_group(self):
        """list(不存在的分组) 返回空列表"""
        registry = CommandPluginRegistry()

        plugin = HelloCommand()
        plugin.meta = CommandMeta(name="hello", group="chat")
        registry.register(plugin)

        assert registry.list("nonexistent") == []

    def test_list_after_unregister(self):
        """unregister 后 list 不再包含"""
        registry = CommandPluginRegistry()

        p1 = HelloCommand()
        p1.meta = CommandMeta(name="hello", group="chat")
        registry.register(p1)

        p2 = GoodbyeCommand()
        p2.meta = CommandMeta(name="goodbye", group="chat")
        registry.register(p2)

        registry.unregister("hello")
        names = {p.meta.name for p in registry.list("chat")}
        assert names == {"goodbye"}

    def test_list_returns_copy(self):
        """list() 返回副本，修改返回值不影响注册表"""
        registry = CommandPluginRegistry()

        plugin = HelloCommand()
        plugin.meta = CommandMeta(name="hello")
        registry.register(plugin)

        result = registry.list()
        result.clear()
        assert registry.count() == 1

    def test_list_groups(self):
        """list_groups() 返回所有分组"""
        registry = CommandPluginRegistry()

        p1 = HelloCommand()
        p1.meta = CommandMeta(name="hello", group="chat")
        registry.register(p1)

        p2 = GoodbyeCommand()
        p2.meta = CommandMeta(name="goodbye", group="admin")
        registry.register(p2)

        p3 = PingCommand()
        p3.meta = CommandMeta(name="ping", group="system")
        registry.register(p3)

        groups = registry.list_groups()
        assert set(groups) == {"chat", "admin", "system"}

    def test_list_groups_default_general(self):
        """未指定 group 的插件归入 'general' 分组"""
        registry = CommandPluginRegistry()

        plugin = HelloCommand()
        plugin.meta = CommandMeta(name="hello")  # 默认 group="general"
        registry.register(plugin)

        assert "general" in registry.list_groups()


# ============================================================
#  CommandPluginRegistry — exists / clear
# ============================================================

class TestCommandPluginRegistryExists:

    def test_exists_true(self):
        """exists() 已注册返回 True"""
        registry = CommandPluginRegistry()
        plugin = HelloCommand()
        plugin.meta = CommandMeta(name="hello")
        registry.register(plugin)

        assert registry.exists("hello") is True

    def test_exists_false(self):
        """exists() 未注册返回 False"""
        registry = CommandPluginRegistry()
        assert registry.exists("nonexistent") is False

    def test_exists_alias(self):
        """exists() 别名也返回 True"""
        registry = CommandPluginRegistry()
        plugin = HelloCommand()
        plugin.meta = CommandMeta(name="hello", aliases=["hi"])
        registry.register(plugin)

        assert registry.exists("hello") is True
        assert registry.exists("hi") is True

    def test_exists_after_unregister(self):
        """unregister 后 exists 返回 False"""
        registry = CommandPluginRegistry()
        plugin = HelloCommand()
        plugin.meta = CommandMeta(name="hello")
        registry.register(plugin)

        registry.unregister("hello")
        assert registry.exists("hello") is False


class TestCommandPluginRegistryClear:

    def test_clear_empties(self):
        """clear() 清空所有注册"""
        registry = CommandPluginRegistry()

        p1 = HelloCommand()
        p1.meta = CommandMeta(name="hello")
        registry.register(p1)

        p2 = GoodbyeCommand()
        p2.meta = CommandMeta(name="goodbye")
        registry.register(p2)

        assert registry.count() == 2
        registry.clear()
        assert registry.count() == 0
        assert registry.list() == []
        assert registry.list_groups() == []

    def test_clear_removes_aliases(self):
        """clear() 清理别名映射"""
        registry = CommandPluginRegistry()
        plugin = HelloCommand()
        plugin.meta = CommandMeta(name="hello", aliases=["hi"])
        registry.register(plugin)

        registry.clear()
        assert registry.exists("hi") is False

    def test_clear_removes_groups(self):
        """clear() 清理分组"""
        registry = CommandPluginRegistry()
        plugin = HelloCommand()
        plugin.meta = CommandMeta(name="hello", group="chat")
        registry.register(plugin)

        registry.clear()
        assert registry.list_groups() == []

    def test_clear_after_clear(self):
        """连续 clear 不崩溃"""
        registry = CommandPluginRegistry()
        registry.clear()
        registry.clear()  # 不应抛异常
        assert registry.count() == 0

    def test_re_register_after_clear(self):
        """clear 后可以重新注册"""
        registry = CommandPluginRegistry()
        plugin = HelloCommand()
        plugin.meta = CommandMeta(name="hello")
        registry.register(plugin)
        registry.clear()

        plugin2 = GoodbyeCommand()
        plugin2.meta = CommandMeta(name="goodbye")
        registry.register(plugin2)
        assert registry.count() == 1
        assert registry.get("goodbye") is plugin2


# ============================================================
#  CommandPluginRegistry — 多实例隔离
# ============================================================

class TestCommandPluginRegistryIsolation:

    def test_multi_instance_isolation(self):
        """多个注册表实例互不影响"""
        r1 = CommandPluginRegistry()
        r2 = CommandPluginRegistry()

        p1 = HelloCommand()
        p1.meta = CommandMeta(name="hello")
        r1.register(p1)

        p2 = GoodbyeCommand()
        p2.meta = CommandMeta(name="goodbye")
        r2.register(p2)

        # r1 有 hello，没有 goodbye
        assert r1.get("hello") is not None
        assert r1.get("goodbye") is None
        assert r1.count() == 1

        # r2 有 goodbye，没有 hello
        assert r2.get("goodbye") is not None
        assert r2.get("hello") is None
        assert r2.count() == 1

    def test_clear_one_does_not_affect_other(self):
        """clear 一个实例不影响另一个"""
        r1 = CommandPluginRegistry()
        r2 = CommandPluginRegistry()

        p1 = HelloCommand()
        p1.meta = CommandMeta(name="hello")
        r1.register(p1)

        p2 = GoodbyeCommand()
        p2.meta = CommandMeta(name="goodbye")
        r2.register(p2)

        r1.clear()

        assert r1.count() == 0
        assert r2.count() == 1
        assert r2.get("goodbye") is not None

    def test_unregister_one_does_not_affect_other(self):
        """unregister 一个实例不影响另一个"""
        r1 = CommandPluginRegistry()
        r2 = CommandPluginRegistry()

        p1 = HelloCommand()
        p1.meta = CommandMeta(name="hello")
        r1.register(p1)

        p2 = HelloCommand()
        p2.meta = CommandMeta(name="hello")
        r2.register(p2)

        r1.unregister("hello")

        assert r1.count() == 0
        assert r2.count() == 1
        assert r2.get("hello") is not None


# ============================================================
#  CommandPluginRegistry — 边界场景
# ============================================================

class TestCommandPluginRegistryEdgeCases:

    def test_register_same_plugin_twice(self):
        """重复注册同一个插件实例（不同名称）"""
        registry = CommandPluginRegistry()

        plugin = HelloCommand()
        plugin.meta = CommandMeta(name="hello")
        registry.register(plugin)

        # 相同实例以不同名称注册
        plugin2 = plugin
        plugin2.meta = CommandMeta(name="hello_v2")
        registry.register(plugin2)

        assert registry.count() == 2
        assert registry.get("hello") is plugin
        assert registry.get("hello_v2") is plugin

    def test_register_multiple_with_same_alias(self):
        """别名冲突: 新注册覆盖旧别名映射"""
        registry = CommandPluginRegistry()

        p1 = HelloCommand()
        p1.meta = CommandMeta(name="hello", aliases=["h"])
        registry.register(p1)
        assert registry.get("h") is p1

        p2 = GoodbyeCommand()
        p2.meta = CommandMeta(name="goodbye", aliases=["h"])
        registry.register(p2)

        # 别名 "h" 现在指向 goodbye
        assert registry.get("h") is p2

    def test_register_plugin_with_empty_aliases(self):
        """空别名列表正常注册"""
        registry = CommandPluginRegistry()
        plugin = HelloCommand()
        plugin.meta = CommandMeta(name="hello", aliases=[])
        registry.register(plugin)

        assert registry.get("hello") is plugin
        assert registry.count() == 1

    def test_list_after_mixed_operations(self):
        """混合操作后 list 状态正确"""
        registry = CommandPluginRegistry()

        p1 = HelloCommand()
        p1.meta = CommandMeta(name="hello", group="chat")
        registry.register(p1)

        p2 = GoodbyeCommand()
        p2.meta = CommandMeta(name="goodbye", group="admin")
        registry.register(p2)

        p3 = PingCommand()
        p3.meta = CommandMeta(name="ping", group="chat")
        registry.register(p3)

        registry.unregister("hello")

        names_all = {p.meta.name for p in registry.list()}
        assert names_all == {"goodbye", "ping"}

        chat_names = {p.meta.name for p in registry.list("chat")}
        assert chat_names == {"ping"}

        admin_names = {p.meta.name for p in registry.list("admin")}
        assert admin_names == {"goodbye"}


# ============================================================
#  command_plugin 装饰器
# ============================================================

class TestCommandPluginDecorator:

    def setup_method(self):
        """每个测试前清空全局注册表"""
        get_plugin_registry().clear()

    def test_decorator_sets_meta(self):
        """装饰器正确设置 meta 元数据"""
        @command_plugin(name="greet", aliases=["g"], group="chat",
                        description="A greeting", usage="[name]", hidden=True)
        class GreetCommand(CommandPlugin):
            def execute(self, ctx) -> bool:
                return True

        assert GreetCommand.meta.name == "greet"
        assert GreetCommand.meta.aliases == ["g"]
        assert GreetCommand.meta.group == "chat"
        assert GreetCommand.meta.description == "A greeting"
        assert GreetCommand.meta.usage == "[name]"
        assert GreetCommand.meta.hidden is True

    def test_decorator_register_to_global(self):
        """装饰器自动注册到全局注册表"""
        @command_plugin(name="mycommand", description="Test command")
        class MyCommand(CommandPlugin):
            def execute(self, ctx) -> bool:
                return True

        registry = get_plugin_registry()
        # "mycommand" 末尾匹配 "command" → 自动去除后缀 → "my"
        assert registry.exists("my")
        plugin = registry.get("my")
        assert isinstance(plugin, MyCommand)
        assert plugin.meta.name == "my"

    def test_decorator_default_name_removes_command_suffix(self):
        """默认名称去掉 'command' 后缀"""
        @command_plugin(description="Test")
        class TestCommand(CommandPlugin):
            def execute(self, ctx) -> bool:
                return True

        assert TestCommand.meta.name == "test"  # "testcommand" → "test"

    def test_decorator_default_name_no_suffix(self):
        """类名不以 command 结尾时使用全小写"""
        @command_plugin(description="Ping")
        class PingPlugin(CommandPlugin):
            def execute(self, ctx) -> bool:
                return True

        assert PingPlugin.meta.name == "pingplugin"

    def test_decorator_explicit_name_overrides_auto(self):
        """显式指定 name 时优先使用指定值（但仍会去除末尾的 'command' 后缀）"""
        @command_plugin(name="test_command", description="Test")
        class TestCommand(CommandPlugin):
            def execute(self, ctx) -> bool:
                return True

        # "test_command" → 去除 "command" 后缀 → "test_"
        assert TestCommand.meta.name == "test_"

    def test_decorator_execute_works(self):
        """装饰后的类实例 execute 正常执行"""
        @command_plugin(name="runner")
        class RunnerCommand(CommandPlugin):
            def execute(self, ctx) -> bool:
                return True

        registry = get_plugin_registry()
        plugin = registry.get("runner")
        assert plugin is not None
        assert plugin.execute(None) is True

    def test_decorator_non_commandplugin_raises(self):
        """不继承 CommandPlugin 时抛 TypeError"""
        with pytest.raises(TypeError, match="必须继承 CommandPlugin"):
            @command_plugin(name="invalid")
            class NotAPlugin:
                def execute(self, ctx) -> bool:
                    return True

    def test_decorator_without_arguments(self):
        """@command_plugin() 无参数时使用默认值"""
        @command_plugin()
        class HelloCmd(CommandPlugin):
            def execute(self, ctx) -> bool:
                return True

        # HelloCmd → "hellocmd"（不以 "command" 结尾，不去除）
        assert HelloCmd.meta.name == "hellocmd"
        assert HelloCmd.meta.aliases == []
        assert HelloCmd.meta.group == "general"
        assert HelloCmd.meta.description == ""
        assert HelloCmd.meta.usage == ""
        assert HelloCmd.meta.hidden is False

    def test_decorator_partial_arguments(self):
        """只传部分参数"""
        @command_plugin(description="Partial test")
        class PartialCmd(CommandPlugin):
            def execute(self, ctx) -> bool:
                return True

        # PartialCmd → "partialcmd"（不以 "command" 结尾，不去除）
        assert PartialCmd.meta.name == "partialcmd"
        assert PartialCmd.meta.description == "Partial test"
        # 其余使用默认值
        assert PartialCmd.meta.aliases == []
        assert PartialCmd.meta.group == "general"

    def test_decorator_register_with_aliases(self):
        """别名也在全局注册表中可查"""
        @command_plugin(name="main", aliases=["m", "primary"])
        class MainCommand(CommandPlugin):
            def execute(self, ctx) -> bool:
                return True

        registry = get_plugin_registry()
        assert registry.exists("main")
        assert registry.get("m") is registry.get("main")
        assert registry.get("primary") is registry.get("main")

    def test_decorator_overwrite_existing(self):
        """同名装饰器覆盖已有的注册"""
        @command_plugin(name="overwrite_me")
        class FirstVersion(CommandPlugin):
            def execute(self, ctx) -> bool:
                return False

        @command_plugin(name="overwrite_me")
        class SecondVersion(CommandPlugin):
            def execute(self, ctx) -> bool:
                return True

        registry = get_plugin_registry()
        plugin = registry.get("overwrite_me")
        assert isinstance(plugin, SecondVersion)
        assert plugin.execute(None) is True


# ============================================================
#  get_plugin_registry()
# ============================================================

class TestGetPluginRegistry:

    def setup_method(self):
        """每个测试前清空全局注册表"""
        registry = get_plugin_registry()
        registry.clear()

    def test_returns_global_registry(self):
        """返回全局注册表实例"""
        registry = get_plugin_registry()
        assert isinstance(registry, CommandPluginRegistry)

    def test_returns_singleton(self):
        """多次调用返回同一个实例"""
        r1 = get_plugin_registry()
        r2 = get_plugin_registry()
        assert r1 is r2

    def test_global_registry_initial_empty(self):
        """初始全局注册表为空"""
        registry = get_plugin_registry()
        assert registry.count() == 0

    def test_decorator_and_get_plugin_registry_consistency(self):
        """装饰器注册后在全局注册表可查"""
        @command_plugin(name="consistency_test")
        class ConsistencyCmd(CommandPlugin):
            def execute(self, ctx) -> bool:
                return True

        registry = get_plugin_registry()
        assert registry.exists("consistency_test")
        assert isinstance(registry.get("consistency_test"), ConsistencyCmd)


# ============================================================
#  生命周期钩子
# ============================================================

class TestLifecycleHooks:

    def setup_method(self):
        get_plugin_registry().clear()

    def test_on_register_called(self):
        """register 时调用 on_register"""
        registry = CommandPluginRegistry()

        class HookPlugin(CommandPlugin):
            def __init__(self):
                self.register_called = False

            def execute(self, ctx) -> bool:
                return True

            def on_register(self):
                self.register_called = True

        plugin = HookPlugin()
        plugin.meta = CommandMeta(name="hook_test")
        registry.register(plugin)
        assert plugin.register_called is True

    def test_on_unregister_called(self):
        """unregister 时调用 on_unregister"""
        registry = CommandPluginRegistry()

        class HookPlugin(CommandPlugin):
            def __init__(self):
                self.unregister_called = False

            def execute(self, ctx) -> bool:
                return True

            def on_unregister(self):
                self.unregister_called = True

        plugin = HookPlugin()
        plugin.meta = CommandMeta(name="hook_test2")
        registry.register(plugin)
        registry.unregister("hook_test2")
        assert plugin.unregister_called is True

    def test_on_register_not_called_if_not_overridden(self):
        """默认 on_register 空实现，不调用自定义逻辑"""
        registry = CommandPluginRegistry()
        plugin = HelloCommand()
        plugin.meta = CommandMeta(name="no_hook")
        # 不抛异常即可
        registry.register(plugin)
        assert True

    def test_clear_does_not_call_on_unregister(self):
        """clear() 不触发 on_unregister（直接清空）"""
        registry = CommandPluginRegistry()

        class HookPlugin(CommandPlugin):
            def __init__(self):
                self.unregister_called = False

            def execute(self, ctx) -> bool:
                return True

            def on_unregister(self):
                self.unregister_called = True

        plugin = HookPlugin()
        plugin.meta = CommandMeta(name="hook_clear")
        registry.register(plugin)
        registry.clear()
        # clear 直接清空字典，不会逐个调用 on_unregister
        assert plugin.unregister_called is False


# ============================================================
#  装饰器 + 全局注册表隔离
# ============================================================

class TestGlobalRegistryIsolation:

    def setup_method(self):
        get_plugin_registry().clear()

    def teardown_method(self):
        get_plugin_registry().clear()

    def test_local_registry_independent_from_global(self):
        """独立注册表不受全局注册表影响"""
        @command_plugin(name="global_cmd")
        class GlobalCmd(CommandPlugin):
            def execute(self, ctx) -> bool:
                return True

        local = CommandPluginRegistry()
        assert local.count() == 0
        assert local.exists("global_cmd") is False

    def test_global_clear_does_not_affect_local(self):
        """clear 全局注册表不影响独立实例"""
        local = CommandPluginRegistry()
        plugin = HelloCommand()
        plugin.meta = CommandMeta(name="local_cmd")
        local.register(plugin)

        get_plugin_registry().clear()
        assert local.count() == 1
        assert local.get("local_cmd") is plugin
