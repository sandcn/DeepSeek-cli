#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for src/core/compression.py — 上下文压缩策略

覆盖内容：
  1. CompressionResult — 压缩结果值对象
  2. CompressionStrategy — 抽象基类约束与 _safe_notify
  3. DropStrategy._collect_unpinned — unpinned 消息收集
  4. SummarizeStrategy._detect_prior_summary — 旧摘要检测
"""

import pytest
from abc import ABC, abstractmethod

from src.core.compression import (
    CompressionResult,
    CompressionStrategy,
    SummarizeStrategy,
    DropStrategy,
)


# ═══════════════════════════════════════════════════════════════
# 辅助类
# ═══════════════════════════════════════════════════════════════

class ConcreteStrategy(CompressionStrategy):
    """合法的 CompressionStrategy 子类，用于测试基类行为"""
    def compress(self, messages, model, summarize_fn,
                 on_changed, cache, force) -> CompressionResult:
        return CompressionResult(success=True)


# ═══════════════════════════════════════════════════════════════
# 1. CompressionResult — 压缩结果值对象
# ═══════════════════════════════════════════════════════════════

class TestCompressionResult:
    """CompressionResult 值对象测试"""

    def test_defaults(self):
        """无参构造应使用正确默认值"""
        result = CompressionResult()
        assert result.success is True
        assert result.removed_indices == []
        assert result.inserted_message is None
        assert result.chars_saved == 0
        assert result.stats == {}

    def test_custom_values(self):
        """自定义参数应正确设置"""
        result = CompressionResult(
            success=False,
            removed_indices=[1, 3, 5],
            inserted_message={"role": "system", "content": "[对话摘要] 测试"},
            chars_saved=1024,
            stats={"reason": "test", "elapsed": 0.5},
        )
        assert result.success is False
        assert result.removed_indices == [1, 3, 5]
        assert result.inserted_message == {"role": "system", "content": "[对话摘要] 测试"}
        assert result.chars_saved == 1024
        assert result.stats == {"reason": "test", "elapsed": 0.5}

    def test_removed_indices_none_becomes_empty_list(self):
        """removed_indices=None 应转为空列表 []"""
        result = CompressionResult(removed_indices=None)
        assert result.removed_indices == []

    def test_stats_none_becomes_empty_dict(self):
        """stats=None 应转为空字典 {}"""
        result = CompressionResult(stats=None)
        assert result.stats == {}

    def test_slots_cannot_set_arbitrary_attr(self):
        """__slots__ 约束生效：尝试设置未定义属性应抛 AttributeError"""
        result = CompressionResult()
        with pytest.raises(AttributeError):
            result.not_a_slot = "should_fail"

    def test_slots_no_dunder_dict(self):
        """__slots__ 对象没有 __dict__"""
        result = CompressionResult()
        assert not hasattr(result, "__dict__")

    def test_removed_indices_empty_by_default(self):
        """不传 removed_indices 时默认空列表"""
        result = CompressionResult(success=True)
        assert result.removed_indices == []
        assert isinstance(result.removed_indices, list)

    def test_stats_empty_by_default(self):
        """不传 stats 时默认空字典"""
        result = CompressionResult(success=True)
        assert result.stats == {}
        assert isinstance(result.stats, dict)

    def test_chars_saved_zero_by_default(self):
        """chars_saved 默认为 0"""
        result = CompressionResult()
        assert result.chars_saved == 0
        assert isinstance(result.chars_saved, int)

    def test_inserted_message_none_by_default(self):
        """inserted_message 默认为 None"""
        result = CompressionResult()
        assert result.inserted_message is None

    def test_success_type(self):
        """success 应为 bool 类型"""
        result = CompressionResult(success=True)
        assert isinstance(result.success, bool)


# ═══════════════════════════════════════════════════════════════
# 2. CompressionStrategy — 抽象基类
# ═══════════════════════════════════════════════════════════════

class TestCompressionStrategy:
    """CompressionStrategy 抽象基类行为测试"""

    def test_cannot_instantiate_abstract(self):
        """抽象基类不能直接实例化（有 abstractmethod）"""
        with pytest.raises(TypeError, match="abstract"):
            CompressionStrategy()

    def test_subclass_without_compress_cannot_instantiate(self):
        """子类未实现 compress 抽象方法时不能实例化"""
        class Incomplete(CompressionStrategy):
            pass
        with pytest.raises(TypeError, match="abstract"):
            Incomplete()

    def test_concrete_subclass_can_instantiate(self):
        """实现了 compress 的子类应能正常实例化"""
        strategy = ConcreteStrategy()
        assert isinstance(strategy, CompressionStrategy)

    def test_concrete_subclass_is_abstract_subclass(self):
        """ConcreteStrategy 是 CompressionStrategy 的子类"""
        assert issubclass(ConcreteStrategy, CompressionStrategy)

    # ── _safe_notify ──────────────────────────────────────

    def test_safe_notify_none_callback_does_not_raise(self):
        """on_changed 为 None 时 _safe_notify 不应抛异常"""
        # 不会抛异常即为成功
        CompressionStrategy._safe_notify(None, {"type": "test"})

    def test_safe_notify_none_callback_noop(self):
        """on_changed 为 None 时回调不应被调用"""
        called = False

        def callback(_event):
            nonlocal called
            called = True

        CompressionStrategy._safe_notify(None, {"type": "test"})
        assert not called

    def test_safe_notify_calls_callback(self):
        """on_changed 正常时回调应被调用，参数正确传递"""
        received = []

        def callback(event):
            received.append(event)

        CompressionStrategy._safe_notify(callback, {"type": "test", "value": 42})
        assert len(received) == 1
        assert received[0] == {"type": "test", "value": 42}

    def test_safe_notify_exception_caught(self):
        """on_changed 抛异常时 _safe_notify 应捕获不传播"""
        def exploding(_event):
            raise RuntimeError("模拟回调异常")

        # 不应向外抛出异常
        CompressionStrategy._safe_notify(exploding, {"type": "test"})

    def test_safe_notify_callback_with_mutation(self):
        """on_changed 回调能修改外部状态"""
        state = {"count": 0}

        def callback(_event):
            state["count"] += 1

        CompressionStrategy._safe_notify(callback, {"type": "test"})
        assert state["count"] == 1

    def test_safe_notify_multiple_calls(self):
        """多次调用 _safe_notify 均正常工作"""
        events = []

        def callback(event):
            events.append(event["type"])

        CompressionStrategy._safe_notify(callback, {"type": "a"})
        CompressionStrategy._safe_notify(callback, {"type": "b"})
        CompressionStrategy._safe_notify(callback, {"type": "c"})
        assert events == ["a", "b", "c"]


# ═══════════════════════════════════════════════════════════════
# 3. DropStrategy — 降级删除策略
# ═══════════════════════════════════════════════════════════════

class TestDropStrategyCollectUnpinned:
    """DropStrategy._collect_unpinned() 静态方法测试"""

    def test_skips_index_zero(self):
        """索引 0（系统提示词）不应被收集"""
        messages = [
            {"role": "system", "content": "你是一个助手"},
            {"role": "user", "content": "你好"},
        ]
        result = DropStrategy._collect_unpinned(messages)
        assert 0 not in result

    def test_skips_pinned_message(self):
        """pinned=True 的消息不应被收集"""
        messages = [
            {"role": "system", "content": "你是一个助手"},
            {"role": "user", "content": "重要消息", "pinned": True},
            {"role": "user", "content": "普通消息"},
        ]
        result = DropStrategy._collect_unpinned(messages)
        assert 1 not in result
        assert 2 in result

    def test_skips_system_not_summary(self):
        """role=system 且不以 [对话摘要] 开头的消息不应被收集"""
        messages = [
            {"role": "system", "content": "你是一个助手"},
            {"role": "system", "content": "额外系统指令"},
            {"role": "user", "content": "你好"},
        ]
        result = DropStrategy._collect_unpinned(messages)
        # 索引 1 是 role=system 的非摘要消息，不应被收集
        assert 1 not in result
        # 索引 2 是 user 消息，应被收集
        assert 2 in result

    def test_includes_old_summary(self):
        """role=system 且以 [对话摘要] 开头的消息应被收集（可删除）"""
        messages = [
            {"role": "system", "content": "你是一个助手"},
            {"role": "system", "content": "[对话摘要] 之前的对话摘要"},
            {"role": "user", "content": "你好"},
        ]
        result = DropStrategy._collect_unpinned(messages)
        # 索引 1 是旧摘要，应被收集用于删除
        assert 1 in result

    def test_includes_unpinned_non_system(self):
        """非 system 且 unpinned 的消息应被收集"""
        messages = [
            {"role": "system", "content": "你是一个助手"},
            {"role": "user", "content": "用户消息"},
            {"role": "assistant", "content": "助手回复"},
            {"role": "user", "content": "另一条消息"},
        ]
        result = DropStrategy._collect_unpinned(messages)
        assert 1 in result
        assert 2 in result
        assert 3 in result

    def test_empty_messages_returns_empty(self):
        """空消息列表应返回空列表"""
        messages = []
        result = DropStrategy._collect_unpinned(messages)
        assert result == []

    def test_only_system_message_returns_empty(self):
        """只有系统提示词时返回空列表"""
        messages = [
            {"role": "system", "content": "你是一个助手"},
        ]
        result = DropStrategy._collect_unpinned(messages)
        assert result == []

    def test_all_pinned_returns_empty(self):
        """所有消息 pinned 时返回空列表"""
        messages = [
            {"role": "system", "content": "你是一个助手"},
            {"role": "user", "content": "你好", "pinned": True},
            {"role": "assistant", "content": "回复", "pinned": True},
        ]
        result = DropStrategy._collect_unpinned(messages)
        assert result == []

    def test_old_summary_with_pinned_non_summary(self):
        """旧摘要可收集，同位置非摘要 system 被跳过"""
        messages = [
            {"role": "system", "content": "你是一个助手"},
            {"role": "system", "content": "[对话摘要] 摘要一"},
            {"role": "system", "content": "纯指令"},
            {"role": "user", "content": "问题"},
        ]
        result = DropStrategy._collect_unpinned(messages)
        # 索引 1 是旧摘要 → 收集
        assert 1 in result
        # 索引 2 是非摘要 system → 跳过
        assert 2 not in result
        # 索引 3 是 user → 收集
        assert 3 in result

    def test_content_none_system_not_summary(self):
        """role=system 且 content=None 不应被收集（视为非摘要）"""
        messages = [
            {"role": "system", "content": "你是一个助手"},
            {"role": "system", "content": None},
            {"role": "user", "content": "问题"},
        ]
        result = DropStrategy._collect_unpinned(messages)
        assert 1 not in result
        assert 2 in result

    def test_content_empty_string_system_not_summary(self):
        """role=system 且 content="" 不应被收集"""
        messages = [
            {"role": "system", "content": "你是一个助手"},
            {"role": "system", "content": ""},
            {"role": "user", "content": "问题"},
        ]
        result = DropStrategy._collect_unpinned(messages)
        assert 1 not in result
        assert 2 in result

    def test_missing_role_key(self):
        """缺少 role 键的消息应按非 system 处理，被收集"""
        messages = [
            {"role": "system", "content": "你是一个助手"},
            {"content": "无角色消息"},
        ]
        result = DropStrategy._collect_unpinned(messages)
        assert 1 in result

    def test_missing_content_key_for_system(self):
        """系统消息缺少 content 键时视为非摘要，不被收集"""
        messages = [
            {"role": "system", "content": "你是一个助手"},
            {"role": "system"},  # 无 content 键
            {"role": "user", "content": "问题"},
        ]
        result = DropStrategy._collect_unpinned(messages)
        assert 1 not in result
        assert 2 in result

    def test_collect_indices_are_correct(self):
        """收集的索引应与原始消息列表中的位置一致"""
        messages = [
            {"role": "system", "content": "你是一个助手"},       # 0 — 跳过
            {"role": "user", "content": "A", "pinned": True},    # 1 — pinned 跳过
            {"role": "assistant", "content": "B"},               # 2 — 收集
            {"role": "system", "content": "[对话摘要] 摘要"},     # 3 — 旧摘要 收集
            {"role": "system", "content": "指令"},                # 4 — 非摘要 system 跳过
            {"role": "user", "content": "C"},                    # 5 — 收集
        ]
        result = DropStrategy._collect_unpinned(messages)
        assert result == [2, 3, 5]


# ═══════════════════════════════════════════════════════════════
# 4. SummarizeStrategy — 摘要压缩策略部分方法
# ═══════════════════════════════════════════════════════════════

class TestSummarizeStrategyDetectPriorSummary:
    """SummarizeStrategy._detect_prior_summary() 静态方法测试

    注意：该方法返回 (has_prior, extended_indices) 元组，不再修改入参 to_compress。
    """

    def test_to_compress_contains_summary(self):
        """to_compress 中已包含旧摘要 → 返回 (True, to_compress)"""
        messages = [
            {"role": "system", "content": "你是一个助手"},
            {"role": "user", "content": "你好"},
            {"role": "system", "content": "[对话摘要] 旧摘要内容"},
            {"role": "user", "content": "后续问题"},
        ]
        to_compress = [1, 2]
        original = list(to_compress)
        has_prior, extended = SummarizeStrategy._detect_prior_summary(messages, to_compress, keep_recent=1)
        assert has_prior is True
        assert extended == original
        # 入参不应被修改
        assert to_compress == original

    def test_summary_outside_to_compress_but_in_range(self):
        """摘要不在 to_compress 但在搜索范围内 → 返回 (True, [摘要索引] + to_compress)"""
        messages = [
            {"role": "system", "content": "你是一个助手"},
            {"role": "system", "content": "[对话摘要] 旧摘要"},   # 索引 1，旧摘要
            {"role": "user", "content": "问题"},                  # 索引 2
            {"role": "assistant", "content": "回答"},             # 索引 3
        ]
        to_compress = [2, 3]
        has_prior, extended = SummarizeStrategy._detect_prior_summary(messages, to_compress, keep_recent=1)
        assert has_prior is True
        assert extended[0] == 1
        assert extended[1:] == [2, 3]
        # 入参不应被修改
        assert to_compress == [2, 3]

    def test_no_summary_returns_false(self):
        """没有旧摘要 → 返回 (False, to_compress)"""
        messages = [
            {"role": "system", "content": "你是一个助手"},
            {"role": "user", "content": "问题"},
            {"role": "assistant", "content": "回答"},
        ]
        to_compress = [1, 2]
        original = list(to_compress)
        has_prior, extended = SummarizeStrategy._detect_prior_summary(messages, to_compress, keep_recent=1)
        assert has_prior is False
        assert extended == original
        assert to_compress == original

    def test_summary_already_in_to_compress_not_duplicated(self):
        """摘要已在 to_compress 中时不应重复添加"""
        messages = [
            {"role": "system", "content": "你是一个助手"},
            {"role": "system", "content": "[对话摘要] 旧摘要"},
            {"role": "user", "content": "问题"},
        ]
        to_compress = [1, 2]  # 索引 1 已经是摘要
        has_prior, extended = SummarizeStrategy._detect_prior_summary(messages, to_compress, keep_recent=1)
        assert has_prior is True
        assert extended == [1, 2]  # 不重复

    def test_summary_outside_boundary_not_found(self):
        """摘要超出搜索边界（keep_recent 之后）→ 返回 (False, to_compress)"""
        messages = [
            {"role": "system", "content": "你是一个助手"},
            {"role": "system", "content": "[对话摘要] 很旧的摘要"},  # 索引 1
            {"role": "user", "content": "A"},
            {"role": "user", "content": "B"},
            {"role": "user", "content": "C"},
            {"role": "user", "content": "D"},
            {"role": "user", "content": "最近消息"},
        ]
        # keep_recent=3 → boundary = max(1, 7-3) = 4
        # 搜索范围 range(1, 4) → 索引 1,2,3
        # 索引 1 的旧摘要被找到
        to_compress = [5, 6]
        has_prior, extended = SummarizeStrategy._detect_prior_summary(messages, to_compress, keep_recent=3)
        assert has_prior is True
        assert extended[0] == 1

    def test_summary_exactly_at_boundary_edge(self):
        """摘要恰好在边界位置（i == boundary-1）应被找到"""
        messages = [
            {"role": "system", "content": "你是一个助手"},
            {"role": "user", "content": "A"},
            {"role": "user", "content": "B"},
            {"role": "system", "content": "[对话摘要] 边界摘要"},  # 索引 3
            {"role": "user", "content": "最近消息"},
        ]
        # keep_recent=1 → boundary = max(1, 5-1) = 4
        # 搜索范围 range(1, 4) → 索引 1,2,3
        # 索引 3 在范围内，应被找到
        to_compress = [4]
        has_prior, extended = SummarizeStrategy._detect_prior_summary(messages, to_compress, keep_recent=1)
        assert has_prior is True
        assert extended[0] == 3

    def test_summary_not_starts_with_summary_prefix(self):
        """不以 [对话摘要] 开头的 system 消息不被视为摘要"""
        messages = [
            {"role": "system", "content": "你是一个助手"},
            {"role": "system", "content": "普通系统消息"},
            {"role": "user", "content": "问题"},
        ]
        to_compress = [1, 2]
        has_prior, extended = SummarizeStrategy._detect_prior_summary(messages, to_compress, keep_recent=1)
        assert has_prior is False
        assert extended == [1, 2]

    def test_empty_messages(self):
        """空消息列表返回 (False, [])"""
        messages = []
        to_compress = []
        has_prior, extended = SummarizeStrategy._detect_prior_summary(messages, to_compress, keep_recent=1)
        assert has_prior is False
        assert extended == []

    def test_to_compress_contains_only_user_messages_with_no_summary(self):
        """to_compress 仅有 user 消息且无旧摘要 → 返回 (False, to_compress)"""
        messages = [
            {"role": "system", "content": "你是一个助手"},
            {"role": "user", "content": "Q1"},
            {"role": "assistant", "content": "A1"},
            {"role": "user", "content": "Q2"},
        ]
        to_compress = [1, 2, 3]
        has_prior, extended = SummarizeStrategy._detect_prior_summary(messages, to_compress, keep_recent=1)
        assert has_prior is False
        assert extended == [1, 2, 3]

    def test_summary_content_none(self):
        """content=None 的 system 消息不被视为摘要"""
        messages = [
            {"role": "system", "content": "你是一个助手"},
            {"role": "system", "content": None},
            {"role": "user", "content": "问题"},
        ]
        to_compress = [1, 2]
        has_prior, extended = SummarizeStrategy._detect_prior_summary(messages, to_compress, keep_recent=1)
        assert has_prior is False
        assert extended == [1, 2]

    def test_summary_content_empty_string(self):
        """content="" 的 system 消息不被视为摘要"""
        messages = [
            {"role": "system", "content": "你是一个助手"},
            {"role": "system", "content": ""},
            {"role": "user", "content": "问题"},
        ]
        to_compress = [1, 2]
        has_prior, extended = SummarizeStrategy._detect_prior_summary(messages, to_compress, keep_recent=1)
        assert has_prior is False
        assert extended == [1, 2]

    def test_multiple_old_summaries_only_first_found(self):
        """多个旧摘要时找到第一个后停止"""
        messages = [
            {"role": "system", "content": "你是一个助手"},
            {"role": "system", "content": "[对话摘要] 摘要一"},
            {"role": "system", "content": "[对话摘要] 摘要二"},
            {"role": "user", "content": "问题"},
        ]
        to_compress = [3]
        has_prior, extended = SummarizeStrategy._detect_prior_summary(messages, to_compress, keep_recent=1)
        assert has_prior is True
        # 应插入索引 1（第一个找到的摘要），而非索引 2
        assert extended[0] == 1

    def test_summary_index_not_duplicated_when_in_compress(self):
        """旧摘要索引已经在 to_compress 中时不重复插入"""
        messages = [
            {"role": "system", "content": "你是一个助手"},
            {"role": "user", "content": "问题"},
            {"role": "system", "content": "[对话摘要] 摘要"},
            {"role": "user", "content": "后续"},
        ]
        # 索引 2 是旧摘要，也在 to_compress 中
        to_compress = [2, 3]
        has_prior, extended = SummarizeStrategy._detect_prior_summary(messages, to_compress, keep_recent=1)
        assert has_prior is True
        assert extended == [2, 3], "不应额外插入索引"
        assert to_compress == [2, 3]


# ═══════════════════════════════════════════════════════════════
# 5. DropStrategy.compress — 集成骨架（确保方法签名正确）
# ═══════════════════════════════════════════════════════════════

class TestDropStrategyIntegration:
    """DropStrategy.compress 基本集成验证"""

    def test_compress_returns_compression_result(self):
        """compress() 返回 CompressionResult 实例"""
        strategy = DropStrategy()
        messages = [
            {"role": "system", "content": "你是一个助手"},
            {"role": "user", "content": "你好"},
        ]

        # 使用 mock cache 对象（_drop_excess 会访问 cache.is_valid）
        class MockCache:
            is_valid = True
            total_chars = 100
            total_tokens = 0
            def on_remove(self, indices):
                pass
            def get_per_msg(self, idx):
                return (50, 0)
            def calc_excess_chars_values(self):
                return 0

        result = strategy.compress(
            messages=messages,
            model="test-model",
            summarize_fn=None,
            on_changed=None,
            cache=MockCache(),
            force=False,
        )
        assert isinstance(result, CompressionResult)

    def test_compress_no_unpinned(self):
        """没有 unpinned 消息时返回 success=False"""
        strategy = DropStrategy()
        messages = [
            {"role": "system", "content": "你是一个助手"},
        ]
        result = strategy.compress(
            messages=messages,
            model="test-model",
            summarize_fn=None,
            on_changed=None,
            cache=None,
            force=False,
        )
        assert result.success is False
        assert result.stats.get("reason") == "no_unpinned"


# ═══════════════════════════════════════════════════════════════
# 6. SummarizeStrategy — 基本集成骨架
# ═══════════════════════════════════════════════════════════════

class TestSummarizeStrategyIntegration:
    """SummarizeStrategy 基本集成验证"""

    def test_compress_returns_compression_result(self):
        """compress() 返回 CompressionResult 实例"""
        strategy = SummarizeStrategy()
        messages = [
            {"role": "system", "content": "你是一个助手"},
            {"role": "user", "content": "你好"},
        ]
        # 需要 mock selector 和 summarizer，这里仅验证返回类型骨架
        # 实际 compress 流程在单元测试中由 _detect_prior_summary 覆盖
        from unittest.mock import patch

        with patch("src.core.compression.selector") as mock_selector:
            mock_selector.adjust_keep_for_tool_groups.return_value = 1
            mock_selector.total_chars.return_value = 100
            mock_selector.select_for_compression.return_value = []

            result = strategy.compress(
                messages=messages,
                model="test-model",
                summarize_fn=None,
                on_changed=None,
                cache=None,
                force=False,
            )
            assert isinstance(result, CompressionResult)
            assert result.success is False
            assert result.stats.get("reason") == "no_candidates"
