"""测试 src.tools.base：Func 基类、ToolMetadata 数据类、元数据装饰器。

测试策略
--------
- 在测试文件内部定义具体的 Func 子类（mock 类）作为测试夹具
- 每个测试类关注一个概念，每个测试方法关注单一行为
- 遵循 Arrange/Act/Assert 模式
- 所有测试可独立运行，不依赖外部资源或网络
"""

import pytest
from src.tools.base import (
    Func,
    ToolMetadata,
    tool_metadata,
    get_tool_metadata,
)


# ═══════════════════════════════════════════════════════════════════════════
# 测试夹具：具体的 Func 子类
# ═══════════════════════════════════════════════════════════════════════════

class _ConcreteTool(Func):
    """最基本的 Func 子类，用于测试基类非抽象行为。"""
    name = "concrete_tool"

    @classmethod
    def to_tool_schema(cls):
        return {"name": cls.name, "parameters": {"type": "object", "properties": {}}}

    async def execute(self) -> str:
        return "executed"


class _FileTool(Func):
    """带 __init__ 参数的 Func 子类，用于测试 from_args。"""
    name = "file_tool"

    def __init__(self, file_path: str, content: str = ""):
        super().__init__()
        self.file_path = file_path
        self.content = content

    @classmethod
    def to_tool_schema(cls):
        return {"name": cls.name, "parameters": {"type": "object", "properties": {}}}

    async def execute(self) -> str:
        return f"read {self.file_path}"


@tool_metadata(parallel_safe=True, requires_network=True, category="io", priority=50, description="测试工具")
class _DecoratedTool(Func):
    """使用 tool_metadata 装饰的 Func 子类。"""
    name = "decorated_tool"

    @classmethod
    def to_tool_schema(cls):
        return {"name": cls.name, "parameters": {"type": "object", "properties": {}}}

    async def execute(self) -> str:
        return "decorated"


@tool_metadata(
    parallel_safe=True,
    requires_network=False,
    requires_terminal=True,
    timeout_estimate=30.0,
    category="interactive",
    priority=10,
    description="交互式测试工具",
)
class _InteractiveTool(Func):
    """全字段自定义元数据的 Func 子类。"""
    name = "interactive_tool"

    @classmethod
    def to_tool_schema(cls):
        return {"name": cls.name, "parameters": {"type": "object", "properties": {}}}

    async def execute(self) -> str:
        return "interactive"


@tool_metadata(
    parallel_safe=False,
    requires_network=False,
    requires_terminal=False,
    category="io",
    tool_category="read",
    description="读取类测试工具",
)
class _ReadTool(Func):
    """使用 tool_metadata 设置 tool_category="read" 的 Func 子类。"""
    name = "read_tool"

    @classmethod
    def to_tool_schema(cls):
        return {"name": cls.name, "parameters": {"type": "object", "properties": {}}}

    async def execute(self) -> str:
        return "read"


# ═══════════════════════════════════════════════════════════════════════════
# 1. Func 基类 — 实例化限制
# ═══════════════════════════════════════════════════════════════════════════

class TestFuncInstantiation:
    """Func 是抽象基类，不能直接实例化。"""

    def test_cannot_instantiate_directly(self):
        """Func 有抽象方法，直接实例化应抛出 TypeError。"""
        with pytest.raises(TypeError):
            Func()

    def test_concrete_subclass_can_instantiate(self):
        """具体的 Func 子类可以正常实例化。"""
        tool = _ConcreteTool()
        assert isinstance(tool, Func)


# ═══════════════════════════════════════════════════════════════════════════
# 2. set_agent
# ═══════════════════════════════════════════════════════════════════════════

class TestSetAgent:
    """set_agent() 正确设置 agent 属性。"""

    def test_set_agent(self):
        tool = _ConcreteTool()
        assert tool.agent is None

        tool.set_agent("test_agent")
        assert tool.agent == "test_agent"

    def test_set_agent_overwrites(self):
        tool = _ConcreteTool()
        tool.set_agent("agent_a")
        tool.set_agent("agent_b")
        assert tool.agent == "agent_b"


# ═══════════════════════════════════════════════════════════════════════════
# 3. _sanitize_display
# ═══════════════════════════════════════════════════════════════════════════

class TestSanitizeDisplay:
    """_sanitize_display() 转义 \\r 和 \\n。"""

    def test_newline_replaced(self):
        result = Func._sanitize_display("line1\nline2")
        assert result == "line1/nline2"

    def test_carriage_return_replaced(self):
        result = Func._sanitize_display("line1\rline2")
        assert result == "line1/rline2"

    def test_both_replaced(self):
        result = Func._sanitize_display("line1\r\nline2")
        assert result == "line1/r/nline2"

    def test_no_special_chars(self):
        result = Func._sanitize_display("normal text")
        assert result == "normal text"

    def test_empty_string(self):
        assert Func._sanitize_display("") == ""

    def test_multiple_occurrences(self):
        result = Func._sanitize_display("a\nb\nc")
        assert result == "a/nb/nc"


# ═══════════════════════════════════════════════════════════════════════════
# 4. from_args
# ═══════════════════════════════════════════════════════════════════════════

class TestFromArgs:
    """from_args() 从字典创建实例。"""

    def test_required_param_only(self):
        """仅提供必需参数。"""
        tool = _FileTool.from_args({"file_path": "/tmp/test.txt"})
        assert isinstance(tool, _FileTool)
        assert tool.file_path == "/tmp/test.txt"
        assert tool.content == ""

    def test_with_optional_param(self):
        """同时提供必需和可选参数。"""
        tool = _FileTool.from_args({"file_path": "/tmp/test.txt", "content": "hello"})
        assert tool.file_path == "/tmp/test.txt"
        assert tool.content == "hello"

    def test_extra_params_ignored(self):
        """额外参数被忽略。"""
        tool = _FileTool.from_args({
            "file_path": "/tmp/test.txt",
            "content": "hello",
            "extra_param": "ignored",
            "another_extra": 42,
        })
        assert tool.file_path == "/tmp/test.txt"
        assert tool.content == "hello"
        # 如果 from_args 传了 extra_param 给 __init__，会 TypeError
        # 所以这个测试同时验证了不会报错 + 结果正确

    def test_missing_required_param_raises(self):
        """缺少必需参数时抛出 ValueError。"""
        with pytest.raises(ValueError, match="缺少必需参数"):
            _FileTool.from_args({})

    def test_empty_args_dict_with_defaults(self):
        """无必需参数但有默认值的情况。"""
        class _DefaultTool(Func):
            name = "default_tool"
            def __init__(self, x: str = "default_x"):
                super().__init__()
                self.x = x
            @classmethod
            def to_tool_schema(cls):
                return {}
            async def execute(self) -> str:
                return self.x

        tool = _DefaultTool.from_args({})
        assert tool.x == "default_x"

    def test_from_args_on_basic_tool(self):
        """没有 __init__ 参数的 Func 子类，from_args 返回实例。"""
        tool = _ConcreteTool.from_args({"unused": "ignored"})
        assert isinstance(tool, _ConcreteTool)


# ═══════════════════════════════════════════════════════════════════════════
# 6. display / web_display
# ═══════════════════════════════════════════════════════════════════════════

class TestDisplay:
    """display() 默认委托给 execute()。"""

    async def test_display_delegates_to_execute(self):
        tool = _ConcreteTool()
        result = await tool.display()
        assert result == "executed"

    async def test_web_display_delegates_to_display(self):
        tool = _ConcreteTool()
        result = await tool.web_display()
        assert result == "executed"


# ═══════════════════════════════════════════════════════════════════════════
# 6. get_metadata（未设置元数据时）
# ═══════════════════════════════════════════════════════════════════════════

class TestGetMetadataNone:
    """get_metadata() 未设置元数据时返回 None。"""

    def test_no_metadata_returns_none(self):
        tool = _ConcreteTool()
        assert tool.get_metadata() is None

    def test_undecorated_class_returns_none(self):
        assert get_tool_metadata(_ConcreteTool) is None


# ═══════════════════════════════════════════════════════════════════════════
# 8. ToolMetadata 数据类
# ═══════════════════════════════════════════════════════════════════════════

class TestToolMetadata:
    """ToolMetadata 数据类字段默认值。"""

    def test_default_values(self):
        meta = ToolMetadata()
        assert meta.parallel_safe is False
        assert meta.requires_network is False
        assert meta.requires_terminal is False
        assert meta.timeout_estimate == 0
        assert meta.category == "general"
        assert meta.priority == 100
        assert meta.tool_category == "general"
        assert meta.description == ""

    def test_custom_values(self):
        meta = ToolMetadata(
            parallel_safe=True,
            requires_network=True,
            requires_terminal=True,
            timeout_estimate=60.0,
            category="io",
            priority=5,
            tool_category="read",
            description="自定义描述",
        )
        assert meta.parallel_safe is True
        assert meta.requires_network is True
        assert meta.requires_terminal is True
        assert meta.timeout_estimate == 60.0
        assert meta.category == "io"
        assert meta.priority == 5
        assert meta.tool_category == "read"
        assert meta.description == "自定义描述"

    def test_partial_custom_values(self):
        """部分覆盖默认值。"""
        meta = ToolMetadata(parallel_safe=True, description="仅设置部分字段")
        assert meta.parallel_safe is True
        assert meta.requires_network is False  # 保持默认
        assert meta.category == "general"      # 保持默认
        assert meta.tool_category == "general"  # 保持默认
        assert meta.description == "仅设置部分字段"

    def test_is_dataclass(self):
        """ToolMetadata 是 dataclass 实例。"""
        from dataclasses import dataclass
        meta = ToolMetadata()
        # dataclass 实例有 __dataclass_fields__
        assert hasattr(meta, "__dataclass_fields__")


# ═══════════════════════════════════════════════════════════════════════════
# 8. tool_metadata 装饰器
# ═══════════════════════════════════════════════════════════════════════════

class TestToolMetadataDecorator:
    """tool_metadata 装饰器为 Func 子类附加元数据。"""

    def test_decorated_class_has_metadata(self):
        meta = get_tool_metadata(_DecoratedTool)
        assert isinstance(meta, ToolMetadata)
        assert meta.parallel_safe is True
        assert meta.requires_network is True
        assert meta.category == "io"
        assert meta.priority == 50
        assert meta.tool_category == "general"  # 默认值
        assert meta.description == "测试工具"

    def test_decorated_class_get_metadata_method(self):
        tool = _DecoratedTool()
        meta = tool.get_metadata()
        assert isinstance(meta, ToolMetadata)
        assert meta.parallel_safe is True

    def test_all_fields_custom(self):
        """所有字段都自定义的装饰器用法。"""
        meta = get_tool_metadata(_InteractiveTool)
        assert meta.parallel_safe is True
        assert meta.requires_network is False
        assert meta.requires_terminal is True
        assert meta.timeout_estimate == 30.0
        assert meta.category == "interactive"
        assert meta.priority == 10
        assert meta.description == "交互式测试工具"

    def test_undecorated_class_returns_none(self):
        """未装饰的类 get_tool_metadata 返回 None。"""
        assert get_tool_metadata(_ConcreteTool) is None

    def test_undecorated_class_instance_returns_none(self):
        """未装饰的类实例 get_metadata 返回 None。"""
        tool = _ConcreteTool()
        assert tool.get_metadata() is None

    def test_tool_category_default_via_decorator(self):
        """未设置 tool_category 时默认为 'general'。"""
        meta = get_tool_metadata(_DecoratedTool)
        assert meta.tool_category == "general"

    def test_tool_category_custom_via_decorator(self):
        """通过装饰器设置 tool_category='read'。"""
        meta = get_tool_metadata(_ReadTool)
        assert isinstance(meta, ToolMetadata)
        assert meta.tool_category == "read"
        assert meta.category == "io"
        assert meta.parallel_safe is False


# ═══════════════════════════════════════════════════════════════════════════
# 10. 综合：抽象方法强制实现
# ═══════════════════════════════════════════════════════════════════════════

class TestAbstractMethods:
    """抽象方法必须在子类中实现。"""

    def test_missing_to_tool_schema_raises(self):
        """未实现 to_tool_schema 的子类不能实例化。"""
        class IncompleteTool(Func):
            name = "incomplete"
            async def execute(self) -> str:
                return ""

        with pytest.raises(TypeError):
            IncompleteTool()

    def test_missing_execute_raises(self):
        """未实现 execute 的子类不能实例化。"""
        class IncompleteTool(Func):
            name = "incomplete"
            @classmethod
            def to_tool_schema(cls):
                return {}

        with pytest.raises(TypeError):
            IncompleteTool()


# ═══════════════════════════════════════════════════════════════════════════
# 11. display_params
# ═══════════════════════════════════════════════════════════════════════════

class TestDisplayParams:
    """display_params() 默认返回空字符串。"""

    def test_default_returns_empty(self):
        result = _ConcreteTool.display_params({"key": "value"})
        assert result == ""

    def test_default_with_empty_dict(self):
        result = _ConcreteTool.display_params({})
        assert result == ""

    def test_default_with_max_len_param(self):
        result = _ConcreteTool.display_params({"key": "value"}, max_len=40)
        assert result == ""


# ═══════════════════════════════════════════════════════════════════════════
# 12. can_use
# ═══════════════════════════════════════════════════════════════════════════

class TestCanUse:
    """can_use() 类方法根据 agent_type 判断工具是否可用。"""

    def test_default_execute_allows_write_file(self):
        """execute agent 可以使用 write_file。"""
        allowed, err = Func.can_use("write_file", "execute")
        assert allowed is True
        assert err is None

    def test_default_execute_allows_read_file(self):
        """execute agent 可以使用 read_file。"""
        allowed, err = Func.can_use("read_file", "execute")
        assert allowed is True
        assert err is None

    def test_execute_excludes_dispatch_agent(self):
        """execute agent 不能使用 dispatch_agent。"""
        allowed, err = Func.can_use("dispatch_agent", "execute")
        assert allowed is False
        assert err is not None
        assert "dispatch_agent" in err
        assert "execute" in err

    def test_execute_excludes_user_select(self):
        """execute agent 不能使用 user_select。"""
        allowed, err = Func.can_use("user_select", "execute")
        assert allowed is False
        assert err is not None

    def test_map_allows_read_only_tools(self):
        """map agent 只能使用只读工具。"""
        # read_file 可用
        allowed, err = Func.can_use("read_file", "map")
        assert allowed is True

        # write_file 不可用
        allowed, err = Func.can_use("write_file", "map")
        assert allowed is False
        assert err is not None

    def test_map_excludes_write_tools(self):
        """map agent 不能使用写入类工具。"""
        for tool in ("write_file", "update_file", "bash", "rm", "mv", "cp", "mk"):
            allowed, err = Func.can_use(tool, "map")
            assert allowed is False, f"map agent 不应能使用 {tool}"

    def test_review_excludes_write_tools(self):
        """review agent 不能使用写入类工具。"""
        for tool in ("write_file", "update_file", "bash", "rm", "mv", "cp", "mk"):
            allowed, err = Func.can_use(tool, "review")
            assert allowed is False, f"review agent 不应能使用 {tool}"

    def test_review_allows_web_search(self):
        """review agent 可以使用 web_search。"""
        allowed, err = Func.can_use("web_search", "review")
        assert allowed is True

    def test_plan_allows_write_file(self):
        """plan agent 可以使用 write_file（但运行时受路径限制）。"""
        allowed, err = Func.can_use("write_file", "plan")
        assert allowed is True
        assert err is None

    def test_plan_allows_update_file(self):
        """plan agent 可以使用 update_file（但运行时受路径限制）。"""
        allowed, err = Func.can_use("update_file", "plan")
        assert allowed is True
        assert err is None

    def test_plan_excludes_bash(self):
        """plan agent 不能使用 bash。"""
        allowed, err = Func.can_use("bash", "plan")
        assert allowed is False
        assert err is not None

    def test_execute_allows_write_file(self):
        """execute agent 可以使用 write_file（无路径白名单限制）。"""
        allowed, err = Func.can_use("write_file", "execute")
        assert allowed is True
        assert err is None

    def test_execute_allows_bash(self):
        """execute agent 可以使用 bash。"""
        allowed, err = Func.can_use("bash", "execute")
        assert allowed is True
        assert err is None

    def test_execute_allows_read_file(self):
        """execute agent 可以使用 read_file。"""
        allowed, err = Func.can_use("read_file", "execute")
        assert allowed is True
        assert err is None

    def test_execute_allows_file_ops(self):
        """execute agent 可以使用 rm/mv/cp/mk。"""
        for tool in ("rm", "mv", "cp", "mk"):
            allowed, err = Func.can_use(tool, "execute")
            assert allowed is True, f"execute agent 应能使用 {tool}"

    def test_execute_excludes_web_search(self):
        """execute agent 不能使用 web_search。"""
        allowed, err = Func.can_use("web_search", "execute")
        assert allowed is False
        assert err is not None

    def test_unknown_agent_type_falls_back_to_execute(self):
        """未知 agent_type 回退 execute 策略。"""
        allowed, err = Func.can_use("write_file", "unknown_type")
        # execute 允许 write_file
        assert allowed is True

    def test_can_use_is_class_method(self):
        """can_use 是类方法，无需实例化即可调用。"""
        # 直接通过 Func 基类调用
        allowed, err = Func.can_use("read_file", "execute")
        assert allowed is True

        # 通过子类调用
        allowed, err = _ConcreteTool.can_use("read_file", "execute")
        assert allowed is True

    def test_can_use_default_agent_type(self):
        """默认 agent_type 为 execute。"""
        allowed, err = Func.can_use("write_file")
        assert allowed is True
        allowed, err = Func.can_use("dispatch_agent")
        assert allowed is False

    def test_agent_type_instance_attribute_default(self):
        """Func 实例的 agent_type 默认为 None。"""
        tool = _ConcreteTool()
        assert tool.agent_type is None

    def test_agent_type_can_be_set(self):
        """agent_type 可以手动设置。"""
        tool = _ConcreteTool()
        tool.agent_type = "plan"
        assert tool.agent_type == "plan"

    # ——— 路径白名单校验 ———

    def test_plan_write_file_in_allowed_dir(self):
        """plan agent 写 .chat/plan/ 下文件 → 允许。"""
        allowed, err = Func.can_use("write_file", "plan", path=".chat/plan/test.md")
        assert allowed is True
        assert err is None

    def test_plan_write_file_outside_allowed_dir(self):
        """plan agent 写外部路径 → 拒绝。"""
        allowed, err = Func.can_use("write_file", "plan", path="../evil.md")
        assert allowed is False
        assert err is not None
        assert "只能在" in err

    def test_plan_update_file_in_allowed_dir(self):
        """plan agent update .chat/plan/ 下文件 → 允许。"""
        allowed, err = Func.can_use("update_file", "plan", path=".chat/plan/test.md")
        assert allowed is True
        assert err is None

    def test_plan_update_file_outside_allowed_dir(self):
        """plan agent update 外部路径 → 拒绝。"""
        allowed, err = Func.can_use("update_file", "plan", path="src/main.py")
        assert allowed is False
        assert err is not None
        assert "只能在" in err

    def test_plan_write_plan_dir_with_traversal(self):
        """plan agent 通过 ../ 穿越 → 拒绝。"""
        allowed, err = Func.can_use("write_file", "plan", path=".chat/plan/../../etc/passwd")
        assert allowed is False
        assert err is not None
        assert "只能在" in err

    def test_path_none_backward_compatible(self):
        """path=None 时不触发路径校验（向后兼容）。"""
        allowed, err = Func.can_use("write_file", "plan", path=None)
        assert allowed is True
        assert err is None

    def test_plan_agent_other_tool_no_path_check(self):
        """plan agent 使用非 write_file/update_file 工具时不触发路径校验。"""
        allowed, err = Func.can_use("read_file", "plan", path="../outside.md")
        assert allowed is True
        assert err is None

    def test_execute_no_path_restriction(self):
        """execute agent 即使传入 path 也不触发路径白名单校验。"""
        allowed, err = Func.can_use("write_file", "execute", path="../outside.md")
        assert allowed is True
        assert err is None

    def test_map_no_path_restriction(self):
        """map agent 即使传入 path 也不触发路径白名单校验。"""
        # map agent 本身不能用 write_file（排除集中），验证拒绝来自排除集而非路径白名单
        allowed, err = Func.can_use("write_file", "map", path="../outside.md")
        assert allowed is False
        assert err is not None
        assert "map" in err
        # read_file 允许且不触发路径校验
        allowed, err = Func.can_use("read_file", "map", path="../outside.md")
        assert allowed is True
        assert err is None
