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

# ═══════════════════════════════════════════════════════════════
# 流式输出实时刷新（2026-08-19 用户需求「上下文百分比要实时刷新」）
#
# 设计：
#   - 写入侧：流式管线（api/stream/pipeline_async.py）在流式输出期间
#     每 ~0.1s 调用 ``update_streaming_usage(ctx.streamed_output_tokens, label)``
#     把「当前已生成的输出估算 tokens」写入模块级全局 ``_streaming_extra_tokens``，
#     并触发活跃 ContextManager.refresh_usage() 重算全局百分比——AI 生成时
#     行首 ``main · N%`` 随输出增长实时上升；
#   - 统计口径：refresh_usage() 计算时在（系统提词 + 工具列表 + 全部消息）
#     基础上叠加流式增量（当前流式输出估算 tokens），占模型上下文窗口比例；
#   - 清零：流式结束（_cleanup_display，幂等）调用 update_streaming_usage(0)
#     清零——随后 assistant 消息追加由 refresh_usage() 按消息全文重算真实值，
#     避免「流式增量 + 消息内容」双计；
#   - SubAgent（label 以 "agent-" 前缀）跳过：其输出计入 SubAgent 独立上下文，
#     不占主 Agent 上下文；主 Agent 流式 label 为 "assistant"（pipeline.py）。
#   - 性能：全局读写为 GIL 原子（无锁）；refresh_usage 在流式期间缓存有效
#     （消息未变不 resync）+ _tools_tokens 结果缓存（_tools_tokens_cache），
#     每 0.1s 刷新路径 O(1)。
# ═══════════════════════════════════════════════════════════════
_streaming_extra_tokens: int = 0
#: 流式刷新失败可见性标志（update_streaming_usage：首次失败 WARNING 一次，
#:   后续同错降级 debug——高频路径防日志刷屏）。
_streaming_fail_logged: bool = False
#: 当前活跃 ContextManager 实例（流式管线无实例引用，经此全局访问；
#:   多实例场景最后一个注册者生效，与全局百分比快照同生命周期语义）。
#:   ⚠️ 并发限制：多个非 SubAgent 流**并发**时（同进程多主 Agent 流），
#:     流式增量互相覆盖（最后一个写入者生效）——当前架构单会话单主 Agent
#:     流（TUI 串行对话），不构成实际冲突；未来多流并发需引入流 ID 聚合。
_active_context_manager: Optional["ContextManager"] = None


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


def set_streaming_extra_tokens(tokens: int) -> None:
    """写入全局流式增量 tokens（当前流式输出估算 tokens）。

    Args:
        tokens: 流式输出估算 tokens（>=0；负值/None/不可解析类型归零）。
    """
    global _streaming_extra_tokens
    try:
        _streaming_extra_tokens = max(0, int(tokens or 0))
    except (TypeError, ValueError, OverflowError):
        _streaming_extra_tokens = 0


def get_streaming_extra_tokens() -> int:
    """读取全局流式增量 tokens（O(1) 无锁，供测试/统计口径验证）。"""
    return _streaming_extra_tokens


def set_active_context_manager(cm: Optional["ContextManager"]) -> None:
    """注册当前活跃 ContextManager 实例（ContextManager.__init__ 调用）。

    流式管线（api 层）无实例引用，经 ``update_streaming_usage`` 访问此
    全局以触发实例级 refresh_usage() 重算全局百分比。
    """
    global _active_context_manager
    _active_context_manager = cm


def update_streaming_usage(delta_tokens: int, label: Optional[str] = None) -> None:
    """流式输出过程中实时刷新上下文使用率（api 流式管线调用入口）。

    仅主 Agent 流式计入（label 以 "agent-" 前缀的 SubAgent 跳过——其输出
    占用 SubAgent 独立上下文，不影响主 Agent 百分比）。写入全局流式增量
    后触发活跃 ContextManager.refresh_usage()（缓存有效时 O(1)，性能好）。

    Args:
        delta_tokens: 当前流式输出估算 tokens（ctx.streamed_output_tokens）。
        label: 流式调用标签；None/主 Agent（"assistant"）计入，SubAgent
            （"agent-N"）跳过。
    """
    if label and label.startswith("agent-"):
        return
    global _streaming_extra_tokens, _streaming_fail_logged
    _streaming_extra_tokens = max(0, int(delta_tokens or 0))
    cm = _active_context_manager
    if cm is None:
        return
    try:
        cm.refresh_usage()
        _streaming_fail_logged = False
    except Exception:
        # 失败可见性：首次失败 WARNING（高频路径防刷屏——本函数每 ~0.1s
        # 调用一次，持续失败时仅记一次 WARNING，后续降级 debug）。
        if not _streaming_fail_logged:
            _streaming_fail_logged = True
            _logger.warning("流式输出实时刷新上下文使用率失败（后续同错仅 debug）", exc_info=True)
        else:
            _logger.debug("流式输出实时刷新上下文使用率失败", exc_info=True)


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
        # 工具 schemas 估算 token 结果缓存（_tools_tokens）——流式输出期间
        # 每 ~0.1s 实时刷新上下文使用率（update_streaming_usage → refresh_usage），
        # 工具列表不变时复用缓存避免重复 json.dumps + estimate_tokens。
        # 指纹（_tools_cache_fp = (len, 元素 id 元组)）校验：set_tools 替换
        # 列表 → id 变化自动失效；原地 append/remove → 长度变化自动失效。
        self._tools_tokens_cache: Optional[int] = None
        self._tools_cache_fp: tuple = ()

        # 策略链：依次尝试，第一个成功即停止
        self._strategies = strategies or [
            SummarizeStrategy(),
            DropStrategy(),
        ]

        # ★ 会话启动即刷新全局上下文使用率（2026-08-19 用户反馈「空闲也要
        #   显示」+「统计系统提词跟工具列表的上下文」）——启动/空闲时行首
        #   常驻显示 ``main · N%``（含系统提词 + 工具列表基础上下文，不再
        #   因「程序没跑」隐藏或归零；上一会话残留值一并覆盖）。
        # 注册为活跃实例（流式管线实时刷新经 update_streaming_usage 访问）。
        set_active_context_manager(self)
        self.refresh_usage()

    def update_model(self, model):
        """更新模型名称。"""
        self.model = model

    def set_tools(self, tools: Optional[list]) -> None:
        """更新工具 schemas 并刷新上下文使用率（工具列表变化后调用）。"""
        self.tools = list(tools or [])
        self._tools_tokens_cache = None  # 工具列表变化 → 估算缓存失效
        self._tools_cache_fp = ()
        self.refresh_usage()

    # ── 缓存管理 ──────────────────────────────────────────

    def _ensure_cache(self):
        """确保缓存已与 messages 列表同步（惰性初始化 + 自动同步）。"""
        if not self._cache.is_valid or len(self._cache) != len(self.messages):
            self._cache.resync(self.messages)

        # 同步提示缓存
        self._hint_chars = self._cache.total_chars
        # 同步全局上下文使用率快照（TUI 模式行行首显示）
        self.refresh_usage()

    def invalidate_cache(self):
        """使缓存失效，下次访问时通过 _ensure_cache() 自动重新同步。

        线程安全：由现有 _lock 保护。
        用于外部（如 session.run_round 异常回滚后）通知缓存已过时。
        """
        with self._lock:
            self._cache.invalidate()
            self._hint_chars = 0
            # 同步全局上下文使用率快照（保持显示，下次 resync 恢复精确值）
            self.refresh_usage()

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
        non_system_count = 0
        for m in messages:
            if m.get("role") != "system":
                non_system_count += 1
                continue
            # content 可能为 list（多模态消息）——isinstance 防御，避免
            # 非字符串 content 调 .startswith 抛 AttributeError。
            content = m.get("content")
            if isinstance(content, str) and content.startswith("[对话摘要]"):
                non_system_count += 1
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
                self.refresh_usage()
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
                content = msg.get("content")
                # content 可能为 list（多模态消息）——isinstance 防御。
                is_summary = isinstance(content, str) and content.startswith("[对话摘要]")
                if not msg.get("pinned") and not (
                    msg.get("role") == "system" and not is_summary
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
                self.refresh_usage()

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

        ★ 结果缓存（_tools_tokens_cache + _tools_cache_fp 指纹）：流式输出
        期间每 ~0.1s 实时刷新上下文使用率（update_streaming_usage →
        refresh_usage）都会调用本方法——工具列表不变时复用缓存，避免每次
        重复 json.dumps + estimate_tokens。指纹 = (len(tools), 元素 id 元组)：
        set_tools 替换 / 原地增删工具均触发失效重算。
        """
        tools = getattr(self, "tools", None) or []
        fp = (len(tools), tuple(id(t) for t in tools))
        if self._tools_tokens_cache is not None and self._tools_cache_fp == fp:
            return self._tools_tokens_cache
        total = 0
        for schema in tools:
            try:
                total += estimate_tokens(json.dumps(schema, ensure_ascii=False))
            except (TypeError, ValueError):
                continue
        self._tools_tokens_cache = total
        self._tools_cache_fp = fp
        return total

    def refresh_usage(self, force: bool = False) -> None:
        """刷新全局上下文使用率（动态刷新入口，2026-08-19 用户需求）。

        统计口径：**系统提词 + 工具列表 + 全部消息**占**模型上下文窗口**
        （model_context_tokens，默认 1M tokens）的百分比——
          - 系统提词：messages 中 role=system 的消息全文（MessageStatsCache
            resync 全量统计 token，含于 total_tokens）；
          - 工具列表：self.tools schemas 序列化估算 token（_tools_tokens，
            结果缓存，工具不变 O(1)）；
          - 消息：MessageStatsCache.total_tokens（含 system，懒同步——长度
            变化才全量 resync，否则复用缓存，性能好）；
          - 流式增量（2026-08-19「上下文百分比要实时刷新」）：模块级全局
            _streaming_extra_tokens——AI 流式生成期间当前已输出的估算
            tokens，经 update_streaming_usage 每 ~0.1s 写入并触发本方法
            重算，行首 ``main · N%`` 随输出增长实时上升；
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

        线程安全：resync 段（可能全量遍历 + 写缓存）在 _lock 内执行——与
        check_and_compress 持锁路径（_ensure_cache）串行化，避免流式线程
        （update_streaming_usage）与压缩线程并发重建 _cache；RLock 可重入，
        持锁调用方（_ensure_cache 等）嵌套进入安全。
        """
        try:
            ctx_tokens = self._config_port.get_model_context_tokens()
            if ctx_tokens <= 0:
                set_context_usage_percent(None)
                return
            with self._lock:
                # 懒同步缓存（长度变化才全量 resync；复用避免每帧重算）；
                # force=True（Ctrl+B 空模式切换等 system 内容变化场景）强制重算。
                if force or not self._cache.is_valid or len(self._cache) != len(self.messages):
                    self._cache.resync(self.messages)
                self._hint_chars = self._cache.total_chars
                tokens = self._cache.total_tokens + self._tools_tokens() + _streaming_extra_tokens
            if tokens <= 0:
                set_context_usage_percent(0.0)
                return
            pct = round(tokens / ctx_tokens * 100, 1)
            set_context_usage_percent(pct)
        except Exception:
            # 防御：配置读取异常等 → 不可用（不中断上下文管理主流程）。
            # 记 debug 便于定位根因（写 None 后 TUI 不显示，用户无感知）。
            _logger.debug("刷新上下文使用率失败", exc_info=True)
            set_context_usage_percent(None)

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

        ★ 自包含缓存一致性：方法内先使统计缓存失效（invalidate_cache）——
        即使调用方未主动失效，后续 refresh_usage / _ensure_cache 也会按最新
        messages 重算（不残留已删消息的统计）。既有调用点（session 回滚）
        在调用前已主动 invalidate，此处双保险幂等无害。

        线程安全：由现有 _lock 保护。
        """
        with self._lock:
            self._cache.invalidate()
            self._hint_chars = 0
            self.refresh_usage()
            self._notify_changed({"type": "remove", "indices": indices})

    def shutdown(self) -> None:
        """释放全局引用（会话结束时调用）。

        将本实例从模块级 ``_active_context_manager`` 全局中注销——避免实例
        （连同 messages 大列表）被全局引用长驻内存。会话结束后显式调用；
        未调用时由后续实例注册覆盖（单会话场景无泄漏）。
        """
        global _active_context_manager
        if _active_context_manager is self:
            _active_context_manager = None
