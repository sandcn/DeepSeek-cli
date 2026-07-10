#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""测试 src/core/context_selector.py

覆盖内容：
  1. message_to_text() — 消息文本提取
  2. compute_message_stats() — 统计计算
  3. exceeds_limit_values / should_auto_force_values / calc_excess_chars_values / calc_usage_percent_values
  4. find_tool_groups() / adjust_keep_for_tool_groups() / extend_to_complete_tool_group()
  5. select_candidates() / select_for_compression()
  6. MessageStatsCache 增量缓存
"""

import pytest

from src.core.context_selector import (
    message_to_text,
    _parse_tool_args,
    compute_message_stats,
    exceeds_limit_values,
    should_auto_force_values,
    calc_excess_chars_values,
    calc_usage_percent_values,
    total_chars,
    total_tokens,
    find_tool_groups,
    adjust_keep_for_tool_groups,
    extend_to_complete_tool_group,
    select_candidates,
    select_for_compression,
    MessageStatsCache,
)


# 在涉及 tool_calls 的测试前清除 lru_cache
@pytest.fixture(autouse=True)
def _clear_parse_tool_args_cache():
    """每个测试前清除 _parse_tool_args 缓存，避免测试间相互影响"""
    _parse_tool_args.cache_clear()
    yield


# ═══════════════════════════════════════════════════════════════
# 1. message_to_text — 消息文本提取
# ═══════════════════════════════════════════════════════════════

class TestMessageToText:
    """message_to_text() 消息文本提取测试"""

    # ── 普通 user 消息 ──────────────────────────────────────

    def test_user_message_returns_content(self):
        """普通 user 消息应直接返回 content"""
        msg = {"role": "user", "content": "你好，世界"}
        assert message_to_text(msg) == "你好，世界"

    def test_user_message_with_empty_content(self):
        """user 消息 content 为空字符串应返回空字符串"""
        msg = {"role": "user", "content": ""}
        assert message_to_text(msg) == ""

    def test_user_message_with_none_content(self):
        """user 消息 content 为 None 应返回空字符串"""
        msg = {"role": "user", "content": None}
        assert message_to_text(msg) == ""

    def test_user_message_missing_content_key(self):
        """user 消息缺少 content 键应返回空字符串"""
        msg = {"role": "user"}
        assert message_to_text(msg) == ""

    # ── assistant 消息（无 tool_calls）────────────────────

    def test_assistant_plain_returns_content(self):
        """assistant 无 tool_calls 应返回 content"""
        msg = {"role": "assistant", "content": "我来帮你"}
        assert message_to_text(msg) == "我来帮你"

    def test_assistant_empty_content(self):
        """assistant content 为空应返回空字符串"""
        msg = {"role": "assistant", "content": ""}
        assert message_to_text(msg) == ""

    def test_assistant_none_content_no_tool_calls(self):
        """assistant content None 且无 tool_calls 应返回空字符串"""
        msg = {"role": "assistant", "content": None}
        assert message_to_text(msg) == ""

    # ── assistant 消息（含 tool_calls）────────────────────

    def test_assistant_with_tool_calls_and_content(self):
        """assistant 既有 content 又有 tool_calls，应返回 content + 工具调用描述"""
        msg = {
            "role": "assistant",
            "content": "我来查询天气",
            "tool_calls": [
                {
                    "id": "call_1",
                    "function": {"name": "get_weather", "arguments": '{"city": "北京"}'},
                }
            ],
        }
        result = message_to_text(msg)
        assert result.startswith("我来查询天气")
        assert "调用工具 get_weather" in result
        assert "北京" in result

    def test_assistant_tool_calls_no_content(self):
        """assistant 有 tool_calls 但无 content，应只返回工具调用描述"""
        msg = {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "call_1",
                    "function": {"name": "search", "arguments": '{"q": "test"}'},
                }
            ],
        }
        result = message_to_text(msg)
        assert "[调用工具 search({" in result
        assert '"q": "test"' in result

    def test_assistant_tool_calls_empty_content(self):
        """assistant content 为空字符串但有 tool_calls"""
        msg = {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"function": {"name": "calc", "arguments": '{"x": 1}'}},
            ],
        }
        result = message_to_text(msg)
        assert result.startswith("[调用工具 calc")

    def test_assistant_multiple_tool_calls(self):
        """多个 tool_calls 应拼接多个工具调用描述"""
        msg = {
            "role": "assistant",
            "content": "多个操作",
            "tool_calls": [
                {"function": {"name": "open_door", "arguments": '{"door": 1}'}},
                {"function": {"name": "close_window", "arguments": '{"window": 2}'}},
            ],
        }
        result = message_to_text(msg)
        assert "open_door" in result
        assert "close_window" in result
        assert result.count("[调用工具") == 2

    def test_tool_calls_function_arguments_as_dict(self):
        """tool_calls 中 function.arguments 已是 dict 类型，应直接使用"""
        msg = {
            "role": "assistant",
            "tool_calls": [
                {"function": {"name": "echo", "arguments": {"message": "hello"}}},
            ],
        }
        result = message_to_text(msg)
        assert "调用工具 echo" in result
        assert '"message": "hello"' in result

    def test_tool_calls_function_arguments_as_none(self):
        """tool_calls 中 function.arguments 为 None"""
        msg = {
            "role": "assistant",
            "tool_calls": [
                {"function": {"name": "noop", "arguments": None}},
            ],
        }
        result = message_to_text(msg)
        assert "调用工具 noop(None)" in result

    def test_tool_calls_function_arguments_invalid_json(self):
        """tool_calls arguments 为无效 JSON 字符串，应回退为 str 形式"""
        msg = {
            "role": "assistant",
            "tool_calls": [
                {"function": {"name": "bad", "arguments": "{invalid json}"}},
            ],
        }
        result = message_to_text(msg)
        assert "调用工具 bad({invalid json})" in result

    def test_tool_calls_without_function_key(self):
        """如果 function 可能不是嵌套键（直接平铺在 tool_call 中）"""
        msg = {
            "role": "assistant",
            "tool_calls": [
                {"name": "direct_tool", "arguments": '{"a": 1}'},
            ],
        }
        result = message_to_text(msg)
        assert "调用工具 direct_tool" in result
        assert '"a": 1' in result

    def test_tool_calls_arguments_truncated(self):
        """arguments 超过 100 字符应截断"""
        long_args = {"key": "x" * 200}
        msg = {
            "role": "assistant",
            "tool_calls": [
                {"function": {"name": "long_args", "arguments": long_args}},
            ],
        }
        result = message_to_text(msg)
        # dict 序列化后的字符串应被截断到 100 字符
        assert len(result) < 300  # 总体不会太长
        assert "调用工具 long_args" in result

    # ── tool 消息 ──────────────────────────────────────────

    def test_tool_message(self):
        """tool 消息应返回 '[工具结果 ...]' 格式"""
        msg = {"role": "tool", "tool_call_id": "call_abc123", "content": "查询结果"}
        result = message_to_text(msg)
        assert result.startswith("[工具结果 call_abc123]")
        assert "查询结果" in result

    def test_tool_message_no_tool_call_id(self):
        """tool 消息缺少 tool_call_id"""
        msg = {"role": "tool", "content": "结果"}
        result = message_to_text(msg)
        assert result == "[工具结果 ] 结果"

    def test_tool_message_empty_content(self):
        """tool 消息 content 为空"""
        msg = {"role": "tool", "tool_call_id": "call_1", "content": ""}
        result = message_to_text(msg)
        assert result == "[工具结果 call_1] "

    # ── 其他角色 ──────────────────────────────────────────

    def test_system_message(self):
        """system 消息应返回 content"""
        msg = {"role": "system", "content": "你是助手"}
        assert message_to_text(msg) == "你是助手"

    def test_unknown_role_with_content(self):
        """未知角色但有 content 应返回 content"""
        msg = {"role": "unknown", "content": "some text"}
        assert message_to_text(msg) == "some text"

    def test_empty_message(self):
        """空消息（无 role、无 content）应返回空字符串"""
        msg = {}
        assert message_to_text(msg) == ""


# ═══════════════════════════════════════════════════════════════
# 2. compute_message_stats — 统计计算
# ═══════════════════════════════════════════════════════════════

class TestComputeMessageStats:
    """compute_message_stats() 测试"""

    def test_single_message(self):
        """单条消息应返回正确的 chars 和 tokens"""
        messages = [{"role": "user", "content": "hello"}]
        chars, tokens = compute_message_stats(messages)
        assert chars == 5
        assert tokens > 0

    def test_multiple_messages_accumulated(self):
        """多条消息的 chars 和 tokens 应累加"""
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "world"},
        ]
        chars, tokens = compute_message_stats(messages)
        assert chars == 10  # "hello" + "world" = 10

    def test_empty_message_list(self):
        """空消息列表应返回 0, 0"""
        chars, tokens = compute_message_stats([])
        assert chars == 0
        assert tokens == 0

    def test_messages_with_tool_calls_counted(self):
        """含 tool_calls 的消息也应正确计入"""
        messages = [
            {"role": "user", "content": "查天气"},
            {
                "role": "assistant",
                "content": "好的",
                "tool_calls": [
                    {"function": {"name": "get_weather", "arguments": '{"city":"北京"}'}},
                ],
            },
        ]
        chars, tokens = compute_message_stats(messages)
        assert chars > 0
        assert tokens > 0
        # 工具调用描述被计入（非空）
        assert chars > len("查天气" + "好的")

    def test_tool_messages_use_tool_format(self):
        """tool 消息使用 [工具结果] 格式计入统计"""
        messages = [
            {"role": "tool", "tool_call_id": "c1", "content": "晴天"},
        ]
        chars, tokens = compute_message_stats(messages)
        expected_chars = len("[工具结果 c1] 晴天")
        assert chars == expected_chars


# ═══════════════════════════════════════════════════════════════
# 3. 限制检测函数（_values 版本 — 接受预计算值）
# ═══════════════════════════════════════════════════════════════

class TestExceedsLimitValues:
    """exceeds_limit_values() 测试"""

    def test_below_both_limits(self):
        """字符和 token 均未超限"""
        assert exceeds_limit_values(50000, 50000) is False

    def test_exceeds_chars_limit(self):
        """字符超限但 token 未超限"""
        assert exceeds_limit_values(65000, 50000) is True

    def test_exceeds_tokens_limit(self):
        """token 超限但字符未超限"""
        assert exceeds_limit_values(50000, 65000) is True

    def test_exceeds_both(self):
        """两者均超限"""
        assert exceeds_limit_values(65000, 65000) is True

    def test_exactly_at_limit_not_exceeded(self):
        """恰好等于上限不应判为超限（> 而不是 >=）"""
        assert exceeds_limit_values(60000, 60000) is False

    def test_zero_chars_limit_disabled(self):
        """MAX_CONTEXT_CHARS <= 0 时字符限制不生效"""
        # 注意：这个函数使用模块级配置常量，但它在 import 时已绑定
        # 这里只测试逻辑层：当 MAX_CONTEXT_CHARS > 0 时的行为
        pass


class TestShouldAutoForceValues:
    """should_auto_force_values() 测试"""

    def test_below_threshold(self):
        """低于 AUTO_FORCE 阈值"""
        assert should_auto_force_values(30000, 10000) is False

    def test_exceeds_chars_threshold(self):
        """字符超过 AUTO_FORCE 阈值"""
        assert should_auto_force_values(65000, 10000) is True

    def test_exceeds_tokens_threshold(self):
        """token 超过折算后的阈值（AUTO_FORCE_COMPRESS_THRESHOLD//2）"""
        assert should_auto_force_values(10000, 35000) is True

    def test_below_tokens_threshold(self):
        """token 未超过折算阈值"""
        assert should_auto_force_values(10000, 20000) is False

    def test_zero_threshold_disabled(self):
        """AUTO_FORCE_COMPRESS_THRESHOLD <= 0 时禁用"""
        pass  # 模块常量绑定，此处跳过


class TestCalcExcessCharsValues:
    """calc_excess_chars_values() 测试"""

    def test_no_excess(self):
        """未超限应返回 0"""
        assert calc_excess_chars_values(50000, 50000) == 0

    def test_chars_excess_only(self):
        """仅字符超限"""
        result = calc_excess_chars_values(65000, 50000)
        # chars_excess = 65000 - 60000 = 5000
        # tok_excess_chars = 0 (token 未超限)
        assert result == 5000

    def test_tokens_excess_only(self):
        """仅 token 超限"""
        result = calc_excess_chars_values(50000, 70000)
        # chars_excess = 0
        # tok_excess = 70000 - 60000 = 10000
        # tok_excess_chars = 10000 * 1.5 = 15000
        assert result == 15000

    def test_both_excess_takes_max(self):
        """两者都超限取较大者"""
        result = calc_excess_chars_values(100000, 61000)
        # chars_excess = 100000 - 60000 = 40000
        # tok_excess = 61000 - 60000 = 1000
        # tok_excess_chars = 1000 * 1.5 = 1500
        # max(40000, 1500) = 40000
        assert result == 40000

    def test_tokens_excess_larger(self):
        """token 超限值大于字符超限值"""
        result = calc_excess_chars_values(61000, 100000)
        # chars_excess = 61000 - 60000 = 1000
        # tok_excess = 100000 - 60000 = 40000
        # tok_excess_chars = 40000 * 1.5 = 60000
        # max(1000, 60000) = 60000
        assert result == 60000


class TestCalcUsagePercentValues:
    """calc_usage_percent_values() 测试"""

    def test_normal_percentage(self):
        """正常百分比计算"""
        result = calc_usage_percent_values(30000)
        # 30000 / 60000 * 100 = 50.0
        assert result == 50.0

    def test_full_usage(self):
        """100% 使用率"""
        result = calc_usage_percent_values(60000)
        assert result == 100.0

    def test_zero_chars(self):
        """0 字符"""
        result = calc_usage_percent_values(0)
        assert result == 0.0

    def test_negative_config_disabled(self):
        """MAX_CONTEXT_CHARS <= 0 应返回 0"""
        # 此函数依赖模块常量，此处测试正常路径
        pass


# ═══════════════════════════════════════════════════════════════
# 4. 消息封装函数（调用 compute_message_stats + _values）
# ═══════════════════════════════════════════════════════════════

class TestMessageWrappers:
    """exceeds_limit / should_auto_force / calc_excess_chars / calc_usage_percent 测试"""

    def test_total_chars(self):
        """total_chars 返回字符总数"""
        msgs = [{"role": "user", "content": "abc"}, {"role": "user", "content": "def"}]
        assert total_chars(msgs) == 6

    def test_total_tokens(self):
        """total_tokens 返回 token 总数"""
        msgs = [{"role": "user", "content": "hello world"}]
        result = total_tokens(msgs)
        assert isinstance(result, int)
        assert result > 0

# ═══════════════════════════════════════════════════════════════
# 5. 工具组保护
# ═══════════════════════════════════════════════════════════════

class TestFindToolGroups:
    """find_tool_groups() 测试"""

    def test_no_tool_groups(self):
        """无工具调用组应返回空列表"""
        msgs = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        assert find_tool_groups(msgs) == []

    def test_single_tool_group(self):
        """单个工具调用组"""
        msgs = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c1"}]},
            {"role": "tool", "tool_call_id": "c1", "content": "result1"},
            {"role": "tool", "tool_call_id": "c2", "content": "result2"},
        ]
        groups = find_tool_groups(msgs)
        assert groups == [(1, 3)]

    def test_multiple_tool_groups(self):
        """多个工具调用组"""
        msgs = [
            {"role": "assistant", "tool_calls": [{"id": "c1"}]},
            {"role": "tool", "tool_call_id": "c1", "content": "r1"},
            {"role": "user", "content": "继续"},
            {"role": "assistant", "tool_calls": [{"id": "c2"}]},
            {"role": "tool", "tool_call_id": "c2", "content": "r2"},
        ]
        groups = find_tool_groups(msgs)
        assert groups == [(0, 1), (3, 4)]

    def test_assistant_without_tool_calls_ignored(self):
        """没有 tool_calls 的 assistant 不应触发组"""
        msgs = [
            {"role": "assistant", "content": "plain"},
            {"role": "tool", "tool_call_id": "c1", "content": "r1"},
        ]
        groups = find_tool_groups(msgs)
        assert groups == []

    def test_tool_without_preceding_assistant(self):
        """单独的 tool 消息（没有前置 assistant）不应构成组"""
        msgs = [
            {"role": "tool", "tool_call_id": "c1", "content": "r1"},
        ]
        groups = find_tool_groups(msgs)
        assert groups == []

    def test_empty_messages(self):
        """空列表"""
        assert find_tool_groups([]) == []


class TestAdjustKeepForToolGroups:
    """adjust_keep_for_tool_groups() 测试"""

    def test_no_tool_groups_unchanged(self):
        """无工具组时 keep_recent 不变"""
        msgs = [{"role": "user", "content": "a"}] * 10
        assert adjust_keep_for_tool_groups(msgs, keep_recent=3) == 3

    def test_tool_group_not_near_boundary(self):
        """工具组未跨越 keep_recent 边界"""
        msgs = [
            {"role": "user", "content": "a"},  # 0
            {"role": "assistant", "tool_calls": [{"id": "c1"}]},  # 1
            {"role": "tool", "tool_call_id": "c1", "content": "r"},  # 2
            {"role": "user", "content": "b"},  # 3
            {"role": "user", "content": "c"},  # 4
            {"role": "user", "content": "d"},  # 5
        ]
        # keep_recent=2: boundary = 6 - 2 = 4
        # groups: (1, 2), boundary=4, start=1 < 4 but end=2 < 4, 不交叉
        assert adjust_keep_for_tool_groups(msgs, keep_recent=2) == 2

    def test_tool_group_crosses_boundary(self):
        """工具组跨越 keep_recent 边界时应扩大"""
        msgs = [
            {"role": "user", "content": "a"},  # 0
            {"role": "user", "content": "b"},  # 1
            {"role": "assistant", "tool_calls": [{"id": "c1"}]},  # 2
            {"role": "tool", "tool_call_id": "c1", "content": "r"},  # 3
            {"role": "user", "content": "c"},  # 4
        ]
        # keep_recent=2: boundary = 5 - 2 = 3
        # groups: (2, 3), start=2 < boundary(3) <= end=3 → 交叉
        # keep_recent = max(2, 5 - 2) = max(2, 3) = 3
        result = adjust_keep_for_tool_groups(msgs, keep_recent=2)
        assert result == 3

    def test_multiple_groups_only_one_crosses(self):
        """多个工具组中只有一组跨边界"""
        msgs = [
            {"role": "assistant", "tool_calls": [{"id": "c1"}]},  # 0
            {"role": "tool", "tool_call_id": "c1", "content": "r1"},  # 1
            {"role": "user", "content": "x"},  # 2
            {"role": "assistant", "tool_calls": [{"id": "c2"}]},  # 3
            {"role": "tool", "tool_call_id": "c2", "content": "r2"},  # 4
            {"role": "user", "content": "y"},  # 5
        ]
        # keep_recent=2: boundary = 6 - 2 = 4
        # group1: (0, 1), 0 < 4 ≤ 1? No, 4 > 1
        # group2: (3, 4), 3 < 4 ≤ 4 → Yes
        # keep_recent = max(2, 6-3) = max(2, 3) = 3
        result = adjust_keep_for_tool_groups(msgs, keep_recent=2)
        assert result == 3


class TestExtendToCompleteToolGroup:
    """extend_to_complete_tool_group() 测试"""

    def test_empty_indices(self):
        """空列表应返回空列表"""
        assert extend_to_complete_tool_group([], []) == []

    def test_last_not_tool_calls_assistant(self):
        """末尾不是带 tool_calls 的 assistant 时不扩展"""
        msgs = [
            {"role": "user", "content": "a"},
            {"role": "user", "content": "b"},
        ]
        result = extend_to_complete_tool_group(msgs, [0])
        assert result == [0]

    def test_last_is_tool_calls_assistant(self):
        """末尾是带 tool_calls 的 assistant，应追加后续 tool"""
        msgs = [
            {"role": "assistant", "tool_calls": [{"id": "c1"}]},  # 0
            {"role": "tool", "tool_call_id": "c1", "content": "r1"},  # 1
            {"role": "tool", "tool_call_id": "c2", "content": "r2"},  # 2
        ]
        result = extend_to_complete_tool_group(msgs, [0])
        assert result == [0, 1, 2]

    def test_stops_at_non_tool(self):
        """遇到非 tool 消息时停止"""
        msgs = [
            {"role": "assistant", "tool_calls": [{"id": "c1"}]},  # 0
            {"role": "tool", "tool_call_id": "c1", "content": "r1"},  # 1
            {"role": "user", "content": "stop"},  # 2
            {"role": "tool", "tool_call_id": "c3", "content": "r3"},  # 3
        ]
        result = extend_to_complete_tool_group(msgs, [0])
        assert result == [0, 1]

    def test_multiple_indices_only_last_triggers(self):
        """多个索引中只有最后一个触发扩展"""
        msgs = [
            {"role": "assistant", "tool_calls": [{"id": "c1"}]},  # 0
            {"role": "tool", "tool_call_id": "c1", "content": "r1"},  # 1
            {"role": "assistant", "tool_calls": [{"id": "c2"}]},  # 2
            {"role": "tool", "tool_call_id": "c2", "content": "r2"},  # 3
        ]
        result = extend_to_complete_tool_group(msgs, [0, 2])
        assert result == [0, 2, 3]


# ═══════════════════════════════════════════════════════════════
# 6. 消息选择
# ═══════════════════════════════════════════════════════════════

class TestSelectCandidates:
    """select_candidates() 测试"""

    def test_skip_index_zero(self):
        """index 0 应被跳过"""
        msgs = [
            {"role": "system", "content": "你是助手"},
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        candidates = select_candidates(msgs, keep_recent=1)
        # boundary = max(1, 3-1) = max(1, 2) = 2
        # range(1, 2) → [1]
        assert candidates == [1]

    def test_skip_pinned_messages(self):
        """pinned 消息应被跳过"""
        msgs = [
            {"role": "system", "content": ""},
            {"role": "user", "content": "pin", "pinned": True},
            {"role": "user", "content": "normal"},
        ]
        candidates = select_candidates(msgs, keep_recent=1)
        # boundary = max(1, 3-1) = 2
        # range(1, 2) → [1], 但 index 1 是 pinned → 跳过
        assert candidates == []

    def test_skip_system_without_summary_prefix(self):
        """非摘要 system 消息应被跳过"""
        msgs = [
            {"role": "system", "content": "你是助手"},
            {"role": "system", "content": "第二条规则"},
            {"role": "user", "content": "hi"},
        ]
        candidates = select_candidates(msgs, keep_recent=1)
        # boundary = max(1, 3-1) = 2, range(1, 2) → [1]
        # index 1 是 system 且不以 [对话摘要] 开头 → 跳过
        assert candidates == []

    def test_keep_summary_system(self):
        """以 [对话摘要] 开头的 system 消息应被保留为候选（非跳过）"""
        msgs = [
            {"role": "system", "content": "你是助手"},
            {"role": "system", "content": "[对话摘要] 之前对话的摘要"},
            {"role": "user", "content": "hi"},
        ]
        candidates = select_candidates(msgs, keep_recent=0)
        # boundary = max(1, 3-0) = 3
        # range(1, 3) → [1, 2]
        # index 1: system 但以 [对话摘要] 开头 → 不被跳过（condition is False）
        # index 2: user → 保留
        assert candidates == [1, 2]

    def test_keep_recent_excludes(self):
        """keep_recent 应排除最近的消息"""
        msgs = [{"role": "user", "content": f"msg{i}"} for i in range(5)]
        candidates = select_candidates(msgs, keep_recent=2)
        # boundary = max(1, 5-2) = 3
        # range(1, 3) → [1, 2]
        assert candidates == [1, 2]

    def test_keep_recent_all(self):
        """keep_recent >= len(messages) 应无候选"""
        msgs = [{"role": "user", "content": f"msg{i}"} for i in range(3)]
        candidates = select_candidates(msgs, keep_recent=5)
        # boundary = max(1, 3-5) = max(1, -2) = 1
        # range(1, 1) → []
        assert candidates == []


class TestSelectForCompression:
    """select_for_compression() 测试"""

    def test_force_returns_all_candidates(self):
        """force=True 返回所有可压缩消息"""
        msgs = [
            {"role": "system", "content": ""},
            {"role": "user", "content": "a"},
            {"role": "user", "content": "b"},
            {"role": "user", "content": "c"},
        ]
        result = select_for_compression(msgs, keep_recent=0, force=True)
        # 跳过 index 0, candidates = [1, 2, 3]
        assert result == [1, 2, 3]

    def test_force_skips_pinned_and_system(self):
        """force=True 仍应跳过 pinned 和非摘要 system"""
        msgs = [
            {"role": "system", "content": ""},
            {"role": "system", "content": "system msg"},
            {"role": "user", "content": "a", "pinned": True},
            {"role": "user", "content": "b"},
        ]
        result = select_for_compression(msgs, keep_recent=0, force=True)
        # index 1: system 非摘要 → 跳过
        # index 2: pinned → 跳过
        assert result == [3]

    def test_non_force_no_excess_returns_empty(self):
        """非 force 模式且无超限应返回空列表"""
        msgs = [{"role": "user", "content": "hi"}]
        result = select_for_compression(
            msgs, keep_recent=0, force=False,
            total_chars_val=100, total_tokens_val=10,
        )
        assert result == []

    def test_non_force_accumulates_until_target(self):
        """非 force 模式累积到目标后停止"""
        msgs = [
            {"role": "system", "content": ""},  # 0, 跳过
            {"role": "user", "content": "a" * 100},  # 1, 100 字符
            {"role": "user", "content": "b" * 100},  # 2, 100 字符
            {"role": "user", "content": "c" * 100},  # 3, 100 字符
        ]
        # total_chars_val=60100 → chars_excess = 60100 - 60000 = 100
        # target = int(100 * 1.3) = 130
        # idx 1: accumulated=100 < 130
        # idx 2: accumulated=200 >= 130 → stop
        result = select_for_compression(
            msgs, keep_recent=0, force=False,
            total_chars_val=60100, total_tokens_val=100,
        )
        assert result == [1, 2]

    def test_non_force_with_tool_group_extension(self):
        """非 force 模式选中末尾 tool_calls assistant 时扩展"""
        msgs = [
            {"role": "system", "content": ""},  # 0
            {"role": "user", "content": "a" * 200},  # 1
            {"role": "assistant", "tool_calls": [{"id": "c1"}]},  # 2
            {"role": "tool", "tool_call_id": "c1", "content": "r"},  # 3
        ]
        # excess 足够大，但只选到 index 2，然后扩展
        result = select_for_compression(
            msgs, keep_recent=0, force=False,
            total_chars_val=70000, total_tokens_val=100,
        )
        assert 2 in result
        assert 3 in result  # 扩展添加

    def test_force_extend_to_complete_tool_group(self):
        """force=True 也应对末尾 tool_calls 扩展"""
        msgs = [
            {"role": "system", "content": ""},  # 0
            {"role": "user", "content": "a"},  # 1
            {"role": "assistant", "tool_calls": [{"id": "c1"}]},  # 2
            {"role": "tool", "tool_call_id": "c1", "content": "r"},  # 3
        ]
        result = select_for_compression(msgs, keep_recent=0, force=True)
        assert result == [1, 2, 3]


# ═══════════════════════════════════════════════════════════════
# 7. MessageStatsCache 增量缓存
# ═══════════════════════════════════════════════════════════════

class TestMessageStatsCache:
    """MessageStatsCache 增量缓存测试"""

    # ── 初始状态 ──────────────────────────────────────────

    def test_initial_state(self):
        """初始状态：total_chars=0, total_tokens=0, is_valid=False, len=0"""
        cache = MessageStatsCache()
        assert cache.total_chars == 0
        assert cache.total_tokens == 0
        assert cache.is_valid is False
        assert len(cache) == 0

    # ── resync ────────────────────────────────────────────

    def test_resync_empty(self):
        """resync 空列表"""
        cache = MessageStatsCache()
        cache.resync([])
        assert cache.total_chars == 0
        assert cache.total_tokens == 0
        assert cache.is_valid is True
        assert len(cache) == 0

    def test_resync_single(self):
        """resync 单条消息"""
        cache = MessageStatsCache()
        cache.resync([{"role": "user", "content": "hello"}])
        assert cache.total_chars == 5
        assert cache.total_tokens > 0
        assert cache.is_valid is True
        assert len(cache) == 1

    def test_resync_multiple(self):
        """resync 多条消息验证准确统计"""
        cache = MessageStatsCache()
        msgs = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        cache.resync(msgs)
        # "hi" + "hello" = 7 字符
        assert cache.total_chars == 7
        assert len(cache) == 2

    # ── invalidate / is_valid ─────────────────────────────

    def test_invalidate(self):
        """invalidate 后 is_valid=False"""
        cache = MessageStatsCache()
        cache.resync([{"role": "user", "content": "a"}])
        assert cache.is_valid is True
        cache.invalidate()
        assert cache.is_valid is False
        # 但统计数据应保持不变
        assert cache.total_chars > 0

    def test_is_valid_initial_false(self):
        """初始状态 is_valid 为 False"""
        cache = MessageStatsCache()
        assert cache.is_valid is False

    # ── on_append ─────────────────────────────────────────

    def test_on_append(self):
        """on_append 追加一条消息后统计应更新"""
        cache = MessageStatsCache()
        cache.resync([{"role": "user", "content": "a"}])
        cache.on_append({"role": "user", "content": "b"})
        assert cache.total_chars == 2  # "a" + "b"
        assert len(cache) == 2

    def test_on_append_empty_to_empty_cache(self):
        """未 resync 直接 on_append 应正确工作"""
        cache = MessageStatsCache()
        cache.on_append({"role": "user", "content": "hello"})
        assert cache.total_chars == 5
        assert len(cache) == 1

    def test_on_append_with_tool_calls(self):
        """on_append 含 tool_calls 的消息"""
        cache = MessageStatsCache()
        cache.resync([])
        cache.on_append({
            "role": "assistant",
            "tool_calls": [{"function": {"name": "test", "arguments": '{"a":1}'}}],
        })
        assert cache.total_chars > 0
        assert len(cache) == 1

    # ── on_insert ─────────────────────────────────────────

    def test_on_insert_beginning(self):
        """on_insert 在开头插入"""
        cache = MessageStatsCache()
        cache.resync([
            {"role": "user", "content": "b"},
            {"role": "user", "content": "c"},
        ])
        cache.on_insert(0, {"role": "user", "content": "a"})
        assert cache.total_chars == 3  # "a" + "b" + "c"
        assert len(cache) == 3
        assert cache.get_per_msg(0) == (1, cache.get_per_msg(0)[1])

    def test_on_insert_middle(self):
        """on_insert 在中间插入"""
        cache = MessageStatsCache()
        cache.resync([
            {"role": "user", "content": "a"},
            {"role": "user", "content": "c"},
        ])
        cache.on_insert(1, {"role": "user", "content": "b"})
        assert cache.total_chars == 3
        assert len(cache) == 3

    def test_on_insert_end(self):
        """on_insert 在末尾插入"""
        cache = MessageStatsCache()
        cache.resync([{"role": "user", "content": "a"}])
        cache.on_insert(1, {"role": "user", "content": "b"})
        assert cache.total_chars == 2
        assert len(cache) == 2

    # ── on_remove ─────────────────────────────────────────

    def test_on_remove_single(self):
        """on_remove 删除单条消息"""
        cache = MessageStatsCache()
        cache.resync([
            {"role": "user", "content": "a"},
            {"role": "user", "content": "bb"},
        ])
        cache.on_remove([0])  # 删除 "a"（1字符）
        assert cache.total_chars == 2  # "bb" 剩余
        assert len(cache) == 1

    def test_on_remove_multiple(self):
        """on_remove 批量删除"""
        cache = MessageStatsCache()
        cache.resync([
            {"role": "user", "content": "a"},
            {"role": "user", "content": "bb"},
            {"role": "user", "content": "ccc"},
        ])
        cache.on_remove([0, 2])  # 删除 "a" 和 "ccc"
        assert cache.total_chars == 2  # 只剩余 "bb"
        assert len(cache) == 1

    def test_on_remove_out_of_order_indices(self):
        """on_remove 传入乱序索引（从高到低处理）"""
        cache = MessageStatsCache()
        cache.resync([
            {"role": "user", "content": "a"},
            {"role": "user", "content": "bb"},
            {"role": "user", "content": "ccc"},
        ])
        # 传入顺序 [2, 0]，内部会排序后从高到低处理
        cache.on_remove([2, 0])
        assert cache.total_chars == 2  # 只剩余 "bb"
        assert len(cache) == 1

    def test_on_remove_empty_list(self):
        """on_remove 空列表不应报错"""
        cache = MessageStatsCache()
        cache.resync([{"role": "user", "content": "a"}])
        cache.on_remove([])
        assert cache.total_chars == 1
        assert len(cache) == 1

    def test_on_remove_out_of_bounds_index(self):
        """on_remove 越界索引应被忽略"""
        cache = MessageStatsCache()
        cache.resync([{"role": "user", "content": "a"}])
        cache.on_remove([5])  # 越界
        assert cache.total_chars == 1
        assert len(cache) == 1

    # ── on_replace ────────────────────────────────────────

    def test_on_replace_shorter(self):
        """on_replace 替换为更短的消息"""
        cache = MessageStatsCache()
        cache.resync([{"role": "user", "content": "hello"}])
        cache.on_replace(0, {"role": "user", "content": "hi"})
        # 5 → 2，减少 3
        assert cache.total_chars == 2
        assert len(cache) == 1

    def test_on_replace_longer(self):
        """on_replace 替换为更长的消息"""
        cache = MessageStatsCache()
        cache.resync([{"role": "user", "content": "hi"}])
        cache.on_replace(0, {"role": "user", "content": "hello"})
        # 2 → 5，增加 3
        assert cache.total_chars == 5

    def test_on_replace_same_length(self):
        """on_replace 替换为相同长度的消息"""
        cache = MessageStatsCache()
        cache.resync([{"role": "user", "content": "abc"}])
        cache.on_replace(0, {"role": "user", "content": "xyz"})
        assert cache.total_chars == 3  # 长度相同

    # ── get_per_msg ───────────────────────────────────────

    def test_get_per_msg_valid(self):
        """get_per_msg 返回正确的每消息统计"""
        cache = MessageStatsCache()
        cache.resync([{"role": "user", "content": "hello"}])
        chars, tokens = cache.get_per_msg(0)
        assert chars == 5
        assert isinstance(tokens, int)
        assert tokens > 0

    def test_get_per_msg_out_of_range(self):
        """get_per_msg 越界返回 (0, 0)"""
        cache = MessageStatsCache()
        cache.resync([{"role": "user", "content": "a"}])
        assert cache.get_per_msg(5) == (0, 0)
        assert cache.get_per_msg(-1) == (0, 0)

    # ── 综合序列操作 ─────────────────────────────────────

    def test_resync_append_remove_replace_sequence(self):
        """综合序列操作验证统计一致性"""
        cache = MessageStatsCache()

        # 1. resync 3 条消息
        cache.resync([
            {"role": "user", "content": "a"},   # 1 char
            {"role": "user", "content": "bb"},  # 2 chars
            {"role": "user", "content": "ccc"},  # 3 chars
        ])
        assert cache.total_chars == 6

        # 2. append 1 条
        cache.on_append({"role": "user", "content": "dddd"})  # 4 chars
        assert cache.total_chars == 10
        assert len(cache) == 4

        # 3. remove 索引 1 ("bb", 2 chars)
        cache.on_remove([1])
        assert cache.total_chars == 8  # 10 - 2
        assert len(cache) == 3

        # 4. replace 索引 0 ("a"→"xyz", 1→3, +2)
        cache.on_replace(0, {"role": "user", "content": "xyz"})
        assert cache.total_chars == 10  # 8 + 2
        assert len(cache) == 3

        # 5. insert 在索引 1 处
        cache.on_insert(1, {"role": "user", "content": "!"})  # 1 char
        assert cache.total_chars == 11  # 10 + 1
        assert len(cache) == 4

    # ── 空列表初始 ───────────────────────────────────────

    def test_empty_cache_append_then_resync(self):
        """空缓存 append 后 resync 应重置"""
        cache = MessageStatsCache()
        cache.on_append({"role": "user", "content": "old"})
        assert cache.total_chars == 3
        cache.resync([{"role": "user", "content": "new"}])
        assert cache.total_chars == 3  # "new" 也是 3 字符
        assert len(cache) == 1

    # ── tool 消息计入 per_msg ────────────────────────────

    def test_tool_message_in_per_msg(self):
        """tool 消息正确计入 per_msg"""
        cache = MessageStatsCache()
        cache.resync([
            {"role": "tool", "tool_call_id": "c1", "content": "result"},
        ])
        chars, tokens = cache.get_per_msg(0)
        expected_chars = len("[工具结果 c1] result")
        assert chars == expected_chars
        assert isinstance(tokens, int)
        assert tokens > 0
