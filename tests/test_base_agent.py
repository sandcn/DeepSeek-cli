"""Tests for src/core/base_agent.py — BaseAgent 消息管理功能"""

import json

import pytest

from src.core.base_agent import BaseAgent


# ═══════════════════════════════════════════════════════════════
# __init__
# ═══════════════════════════════════════════════════════════════

class TestInit:
    """BaseAgent 初始化"""

    def test_messages_initialized_as_empty_list(self):
        """messages 初始化为空列表"""
        agent = BaseAgent()
        assert agent.messages == []

    def test_model_initialized_as_none(self):
        """model 初始化为 None"""
        agent = BaseAgent()
        assert agent.model is None

    def test_tools_initialized_as_empty_list(self):
        """tools 初始化为空列表"""
        agent = BaseAgent()
        assert agent.tools == []

    def test_all_attributes_initialized_correctly(self):
        """所有初始属性同时验证"""
        agent = BaseAgent()
        assert agent.messages == []
        assert agent.model is None
        assert agent.tools == []


# ═══════════════════════════════════════════════════════════════
# add_user_message
# ═══════════════════════════════════════════════════════════════

class TestAddUserMessage:
    """add_user_message 方法"""

    def test_add_string_message(self):
        """传入字符串 → messages 追加 {'role': 'user', 'content': '字符串内容'}"""
        agent = BaseAgent()
        agent.add_user_message("你好")
        assert len(agent.messages) == 1
        assert agent.messages[0] == {"role": "user", "content": "你好"}

    def test_add_none_message(self):
        """传入 None → content 转为空字符串"""
        agent = BaseAgent()
        agent.add_user_message(None)
        assert len(agent.messages) == 1
        assert agent.messages[0] == {"role": "user", "content": ""}

    def test_add_integer_message(self):
        """传入整数 42 → content 转为 '42'（str(content)）"""
        agent = BaseAgent()
        agent.add_user_message(42)
        assert len(agent.messages) == 1
        assert agent.messages[0] == {"role": "user", "content": "42"}

    def test_add_empty_string(self):
        """传入空字符串 → 追加 {'role': 'user', 'content': ''}"""
        agent = BaseAgent()
        agent.add_user_message("")
        assert len(agent.messages) == 1
        assert agent.messages[0] == {"role": "user", "content": ""}

    def test_consecutive_calls_maintain_order(self):
        """连续多次调用 → 消息按顺序追加"""
        agent = BaseAgent()
        agent.add_user_message("first")
        agent.add_user_message("second")
        agent.add_user_message("third")
        assert len(agent.messages) == 3
        assert agent.messages[0] == {"role": "user", "content": "first"}
        assert agent.messages[1] == {"role": "user", "content": "second"}
        assert agent.messages[2] == {"role": "user", "content": "third"}

    def test_add_float_value(self):
        """传入浮点数 → content 转为字符串表示"""
        agent = BaseAgent()
        agent.add_user_message(3.14)
        assert agent.messages[0] == {"role": "user", "content": "3.14"}

    def test_add_boolean_value(self):
        """传入布尔值 → content 转为 'True' 或 'False'"""
        agent = BaseAgent()
        agent.add_user_message(True)
        assert agent.messages[0] == {"role": "user", "content": "True"}


# ═══════════════════════════════════════════════════════════════
# _append_assistant_message
# ═══════════════════════════════════════════════════════════════

class TestAppendAssistantMessage:
    """_append_assistant_message 方法"""

    def test_only_content(self):
        """仅传 content → messages 追加 assistant 消息，reasoning_content 为空字符串"""
        agent = BaseAgent()
        agent._append_assistant_message("你好")
        assert len(agent.messages) == 1
        assert agent.messages[0] == {
            "role": "assistant",
            "content": "你好",
            "reasoning_content": "",
        }

    def test_content_empty_string(self):
        """content 为空字符串 → content 保持空字符串"""
        agent = BaseAgent()
        agent._append_assistant_message("")
        assert agent.messages[0]["content"] == ""

    def test_content_none_without_tool_calls(self):
        """content 为 None 且无 tool_calls → content 回退为空字符串"""
        agent = BaseAgent()
        agent._append_assistant_message(None)
        assert agent.messages[0]["content"] == ""

    def test_reasoning_content_string(self):
        """reasoning_content 传入字符串 → 保留原值"""
        agent = BaseAgent()
        agent._append_assistant_message("回答", reasoning_content="思考过程")
        assert agent.messages[0]["reasoning_content"] == "思考过程"

    def test_reasoning_content_none(self):
        """reasoning_content 传入 None → 回退为空字符串"""
        agent = BaseAgent()
        agent._append_assistant_message("回答", reasoning_content=None)
        assert agent.messages[0]["reasoning_content"] == ""

    def test_reasoning_content_integer(self):
        """reasoning_content 传入整数 → 回退为空字符串"""
        agent = BaseAgent()
        agent._append_assistant_message("回答", reasoning_content=123)
        assert agent.messages[0]["reasoning_content"] == ""

    def test_reasoning_content_empty_string(self):
        """reasoning_content 传入空字符串 → 保留空字符串"""
        agent = BaseAgent()
        agent._append_assistant_message("回答", reasoning_content="")
        assert agent.messages[0]["reasoning_content"] == ""

    def test_tool_calls_content_is_none(self):
        """tool_calls 非空 → content 为 None（非空字符串）"""
        agent = BaseAgent()
        agent._append_assistant_message(
            "不应出现的内容",
            tool_calls=[{"id": "call_1", "name": "get_weather", "arguments": "{}"}],
        )
        assert agent.messages[0]["content"] is None

    def test_tool_calls_arguments_string(self):
        """tool_calls 中 arguments 为字符串 → 直接使用"""
        agent = BaseAgent()
        agent._append_assistant_message(
            None,
            tool_calls=[{"id": "call_1", "name": "get_weather", "arguments": '{"city": "北京"}'}],
        )
        tc = agent.messages[0]["tool_calls"][0]
        assert tc["function"]["arguments"] == '{"city": "北京"}'

    def test_tool_calls_arguments_dict(self):
        """tool_calls 中 arguments 为 dict → 序列化为 JSON 字符串"""
        agent = BaseAgent()
        agent._append_assistant_message(
            None,
            tool_calls=[{"id": "call_2", "name": "search", "arguments": {"q": "test", "limit": 10}}],
        )
        tc = agent.messages[0]["tool_calls"][0]
        assert tc["function"]["arguments"] == '{"q": "test", "limit": 10}'

    def test_tool_calls_arguments_none(self):
        """tool_calls 中 arguments 为 None → 空字符串"""
        agent = BaseAgent()
        agent._append_assistant_message(
            None,
            tool_calls=[{"id": "call_3", "name": "noop", "arguments": None}],
        )
        tc = agent.messages[0]["tool_calls"][0]
        assert tc["function"]["arguments"] == ""

    def test_tool_calls_structure(self):
        """每条 tool_call 包含 id, type='function', function.name, function.arguments"""
        agent = BaseAgent()
        agent._append_assistant_message(
            None,
            tool_calls=[{"id": "call_1", "name": "get_weather", "arguments": "{}"}],
        )
        tc = agent.messages[0]["tool_calls"][0]
        assert tc["id"] == "call_1"
        assert tc["type"] == "function"
        assert tc["function"]["name"] == "get_weather"
        assert tc["function"]["arguments"] == "{}"

    def test_multiple_tool_calls(self):
        """多条 tool_call 全部正确处理"""
        agent = BaseAgent()
        agent._append_assistant_message(
            None,
            tool_calls=[
                {"id": "call_1", "name": "func_a", "arguments": '{"x": 1}'},
                {"id": "call_2", "name": "func_b", "arguments": {"y": 2}},
                {"id": "call_3", "name": "func_c", "arguments": None},
            ],
        )
        tcs = agent.messages[0]["tool_calls"]
        assert len(tcs) == 3
        assert tcs[0]["function"]["arguments"] == '{"x": 1}'
        assert tcs[1]["function"]["arguments"] == '{"y": 2}'
        assert tcs[2]["function"]["arguments"] == ""

    def test_tool_calls_with_reasoning_content(self):
        """tool_calls 与 reasoning_content 同时存在"""
        agent = BaseAgent()
        agent._append_assistant_message(
            None,
            tool_calls=[{"id": "call_1", "name": "search", "arguments": "{}"}],
            reasoning_content="正在搜索...",
        )
        msg = agent.messages[0]
        assert msg["content"] is None
        assert msg["reasoning_content"] == "正在搜索..."
        assert len(msg["tool_calls"]) == 1


# ═══════════════════════════════════════════════════════════════
# _append_tool_result
# ═══════════════════════════════════════════════════════════════

class TestAppendToolResult:
    """_append_tool_result 方法"""

    def test_single_tool_result(self):
        """追加单条 tool result 消息"""
        agent = BaseAgent()
        agent._append_tool_result("call_xxx", "结果")
        assert len(agent.messages) == 1
        assert agent.messages[0] == {
            "role": "tool",
            "tool_call_id": "call_xxx",
            "content": "结果",
        }

    def test_multiple_tool_results(self):
        """连续追加多条 tool result 消息"""
        agent = BaseAgent()
        agent._append_tool_result("call_1", "结果1")
        agent._append_tool_result("call_2", "结果2")
        agent._append_tool_result("call_3", "结果3")
        assert len(agent.messages) == 3
        assert agent.messages[0]["tool_call_id"] == "call_1"
        assert agent.messages[1]["tool_call_id"] == "call_2"
        assert agent.messages[2]["tool_call_id"] == "call_3"

    def test_tool_result_empty_content(self):
        """tool result 内容为空字符串"""
        agent = BaseAgent()
        agent._append_tool_result("call_xxx", "")
        assert agent.messages[0] == {
            "role": "tool",
            "tool_call_id": "call_xxx",
            "content": "",
        }

    def test_tool_result_with_long_content(self):
        """tool result 包含较长内容"""
        agent = BaseAgent()
        content = "x" * 1000
        agent._append_tool_result("call_xxx", content)
        assert agent.messages[0]["content"] == content
        assert len(agent.messages[0]["content"]) == 1000


# ═══════════════════════════════════════════════════════════════
# 多消息交互
# ═══════════════════════════════════════════════════════════════

class TestMessageSequence:
    """完整消息序列测试"""

    def test_user_assistant_sequence(self):
        """user → assistant 简单对话"""
        agent = BaseAgent()
        agent.add_user_message("你好")
        agent._append_assistant_message("你好！有什么可以帮你的？")
        assert len(agent.messages) == 2
        assert agent.messages[0] == {"role": "user", "content": "你好"}
        assert agent.messages[1] == {
            "role": "assistant",
            "content": "你好！有什么可以帮你的？",
            "reasoning_content": "",
        }

    def test_user_assistant_tool_assistant_sequence(self):
        """user → assistant(含tool_calls) → tool → assistant 完整消息序列"""
        agent = BaseAgent()

        # user
        agent.add_user_message("今天北京的天气怎么样？")
        assert agent.messages[-1] == {"role": "user", "content": "今天北京的天气怎么样？"}

        # assistant (with tool calls)
        agent._append_assistant_message(
            None,
            tool_calls=[{"id": "call_weather", "name": "get_weather", "arguments": {"city": "北京"}}],
            reasoning_content="用户想查询北京天气，需要调用天气查询工具",
        )
        assert agent.messages[-1]["role"] == "assistant"
        assert agent.messages[-1]["content"] is None
        assert agent.messages[-1]["reasoning_content"] == "用户想查询北京天气，需要调用天气查询工具"
        assert agent.messages[-1]["tool_calls"][0]["function"]["arguments"] == '{"city": "北京"}'

        # tool result
        agent._append_tool_result("call_weather", '{"temperature": 25, "condition": "晴"}')
        assert agent.messages[-1] == {
            "role": "tool",
            "tool_call_id": "call_weather",
            "content": '{"temperature": 25, "condition": "晴"}',
        }

        # assistant (final reply)
        agent._append_assistant_message("北京今天天气晴朗，气温25°C，适合外出。")
        assert agent.messages[-1] == {
            "role": "assistant",
            "content": "北京今天天气晴朗，气温25°C，适合外出。",
            "reasoning_content": "",
        }

        # 验证完整序列长度和角色顺序
        assert len(agent.messages) == 4
        roles = [m["role"] for m in agent.messages]
        assert roles == ["user", "assistant", "tool", "assistant"]

    def test_multi_turn_conversation(self):
        """多轮对话：user→assistant→user→assistant(tool)→tool→assistant"""
        agent = BaseAgent()

        # 第一轮
        agent.add_user_message("你好")
        agent._append_assistant_message("你好！")

        # 第二轮
        agent.add_user_message("搜索一下人工智能")
        agent._append_assistant_message(
            None,
            tool_calls=[{"id": "call_search", "name": "search", "arguments": {"q": "人工智能"}}],
            reasoning_content="正在搜索",
        )
        agent._append_tool_result("call_search", "搜索结果...")
        agent._append_assistant_message("这是关于人工智能的搜索结果。")

        # 验证
        assert len(agent.messages) == 6
        expected_roles = ["user", "assistant", "user", "assistant", "tool", "assistant"]
        assert [m["role"] for m in agent.messages] == expected_roles
        assert agent.messages[0]["content"] == "你好"
        assert agent.messages[2]["content"] == "搜索一下人工智能"
        assert agent.messages[5]["content"] == "这是关于人工智能的搜索结果。"

    def test_multiple_tool_calls_in_one_message(self):
        """一条 assistant 消息中多条 tool_calls + 多条 tool result"""
        agent = BaseAgent()
        agent.add_user_message("查天气和新闻")

        agent._append_assistant_message(
            None,
            tool_calls=[
                {"id": "c1", "name": "get_weather", "arguments": {"city": "北京"}},
                {"id": "c2", "name": "get_news", "arguments": {"category": "科技"}},
            ],
        )

        agent._append_tool_result("c1", "晴天")
        agent._append_tool_result("c2", "科技新闻...")

        agent._append_assistant_message("已为您查询完毕。")

        assert len(agent.messages) == 5
        assert [m["role"] for m in agent.messages] == [
            "user", "assistant", "tool", "tool", "assistant",
        ]
        assert len(agent.messages[1]["tool_calls"]) == 2
        assert agent.messages[2]["tool_call_id"] == "c1"
        assert agent.messages[3]["tool_call_id"] == "c2"
