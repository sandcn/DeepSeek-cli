#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""上下文管理器

ContextManager 是上下文压缩的唯一对外接口。
通过 on_messages_changed 回调解耦 sandbox manager。

架构设计：
- 策略模式：压缩行为由可插拔的 CompressionStrategy 实现
- 增量缓存：MessageStatsCache 维护消息统计，避免全量遍历
- 降级链：摘要策略失败 → 自动降级到删除策略
"""

import logging
import threading
from typing import Optional

_logger = logging.getLogger(__name__)
from .constants import YELLOW, RESET, audit_log as _log
from . import context_selector as selector
from .context_selector import MessageStatsCache
from .compression import CompressionResult, CompressionStrategy, SummarizeStrategy, DropStrategy  # noqa: F401 — re-exported for backward compat
from ..core.ports.config import ConfigPort
from ..core.adapters.config import DefaultConfigAdapter
from ..core.ports.output import get_default_output_port as _get_out  # noqa: E402


# ═══════════════════════════════════════════════════════════════
# 上下文管理器
# ═══════════════════════════════════════════════════════════════

class ContextManager:
    """上下文压缩管理器。

    采用策略模式，压缩行为由可配置的策略链驱动。
    内置增量统计缓存（MessageStatsCache），避免全量遍历。

    ⚠️ 锁层次（必须遵守，防止死锁）:
        ContextManager._lock → SandboxManager.lock
    解释：ContextManager 持有 _lock 期间可能通过 on_messages_changed 回调
    调用 SandboxManager.shift_indices()/remap_indices()（获取 SandboxManager.lock）。
    任何新的代码路径不得以相反顺序获取这两个锁。

    Args:
        messages: 消息列表引用（就地修改）
        model: 模型名称
        summarize_fn: 摘要生成函数，默认 call_model_sync
        on_messages_changed: 消息变更回调，接收事件字典：
            {"type": "insert", "index": int}
            {"type": "remove", "indices": list[int]}
        strategies: 压缩策略列表（按优先级排序），
                    默认 [SummarizeStrategy, DropStrategy]
    """

    def __init__(self, messages, model, summarize_fn=None,
                 on_messages_changed=None,
                 strategies: Optional[list[CompressionStrategy]] = None,
                 config_port: Optional[ConfigPort] = None):
        self.messages = messages
        self.model = model
        self._on_changed = on_messages_changed
        if summarize_fn is None:
            from ..api.model_async import call_model_sync
            summarize_fn = call_model_sync
        self._summarize_fn = summarize_fn
        self._lock = threading.RLock()
        self._config_port = config_port or DefaultConfigAdapter()

        # 增量统计缓存（惰性同步）
        self._cache = MessageStatsCache()

        # 提示缓存（无锁读取，用于 get_compress_hint）
        self._hint_chars = 0

        # 策略链：依次尝试，第一个成功即停止
        self._strategies = strategies or [
            SummarizeStrategy(),
            DropStrategy(),
        ]

    def update_model(self, model):
        """更新模型名称。"""
        self.model = model

    # ── 缓存管理 ──────────────────────────────────────────

    def _ensure_cache(self):
        """确保缓存已与 messages 列表同步（惰性初始化 + 自动同步）。"""
        if not self._cache.is_valid or len(self._cache) != len(self.messages):
            self._cache.resync(self.messages)

        # 同步提示缓存
        self._hint_chars = self._cache.total_chars

    def invalidate_cache(self):
        """使缓存失效，下次访问时通过 _ensure_cache() 自动重新同步。

        线程安全：由现有 _lock 保护。
        用于外部（如 session.run_round 异常回滚后）通知缓存已过时。
        """
        with self._lock:
            self._cache.invalidate()
            self._hint_chars = 0

    # ── 压缩入口 ──────────────────────────────────────────

    def check_and_compress(self, force=False):
        """检查并执行上下文压缩。

        Args:
            force: 是否强制全量压缩
        """
        with self._lock:
            messages = self.messages

            # 检查是否有足够的非系统消息可压缩
            if not self._has_compressible_messages(messages):
                return

            # 确保缓存已同步
            self._ensure_cache()

            total_chars_val = self._cache.total_chars
            total_tokens_val = self._cache.total_tokens

            force, should = self._should_compress(force, total_chars_val, total_tokens_val)
            if not should:
                return

            self._do_compress(force)

    @staticmethod
    def _has_compressible_messages(messages) -> bool:
        """检查是否有足够的非系统消息可供压缩。"""
        non_system_count = sum(
            1 for m in messages
            if m.get("role") != "system"
            or (m.get("content") or "").startswith("[对话摘要]")
        )
        return non_system_count > 2

    def _should_compress(self, force, total_chars_val, total_tokens_val):
        """判断是否应该执行压缩，返回 (force, 是否压缩)。"""
        max_context_chars = self._config_port.get_max_context_chars()
        max_context_tokens = self._config_port.get_max_context_tokens()
        auto_force_threshold = self._config_port.get_auto_force_compress_threshold()
        if not force and selector.should_auto_force_values(
            total_chars_val, total_tokens_val,
            auto_force_threshold=auto_force_threshold,
            max_context_tokens=max_context_tokens,
        ):
            force = True
        if force or selector.exceeds_limit_values(
            total_chars_val, total_tokens_val,
            max_context_chars=max_context_chars,
            max_context_tokens=max_context_tokens,
        ):
            return force, True
        return force, False

    def _do_compress(self, force):
        """执行压缩：按策略链依次尝试，第一个成功即停止。"""
        for strategy in self._strategies:
            result = strategy.compress(
                self.messages, self.model, self._summarize_fn,
                self._on_changed, self._cache, force,
            )
            if result.success:
                # 同步提示缓存
                self._hint_chars = self._cache.total_chars if self._cache.is_valid else 0
                return

        _log("CONTEXT_TRIM", "所有压缩策略均失败")

    # ── 消息数量限制 ──────────────────────────────────────

    def enforce_message_limit(self):
        """强制执行会话消息数量限制。

        Returns:
            删除的消息数，0 表示未执行删除
        """
        max_session_messages = self._config_port.get_max_session_messages()
        with self._lock:
            messages = self.messages
            if max_session_messages <= 0 or len(messages) <= max_session_messages:
                return 0

            need = len(messages) - max_session_messages
            unpinned_indices = []
            for i in range(1, len(messages)):
                if len(unpinned_indices) >= need:
                    break
                msg = messages[i]
                if not msg.get("pinned") and not (
                    msg.get("role") == "system"
                    and not (msg.get("content") or "").startswith("[对话摘要]")
                ):
                    unpinned_indices.append(i)

            if not unpinned_indices:
                return 0

            removed = len(unpinned_indices)
            to_delete = sorted(unpinned_indices)
            for idx in reversed(to_delete):
                messages.pop(idx)

            # 更新缓存
            if self._cache.is_valid:
                self._cache.on_remove(to_delete)
                self._hint_chars = self._cache.total_chars

            self._notify_changed({"type": "remove", "indices": unpinned_indices})

            _log("SESSION_LIMIT", f"删除 {removed} 条消息以保持限制 ({max_session_messages})")
            _get_out().write(
                f"{YELLOW}消息数达到限制 ({max_session_messages})，已删除 {removed} 条{RESET}",
                level="raw",
                source="context",
            )

            return removed

    # ── 压缩提示 ──────────────────────────────────────────

    def get_compress_hint(self):
        """返回压缩提示文本，无需提示时返回空字符串。

        无锁读取缓存值，适合 UI 渲染调用。
        """
        max_context_chars = self._config_port.get_max_context_chars()
        if not self.messages or max_context_chars <= 0:
            return ""

        chars = self._hint_chars
        if chars <= 0:
            return ""

        pct = chars / max_context_chars * 100
        if pct >= 90:
            return f"上下文 {pct:.0f}% /compress"
        elif pct >= 80:
            return f"上下文 {pct:.0f}%"
        return ""

    # ── 回调通知 ──────────────────────────────────────────

    def _notify_changed(self, event):
        if self._on_changed:
            try:
                self._on_changed(event)
            except Exception:
                _logger.exception("ContextManager 回调异常")

    def notify_messages_removed(self, indices: list[int]):
        """手动通知消息已被删除，触发 _on_changed 回调同步。

        用于外部在直接操作 messages 列表后（如异常回滚 pop），
        手动触发 sandbox manager 的索引同步。

        线程安全：由现有 _lock 保护。
        """
        with self._lock:
            self._notify_changed({"type": "remove", "indices": indices})
