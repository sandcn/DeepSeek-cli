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

# ═══════════════════════════════════════════════════════════════
# 架构违反标记 — 已知技术债务（方案B已修复）
#
# ContextManager 的 summarize_fn 默认值原直接依赖
# src.api.model_async.call_model_sync，违反「核心层不依赖基础设施层」
# 原则。已在 src.core.adapters.model.SyncModelBridge 中修复：
# summarize_fn 的默认值改为通过 SyncModelBridge().summarize
# 桥接，消除对 api 层的直接导入依赖。
# ═══════════════════════════════════════════════════════════════

import json
import logging
import threading
from typing import Optional

_logger = logging.getLogger(__name__)
from .constants import YELLOW, DIM, RESET, audit_log as _log
from . import context_selector as selector
from .context_selector import MessageStatsCache
from ..api.tokens import estimate_tokens
from .compression import CompressionResult, CompressionStrategy, SummarizeStrategy, DropStrategy  # noqa: F401 — re-exported for backward compat
from .ports.config import ConfigPort
from .ports.output import OutputPort
from .adapters.config import DefaultConfigAdapter


# ═══════════════════════════════════════════════════════════════
# 全局上下文使用率快照（TUI 模式行行首显示用，性能：O(1) 无锁读）
#
# 设计（2026-08-19 用户需求「mainagent 上下文使用百分比，要性能好」）：
#   - 写入侧：ContextManager 在**缓存同步点**（_ensure_cache resync /
#     _do_compress / enforce_message_limit / invalidate_cache）一次性计算
#     百分比并写入本模块级全局（低频，锁/开销可忽略）；
#   - 读取侧：TUI 渲染线程每帧直接读本全局 int（无锁、无除法、无扫描），
#     与状态栏 token 速度快照（api.stats）同模式，零每帧计算成本；
#   - 常驻显示（2026-08-19 用户反馈「空闲也要显示」）：会话启动即写 0，
#     空闲/无消息时保持 0% 显示（不隐藏）——上下文使用率是会话级指标，
#     与是否活跃无关；仅配置禁用（model_context_tokens<=0）时写 None 不显示。
#   - 精度（2026-08-19 用户反馈「百分比有 1 位小数」）：快照存 round 到
#     1 位小数的 float 百分比，TUI 显示 ``main · 45.3%``。
# ═══════════════════════════════════════════════════════════════
_context_usage_percent: Optional[float] = None


def set_context_usage_percent(pct: Optional[float]) -> None:
    """写入全局上下文使用百分比快照（ContextManager 缓存同步点调用）。

    Args:
        pct: 上下文使用百分比（0-100，1 位小数）；None 表示不可用（配置禁用）。
    """
    global _context_usage_percent
    _context_usage_percent = pct


def get_context_usage_percent() -> Optional[float]:
    """读取全局上下文使用百分比（O(1) 无锁，适合 UI 渲染每帧调用）。

    Returns:
        0-100 的浮点百分比（1 位小数）；None 表示不可用（配置禁用，TUI 不显示）。
    """
    return _context_usage_percent


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
        config_port: 配置端口（max_context_chars 等读取）
        output_port: 输出端口
        tools: 当前工具 schemas（list[dict]）——上下文使用率统计的一部分
            （工具列表随系统提词一起发送给模型，须计入上下文占用）。
    """

    def __init__(self, messages, model, summarize_fn=None,
                 on_messages_changed=None,
                 strategies: Optional[list[CompressionStrategy]] = None,
                 config_port: Optional[ConfigPort] = None,
                 output_port: Optional[OutputPort] = None,
                 tools: Optional[list] = None):
        self.messages = messages
        self.model = model
        self._on_changed = on_messages_changed
        if summarize_fn is None:
            from .adapters.model import SyncModelBridge
            summarize_fn = SyncModelBridge().summarize
        self._summarize_fn = summarize_fn
        self._lock = threading.RLock()
        self._config_port = config_port or DefaultConfigAdapter()
        self._output_port = output_port

        # 增量统计缓存（惰性同步）
        self._cache = MessageStatsCache()

        # 提示缓存（无锁读取，用于 get_compress_hint）
        self._hint_chars = 0

        # 工具 schemas（上下文使用率统计的一部分；可经 set_tools 更新）
        self.tools = list(tools or [])

        # 策略链：依次尝试，第一个成功即停止
        self._strategies = strategies or [
            SummarizeStrategy(),
            DropStrategy(),
        ]

        # ★ 会话启动即刷新全局上下文使用率（2026-08-19 用户反馈「空闲也要
        #   显示」+「统计系统提词跟工具列表的上下文」）——启动/空闲时行首
        #   常驻显示 ``main · N%``（含系统提词 + 工具列表基础上下文，不再
        #   因「程序没跑」隐藏或归零；上一会话残留值一并覆盖）。
        self.refresh_usage()

    def update_model(self, model):
        """更新模型名称。"""
        self.model = model

    def set_tools(self, tools: Optional[list]) -> None:
        """更新工具 schemas 并刷新上下文使用率（工具列表变化后调用）。"""
        self.tools = list(tools or [])
        self.refresh_usage()

    # ── 缓存管理 ──────────────────────────────────────────

    def _ensure_cache(self):
        """确保缓存已与 messages 列表同步（惰性初始化 + 自动同步）。"""
        if not self._cache.is_valid or len(self._cache) != len(self.messages):
            self._cache.resync(self.messages)

        # 同步提示缓存
        self._hint_chars = self._cache.total_chars
        # 同步全局上下文使用率快照（TUI 模式行行首显示）
        self._sync_usage_percent()

    def invalidate_cache(self):
        """使缓存失效，下次访问时通过 _ensure_cache() 自动重新同步。

        线程安全：由现有 _lock 保护。
        用于外部（如 session.run_round 异常回滚后）通知缓存已过时。
        """
        with self._lock:
            self._cache.invalidate()
            self._hint_chars = 0
            # 同步全局上下文使用率快照（保持显示，下次 resync 恢复精确值）
            self._sync_usage_percent()

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
        on_info = None
        if self._output_port:
            on_info = lambda text: self._output_port.write(
                f"{DIM}{text}{RESET}", level="raw", source="context",
            )
        for strategy in self._strategies:
            result = strategy.compress(
                self.messages, self.model, self._summarize_fn,
                self._on_changed, self._cache, force,
                on_info=on_info,
            )
            if result.success:
                # 同步提示缓存
                self._hint_chars = self._cache.total_chars if self._cache.is_valid else 0
                # 同步全局上下文使用率快照（TUI 模式行行首显示）
                self._sync_usage_percent()
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
                # 同步全局上下文使用率快照（TUI 模式行行首显示）
                self._sync_usage_percent()

            self._notify_changed({"type": "remove", "indices": unpinned_indices})

            _log("SESSION_LIMIT", f"删除 {removed} 条消息以保持限制 ({max_session_messages})")
            if self._output_port:
                self._output_port.write(
                    f"{YELLOW}消息数达到限制 ({max_session_messages})，已删除 {removed} 条{RESET}",
                    level="raw",
                    source="context",
                )

            return removed

    # ── 上下文使用率（TUI 模式行行首显示） ────────────────

    def _tools_tokens(self) -> int:
        """工具列表（schemas）序列化后的估算 token 数——上下文固定开销。

        工具 schemas 随系统提词一起发送给模型（每个请求都占用上下文），
        须计入上下文使用率统计。estimate_tokens 有 lru_cache（性能好）；
        JSON 序列化失败的单条 schema 跳过。
        """
        total = 0
        for schema in getattr(self, "tools", None) or []:
            try:
                total += estimate_tokens(json.dumps(schema, ensure_ascii=False))
            except (TypeError, ValueError):
                continue
        return total

    def refresh_usage(self, force: bool = False) -> None:
        """刷新全局上下文使用率（动态刷新入口，2026-08-19 用户需求）。

        统计口径：**系统提词 + 工具列表 + 全部消息**占**模型上下文窗口**
        （model_context_tokens，默认 1M tokens）的百分比——
          - 系统提词：messages 中 role=system 的消息全文（MessageStatsCache
            resync 全量统计 token，含于 total_tokens）；
          - 工具列表：self.tools schemas 序列化估算 token（_tools_tokens）；
          - 消息：MessageStatsCache.total_tokens（含 system，懒同步——长度
            变化才全量 resync，否则复用缓存，性能好）；
          - 分母：get_model_context_tokens()（模型上下文窗口，默认 1M token）。
        计算一次性写入全局快照，TUI 渲染线程每帧 O(1) 无锁读取。

        Args:
            force: 强制全量 resync（默认 False 懒同步）。系统提词**内容**变化
                但消息条数不变时（如 Ctrl+B 空模式切换 rebuild_system_prompt
                ——system 消息数相同、内容替换）懒同步会命中旧缓存 → 百分比
                不更新；此类场景须传 force=True 强制重算（低频，O(n) 可接受）。

        动态刷新调用点：会话启动（__init__）、消息追加（BaseAgent 消息
        方法）、系统提词重建（rebuild_system_prompt 传 force=True）、工具
        更新（set_tools）、缓存同步（_ensure_cache/_do_compress/
        enforce_message_limit/invalidate_cache）。

        常驻显示语义（「空闲也要显示」）：无消息时系统提词+工具列表仍占
        上下文（写实际百分比，不隐藏）；仅配置禁用（model_context_tokens
        <=0）时写 None（TUI 不显示该段）。

        精度（「百分比有 1 位小数」）：百分比 round 到 1 位小数后写入全局。
        """
        try:
            ctx_tokens = self._config_port.get_model_context_tokens()
            if ctx_tokens <= 0:
                set_context_usage_percent(None)
                return
            # 懒同步缓存（长度变化才全量 resync；复用避免每帧重算）；
            # force=True（Ctrl+B 空模式切换等 system 内容变化场景）强制重算。
            if force or not self._cache.is_valid or len(self._cache) != len(self.messages):
                self._cache.resync(self.messages)
            self._hint_chars = self._cache.total_chars
            tokens = self._cache.total_tokens + self._tools_tokens()
            if tokens <= 0:
                set_context_usage_percent(0.0)
                return
            pct = round(tokens / ctx_tokens * 100, 1)
            set_context_usage_percent(pct)
        except Exception:
            # 防御：配置读取异常等 → 不可用（不中断上下文管理主流程）
            set_context_usage_percent(None)

    def _sync_usage_percent(self) -> None:
        """兼容内部别名：委托 refresh_usage（既有调用点语义不变）。

        _ensure_cache / _do_compress / enforce_message_limit /
        invalidate_cache 内的同步点统一走 refresh_usage 全量统计口径
        （含系统提词 + 工具列表）。
        """
        self.refresh_usage()

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
        if pct >= 80:
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
