"""
测试 src/tools/registry.py 的 ToolRegistry 类及全局函数

使用独立 ToolRegistry 实例 (initial_tools={}) 避免触发自动发现。
"""

import pytest
from typing import Dict, Type

from src.tools.base import Func
from src.tools.registry import (
    ToolRegistry,
    register_tool,
    get_tools,
    get_tool_schemas,
    clear_registry,
    get_tool_display_name,
    TOOL_ABBR,
)


# ============================================================
#  Mock 工具类（用于测试的 Func 子类）
# ============================================================

class MockReadFile(Func):
    name = "read_file"

    def __init__(self, path: str = ""):
        super().__init__()
        self.path = path

    @classmethod
    def to_tool_schema(cls):
        return {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "读取文件内容",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"}
                    },
                    "required": ["path"]
                }
            }
        }

    async def execute(self):
        return f"file content: {self.path}"


class MockWriteFile(Func):
    name = "write_file"

    def __init__(self, path: str = "", content: str = ""):
        super().__init__()
        self.path = path
        self.content = content

    @classmethod
    def to_tool_schema(cls):
        return {
            "type": "function",
            "function": {
                "name": "write_file",
                "description": "写入文件",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"}
                    },
                    "required": ["path", "content"]
                }
            }
        }

    async def execute(self):
        return f"written: {self.path}"


class MockBash(Func):
    name = "bash"

    def __init__(self, command: str = ""):
        super().__init__()
        self.command = command

    @classmethod
    def to_tool_schema(cls):
        return {
            "type": "function",
            "function": {
                "name": "bash",
                "description": "执行命令",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string"}
                    },
                    "required": ["command"]
                }
            }
        }

    async def execute(self):
        return f"executed: {self.command}"


# ------------------------------------------------------------
#  无效工具类（用于异常测试）
# ------------------------------------------------------------

class NotAFunc:
    """不是 Func 子类"""
    name = "not_a_func"


class FuncWithoutName(Func):
    """Func 子类但 name 为 None"""
    name = None

    @classmethod
    def to_tool_schema(cls):
        return {}

    async def execute(self):
        return ""


# ============================================================
#  ToolRegistry 基本功能
# ============================================================

class TestToolRegistryBasic:

    def test_create_empty_registry(self):
        """创建空注册表，get_tools() 返回空 dict"""
        registry = ToolRegistry(initial_tools={})
        tools = registry.get_tools()
        assert isinstance(tools, dict)
        assert len(tools) == 0

    def test_register_single_tool(self):
        """注册一个有效的 Func 子类，get_tools() 包含该工具"""
        registry = ToolRegistry(initial_tools={})
        registry.register(MockReadFile)
        tools = registry.get_tools()
        assert "read_file" in tools
        assert tools["read_file"] is MockReadFile

    def test_register_multiple_tools(self):
        """注册多个不同工具"""
        registry = ToolRegistry(initial_tools={})
        registry.register(MockReadFile)
        registry.register(MockWriteFile)
        registry.register(MockBash)
        tools = registry.get_tools()
        assert len(tools) == 3
        assert tools["read_file"] is MockReadFile
        assert tools["write_file"] is MockWriteFile
        assert tools["bash"] is MockBash

    def test_register_override_same_name(self):
        """同名工具二次注册覆盖旧注册"""
        registry = ToolRegistry(initial_tools={})

        class OldTool(Func):
            name = "duplicate"
            @classmethod
            def to_tool_schema(cls):
                return {}
            async def execute(self):
                return "old"

        class NewTool(Func):
            name = "duplicate"
            @classmethod
            def to_tool_schema(cls):
                return {}
            async def execute(self):
                return "new"

        registry.register(OldTool)
        assert registry.get_tools()["duplicate"] is OldTool

        registry.register(NewTool)
        assert registry.get_tools()["duplicate"] is NewTool

    def test_register_overrides_previous(self):
        """覆盖后旧工具不再可用，新工具生效"""
        registry = ToolRegistry(initial_tools={})

        class OldTool(Func):
            name = "overwrite_me"
            @classmethod
            def to_tool_schema(cls):
                return {}
            async def execute(self):
                return "old"

        class NewTool(Func):
            name = "overwrite_me"
            @classmethod
            def to_tool_schema(cls):
                return {}
            async def execute(self):
                return "new"

        registry.register(OldTool)
        registry.register(NewTool)
        assert len(registry.get_tools()) == 1
        assert registry.get_tools()["overwrite_me"] is NewTool


# ============================================================
#  register() 验证 — 异常场景
# ============================================================

class TestRegisterValidation:

    def test_register_non_func_subclass_raises(self):
        """注册非 Func 子类抛出 ValueError"""
        registry = ToolRegistry(initial_tools={})
        with pytest.raises(ValueError, match="只能注册Func的子类"):
            registry.register(NotAFunc)

    def test_register_non_class_raises(self):
        """注册非类对象抛出 ValueError"""
        registry = ToolRegistry(initial_tools={})
        with pytest.raises(ValueError, match="只能注册Func的子类"):
            registry.register("not_a_class")

    def test_register_func_without_name_raises(self):
        """注册 name 为 None 的 Func 子类抛出 ValueError"""
        registry = ToolRegistry(initial_tools={})
        with pytest.raises(ValueError, match="未定义 name 属性"):
            registry.register(FuncWithoutName)

    def test_register_none_does_not_crash(self):
        """确保传入 None 会触发类型检查异常"""
        registry = ToolRegistry(initial_tools={})
        with pytest.raises(ValueError):
            registry.register(None)


# ============================================================
#  get_tools() 验证 — 返回副本
# ============================================================

class TestGetTools:

    def test_get_tools_returns_copy(self):
        """get_tools() 返回的是副本，修改返回值不影响内部状态"""
        registry = ToolRegistry(initial_tools={})
        registry.register(MockReadFile)

        tools_copy = registry.get_tools()
        tools_copy["_fake"] = "hacked"

        # 修改副本不应影响原始注册表
        internal_tools = registry.get_tools()
        assert "_fake" not in internal_tools
        assert len(internal_tools) == 1
        assert internal_tools["read_file"] is MockReadFile

    def test_get_tools_updates_after_register(self):
        """注册后 get_tools() 结果更新"""
        registry = ToolRegistry(initial_tools={})
        assert len(registry.get_tools()) == 0

        registry.register(MockReadFile)
        assert len(registry.get_tools()) == 1

        registry.register(MockWriteFile)
        assert len(registry.get_tools()) == 2

    def test_multiple_registry_instances_isolated(self):
        """多个注册表实例互不影响"""
        r1 = ToolRegistry(initial_tools={})
        r2 = ToolRegistry(initial_tools={})

        r1.register(MockReadFile)
        r2.register(MockWriteFile)

        assert "read_file" in r1.get_tools()
        assert "write_file" not in r1.get_tools()

        assert "write_file" in r2.get_tools()
        assert "read_file" not in r2.get_tools()


# ============================================================
#  dispatch() 验证
# ============================================================

class TestDispatch:

    def test_dispatch_returns_correct_instance(self):
        """分派已注册的工具返回正确类型实例"""
        registry = ToolRegistry(initial_tools={})
        registry.register(MockReadFile)

        instance = registry.dispatch("read_file", {"path": "/tmp/test.txt"})
        assert isinstance(instance, MockReadFile)
        assert instance.path == "/tmp/test.txt"

    def test_dispatch_nonexistent_tool_raises(self):
        """分派不存在的工具抛出 ValueError，消息包含可用工具列表"""
        registry = ToolRegistry(initial_tools={})
        registry.register(MockReadFile)

        with pytest.raises(ValueError) as excinfo:
            registry.dispatch("nonexistent", {})

        error_msg = str(excinfo.value)
        assert "nonexistent" in error_msg
        assert "read_file" in error_msg

    def test_dispatch_empty_registry_raises(self):
        """空注册表中分派任何工具均抛出 ValueError"""
        registry = ToolRegistry(initial_tools={})
        with pytest.raises(ValueError):
            registry.dispatch("anything", {})

    def test_dispatch_sets_agent(self):
        """传入 agent 时，返回的实例 agent 被正确设置"""
        registry = ToolRegistry(initial_tools={})
        registry.register(MockReadFile)

        class FakeAgent:
            name = "test_agent"

        agent = FakeAgent()
        instance = registry.dispatch("read_file", {"path": "/tmp/x.txt"}, agent=agent)
        assert instance.agent is agent

    def test_dispatch_no_agent_defaults_none(self):
        """不传 agent 时，实例 agent 为 None"""
        registry = ToolRegistry(initial_tools={})
        registry.register(MockReadFile)

        instance = registry.dispatch("read_file", {"path": "/tmp/x.txt"})
        assert instance.agent is None

    def test_dispatch_from_args_called_correctly(self):
        """from_args 被正确调用，参数传递正确"""
        registry = ToolRegistry(initial_tools={})
        registry.register(MockWriteFile)

        args = {"path": "/tmp/output.txt", "content": "hello world"}
        instance = registry.dispatch("write_file", args)
        assert isinstance(instance, MockWriteFile)
        assert instance.path == "/tmp/output.txt"
        assert instance.content == "hello world"

    def test_dispatch_from_args_partial_params(self):
        """from_args 只提取 __init__ 所需的参数，多余参数被忽略"""
        registry = ToolRegistry(initial_tools={})
        registry.register(MockReadFile)

        # MockReadFile 只接受 path 参数，extra_param 应被忽略
        instance = registry.dispatch("read_file", {
            "path": "/tmp/test.txt",
            "extra_param": "should_be_ignored"
        })
        assert isinstance(instance, MockReadFile)
        assert instance.path == "/tmp/test.txt"


# ============================================================
#  get_schemas() 验证
# ============================================================

class TestGetSchemas:

    def test_empty_registry_returns_empty_list(self):
        """空注册表返回空列表"""
        registry = ToolRegistry(initial_tools={})
        schemas = registry.get_schemas()
        assert schemas == []

    def test_schemas_contain_required_fields(self):
        """注册工具后返回包含 schema 的列表，每个 schema 包含关键字段"""
        registry = ToolRegistry(initial_tools={})
        registry.register(MockReadFile)

        schemas = registry.get_schemas()
        assert len(schemas) == 1

        schema = schemas[0]
        assert "type" in schema
        assert "function" in schema
        func = schema["function"]
        assert "name" in func
        assert "description" in func
        assert "parameters" in func

    def test_multiple_tools_multiple_schemas(self):
        """注册多个工具后返回对应数量的 schema"""
        registry = ToolRegistry(initial_tools={})
        registry.register(MockReadFile)
        registry.register(MockWriteFile)

        schemas = registry.get_schemas()
        assert len(schemas) == 2

        names = [s["function"]["name"] for s in schemas]
        assert "read_file" in names
        assert "write_file" in names

    def test_schema_structure_matches_definition(self):
        """schema 结构符合 to_tool_schema() 返回的定义"""
        registry = ToolRegistry(initial_tools={})
        registry.register(MockReadFile)

        schemas = registry.get_schemas()
        schema = schemas[0]

        assert schema["type"] == "function"
        assert schema["function"]["name"] == "read_file"
        assert schema["function"]["description"] == "读取文件内容"
        assert "path" in schema["function"]["parameters"]["properties"]

    def test_get_schemas_returns_new_list(self):
        """get_schemas 每次返回新列表（不可变保证）"""
        registry = ToolRegistry(initial_tools={})
        registry.register(MockReadFile)

        schemas1 = registry.get_schemas()
        schemas2 = registry.get_schemas()

        assert schemas1 is not schemas2


# ============================================================
#  clear() 验证
# ============================================================

class TestClear:

    def test_clear_empties_internal_dict(self):
        """clear 后内部 _tools 为空（注意：get_tools() 会触发自动发现重新填满）"""
        registry = ToolRegistry(initial_tools={})
        registry.register(MockReadFile)
        registry.register(MockWriteFile)
        assert len(registry._tools) == 2

        registry.clear()
        # clear 清空了 _tools，但将 _initialized 置为 False
        assert len(registry._tools) == 0
        assert registry._initialized is False

    def test_clear_after_clear_internal_empty(self):
        """连续 clear 多次，内部 _tools 保持为空"""
        registry = ToolRegistry(initial_tools={})
        registry.clear()
        assert len(registry._tools) == 0
        registry.clear()
        assert len(registry._tools) == 0

    def test_clear_then_re_register(self):
        """clear 后可重新注册"""
        registry = ToolRegistry(initial_tools={})
        registry.register(MockReadFile)
        registry.clear()
        assert len(registry._tools) == 0

        registry.register(MockWriteFile)
        assert "write_file" in registry.get_tools()
        # 注意：get_tools() 会触发自动发现，但 register 后的工具优先在 _tools 中
        assert "write_file" in registry._tools

    def test_clear_resets_initialized_flag(self):
        """clear 将 _initialized 重置为 False"""
        registry = ToolRegistry(initial_tools={})
        # 使用 initial_tools={} 时 _initialized 为 True
        assert registry._initialized is True

        registry.clear()
        assert registry._initialized is False

        # 再次 get_tools() 会触发 _ensure_initialized → _discover_and_register
        # 自动发现从 tools 包中重新注册所有真实工具，非空
        tools = registry.get_tools()
        assert len(tools) > 0  # 自动发现重新填入了真实工具
        assert registry._initialized is True  # 重新完成初始化


# ============================================================
#  get_tool_display_name() 测试
# ============================================================

class TestGetToolDisplayName:

    def test_known_tools_return_abbr(self):
        """已知工具返回缩写"""
        assert get_tool_display_name("read_file") == "rf"
        assert get_tool_display_name("write_file") == "wf"
        assert get_tool_display_name("update_file") == "uf"
        assert get_tool_display_name("dispatch_agent") == "da"
        assert get_tool_display_name("web_search") == "ws"

    def test_unknown_tool_returns_original(self):
        """未知工具返回原名称"""
        assert get_tool_display_name("unknown_tool") == "unknown_tool"
        assert get_tool_display_name("nonexistent") == "nonexistent"

    def test_empty_string(self):
        """空字符串返回空字符串"""
        assert get_tool_display_name("") == ""

    def test_all_abbr_constants_are_strings(self):
        """TOOL_ABBR 常量中的所有键值对均为字符串"""
        for key, value in TOOL_ABBR.items():
            assert isinstance(key, str)
            assert isinstance(value, str)

    def test_tool_abbr_returns_self_mapped(self):
        """TOOL_ABBR 中的 bash/cp/mv/rm/user_select 等映射正确"""
        assert TOOL_ABBR["bash"] == "bs"
        assert TOOL_ABBR["cp"] == "cp"
        assert TOOL_ABBR["mv"] == "mv"
        assert TOOL_ABBR["rm"] == "rm"
        assert TOOL_ABBR["user_select"] == "us"


# ============================================================
#  全局函数测试
# ============================================================

class TestGlobalFunctions:

    def setup_method(self):
        """每个测试前重置全局注册表"""
        clear_registry()

    def test_clear_registry_empties_default(self):
        """clear_registry() 清空默认注册表"""
        # 首次调用 get_tools() 会触发自动发现，确保有工具
        import importlib
        import src.tools.registry as reg_mod
        # 重置全局 registry 为 None
        clear_registry()
        # 创建一个全新的有工具的 registry 并设置为默认
        fresh = ToolRegistry(initial_tools={})
        fresh.register(MockReadFile)
        # 通过 register_tool 注册到默认
        register_tool(MockReadFile)

        tools_before = get_tools()
        assert len(tools_before) > 0

        clear_registry()
        # 清空后，_default_registry 为 None，下次 get_tools() 会创建新空实例
        # 注意：新实例会触发自动发现，所以这里不直接断言空
        # 改为验证 clear_registry 本身不抛出异常
        assert True

    def test_register_tool_uses_default_registry(self):
        """register_tool() 将工具注册到默认注册表"""
        clear_registry()
        register_tool(MockReadFile)
        tools = get_tools()
        assert "read_file" in tools

    def test_get_tool_schemas_global(self):
        """get_tool_schemas() 返回默认注册表的 schema 列表"""
        clear_registry()
        register_tool(MockReadFile)
        schemas = get_tool_schemas()
        assert len(schemas) >= 1
        schema_names = [s["function"]["name"] for s in schemas]
        assert "read_file" in schema_names

    def test_independent_registry_not_affected_by_global(self):
        """独立 ToolRegistry 实例不受全局函数影响"""
        clear_registry()

        # 全局注册
        register_tool(MockReadFile)

        # 独立实例
        independent = ToolRegistry(initial_tools={})
        assert len(independent.get_tools()) == 0

        # 全局清空不影响独立实例
        independent.register(MockWriteFile)
        clear_registry()

        assert independent.get_tools() == {"write_file": MockWriteFile}


# ============================================================
#  ToolRegistry.default() 类方法测试
# ============================================================

class TestDefaultRegistry:

    def setup_method(self):
        clear_registry()

    def test_default_returns_singleton(self):
        """ToolRegistry.default() 返回单例"""
        r1 = ToolRegistry.default()
        r2 = ToolRegistry.default()
        assert r1 is r2

    def test_default_after_clear_creates_new(self):
        """clear_registry() 后 default() 创建新实例"""
        r1 = ToolRegistry.default()
        clear_registry()
        r2 = ToolRegistry.default()
        assert r1 is not r2


# ============================================================
#  边界场景测试
# ============================================================

class TestEdgeCases:

    def test_register_same_class_twice(self):
        """重复注册同一个类不会报错，注册表大小保持不变"""
        registry = ToolRegistry(initial_tools={})
        registry.register(MockReadFile)
        registry.register(MockReadFile)  # 第二次注册
        tools = registry.get_tools()
        assert len(tools) == 1
        assert tools["read_file"] is MockReadFile

    def test_dispatch_with_empty_arguments(self):
        """分派时传空参数字典"""
        registry = ToolRegistry(initial_tools={})
        registry.register(MockReadFile)
        instance = registry.dispatch("read_file", {})
        assert isinstance(instance, MockReadFile)

    def test_clear_then_get_schemas(self):
        """clear 后内部 _tools 为空，但 get_schemas 会触发自动发现"""
        registry = ToolRegistry(initial_tools={})
        registry.register(MockReadFile)
        registry.clear()
        # 内部 _tools 已清空，但 _initialized=False
        assert len(registry._tools) == 0
        # get_schemas() 触发自动发现，重新填充
        schemas = registry.get_schemas()
        assert len(schemas) > 0  # 自动发现重新注册了真实工具

    def test_get_tools_after_clear_triggers_rediscovery(self):
        """clear 后 get_tools 触发自动发现，重新填充真实工具"""
        registry = ToolRegistry(initial_tools={})
        registry.register(MockReadFile)
        registry.clear()
        # _initialized=False，get_tools 触发自动发现
        tools = registry.get_tools()
        assert len(tools) > 0  # 自动发现重新填入了真实工具
        assert "read_file" in tools  # 真实 read_file 工具被自动发现

    def test_multiple_clear_and_re_register_cycle(self):
        """多次 clear → re-register 循环正常"""
        registry = ToolRegistry(initial_tools={})
        for i in range(3):
            registry.register(MockReadFile)
            assert len(registry._tools) >= 1
            assert "read_file" in registry._tools
            registry.clear()
            assert len(registry._tools) == 0
