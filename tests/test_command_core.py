"""Tests for src/core/_command_core.py — 命令调度基础设施"""

import types
import time
from unittest.mock import MagicMock

import pytest

from src.core.internal.commands._command_core import (
    register_command,
    handle_command,
    CommandContext,
    _pop_assistant_tool_messages,
    get_registered_command_names,
    _format_cost_duration,
    show_cost,
    COMMANDS_HELP,
)


# ═══════════════════════════════════════════════════════════
#  Fixtures
# ═══════════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def cleanup_commands():
    """每个测试前后清理全局 _commands 字典，确保测试隔离"""
    from src.core.internal.commands import _command_core
    _command_core._commands.clear()
    yield
    _command_core._commands.clear()


@pytest.fixture
def sample_handlers():
    """预置一组命令处理函数和测试所需上下文参数"""
    results = []

    def cmd_hello(ctx):
        results.append(("hello", ctx.arg))
        return "Hello " + ctx.arg if ctx.arg else "Hello world"

    def cmd_goodbye(ctx):
        results.append(("goodbye", ctx.arg))
        return True

    register_command("/hello", cmd_hello, help_text="Say hello")
    register_command("/goodbye", cmd_goodbye, help_text="Say goodbye")

    return {
        "handlers": {"hello": cmd_hello, "goodbye": cmd_goodbye},
        "results": results,
    }


@pytest.fixture
def ctx_kwargs():
    """CommandContext 构建所需的关键字参数"""
    return dict(
        messages=[{"role": "user", "content": "hi"}],
        state={"mode": "chat"},
        arg="test_arg",
        build_system_prompt=lambda: "system prompt",
        get_user_input=lambda prompt: "user input",
        context_manager=types.SimpleNamespace(get_context=lambda: "ctx"),
    )


# ═══════════════════════════════════════════════════════════
#  TestRegisterCommand
# ═══════════════════════════════════════════════════════════

class TestRegisterCommand:
    """注册命令功能测试"""

    def test_register_and_invoke(self, sample_handlers):
        """注册一个命令后能通过 handle_command 调用"""
        result = handle_command(
            "/hello world",
            messages=[], state={},
            build_system_prompt=lambda: "",
            get_user_input=lambda p: "",
        )
        assert result == "Hello world"

    def test_handler_return_value_passed_through(self, sample_handlers):
        """命令处理函数的返回值正确传回"""
        result = handle_command(
            "/goodbye",
            messages=[], state={},
            build_system_prompt=lambda: "",
            get_user_input=lambda p: "",
        )
        assert result is True

    def test_command_name_case_insensitive(self, sample_handlers):
        """命令名大小写不敏感"""
        result = handle_command(
            "/HELLO friend",
            messages=[], state={},
            build_system_prompt=lambda: "",
            get_user_input=lambda p: "",
        )
        assert result == "Hello friend"

        result2 = handle_command(
            "/Hello",
            messages=[], state={},
            build_system_prompt=lambda: "",
            get_user_input=lambda p: "",
        )
        assert result2 == "Hello world"

    def test_register_multiple_commands(self):
        """可以注册多个不同命令"""
        def handler_a(ctx): return "A"
        def handler_b(ctx): return "B"
        def handler_c(ctx): return "C"

        register_command("/a", handler_a)
        register_command("/b", handler_b)
        register_command("/c", handler_c)

        assert handle_command("/a", [], {}, lambda: "", lambda p: "") == "A"
        assert handle_command("/b", [], {}, lambda: "", lambda p: "") == "B"
        assert handle_command("/c", [], {}, lambda: "", lambda p: "") == "C"

    def test_reregister_overwrites(self):
        """同名命令重新注册会覆盖旧处理函数"""
        def old_handler(ctx): return "old"

        register_command("/dup", old_handler)
        assert handle_command("/dup", [], {}, lambda: "", lambda p: "") == "old"

        def new_handler(ctx): return "new"
        register_command("/dup", new_handler)
        assert handle_command("/dup", [], {}, lambda: "", lambda p: "") == "new"

    def test_handler_receives_context_with_arg(self, sample_handlers):
        """处理函数收到的 ctx.arg 包含正确参数"""
        captured = []

        def capture(ctx):
            captured.append(ctx.arg)
            return True

        register_command("/capture", capture)

        handle_command("/capture foo bar", [], {}, lambda: "", lambda p: "")
        assert captured == ["foo bar"]

        handle_command("/capture", [], {}, lambda: "", lambda p: "")
        assert captured == ["foo bar", ""]

    def test_handler_can_return_false(self):
        """处理函数可以返回 False"""
        def handler(ctx): return False
        register_command("/false", handler)
        result = handle_command("/false", [], {}, lambda: "", lambda p: "")
        assert result is False

    def test_handler_can_return_none(self):
        """处理函数可以返回 None"""
        def handler(ctx): return None
        register_command("/none", handler)
        result = handle_command("/none", [], {}, lambda: "", lambda p: "")
        assert result is None


# ═══════════════════════════════════════════════════════════
#  TestHandleCommand
# ═══════════════════════════════════════════════════════════

class TestHandleCommand:
    """命令调度测试"""

    def test_slash_command_recognized(self):
        """/xxx 形式的命令被正确识别和处理"""
        def handler(ctx):
            ctx.state["handled"] = True
            return True

        register_command("/test", handler)
        state = {}
        result = handle_command("/test", [], state, lambda: "", lambda p: "")
        assert result is True
        assert state["handled"] is True

    def test_unregistered_command_returns_false(self):
        """未注册的命令返回 False"""
        result = handle_command("/nonexistent", [], {}, lambda: "", lambda p: "")
        assert result is False

    def test_arg_extracted_single_word(self):
        """/cmd arg 中的 arg 被正确提取"""
        captured = []

        def handler(ctx):
            captured.append(ctx.arg)
            return True

        register_command("/echo", handler)
        handle_command("/echo hello", [], {}, lambda: "", lambda p: "")
        assert captured == ["hello"]

    def test_arg_extracted_multiple_words(self):
        """/cmd arg1 arg2 中的多个词作为整体提取"""
        captured = []

        def handler(ctx):
            captured.append(ctx.arg)
            return True

        register_command("/echo", handler)
        handle_command("/echo hello world foo", [], {}, lambda: "", lambda p: "")
        assert captured == ["hello world foo"]

    def test_arg_extracted_trailing_spaces(self):
        """参数中多余空格被 strip"""
        captured = []

        def handler(ctx):
            captured.append(ctx.arg)
            return True

        register_command("/echo", handler)
        handle_command("/echo   padded   ", [], {}, lambda: "", lambda p: "")
        assert captured == ["padded"]

    def test_no_arg_command(self):
        """无参数的 /cmd 处理，arg 为空字符串"""
        captured = []

        def handler(ctx):
            captured.append(ctx.arg)
            return True

        register_command("/bare", handler)
        handle_command("/bare", [], {}, lambda: "", lambda p: "")
        assert captured == [""]

    def test_no_arg_with_whitespace(self):
        """/cmd   （仅空格）时 arg 为空字符串"""
        captured = []

        def handler(ctx):
            captured.append(ctx.arg)
            return True

        register_command("/bare", handler)
        handle_command("/bare   ", [], {}, lambda: "", lambda p: "")
        assert captured == [""]

    def test_messages_passed_to_handler(self):
        """messages 正确传递到 ctx"""
        msgs = [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}]
        captured = []

        def handler(ctx):
            captured.append(ctx.messages)
            return True

        register_command("/test", handler)
        handle_command("/test", msgs, {}, lambda: "", lambda p: "")
        assert captured[0] is msgs

    def test_state_passed_to_handler(self):
        """state 正确传递到 ctx"""
        state_obj = {"key": "value"}
        captured = []

        def handler(ctx):
            captured.append(ctx.state)
            return True

        register_command("/test", handler)
        handle_command("/test", [], state_obj, lambda: "", lambda p: "")
        assert captured[0] is state_obj

    def test_build_system_prompt_passed_to_handler(self):
        """build_system_prompt 可调用对象正确传递"""
        def my_prompt(): return "custom prompt"
        captured = []

        def handler(ctx):
            captured.append(ctx.build_system_prompt())
            return True

        register_command("/test", handler)
        handle_command("/test", [], {}, my_prompt, lambda p: "")
        assert captured == ["custom prompt"]

    def test_get_user_input_passed_to_handler(self):
        """get_user_input 可调用对象正确传递"""
        captured = []

        def handler(ctx):
            captured.append(ctx.get_user_input("prompt> "))
            return True

        register_command("/test", handler)
        handle_command("/test", [], {}, lambda: "", lambda p: "echo")
        assert captured == ["echo"]

    def test_context_manager_passed_to_handler(self):
        """context_manager 正确传递到 ctx"""
        ctx_mgr = types.SimpleNamespace(data="manager")
        captured = []

        def handler(ctx):
            captured.append(ctx.context_manager)
            return True

        register_command("/test", handler)
        handle_command("/test", [], {}, lambda: "", lambda p: "", context_manager=ctx_mgr)
        assert captured[0] is ctx_mgr

    def test_context_manager_defaults_to_none(self):
        """不传 context_manager 时 ctx.context_manager 为 None"""
        captured = []

        def handler(ctx):
            captured.append(ctx.context_manager)
            return True

        register_command("/test", handler)
        handle_command("/test", [], {}, lambda: "", lambda p: "")
        assert captured[0] is None

    def test_empty_command_string(self):
        """空字符串（不以 / 开头）— 不经过命令处理"""
        # handle_command 仅被 /cmd 形式的字符串调用
        # 空字符串直接返回 False（不会被当作 /cmd 调用，但 / 单独出现会解析为 command=/ 和 arg=""）
        # 实际上 handle_command 对任何字符串都会尝试解析
        result = handle_command("/", [], {}, lambda: "", lambda p: "")
        assert result is False  # "/" 未注册


# ═══════════════════════════════════════════════════════════
#  TestCommandContext
# ═══════════════════════════════════════════════════════════

class TestCommandContext:
    """CommandContext 初始化测试"""

    def test_all_attributes_initialized(self, ctx_kwargs):
        """正确初始化所有属性"""
        ctx = CommandContext(**ctx_kwargs)
        assert ctx.messages == [{"role": "user", "content": "hi"}]
        assert ctx.state == {"mode": "chat"}
        assert ctx.arg == "test_arg"
        assert ctx.build_system_prompt() == "system prompt"
        assert ctx.get_user_input("q:") == "user input"
        assert ctx.context_manager.get_context() == "ctx"

    def test_edit_msg_defaults_to_none(self, ctx_kwargs):
        """edit_msg 默认为 None"""
        ctx = CommandContext(**ctx_kwargs)
        assert ctx.edit_msg is None

    def test_edit_msg_can_be_set(self, ctx_kwargs):
        """edit_msg 可以被赋值"""
        ctx = CommandContext(**ctx_kwargs)
        ctx.edit_msg = {"role": "user", "content": "edited"}
        assert ctx.edit_msg["content"] == "edited"

    def test_edit_msg_set_to_dict(self, ctx_kwargs):
        """edit_msg 设为 dict 正常"""
        ctx = CommandContext(**ctx_kwargs)
        edit = {"role": "assistant", "content": "modified"}
        ctx.edit_msg = edit
        assert ctx.edit_msg is edit

    def test_messages_mutable(self, ctx_kwargs):
        """messages 列表可修改"""
        ctx = CommandContext(**ctx_kwargs)
        ctx.messages.append({"role": "assistant", "content": "reply"})
        assert len(ctx.messages) == 2

    def test_state_mutable(self, ctx_kwargs):
        """state 字典可修改"""
        ctx = CommandContext(**ctx_kwargs)
        ctx.state["new_key"] = "new_value"
        assert ctx.state["new_key"] == "new_value"

    def test_arg_empty_string(self):
        """arg 为空字符串正常"""
        ctx = CommandContext(
            messages=[], state={}, arg="",
            build_system_prompt=lambda: "",
            get_user_input=lambda p: "",
            context_manager=None,
        )
        assert ctx.arg == ""

    def test_arg_whitespace_string(self):
        """arg 为空白字符串正常"""
        ctx = CommandContext(
            messages=[], state={}, arg="   ",
            build_system_prompt=lambda: "",
            get_user_input=lambda p: "",
            context_manager=None,
        )
        assert ctx.arg == "   "

    def test_slots_defined(self):
        """使用 __slots__，没有 __dict__"""
        ctx = CommandContext(
            messages=[], state={}, arg="",
            build_system_prompt=lambda: "",
            get_user_input=lambda p: "",
            context_manager=None,
        )
        with pytest.raises(AttributeError):
            _ = ctx.__dict__

    def test_all_slots_accessible(self, ctx_kwargs):
        """所有 __slots__ 中定义的属性都可访问"""
        ctx = CommandContext(**ctx_kwargs)
        for attr in ("messages", "state", "arg", "build_system_prompt",
                     "get_user_input", "context_manager", "edit_msg",
                     "ui_adapter"):
            assert hasattr(ctx, attr)

    def test_context_manager_none(self):
        """context_manager 为 None 时可以正常创建"""
        ctx = CommandContext(
            messages=[], state={}, arg="test",
            build_system_prompt=lambda: "",
            get_user_input=lambda p: "",
            context_manager=None,
        )
        assert ctx.context_manager is None
        assert ctx.arg == "test"

    def test_callables_identity(self):
        """build_system_prompt / get_user_input 保持引用同一性"""
        def sp(): return "sp"
        def gui(p): return "gui"
        ctx = CommandContext(
            messages=[], state={}, arg="",
            build_system_prompt=sp,
            get_user_input=gui,
            context_manager=None,
        )
        assert ctx.build_system_prompt is sp
        assert ctx.get_user_input is gui


# ═══════════════════════════════════════════════════════════
#  TestPopAssistantToolMessages
# ═══════════════════════════════════════════════════════════

class TestPopAssistantToolMessages:
    """_pop_assistant_tool_messages 测试"""

    def test_remove_trailing_assistant(self):
        """从末尾移除 assistant 消息"""
        msgs = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "reply"},
        ]
        count = _pop_assistant_tool_messages(msgs)
        assert count == 1
        assert msgs == [{"role": "user", "content": "hi"}]

    def test_remove_trailing_tool(self):
        """从末尾移除 tool 消息（连带前面的 assistant 一起移除）"""
        msgs = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "call"},
            {"role": "tool", "content": "result"},
        ]
        count = _pop_assistant_tool_messages(msgs)
        assert count == 2  # assistant + tool 都被移除
        assert msgs == [
            {"role": "user", "content": "hi"},
        ]

    def test_remove_multiple_trailing(self):
        """从末尾移除所有连续的 assistant/tool 消息"""
        msgs = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "call1"},
            {"role": "tool", "content": "result1"},
            {"role": "assistant", "content": "call2"},
            {"role": "tool", "content": "result2"},
        ]
        count = _pop_assistant_tool_messages(msgs)
        assert count == 4  # 全部 4 条 assistant/tool 被移除
        assert msgs == [
            {"role": "user", "content": "hi"},
        ]

    def test_does_not_remove_user_message(self):
        """不移除以 user 结尾的消息"""
        msgs = [
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "q2"},
        ]
        count = _pop_assistant_tool_messages(msgs)
        assert count == 0
        assert len(msgs) == 3

    def test_does_not_remove_system_message(self):
        """不移除以 system 结尾的消息"""
        msgs = [
            {"role": "system", "content": "sys"},
        ]
        count = _pop_assistant_tool_messages(msgs)
        assert count == 0
        assert msgs == [{"role": "system", "content": "sys"}]

    def test_remove_all_assistant_tool(self):
        """移除所有结尾的 assistant/tool，直到遇到 user/system 或空"""
        msgs = [
            {"role": "assistant", "content": "a1"},
            {"role": "tool", "content": "t1"},
            {"role": "assistant", "content": "a2"},
            {"role": "tool", "content": "t2"},
        ]
        count = _pop_assistant_tool_messages(msgs)
        assert count == 4
        assert msgs == []

    def test_mixed_roles_stops_at_user(self):
        """在 user 消息处停止，不移除 user 之前的内容"""
        msgs = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "a1"},
            {"role": "tool", "content": "t1"},
            {"role": "user", "content": "followup"},
            {"role": "assistant", "content": "a2"},
            {"role": "tool", "content": "t2"},
        ]
        count = _pop_assistant_tool_messages(msgs)
        assert count == 2
        assert msgs == [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "a1"},
            {"role": "tool", "content": "t1"},
            {"role": "user", "content": "followup"},
        ]

    def test_empty_list_returns_zero(self):
        """空列表返回 0"""
        msgs = []
        count = _pop_assistant_tool_messages(msgs)
        assert count == 0
        assert msgs == []

    def test_single_element_user(self):
        """单元素 user 列表返回 0"""
        msgs = [{"role": "user", "content": "hello"}]
        count = _pop_assistant_tool_messages(msgs)
        assert count == 0
        assert msgs == [{"role": "user", "content": "hello"}]

    def test_single_element_assistant(self):
        """单元素 assistant 列表被移除"""
        msgs = [{"role": "assistant", "content": "hello"}]
        count = _pop_assistant_tool_messages(msgs)
        assert count == 1
        assert msgs == []

    def test_messages_unchanged_for_user_end(self):
        """以 user 结尾时消息列表不变"""
        msgs = [
            {"role": "assistant", "content": "a"},
            {"role": "user", "content": "u"},
        ]
        original = list(msgs)
        count = _pop_assistant_tool_messages(msgs)
        assert count == 0
        assert msgs == original

    def test_removes_only_tool(self):
        """移除末尾连续的 assistant/tool 消息"""
        msgs = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "call"},
            {"role": "tool", "content": "result"},
            {"role": "tool", "content": "result2"},
        ]
        count = _pop_assistant_tool_messages(msgs)
        assert count == 3  # assistant + 两个 tool 都被移除
        assert msgs == [
            {"role": "user", "content": "hi"},
        ]

    def test_return_value_is_int(self):
        """返回值是整数"""
        msgs = [{"role": "assistant", "content": "x"}]
        count = _pop_assistant_tool_messages(msgs)
        assert isinstance(count, int)


# ═══════════════════════════════════════════════════════════
#  TestGetRegisteredCommandNames
# ═══════════════════════════════════════════════════════════

class TestGetRegisteredCommandNames:
    """get_registered_command_names 测试"""

    def test_returns_sorted_names(self):
        """返回排序后的命令名列表"""
        register_command("/zzz", lambda ctx: None)
        register_command("/aaa", lambda ctx: None)
        register_command("/mmm", lambda ctx: None)

        names = get_registered_command_names()
        assert names == ["/aaa", "/mmm", "/zzz"]

    def test_help_first(self):
        """/help 排首位"""
        register_command("/help", lambda ctx: None)
        register_command("/clear", lambda ctx: None)
        register_command("/edit", lambda ctx: None)

        names = get_registered_command_names()
        assert names[0] == "/help"
        assert names[1] == "/clear"
        assert names[2] == "/edit"

    def test_help_first_with_many_commands(self):
        """/help 始终排首位，即使有更小的字符串"""
        register_command("/help", lambda ctx: None)
        register_command("/a", lambda ctx: None)
        register_command("/b", lambda ctx: None)

        names = get_registered_command_names()
        assert names[0] == "/help"

    def test_empty_when_no_commands(self):
        """没有注册命令时返回空列表"""
        names = get_registered_command_names()
        assert names == []

    def test_returns_copy(self):
        """返回的列表是副本，修改不影响内部状态"""
        register_command("/test", lambda ctx: None)
        names = get_registered_command_names()
        names.append("/injected")
        names2 = get_registered_command_names()
        assert names2 == ["/test"]

    def test_single_command(self):
        """只有一个命令时正常返回"""
        register_command("/single", lambda ctx: None)
        assert get_registered_command_names() == ["/single"]

    def test_commands_without_slash(self):
        """注册时不带 / 的命令（内部机制允许）"""
        register_command("help", lambda ctx: None)  # 无斜杠
        register_command("/clear", lambda ctx: None)

        names = get_registered_command_names()
        assert "help" in names
        assert "/clear" in names

    def test_sorting_stable(self):
        """多次调用返回相同顺序"""
        register_command("/b", lambda ctx: None)
        register_command("/a", lambda ctx: None)
        register_command("/c", lambda ctx: None)

        assert get_registered_command_names() == get_registered_command_names()


# ═══════════════════════════════════════════════════════════
#  TestShowCost / _format_cost_duration
# ═══════════════════════════════════════════════════════════

class TestFormatCostDuration:
    """_format_cost_duration 辅助函数测试"""

    def test_seconds(self):
        """不足 60 秒返回 X秒 格式"""
        assert _format_cost_duration(0) == "0秒"
        assert _format_cost_duration(1) == "1秒"
        assert _format_cost_duration(30) == "30秒"
        assert _format_cost_duration(59) == "59秒"

    def test_seconds_float_truncated(self):
        """秒数按四舍五入取整（实际是 format 截断）"""
        result = _format_cost_duration(45.7)
        assert result == "46秒"

    def test_minutes(self):
        """60 秒到 3599 秒返回 X分Y秒 格式"""
        assert _format_cost_duration(60) == "1分0秒"
        assert _format_cost_duration(61) == "1分1秒"
        assert _format_cost_duration(120) == "2分0秒"
        assert _format_cost_duration(150) == "2分30秒"
        assert _format_cost_duration(3599) == "59分59秒"

    def test_minutes_boundary(self):
        """边界值测试：59秒→秒；60秒→分"""
        assert _format_cost_duration(59) == "59秒"
        assert _format_cost_duration(60) == "1分0秒"

    def test_hours(self):
        """3600 秒及以上返回 X小时Y分 格式"""
        assert _format_cost_duration(3600) == "1小时0分"
        assert _format_cost_duration(3661) == "1小时1分"
        assert _format_cost_duration(7200) == "2小时0分"
        assert _format_cost_duration(7380) == "2小时3分"
        assert _format_cost_duration(86399) == "23小时59分"

    def test_hours_boundary(self):
        """边界值测试：3599秒→分；3600秒→小时"""
        assert _format_cost_duration(3599) == "59分59秒"
        assert _format_cost_duration(3600) == "1小时0分"

    def test_large_values(self):
        """大数值正常处理"""
        assert _format_cost_duration(100000) == "27小时46分"
        assert _format_cost_duration(999999) == "277小时46分"


class TestShowCost:
    """show_cost 测试（需要 mock 外部依赖）"""

    def _make_ctx(self, model="gpt-4", config_port=None):
        """创建模拟 CommandContext，供 show_cost 测试使用。"""
        return CommandContext(
            messages=[], state={"model": model}, arg="",
            build_system_prompt=lambda: "",
            get_user_input=lambda p: "",
            context_manager=None,
            config_port=config_port,
        )

    def test_uses_token_prices(self, monkeypatch):
        """使用 TOKEN_PRICES 计算费用（config_port=None，回退到 direct import）"""
        from src.core.internal.commands import _command_core

        # Mock 外部依赖
        monkeypatch.setattr(_command_core, "TOKEN_PRICES", {
            "gpt-4": {"input": 0.03, "output": 0.06},
        })
        monkeypatch.setattr(_command_core, "get_token_stats", lambda: {
            "input": 1_000_000, "output": 500_000, "calls": 5,
        })
        monkeypatch.setattr(_command_core, "get_session_start_time", lambda: time.time() - 120)
        monkeypatch.setattr(_command_core, "_get_out", lambda: _MockOutputPort())

        ctx = self._make_ctx(model="gpt-4")
        # 不应抛异常
        show_cost(ctx)

    def test_unknown_model_fallback(self, monkeypatch):
        """未知模型回退到第一个可用价格"""
        from src.core.internal.commands import _command_core

        monkeypatch.setattr(_command_core, "TOKEN_PRICES", {
            "gpt-4": {"input": 0.03, "output": 0.06},
            "gpt-3.5": {"input": 0.01, "output": 0.02},
        })
        monkeypatch.setattr(_command_core, "get_token_stats", lambda: {
            "input": 0, "output": 0, "calls": 0,
        })
        monkeypatch.setattr(_command_core, "get_session_start_time", lambda: time.time())
        monkeypatch.setattr(_command_core, "_get_out", lambda: _MockOutputPort())

        ctx = self._make_ctx(model="unknown-model")
        # 未知模型使用第一个价格表
        show_cost(ctx)

    def test_empty_token_prices_fallback_default(self, monkeypatch):
        """TOKEN_PRICES 为空时使用默认价格"""
        from src.core.internal.commands import _command_core

        monkeypatch.setattr(_command_core, "TOKEN_PRICES", {})
        monkeypatch.setattr(_command_core, "get_token_stats", lambda: {
            "input": 0, "output": 0, "calls": 0,
        })
        monkeypatch.setattr(_command_core, "get_session_start_time", lambda: time.time())
        monkeypatch.setattr(_command_core, "_get_out", lambda: _MockOutputPort())

        ctx = self._make_ctx(model="gpt-4")
        show_cost(ctx)  # 使用默认价格 0.01/0.03

    def test_zero_tokens(self, monkeypatch):
        """零 token 时费用为 0"""
        from src.core.internal.commands import _command_core
        outputs = []

        class Port:
            def write(self, text, level="info", source="core"):
                outputs.append(text)

        monkeypatch.setattr(_command_core, "TOKEN_PRICES", {
            "gpt-4": {"input": 0.03, "output": 0.06},
        })
        monkeypatch.setattr(_command_core, "get_token_stats", lambda: {
            "input": 0, "output": 0, "calls": 0,
        })
        monkeypatch.setattr(_command_core, "get_session_start_time", lambda: time.time())
        monkeypatch.setattr(_command_core, "_get_out", lambda: Port())

        ctx = self._make_ctx(model="gpt-4")
        show_cost(ctx)
        cost_lines = [l for l in outputs if "$" in l]
        assert any("$0.0000" in l for l in cost_lines)

    def test_output_port_write_called(self, monkeypatch):
        """show_cost 调用 _out.write 输出结果"""
        from src.core.internal.commands import _command_core
        outputs = []

        class Port:
            def write(self, text, level="info", source="core"):
                outputs.append(text)

        monkeypatch.setattr(_command_core, "TOKEN_PRICES", {
            "gpt-4": {"input": 0.03, "output": 0.06},
        })
        monkeypatch.setattr(_command_core, "get_token_stats", lambda: {
            "input": 1000, "output": 500, "calls": 2,
        })
        monkeypatch.setattr(_command_core, "get_session_start_time", lambda: time.time() - 300)
        monkeypatch.setattr(_command_core, "_get_out", lambda: Port())

        ctx = self._make_ctx(model="gpt-4")
        show_cost(ctx)
        assert len(outputs) > 0
        # 验证包含了关键信息
        all_text = "\n".join(outputs)
        assert "gpt-4" in all_text
        assert "费用统计" in all_text or "┌" in all_text or "└" in all_text

    def test_config_port_fallback(self, monkeypatch):
        """config_port 为 None 时回退到 TOKEN_PRICES（向后兼容）"""
        from src.core.internal.commands import _command_core

        monkeypatch.setattr(_command_core, "TOKEN_PRICES", {
            "gpt-4": {"input": 0.03, "output": 0.06},
        })
        monkeypatch.setattr(_command_core, "get_token_stats", lambda: {
            "input": 500_000, "output": 250_000, "calls": 1,
        })
        monkeypatch.setattr(_command_core, "get_session_start_time", lambda: time.time())
        monkeypatch.setattr(_command_core, "_get_out", lambda: _MockOutputPort())

        ctx = self._make_ctx(model="gpt-4", config_port=None)
        show_cost(ctx)  # 不应抛异常

    def test_config_port_used(self, monkeypatch):
        """config_port 存在时优先通过端口获取价格"""
        from src.core.internal.commands import _command_core

        # 通过 config_port 返回不同价格，确保它不是来自 TOKEN_PRICES
        mock_cp = MagicMock()
        mock_cp.get_token_prices.return_value = {
            "gpt-4": {"input": 0.05, "output": 0.10},
        }

        monkeypatch.setattr(_command_core, "TOKEN_PRICES", {
            "gpt-4": {"input": 0.03, "output": 0.06},  # 这个不应被使用
        })
        monkeypatch.setattr(_command_core, "get_token_stats", lambda: {
            "input": 0, "output": 0, "calls": 0,
        })
        monkeypatch.setattr(_command_core, "get_session_start_time", lambda: time.time())
        monkeypatch.setattr(_command_core, "_get_out", lambda: _MockOutputPort())

        ctx = self._make_ctx(model="gpt-4", config_port=mock_cp)
        show_cost(ctx)
        # 验证使用了 config_port 而非 TOKEN_PRICES
        mock_cp.get_token_prices.assert_called()


# ═══════════════════════════════════════════════════════════
#  TestCommandHelpText
# ═══════════════════════════════════════════════════════════

class TestCommandHelpText:
    """COMMANDS_HELP 常量测试"""

    def test_help_text_defined(self):
        """COMMANDS_HELP 是字符串且非空"""
        assert isinstance(COMMANDS_HELP, str)
        assert len(COMMANDS_HELP) > 0

    def test_help_contains_common_commands(self):
        """帮助文本包含常见命令"""
        assert "/clear" in COMMANDS_HELP
        assert "/help" in COMMANDS_HELP
        assert "/model" in COMMANDS_HELP
        assert "/cost" in COMMANDS_HELP
        assert "/system" in COMMANDS_HELP

    def test_help_contains_exit_hint(self):
        """帮助文本包含退出提示"""
        assert "exit" in COMMANDS_HELP


# ═══════════════════════════════════════════════════════════
#  TestHandleCommandIntegration
# ═══════════════════════════════════════════════════════════

class TestHandleCommandIntegration:
    """handle_command 集成测试"""

    def test_command_modifies_state(self):
        """命令可以修改 state"""
        state = {"count": 0}

        def increment(ctx):
            ctx.state["count"] += 1
            return True

        register_command("/inc", increment)
        handle_command("/inc", [], state, lambda: "", lambda p: "")
        assert state["count"] == 1

        handle_command("/inc", [], state, lambda: "", lambda p: "")
        assert state["count"] == 2

    def test_command_modifies_messages(self):
        """命令可以修改 messages"""
        messages = [{"role": "user", "content": "original"}]

        def appender(ctx):
            ctx.messages.append({"role": "assistant", "content": "appended"})
            return True

        register_command("/append", appender)
        handle_command("/append", messages, {}, lambda: "", lambda p: "")
        assert len(messages) == 2
        assert messages[1]["content"] == "appended"

    def test_multiple_commands_isolated(self):
        """多个命令互不影响"""
        def cmd1(ctx):
            ctx.state["cmd1_called"] = True
            return "one"

        def cmd2(ctx):
            ctx.state["cmd2_called"] = True
            return "two"

        register_command("/cmd1", cmd1)
        register_command("/cmd2", cmd2)

        state = {}
        r1 = handle_command("/cmd1", [], state, lambda: "", lambda p: "")
        assert r1 == "one"
        assert state.get("cmd2_called") is None

        r2 = handle_command("/cmd2", [], state, lambda: "", lambda p: "")
        assert r2 == "two"
        assert state["cmd1_called"] is True

    def test_register_then_unregister_after_cleanup(self):
        """确保 cleanup_commands fixture 正确清理"""
        register_command("/temp", lambda ctx: "temp")
        assert handle_command("/temp", [], {}, lambda: "", lambda p: "") == "temp"

    # cleanup_commands fixture 会在本测试后清理 _commands


# ═══════════════════════════════════════════════════════════
#  辅助
# ═══════════════════════════════════════════════════════════

class _MockOutputPort:
    """模拟输出端口，不执行实际输出"""
    def write(self, text: str, level: str = "info", source: str = "core") -> None:
        pass

    def write_with_lock(self, text: str, level: str = "info", source: str = "core") -> None:
        pass

    def locked(self):
        from contextlib import nullcontext
        return nullcontext()
