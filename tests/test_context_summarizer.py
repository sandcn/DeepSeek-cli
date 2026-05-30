#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""测试 src.core.context_summarizer：摘要 prompt 构建与摘要生成。

测试策略
--------
- 直接导入被测试模块（依赖树可正常加载）
- 纯函数测试，不调用外部模型/summarize_fn（使用 mock）
- 每个测试函数关注单个函数的一种行为，遵循"一个断言概念一个测试"
- 边界值、异常路径、正常路径全覆盖
- 使用 pytest class 组织测试
"""

import sys
import pytest
from unittest.mock import MagicMock, patch, ANY

from src.core.context_summarizer import (
    build_summary_prompt,
    summarize,
    _SUMMARY_SYSTEM,
    _SUMMARY_TEMPLATE,
    _PRIOR_HINT_MERGE,
)

# ── 测试数据 ────────────────────────────────────────────────────────────────

SINGLE_MSG = [
    {"role": "user", "content": "你好，请帮我写一个Python函数"},
]

MULTI_MSGS = [
    {"role": "user", "content": "帮我写一个fibonacci函数"},
    {"role": "assistant", "content": "def fib(n):\n    if n <= 1: return n\n    return fib(n-1) + fib(n-2)"},
    {"role": "user", "content": "改成迭代版本"},
]

TOOL_MSGS = [
    {"role": "user", "content": "运行一下代码"},
    {"role": "assistant", "content": "让我运行代码", "tool_calls": [{"function": {"name": "run_code", "arguments": '{"code": "print(1)"}'}}]},
    {"role": "tool", "content": "1\n", "tool_call_id": "call_123"},
]

LONG_MSG = [
    {"role": "user", "content": "A" * 10000},  # 测试截断
]

PRIOR_MSGS = [
    {"role": "system", "content": "[对话摘要] 之前的对话摘要"},
    {"role": "user", "content": "继续之前的话题"},
]


# ═══════════════════════════════════════════════════════════════════════════
# 1. 常量验证
# ═══════════════════════════════════════════════════════════════════════════

class TestConstants:
    """_SUMMARY_SYSTEM / _SUMMARY_TEMPLATE / _PRIOR_HINT_MERGE 常量验证。"""

    def test_summary_system_exists_and_not_empty(self):
        assert _SUMMARY_SYSTEM, "_SUMMARY_SYSTEM 不应为空"
        assert len(_SUMMARY_SYSTEM) > 50

    def test_summary_template_exists_and_not_empty(self):
        assert _SUMMARY_TEMPLATE, "_SUMMARY_TEMPLATE 不应为空"
        assert len(_SUMMARY_TEMPLATE) > 100

    def test_prior_hint_merge_exists_and_not_empty(self):
        assert _PRIOR_HINT_MERGE, "_PRIOR_HINT_MERGE 不应为空"
        assert "对话摘要" in _PRIOR_HINT_MERGE

    def test_template_has_prior_hint_placeholder(self):
        assert "{prior_hint}" in _SUMMARY_TEMPLATE

    def test_template_has_conversation_placeholder(self):
        assert "{conversation}" in _SUMMARY_TEMPLATE

    def test_template_has_separator_line(self):
        assert "--- 对话内容 ---" in _SUMMARY_TEMPLATE


# ═══════════════════════════════════════════════════════════════════════════
# 2. build_summary_prompt — 基本场景
# ═══════════════════════════════════════════════════════════════════════════

class TestBuildSummaryPromptBasic:
    """build_summary_prompt 基本调用场景。"""

    def test_empty_messages_returns_empty_string(self):
        """空消息列表 → 返回空字符串。"""
        result = build_summary_prompt([], False)
        assert result == ""

    def test_empty_messages_ignores_has_prior_summary(self):
        """空消息列表时 has_prior_summary 不影响结果。"""
        assert build_summary_prompt([], True) == ""
        assert build_summary_prompt([], False) == ""

    def test_single_user_message(self):
        """一条 user 消息 → 生成包含 role 标记的 prompt。"""
        result = build_summary_prompt(SINGLE_MSG, False)
        assert "--- 对话内容 ---" in result
        assert "user: 你好，请帮我写一个Python函数" in result
        assert result.count("user:") == 1

    def test_single_assistant_message(self):
        """一条 assistant 消息。"""
        msgs = [{"role": "assistant", "content": "这是回复内容"}]
        result = build_summary_prompt(msgs, False)
        assert "assistant: 这是回复内容" in result

    def test_multiple_messages(self):
        """多条消息（user + assistant）→ 拼接正确。"""
        result = build_summary_prompt(MULTI_MSGS, False)
        assert "user: 帮我写一个fibonacci函数" in result
        assert "assistant: def fib(n):" in result
        assert "user: 改成迭代版本" in result

    def test_messages_order_preserved(self):
        """消息顺序保持一致。"""
        result = build_summary_prompt(MULTI_MSGS, False)
        idx_fib = result.index("fibonacci")
        idx_def = result.index("def fib(n)")
        idx_iter = result.index("迭代版本")
        assert idx_fib < idx_def < idx_iter, "消息顺序应与输入一致"

    def test_tool_message(self):
        """tool 角色消息带有 [工具结果] 前缀。"""
        result = build_summary_prompt(TOOL_MSGS, False)
        assert "tool: [工具结果 call_123] 1" in result

    def test_assistant_with_tool_calls(self):
        """assistant 带 tool_calls 的消息包含工具调用信息。"""
        result = build_summary_prompt(TOOL_MSGS, False)
        assert "[调用工具 run_code" in result

    def test_message_contains_only_role_and_content(self):
        """消息只有 role 和 content 字段时正常处理。"""
        msgs = [{"role": "user", "content": "简单消息"}]
        result = build_summary_prompt(msgs, False)
        assert "user: 简单消息" in result

    def test_message_with_unknown_role(self):
        """未知 role 保留原始 role 值。"""
        msgs = [{"role": "custom_role", "content": "测试"}]
        result = build_summary_prompt(msgs, False)
        assert "custom_role: 测试" in result

    def test_message_with_empty_content(self):
        """content 为空时显示空内容。"""
        msgs = [{"role": "user", "content": ""}]
        result = build_summary_prompt(msgs, False)
        assert "user: " in result

    def test_message_missing_content_key(self):
        """缺少 content key 时安全处理。"""
        msgs = [{"role": "user"}]
        result = build_summary_prompt(msgs, False)
        assert "user: " in result

    def test_message_content_is_none(self):
        """content 为 None 时安全处理。"""
        msgs = [{"role": "user", "content": None}]
        result = build_summary_prompt(msgs, False)
        assert "user: " in result


# ═══════════════════════════════════════════════════════════════════════════
# 3. build_summary_prompt — 截断逻辑
# ═══════════════════════════════════════════════════════════════════════════

class TestBuildSummaryPromptTruncation:
    """build_summary_prompt 截断行为。"""

    def test_short_message_not_truncated(self):
        """短消息不会被截断。"""
        msgs = [{"role": "user", "content": "短消息"}]
        result = build_summary_prompt(msgs, False)
        assert "短消息" in result

    def test_long_non_tool_message_truncated_by_per_msg(self):
        """非 tool 长消息按 per_msg（budget_chars // n）截断。"""
        # 1条消息：per_msg = max(200, 3000) = 3000
        # content 10000 字符 → 截断到 3000
        result = build_summary_prompt(LONG_MSG, False)
        content_part = result.split("--- 对话内容 ---")[1]
        # 提取 user: 后面的内容
        user_text = content_part.split("user: ", 1)[1]
        assert len(user_text) == 3000, f"期望 3000 字符，实际 {len(user_text)}"

    def test_long_tool_message_truncated_by_tool_output_truncate(self):
        """tool 消息按 TOOL_OUTPUT_TRUNCATE 截断并附加后缀。"""
        # 构建一条超长 tool 消息（超过 500 字符）
        long_tool_content = "X" * 1000
        msgs = [
            {"role": "user", "content": "运行"},
            {"role": "tool", "content": long_tool_content, "tool_call_id": "call_999"},
        ]
        result = build_summary_prompt(msgs, False)
        # tool 截断长度 = TOOL_OUTPUT_TRUNCATE (500) + "...(已截断)"
        # 注意 message_to_text 给 tool 消息加了前缀 "[工具结果 call_999] "
        # 所以 text 的长度 = 前缀 + "X"*1000
        # 截断后 text[:500] + "...(已截断)" 共 500+5=505 字符
        assert "...(已截断)" in result
        tool_line = [line for line in result.split("\n") if line.startswith("tool:")][0]
        # 后缀应该在末尾
        assert tool_line.endswith("...(已截断)")

    def test_tool_message_shorter_than_truncate_not_cut(self):
        """tool 消息短于 TOOL_OUTPUT_TRUNCATE 时不被截断。"""
        short_tool = "正常输出"
        msgs = [
            {"role": "user", "content": "运行"},
            {"role": "tool", "content": short_tool, "tool_call_id": "call_111"},
        ]
        result = build_summary_prompt(msgs, False)
        assert short_tool in result
        assert "...(已截断)" not in result

    def test_per_msg_has_minimum_200_chars(self):
        """per_msg 最小值为 200。"""
        # 20 条消息时：per_msg = max(200, 3000 // 20) = max(200, 150) = 200
        msgs = [{"role": "user", "content": f"消息{i}"} for i in range(20)]
        result = build_summary_prompt(msgs, False)
        # 每条消息内容应该最多 200 字符（但"消息i"很短，不会被截断）
        # 验证每条消息都存在
        for i in range(20):
            assert f"消息{i}" in result

    def test_many_short_messages_not_truncated(self):
        """消息很多但每条都很短时，per_msg 下限 200 足够容纳。"""
        msgs = [{"role": "user", "content": "hi"} for _ in range(50)]
        # per_msg = max(200, 3000 // 50) = max(200, 60) = 200
        result = build_summary_prompt(msgs, False)
        assert result.count("user: hi") == 50

    def test_assistant_long_text_truncated_by_per_msg(self):
        """assistant 消息也按 per_msg 截断（非 tool 角色）。"""
        long_content = "B" * 5000
        msgs = [{"role": "user", "content": "q"}, {"role": "assistant", "content": long_content}]
        # 2条消息：per_msg = max(200, 3000 // 2) = max(200, 1500) = 1500
        result = build_summary_prompt(msgs, False)
        content_part = result.split("--- 对话内容 ---")[1]
        # 第一行：user: q
        # 第二行：assistant: <1500 chars>
        lines = content_part.strip().split("\n")
        assistant_line = [l for l in lines if l.startswith("assistant:")][0]
        assistant_text = assistant_line.split("assistant: ", 1)[1]
        assert len(assistant_text) == 1500, f"期望 1500 字符，实际 {len(assistant_text)}"


# ═══════════════════════════════════════════════════════════════════════════
# 4. build_summary_prompt — 旧摘要处理
# ═══════════════════════════════════════════════════════════════════════════

class TestBuildSummaryPromptPriorSummary:
    """has_prior_summary 参数行为。"""

    def test_no_prior_summary(self):
        """has_prior_summary=False → 不插入旧摘要合并提示。"""
        result = build_summary_prompt(SINGLE_MSG, False)
        assert _PRIOR_HINT_MERGE.strip() not in result
        # 确保 prior_hint 不包含 _PRIOR_HINT_MERGE 的内容
        assert "之前的摘要" not in result

    def test_with_prior_summary(self):
        """has_prior_summary=True → 插入旧摘要合并提示。"""
        result = build_summary_prompt(SINGLE_MSG, True)
        assert "之前的摘要" in result
        assert "整合为一份完整摘要" in result

    def test_prior_hint_before_conversation_separator(self):
        """旧摘要提示位于 '--- 对话内容 ---' 之前。"""
        result = build_summary_prompt(SINGLE_MSG, True)
        idx_hint = result.index("之前的摘要")
        idx_sep = result.index("--- 对话内容 ---")
        assert idx_hint < idx_sep, "旧摘要提示应出现在分隔行之前"

    def test_prior_hint_not_in_no_prior_output(self):
        """没有旧摘要时，分隔行之前不应有合并提示。"""
        result = build_summary_prompt(SINGLE_MSG, False)
        idx_sep = result.index("--- 对话内容 ---")
        before_sep = result[:idx_sep]
        assert "之前的摘要" not in before_sep
        assert "整合" not in before_sep


# ═══════════════════════════════════════════════════════════════════════════
# 5. build_summary_prompt — 格式验证
# ═══════════════════════════════════════════════════════════════════════════

class TestBuildSummaryPromptFormat:
    """输出格式正确性。"""

    def test_contains_separator_line(self):
        """输出包含 '--- 对话内容 ---' 分隔行。"""
        result = build_summary_prompt(SINGLE_MSG, False)
        assert "--- 对话内容 ---" in result

    def test_separator_appears_once(self):
        """分隔行只出现一次。"""
        result = build_summary_prompt(SINGLE_MSG, False)
        assert result.count("--- 对话内容 ---") == 1

    def test_message_role_prefix_format(self):
        """每条消息首行以 'role: ' 格式前缀开头。"""
        result = build_summary_prompt(MULTI_MSGS, False)
        after_sep = result.split("--- 对话内容 ---")[1]
        lines = [l for l in after_sep.split("\n") if l.strip()]
        # 每条消息的第一行应该以 role: 开头
        # MULTI_MSGS 中 assistant 消息包含换行，所以消息行数 > 消息数
        role_lines = [l for l in lines if l[0].isalpha() and ": " in l
                      and l.split(": ", 1)[0] in ("user", "assistant", "tool", "system")]
        assert len(role_lines) == len(MULTI_MSGS), f"期望 {len(MULTI_MSGS)} 条带 role 前缀的行，实际 {len(role_lines)}"
        for line in role_lines:
            role_prefix = line.split(": ", 1)[0]
            assert role_prefix in ("user", "assistant"), f"未知 role 前缀: {role_prefix}"

    def test_template_header_present(self):
        """输出包含模板的头部内容。"""
        result = build_summary_prompt(SINGLE_MSG, False)
        assert "将以下对话压缩为结构化摘要" in result
        assert "保留优先级" in result

    def test_template_and_conversation_are_joined_correctly(self):
        """模板头部 → (prior_hint) → 分隔行 → 对话内容 顺序正确。"""
        result = build_summary_prompt(MULTI_MSGS, False)
        idx_header = result.index("将以下对话压缩为结构化摘要")
        idx_sep = result.index("--- 对话内容 ---")
        idx_conversation = result.index("帮我写一个fibonacci函数")
        assert idx_header < idx_sep < idx_conversation, "各部分顺序不正确"

    def test_prior_summary_order_with_prior(self):
        """有旧摘要时：模板 → prior_hint → 分隔行 → 对话内容。"""
        result = build_summary_prompt(SINGLE_MSG, True)
        idx_header = result.index("将以下对话压缩为结构化摘要")
        idx_hint = result.index("之前的摘要")
        idx_sep = result.index("--- 对话内容 ---")
        assert idx_header < idx_hint < idx_sep, "有旧摘要时各部分顺序不正确"

    def test_conversation_lines_joined_with_newline(self):
        """多条消息以换行符连接（消息本身不含换行时一一对应）。"""
        simple_msgs = [
            {"role": "user", "content": "第一条消息"},
            {"role": "assistant", "content": "第二条消息"},
            {"role": "user", "content": "第三条消息"},
        ]
        result = build_summary_prompt(simple_msgs, False)
        after_sep = result.split("--- 对话内容 ---")[1]
        role_lines = [l for l in after_sep.split("\n") if l.strip()]
        assert len(role_lines) == len(simple_msgs), f"期望 {len(simple_msgs)} 行，实际 {len(role_lines)}"


# ═══════════════════════════════════════════════════════════════════════════
# 6. summarize — 函数契约验证
# ═══════════════════════════════════════════════════════════════════════════

class TestSummarize:
    """summarize 函数输入输出合约验证。"""

    def test_empty_messages_raises_value_error(self):
        """空消息列表 → 抛出 ValueError。"""
        with pytest.raises(ValueError, match="没有可压缩的消息"):
            summarize([], False, MagicMock(), "test_model")

    def test_summarize_fn_called_with_correct_params(self):
        """验证 summarize_fn 被正确的参数调用。"""
        mock_fn = MagicMock(return_value=("", "摘要内容摘要内容摘要内容摘要内容摘要", {"prompt_tokens": 100, "completion_tokens": 50}, None))
        summarize(SINGLE_MSG, False, mock_fn, "gpt-4")

        # 验证 summarize_fn 被调用一次（成功，不需要重试）
        mock_fn.assert_called_once()
        args, kwargs = mock_fn.call_args
        messages, model = args[0], kwargs.get("model")

        # 验证 messages 结构
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == _SUMMARY_SYSTEM
        assert messages[1]["role"] == "user"
        assert "--- 对话内容 ---" in messages[1]["content"]

        # 验证 model 参数
        assert model == "gpt-4"

    def test_summarize_passes_has_prior_summary(self):
        """验证 has_prior_summary 正确传递给 build_summary_prompt。"""
        mock_fn = MagicMock(return_value=("", "摘要内容摘要内容摘要内容摘要内容摘要", {"prompt_tokens": 100, "completion_tokens": 50}, None))

        # 有旧摘要
        summarize(SINGLE_MSG, True, mock_fn, "test")
        args, _ = mock_fn.call_args
        user_msg = args[0][1]["content"]
        assert "之前的摘要" in user_msg

        # 重置 mock
        mock_fn.reset_mock()

        # 无旧摘要
        summarize(SINGLE_MSG, False, mock_fn, "test")
        args, _ = mock_fn.call_args
        user_msg = args[0][1]["content"]
        assert "之前的摘要" not in user_msg

    def test_summarize_returns_tuple(self):
        """返回值格式为 (摘要文本, usage字典)。"""
        mock_fn = MagicMock(return_value=("", "这是摘要内容这是摘要内容这是摘要内容", {"prompt_tokens": 100, "completion_tokens": 50}, None))
        result = summarize(SINGLE_MSG, False, mock_fn, "test")
        assert isinstance(result, tuple)
        assert len(result) == 2
        summary_text, usage = result
        assert isinstance(summary_text, str)
        assert isinstance(usage, dict)

    def test_summarize_retries_on_failure(self):
        """失败时自动重试。"""
        mock_fn = MagicMock()
        # 第一次失败，第二次成功
        mock_fn.side_effect = [
            Exception("API错误"),
            ("", "最终摘要成功最终摘要成功最终摘要成功最终", {"t": 1}, None),
        ]
        result = summarize(SINGLE_MSG, False, mock_fn, "test")
        assert mock_fn.call_count == 2
        assert result[0] == "最终摘要成功最终摘要成功最终摘要成功最终"

    def test_summarize_raises_after_all_retries(self):
        """所有重试都失败后抛出异常。"""
        mock_fn = MagicMock(side_effect=Exception("持续失败"))
        with pytest.raises(Exception):
            summarize(SINGLE_MSG, False, mock_fn, "test")
        assert mock_fn.call_count == 2  # 初始 + 1次重试

    def test_summarize_short_summary_triggers_retry(self):
        """摘要内容不足 10 字符时触发重试。"""
        mock_fn = MagicMock()
        # 第一次返回太短的摘要（去除 strip 后 < 10），第二次返回有效摘要
        mock_fn.side_effect = [
            ("", "短", {"t": 1}, None),
            ("", "有效摘要内容有效摘要内容有效摘要内容有效", {"t": 2}, None),
        ]
        result = summarize(SINGLE_MSG, False, mock_fn, "test")
        assert mock_fn.call_count == 2
        assert result[0] == "有效摘要内容有效摘要内容有效摘要内容有效"

    def test_summarize_returns_stripped_summary(self):
        """返回的摘要经过 strip 处理。"""
        mock_fn = MagicMock(return_value=("", "  带空白的摘要内容带空白的摘要内容  ", {"t": 1}, None))
        result = summarize(SINGLE_MSG, False, mock_fn, "test")
        assert result[0] == "带空白的摘要内容带空白的摘要内容"
        assert result[0] == result[0].strip()

    def test_summarize_passes_model_name(self):
        """model 参数正确传递给 summarize_fn。"""
        mock_fn = MagicMock(return_value=("", "有效摘要内容有效摘要内容有效摘要内容有效摘要内容", {"t": 1}, None))
        summarize(SINGLE_MSG, False, mock_fn, "gpt-4-turbo")
        _, kwargs = mock_fn.call_args
        assert kwargs.get("model") == "gpt-4-turbo"


# ═══════════════════════════════════════════════════════════════════════════
# 7. 综合边界情况
# ═══════════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    """跨函数的边界条件综合测试。"""

    def test_system_message_in_conversation(self):
        """system 消息作为对话内容的一部分出现。"""
        msgs = [
            {"role": "system", "content": "你是助手"},
            {"role": "user", "content": "你好"},
        ]
        result = build_summary_prompt(msgs, False)
        assert "system: 你是助手" in result
        assert "user: 你好" in result

    def test_single_character_messages(self):
        """单字符消息。"""
        msgs = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]
        result = build_summary_prompt(msgs, False)
        assert "user: a" in result
        assert "assistant: b" in result

    def test_unicode_content(self):
        """Unicode 内容正常保留。"""
        msgs = [{"role": "user", "content": "你好世界 🌍 \u4f60\u597d"}]
        result = build_summary_prompt(msgs, False)
        assert "你好世界" in result
        assert "\u4f60\u597d" in result

    def test_special_characters_in_content(self):
        """特殊字符（换行符、制表符）在消息内容中。"""
        content = "line1\nline2\tindented"
        msgs = [{"role": "user", "content": content}]
        result = build_summary_prompt(msgs, False)
        assert "line1\nline2\tindented" in result

    def test_tool_msg_with_tool_call_id_prefix(self):
        """tool 消息的 [工具结果 id] 前缀格式。"""
        msgs = [{"role": "tool", "content": "output", "tool_call_id": "abc123xyz789"}]
        result = build_summary_prompt(msgs, False)
        assert "tool: [工具结果 abc123xyz789] output" in result

    def test_multiple_tool_messages(self):
        """多条 tool 消息。"""
        msgs = [
            {"role": "tool", "content": "结果1", "tool_call_id": "call_1"},
            {"role": "tool", "content": "结果2", "tool_call_id": "call_2"},
        ]
        result = build_summary_prompt(msgs, False)
        assert result.count("tool:") == 2
        assert "call_1" in result
        assert "call_2" in result

    def test_messages_with_exactly_per_msg_length_not_truncated(self):
        """消息长度恰好等于 per_msg 时不被截断。"""
        # 2条消息：per_msg = 1500
        content = "C" * 1500
        msgs = [{"role": "user", "content": content}]
        result = build_summary_prompt(msgs, False)
        # 注意：还有另一条消息吗？msgs只有1条，per_msg=3000
        # 我们改成3条消息让per_msg=1000
        msgs = [
            {"role": "user", "content": "C" * 1000},
            {"role": "user", "content": "D" * 1000},
            {"role": "user", "content": "E" * 1000},
        ]
        result = build_summary_prompt(msgs, False)
        after_sep = result.split("--- 对话内容 ---")[1]
        lines = [l for l in after_sep.split("\n") if l.strip()]
        for line in lines:
            # 每行都是 1000 字符的 content
            assert len(line.split(": ", 1)[1]) == 1000

    def test_mixed_tool_and_non_tool_truncation(self):
        """混合 tool 和非 tool 消息，各自按不同规则截断。"""
        # 1条 user + 1条 tool，per_msg = 1500
        # tool 的 text 包含前缀，超过 500 时按 TOOL_OUTPUT_TRUNCATE 截断
        # user 的 text 超过 1500 时按 per_msg 截断
        msgs = [
            {"role": "user", "content": "U" * 3000},
            {"role": "tool", "content": "T" * 1000, "tool_call_id": "call_x"},
        ]
        result = build_summary_prompt(msgs, False)
        after_sep = result.split("--- 对话内容 ---")[1]
        lines = [l for l in after_sep.split("\n") if l.strip()]

        # user line
        user_line = [l for l in lines if l.startswith("user:")][0]
        user_text = user_line.split("user: ", 1)[1]
        assert len(user_text) == 1500, f"user 应按 per_msg(1500) 截断，实际 {len(user_text)}"

        # tool line
        tool_line = [l for l in lines if l.startswith("tool:")][0]
        # tool text = "[工具结果 call_x] " + "T"*1000，共 18+1000=1018 字符
        # 截断后：[工具结果 call_x] <前500-18=482个T>...(已截断)
        assert "...(已截断)" in tool_line

    def test_large_number_of_messages(self):
        """大量消息时正常处理，不崩溃。"""
        msgs = [{"role": "user", "content": f"msg{i}"} for i in range(100)]
        result = build_summary_prompt(msgs, False)
        assert result.count("user:") == 100

    def test_budget_chars_calculation(self):
        """验证 budget_chars = SUMMARY_TOKEN_BUDGET * 1.5 = 3000。"""
        # 7条消息：per_msg = max(200, 3000 // 7) = max(200, 428) = 428
        # 15条消息：per_msg = max(200, 200) = 200
        msgs_7 = [{"role": "user", "content": "x"} for _ in range(7)]
        msgs_15 = [{"role": "user", "content": "y"} for _ in range(15)]

        result_7 = build_summary_prompt(msgs_7, False)
        result_15 = build_summary_prompt(msgs_15, False)

        # 验证都能正常生成
        assert "--- 对话内容 ---" in result_7
        assert "--- 对话内容 ---" in result_15

    def test_summarize_empty_content_after_retry(self):
        """所有重试都返回空内容/短内容时抛出 ValueError。"""
        mock_fn = MagicMock(return_value=("", "短", {"t": 1}, None))
        with pytest.raises(ValueError, match="摘要内容无效"):
            summarize(SINGLE_MSG, False, mock_fn, "test")
        assert mock_fn.call_count == 2

    def test_summarize_none_summary(self):
        """summarize_fn 返回 None 作为 summary 时触发重试。"""
        mock_fn = MagicMock()
        mock_fn.side_effect = [
            ("", None, {"t": 1}, None),
            ("", "有效摘要有效摘要有效摘要有效摘要有效摘要", {"t": 2}, None),
        ]
        result = summarize(SINGLE_MSG, False, mock_fn, "test")
        assert mock_fn.call_count == 2
        assert result[0] == "有效摘要有效摘要有效摘要有效摘要有效摘要"
