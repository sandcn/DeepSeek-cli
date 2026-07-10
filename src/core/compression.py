#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""上下文压缩策略

使用策略模式设计，压缩行为由可插拔的 CompressionStrategy 实现。
ContextManager 按注册顺序尝试各策略，成功即停止。
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import field
from src._compat import dataclass


_logger = logging.getLogger(__name__)
from .constants import GREEN, YELLOW, DIM, RESET
from . import context_selector as selector
from . import context_summarizer as summarizer
from .constants import format_token_k, audit_log as _log


from ..core.adapters.output import get_default_output_port as _get_out  # noqa: E402


# ═══════════════════════════════════════════════════════════════
# 压缩结果
# ═══════════════════════════════════════════════════════════════

@dataclass(slots=True)
class CompressionResult:
    """一次压缩操作的结果。

    Attributes:
        success: 是否成功
        removed_indices: 被删除的消息索引列表（原始位置）
        inserted_message: 插入的摘要消息（如有）
        chars_saved: 节省的字符数
        stats: 附加统计信息 {key: value}
    """
    success: bool = True
    removed_indices: list = field(default_factory=list)
    inserted_message: dict | None = None
    chars_saved: int = 0
    stats: dict = field(default_factory=dict)

    def __post_init__(self):
        self.removed_indices = self.removed_indices or []
        self.stats = self.stats or {}


# ═══════════════════════════════════════════════════════════════
# 压缩策略基类
# ═══════════════════════════════════════════════════════════════

class CompressionStrategy(ABC):
    """上下文压缩策略基类。

    子类实现 compress() 方法，返回 CompressionResult。
    ContextManager 按注册顺序尝试各策略，成功即停止。
    """

    @abstractmethod
    def compress(self, messages, model, summarize_fn,
                 on_changed, cache, force) -> CompressionResult:
        """执行压缩。

        Args:
            messages: 消息列表（就地修改）
            model: 模型名称
            summarize_fn: 摘要生成函数
            on_changed: 消息变更回调
            cache: 增量统计缓存（操作后须更新）
            force: 是否强制全量压缩

        Returns:
            CompressionResult
        """

    @staticmethod
    def _safe_notify(on_changed, event):
        """安全地通知回调，捕获并记录异常。"""
        if on_changed:
            try:
                on_changed(event)
            except Exception:
                _logger.exception("ContextManager 回调异常")


# ═══════════════════════════════════════════════════════════════
# 摘要压缩策略
# ═══════════════════════════════════════════════════════════════

class SummarizeStrategy(CompressionStrategy):
    """摘要模式压缩策略。

    选择可压缩消息 → 调用模型生成结构化摘要 → 替换为摘要消息。
    是主要压缩方式，信息损失最小。
    """

    def compress(self, messages, model, summarize_fn,
                 on_changed, cache, force) -> CompressionResult:
        keep_recent = selector.adjust_keep_for_tool_groups(messages)
        # cache 可能为 None（单元测试直接调用时）或未同步
        if cache is not None and cache.is_valid:
            total_chars_val = cache.total_chars
            total_tokens_val = cache.total_tokens
        else:
            total_chars_val = selector.total_chars(messages)
            total_tokens_val = 0

        to_compress = selector.select_for_compression(
            messages, keep_recent, force, total_chars_val, total_tokens_val,
        )

        if not to_compress:
            return CompressionResult(success=False, stats={"reason": "no_candidates"})

        chars_before = total_chars_val

        # 检查并整合旧摘要（不修改入参 to_compress）
        has_prior_summary, to_compress = self._detect_prior_summary(messages, to_compress, keep_recent)

        msgs_to_compress = [messages[i] for i in to_compress]
        _get_out().write(f"{DIM}正在压缩 {len(to_compress)} 条消息...{RESET}", level="raw", source="context")

        try:
            start_time = time.time()
            summary, usage = summarizer.summarize(
                msgs_to_compress, has_prior_summary,
                summarize_fn, model,
            )
            elapsed = time.time() - start_time

            self._apply_summary(messages, to_compress, summary, on_changed, cache)
            self._report_success(to_compress, chars_before, cache, usage, elapsed, messages)

            return CompressionResult(
                success=True,
                removed_indices=to_compress,
                inserted_message={"role": "system", "content": f"[对话摘要] {summary}"},
                chars_saved=chars_before - (cache.total_chars if cache.is_valid
                                            else selector.total_chars(messages)),
                stats={"usage": usage, "elapsed": elapsed},
            )

        except (KeyError, ValueError, IndexError) as e:
            _log("CONTEXT_TRIM_FAIL", f"摘要压缩失败: {e}")
            _get_out().write(f"{YELLOW}摘要压缩失败，执行降级: {e}{RESET}", level="raw", source="context")
            return CompressionResult(success=False, stats={"error": str(e)})

        except Exception as e:
            _log("CONTEXT_TRIM_FAIL", f"摘要压缩异常: {e}")
            _get_out().write(f"{YELLOW}摘要压缩异常，执行降级: {e}{RESET}", level="raw", source="context")
            return CompressionResult(success=False, stats={"error": str(e)})

    # ── 内部方法 ──────────────────────────────────────────

    @staticmethod
    def _detect_prior_summary(messages, to_compress, keep_recent) -> tuple[bool, list]:
        """检测待压缩范围内是否包含旧摘要，若无则向前查找并返回扩展后的索引列表。

        返回 (has_prior, extended_indices) 元组，不修改入参 to_compress。
        """
        has_prior = any(
            messages[i].get("role") == "system"
            and (messages[i].get("content") or "").startswith("[对话摘要]")
            for i in to_compress
        )

        if not has_prior:
            boundary = max(1, len(messages) - keep_recent)
            for i in range(1, boundary):
                msg = messages[i]
                if msg.get("role") == "system" and (msg.get("content") or "").startswith("[对话摘要]"):
                    has_prior = True
                    return (True, [i] + list(to_compress))

        return (has_prior, list(to_compress))

    @staticmethod
    def _apply_summary(messages, to_compress, summary, on_changed, cache):
        """应用摘要：删除旧消息 → 插入摘要消息 → 更新缓存。

        摘要插入在所有非摘要 system 消息之后（而非硬编码 index=1），
        以兼容多 parts 系统提示词结构。
        """
        # 删除前计算摘要插入位置：跳过所有非摘要 system 消息
        system_end = 0
        for m in messages:
            if m.get("role") == "system" and not (m.get("content") or "").startswith("[对话摘要]"):
                system_end += 1
            else:
                break

        # 从后往前删除（避免索引偏移）
        for idx in sorted(to_compress, reverse=True):
            if idx < len(messages):
                messages.pop(idx)

        if cache.is_valid:
            cache.on_remove(to_compress)

        # 在所有非摘要 system 消息之后插入摘要
        summary_msg = {"role": "system", "content": f"[对话摘要] {summary}"}
        messages.insert(system_end, summary_msg)

        if cache.is_valid:
            cache.on_insert(system_end, summary_msg)

        # 通知回调（先 remove 后 insert）
        SummarizeStrategy._safe_notify(on_changed, {"type": "remove", "indices": to_compress})
        SummarizeStrategy._safe_notify(on_changed, {"type": "insert", "index": system_end})

    @staticmethod
    def _report_success(to_compress, chars_before, cache, usage, elapsed, messages):
        """输出压缩成功日志。"""
        chars_after = cache.total_chars if cache.is_valid else selector.total_chars(messages)
        saved = chars_before - chars_after
        pinned = sum(1 for m in messages if m.get("pinned"))

        token_info = ""
        if usage and "input" in usage and "output" in usage:
            time_info = f" {elapsed:.1f}s" if elapsed >= 0.1 else ""

            def _kt(n):
                return format_token_k(n)
            token_info = f" {_kt(usage['input'])}/{_kt(usage['output'])}t{time_info}"

        def _kc(n):
            return format_token_k(n)

        pin_info = f" {pinned} pinned" if pinned else ""
        _get_out().write(
            f"{GREEN}+ 压缩 {len(to_compress)} 条 "
            f"{_kc(chars_before)}→{_kc(chars_after)} 节省 {_kc(saved)}"
            f"{token_info}{pin_info}{RESET}",
            level="raw",
            source="context",
        )
        _log("CONTEXT_TRIM", f"压缩 {len(to_compress)} 条消息, 节省 {saved} 字符")


# ═══════════════════════════════════════════════════════════════
# 删除降级策略
# ═══════════════════════════════════════════════════════════════

class DropStrategy(CompressionStrategy):
    """降级删除策略。

    直接删除 unpinned 消息释放上下文空间。
    - force=True: 删除所有 unpinned 消息
    - force=False: 仅删除足够释放超出量的消息
    """

    def compress(self, messages, model, summarize_fn,
                 on_changed, cache, force) -> CompressionResult:
        unpinned_indices = self._collect_unpinned(messages)
        if not unpinned_indices:
            return CompressionResult(success=False, stats={"reason": "no_unpinned"})

        if force:
            return self._drop_all(messages, unpinned_indices, on_changed, cache)
        else:
            return self._drop_excess(messages, unpinned_indices, on_changed, cache)

    # ── 内部方法 ──────────────────────────────────────────

    @staticmethod
    def _collect_unpinned(messages):
        """收集可删除的 unpinned 消息索引（跳过系统提示词和旧摘要）。"""
        return [
            i for i in range(1, len(messages))
            if not messages[i].get("pinned")
            and not (messages[i].get("role") == "system"
                     and not (messages[i].get("content") or "").startswith("[对话摘要]"))
        ]

    @staticmethod
    def _drop_all(messages, indices, on_changed, cache):
        """强制模式：删除所有 unpinned 消息。"""
        chars_before = cache.total_chars if cache.is_valid else selector.total_chars(messages)

        for idx in reversed(indices):
            messages.pop(idx)

        if cache.is_valid:
            cache.on_remove(indices)

        DropStrategy._safe_notify(on_changed, {"type": "remove", "indices": sorted(indices)})

        saved = chars_before - (cache.total_chars if cache.is_valid
                                else selector.total_chars(messages))
        _log("CONTEXT_TRIM", f"降级删除 {len(indices)} 条旧消息")
        return CompressionResult(
            success=True,
            removed_indices=sorted(indices),
            chars_saved=saved,
            stats={"mode": "force_drop"},
        )

    @staticmethod
    def _drop_excess(messages, indices, on_changed, cache):
        """非强制模式：计算超出量，批量删除直到释放足够空间。"""
        chars_before = cache.total_chars if cache.is_valid else selector.total_chars(messages)
        tokens_before = cache.total_tokens if cache.is_valid else 0

        need = selector.calc_excess_chars_values(chars_before, tokens_before)
        if need <= 0:
            return CompressionResult(success=False, stats={"reason": "no_excess"})

        # 先批量收集需要删除的索引（按从后往前的顺序），再统一执行
        freed = 0
        to_remove = []
        for idx in reversed(indices):
            if cache.is_valid:
                c, _ = cache.get_per_msg(idx)
            else:
                c = len(selector.message_to_text(messages[idx]))
            to_remove.append(idx)
            freed += c
            if freed >= need:
                break

        # 批量删除（从后往前避免索引偏移）
        for idx in to_remove:
            messages.pop(idx)

        if cache.is_valid:
            cache.on_remove(to_remove)

        if to_remove:
            DropStrategy._safe_notify(on_changed, {"type": "remove", "indices": sorted(to_remove)})
            _log("CONTEXT_TRIM", f"降级删除 {len(to_remove)} 条旧消息，释放 {freed} 字符")

        return CompressionResult(
            success=True,
            removed_indices=sorted(to_remove),
            chars_saved=freed,
            stats={"mode": "excess_drop"},
        )
