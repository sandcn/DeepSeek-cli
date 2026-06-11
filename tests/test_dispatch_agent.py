"""测试 DispatchAgents — 并行子 Agent 调度工具

测试策略
--------
- 核心逻辑（参数解析、schema、格式化）不依赖外部状态，直接测试
- execute 方法通过 mock agent 和 mock ParallelExecutor 测试各分支
- 每个测试类关注一个概念，每个测试方法覆盖单一场景
- 遵循 Arrange/Act/Assert 模式
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.tools.dispatch_agent import DispatchAgents


# ═══════════════════════════════════════════════════════════════════════════
# 1. __init__ 参数
# ═══════════════════════════════════════════════════════════════════════════

class TestDispatchAgentsInit:
    """__init__ 参数验证"""

    def test_init_basic(self):
        da = DispatchAgents(description="分析模块", prompt="请读取并分析 user.py")
        assert da.description == "分析模块"
        assert da.prompt == "请读取并分析 user.py"

    def test_init_empty_description(self):
        da = DispatchAgents(description="", prompt="do something")
        assert da.description == ""
        assert da.prompt == "do something"

    def test_init_empty_prompt(self):
        da = DispatchAgents(description="task", prompt="")
        assert da.description == "task"
        assert da.prompt == ""

    def test_init_both_empty(self):
        da = DispatchAgents(description="", prompt="")
        assert da.description == ""
        assert da.prompt == ""

    def test_init_default_agent_type(self):
        """默认 target_agent_type 为 ordinary"""
        da = DispatchAgents(description="task", prompt="do it")
        assert da.target_agent_type == "ordinary"

    def test_init_custom_agent_type(self):
        """可以指定 target_agent_type"""
        da = DispatchAgents(description="task", prompt="do it", target_agent_type="ordinary")
        assert da.target_agent_type == "ordinary"

    def test_init_map_agent_type(self):
        """map 类型正确设置"""
        da = DispatchAgents(description="分析项目", prompt="生成项目地图", target_agent_type="map")
        assert da.target_agent_type == "map"


# ═══════════════════════════════════════════════════════════════════════════
# 2. from_args 参数解析
# ═══════════════════════════════════════════════════════════════════════════

class TestDispatchAgentsFromArgs:
    """from_args 参数解析"""

    def test_from_args_both_params(self):
        da = DispatchAgents.from_args({
            "description": "分析 user.py",
            "prompt": "完整读取并分析 user.py 模块",
        })
        assert da.description == "分析 user.py"
        assert da.prompt == "完整读取并分析 user.py 模块"

    def test_from_args_missing_description(self):
        da = DispatchAgents.from_args({"prompt": "do something"})
        assert da.description == ""
        assert da.prompt == "do something"

    def test_from_args_missing_prompt(self):
        da = DispatchAgents.from_args({"description": "task"})
        assert da.description == "task"
        assert da.prompt == ""

    def test_from_args_empty_dict(self):
        da = DispatchAgents.from_args({})
        assert da.description == ""
        assert da.prompt == ""

    def test_from_args_extra_params_ignored(self):
        da = DispatchAgents.from_args({
            "description": "task",
            "prompt": "do it",
            "extra": "ignored",
        })
        assert da.description == "task"
        assert da.prompt == "do it"

    def test_from_args_with_type(self):
        """from_args 解析 type 参数"""
        da = DispatchAgents.from_args({
            "description": "task",
            "prompt": "do it",
            "type": "ordinary",
        })
        assert da.target_agent_type == "ordinary"

    def test_from_args_with_map_type(self):
        """from_args 解析 map 类型"""
        da = DispatchAgents.from_args({
            "description": "分析项目结构",
            "prompt": "生成完整项目地图",
            "type": "map",
        })
        assert da.target_agent_type == "map"

    def test_from_args_default_type(self):
        """from_args 缺省 type 时默认 ordinary"""
        da = DispatchAgents.from_args({
            "description": "task",
            "prompt": "do it",
        })
        assert da.target_agent_type == "ordinary"


# ═══════════════════════════════════════════════════════════════════════════
# 3. to_tool_schema schema 结构
# ═══════════════════════════════════════════════════════════════════════════

class TestDispatchAgentsSchema:
    """to_tool_schema schema 结构"""

    def test_schema_top_level(self):
        schema = DispatchAgents.to_tool_schema()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "dispatch_agent"
        assert "description" in schema["function"]
        assert "parameters" in schema["function"]

    def test_schema_description_contains_batch_info(self):
        """description 包含并行调度说明"""
        desc = DispatchAgents.to_tool_schema()["function"]["description"]
        assert "dispatch_agent" in desc
        assert "并行" in desc
        assert "共享" in desc or "独立" in desc

    def test_schema_parameters_properties(self):
        props = DispatchAgents.to_tool_schema()["function"]["parameters"]["properties"]
        assert "description" in props
        assert "prompt" in props
        assert "type" in props
        assert props["description"]["type"] == "string"
        assert props["prompt"]["type"] == "string"
        assert props["type"]["type"] == "string"
        assert props["type"]["enum"] == ["ordinary", "map", "review", "plan"]

    def test_schema_parameters_required(self):
        required = DispatchAgents.to_tool_schema()["function"]["parameters"]["required"]
        assert "description" in required
        assert "prompt" in required

    def test_schema_description_guide(self):
        """description 参数有指导性说明"""
        desc_prop = DispatchAgents.to_tool_schema()["function"]["parameters"]["properties"]["description"]["description"]
        assert len(desc_prop) > 10
        assert "子任务" in desc_prop or "标题" in desc_prop

    def test_schema_prompt_guide(self):
        """prompt 参数有指导性说明"""
        prompt_prop = DispatchAgents.to_tool_schema()["function"]["parameters"]["properties"]["prompt"]["description"]
        assert len(prompt_prop) > 10
        assert "指令" in prompt_prop


# ═══════════════════════════════════════════════════════════════════════════
# 4. display_params 参数摘要
# ═══════════════════════════════════════════════════════════════════════════

class TestDispatchAgentsDisplayParams:
    """display_params 参数摘要"""

    def test_basic(self):
        result = DispatchAgents.display_params({"description": "分析 user.py"})
        assert "agent: 分析 user.py" in result

    def test_missing_description(self):
        result = DispatchAgents.display_params({})
        assert "agent: ?" in result

    def test_empty_description(self):
        result = DispatchAgents.display_params({"description": ""})
        assert "agent: " in result

    def test_truncation(self):
        long_desc = "a" * 200
        result = DispatchAgents.display_params({"description": long_desc}, max_len=30)
        assert len(result) <= 30


# ═══════════════════════════════════════════════════════════════════════════
# 5. execute 执行逻辑
# ═══════════════════════════════════════════════════════════════════════════

class TestDispatchAgentsExecute:
    """execute 各分支"""

    async def test_missing_description(self):
        """缺少 description → 错误提示"""
        da = DispatchAgents(description="", prompt="do something")
        result = await da.execute()
        assert "错误" in result
        assert "描述" in result or "description" in result.lower()

    async def test_missing_prompt(self):
        """缺少 prompt → 错误提示"""
        da = DispatchAgents(description="task", prompt="")
        result = await da.execute()
        assert "错误" in result
        assert "指令" in result or "prompt" in result.lower()

    async def test_both_missing(self):
        """两者都缺 → 错误提示"""
        da = DispatchAgents(description="", prompt="")
        result = await da.execute()
        assert "错误" in result

    async def test_no_agent_set(self):
        """未关联 agent → 错误提示"""
        da = DispatchAgents(description="task", prompt="do something")
        assert da.agent is None
        result = await da.execute()
        assert "错误" in result
        assert "未关联" in result

    async def test_shared_executor_batch_mode(self):
        """_shared_executor 批处理模式 → 添加到 executor 并等待结果"""
        # 准备 mock executor
        mock_executor = MagicMock()
        mock_executor.is_batch_mode = True
        mock_executor.add_agent.return_value = 0
        mock_executor.register_and_wait = AsyncMock()
        mock_executor.get_result.return_value = {
            "description": "分析 user.py",
            "result": "分析完成：user.py 包含 3 个函数",
        }

        # 准备 mock agent
        mock_agent = MagicMock()
        mock_agent._shared_executor = mock_executor

        da = DispatchAgents(description="分析 user.py", prompt="读取 user.py")
        da.set_agent(mock_agent)

        result = await da.execute()

        # 验证调用链
        mock_executor.add_agent.assert_called_once_with(
            "分析 user.py", "读取 user.py", agent_type="ordinary",
            model=mock_agent.model, tool_label="",
        )

    async def test_shared_executor_map_type(self):
        """map 类型 agent_type 正确传递给 executor"""
        mock_executor = MagicMock()
        mock_executor.is_batch_mode = True
        mock_executor.add_agent.return_value = 0
        mock_executor.register_and_wait = AsyncMock()
        mock_executor.get_result.return_value = {
            "description": "项目地图",
            "result": "项目包含 5 个核心模块",
        }

        mock_agent = MagicMock()
        mock_agent._shared_executor = mock_executor

        da = DispatchAgents(description="项目地图", prompt="分析项目", target_agent_type="map")
        da.set_agent(mock_agent)

        result = await da.execute()

        mock_executor.add_agent.assert_called_once_with(
            "项目地图", "分析项目", agent_type="map",
            model=mock_agent.model, tool_label="",
        )
        assert "## 项目地图" in result
        assert "项目包含 5 个核心模块" in result

    async def test_shared_executor_review_type(self):
        """review 类型 agent_type 正确传递给 executor"""
        mock_executor = MagicMock()
        mock_executor.is_batch_mode = True
        mock_executor.add_agent.return_value = 0
        mock_executor.register_and_wait = AsyncMock()
        mock_executor.get_result.return_value = {
            "description": "代码审查",
            "result": "P0: 0, P1: 2, P2: 3, P3: 1",
        }

        mock_agent = MagicMock()
        mock_agent._shared_executor = mock_executor

        da = DispatchAgents(description="代码审查", prompt="审查 user.py", target_agent_type="review")
        da.set_agent(mock_agent)

        result = await da.execute()

        mock_executor.add_agent.assert_called_once_with(
            "代码审查", "审查 user.py", agent_type="review",
            model=mock_agent.model, tool_label="",
        )

    async def test_shared_executor_with_tool_label(self):
        """tool_label 正确传递"""
        mock_executor = MagicMock()
        mock_executor.is_batch_mode = True
        mock_executor.add_agent.return_value = 1
        mock_executor.register_and_wait = AsyncMock()
        mock_executor.get_result.return_value = {
            "description": "task",
            "result": "done",
        }

        mock_agent = MagicMock()
        mock_agent._shared_executor = mock_executor

        da = DispatchAgents(description="task", prompt="do it")
        da.tool_label = "web_search"
        da.set_agent(mock_agent)

        await da.execute()

        mock_executor.add_agent.assert_called_once_with(
            "task", "do it", agent_type="ordinary",
            model=mock_agent.model, tool_label="web_search",
        )

    async def test_shared_executor_multiple_results(self):
        """多次调用 executor 使用不同 index"""
        mock_executor = MagicMock()
        mock_executor.is_batch_mode = True
        mock_executor.add_agent.side_effect = [0, 1]  # 返回不同 index
        mock_executor.register_and_wait = AsyncMock()
        mock_executor.get_result.side_effect = [
            {"description": "task1", "result": "result1"},
            {"description": "task2", "result": "result2"},
        ]

        mock_agent = MagicMock()
        mock_agent._shared_executor = mock_executor

        da1 = DispatchAgents(description="task1", prompt="do 1")
        da2 = DispatchAgents(description="task2", prompt="do 2")
        da1.set_agent(mock_agent)
        da2.set_agent(mock_agent)

        r1 = await da1.execute()
        r2 = await da2.execute()

        assert "result1" in r1
        assert "result2" in r2
        assert mock_executor.get_result.call_args_list[0][0][0] == 0
        assert mock_executor.get_result.call_args_list[1][0][0] == 1

    async def test_independent_mode_no_executor(self):
        """独立模式（无 shared_executor）→ 错误提示"""
        mock_agent = MagicMock()
        # 确保 agent 有 _shared_executor 但为 None
        del mock_agent._shared_executor
        # 或者显式设为 None
        mock_agent._shared_executor = None

        da = DispatchAgents(description="task", prompt="do something")
        da.set_agent(mock_agent)

        result = await da.execute()
        assert "错误" in result

    async def test_shared_executor_not_batch_mode(self):
        """shared_executor 非批处理模式 → 错误提示"""
        mock_executor = MagicMock()
        mock_executor.is_batch_mode = False

        mock_agent = MagicMock()
        mock_agent._shared_executor = mock_executor

        da = DispatchAgents(description="task", prompt="do something")
        da.set_agent(mock_agent)

        result = await da.execute()
        assert "错误" in result


# ═══════════════════════════════════════════════════════════════════════════
# 6. _format_single 结果格式化
# ═══════════════════════════════════════════════════════════════════════════

class TestDispatchAgentsFormatSingle:
    """_format_single 结果格式化"""

    def test_normal_result(self):
        result = DispatchAgents._format_single({
            "description": "分析模块A",
            "result": "模块分析完成，包含 5 个函数",
        })
        assert "## 分析模块A" in result
        assert "模块分析完成" in result
        assert "错误" not in result

    def test_result_with_error_field(self):
        result = DispatchAgents._format_single({
            "description": "有问题的任务",
            "error": "文件不存在",
        })
        assert "## 有问题的任务" in result
        assert "错误: 文件不存在" in result

    def test_empty_result(self):
        result = DispatchAgents._format_single({
            "description": "空结果",
            "result": "",
        })
        assert "## 空结果" in result
        # result 为空时，格式化后不出现额外内容

    def test_error_without_result(self):
        result = DispatchAgents._format_single({
            "description": "task",
            "error": "权限不足",
            "result": "partial data",
        })
        # error 优先
        assert "错误: 权限不足" in result
        assert "partial data" not in result

    def test_none_result(self):
        result = DispatchAgents._format_single({
            "description": "task",
            "result": None,
        })
        assert "## task" in result

    def test_empty_description(self):
        result = DispatchAgents._format_single({
            "description": "",
            "result": "done",
        })
        assert "## " in result
        assert "done" in result
