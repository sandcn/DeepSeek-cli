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
# src.api.model_async（模型调用），违反「核心层不依赖基础设施层」
# 原则。已在 src.core.adapters.model.SyncModelBridge 中修复：
# summarize_fn 的默认值改为通过 SyncModelBridge().summarize
# 桥接（内部走流式摘要调用），消除对 api 层的直接导入依赖。
# ═══════════════════════════════════════════════════════════════

import json
import logging
import threading
from typing import Optional

_logger = logging.getLogger(__name__)
from .constants import YELLOW, DIM, RESET, STREAM_LABEL_SUMMARIZE, audit_log as _log
from . import context_selector as selector
from .context_selector import MessageStatsCache
from .tokens import estimate_tokens
from .image_tokens import estimate_messages_image_tokens
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
#   - 真实校准（2026-10 用户反馈「main 上下文百分比统计不准」）：
#     服务端 usage.prompt_tokens（真实输入 token）经 api 管线
#     notify_prompt_usage → update_real_prompt_usage → ContextManager.
#     set_prompt_baseline 写入基线；refresh_usage 在基线有效时以真实值为底
#     叠加新增消息估算（基线失效自动回退纯估算），并使用官方 token 系数
#     （见 core.tokens）修正估算偏差。
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
#     基础上叠加流式增量（当前流式输出的 **content + 工具调用参数** 的整体
#     估算——与消息追加后 MessageStatsCache 同口径，reasoning 不随请求回传
#     不计入），占模型上下文窗口比例；
#   - 清零：流式结束（_cleanup_display，幂等）调用 update_streaming_usage(0)
#     清零——随后 assistant 消息追加由 refresh_usage() 按消息全文重算真实值，
#     避免「流式增量 + 消息内容」双计；
#   - SubAgent（label "agent-N" / 后台 "sa-xxx"）跳过：其输出计入 SubAgent
#     独立上下文，不占主 Agent 上下文；主 Agent 流式 label 为 "assistant"
#     （pipeline.py）。★ 2026-08-20 修复：后台 subagent（subagent 工具直接
#     后台派发）label 为 task_id（"sa-xxx"）而非 "agent-" 前缀——修复前其
#     流式增量写入全局并触发主 Agent refresh_usage()，主 Agent 上下文百分比
#     被 subagent 动态信息污染（虚高/抖动/干扰主 Agent 流式增量）。
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


#: 内部（非主 Agent / 非 SubAgent 对话）流式调用标签集合：其输出不进入主对话
#: 上下文，不得计入 ``main · N%`` 上下文使用率的实时增量。
#: 单一真源见 ``core.constants.STREAM_LABEL_SUMMARIZE``（上下文压缩摘要）。
_INTERNAL_STREAM_LABELS = frozenset({STREAM_LABEL_SUMMARIZE})


def _is_subagent_stream_label(label: Optional[str]) -> bool:
    """判断流式调用 label 是否属于 SubAgent（其输出不占主 Agent 上下文）。

    两种 SubAgent label 约定（与 TUI 面板/轨迹/后台任务表一致）：
      - 前台 subagent（ParallelExecutor 直接 spawner 调用）：``agent-N``
        （序号生成，见 _subagent_spawner._spawn_subagent）；
      - 后台 subagent（subagent 工具直接后台派发）：``sa-xxx``（task_id，
        见 tools/subagent.py._execute_background——spec["label"]=task_id）。
    主 Agent 流式 label 为 "assistant"（pipeline.py）；label 为 None
    （非 TUI/缺省路径）计入主 Agent。SubAgent 输出占用其独立上下文，
    不应写入全局流式增量、也不应触发主 Agent 百分比重算。

    Args:
        label: 流式调用标签。

    Returns:
        True 表示该流式属于 SubAgent（应跳过主 Agent 上下文统计）。
    """
    return bool(label and (label.startswith("agent-") or label.startswith("sa-")))


def _is_internal_stream_label(label: Optional[str]) -> bool:
    """判断流式调用 label 是否属于**内部**调用（不占主 Agent 上下文）。

    内部调用示例：上下文压缩摘要（``STREAM_LABEL_SUMMARIZE``，api 经
    ``call_model_summarize_async`` 以流式发起）——其输出写回检查点而非主消息
    列表，输入也不是主对话，因此既不能写入全局流式增量、也不能触发主 Agent
    百分比重算（否则压缩期间 ``main · N%`` 会被摘要内容虚高）。

    Args:
        label: 流式调用标签。

    Returns:
        True 表示该流式属于内部调用（应跳过主 Agent 上下文统计）。
    """
    return bool(label and label in _INTERNAL_STREAM_LABELS)


def update_real_prompt_usage(prompt_tokens: int, label: Optional[str] = None) -> None:
    """真实 usage 到达时以 ``prompt_tokens`` 校准上下文使用率（api 管线调用）。

    ★ 2026-10（用户反馈「main 上下文百分比统计不准」）：纯启发式估算与
    服务端真实 token 存在偏差（DeepSeek 官方中文字符 ≈ 0.6 token/字符，
    估算系数修正后仍无法覆盖模板/工具 JSON 结构等开销）。真实
    ``prompt_tokens`` 是权威值，作为**基线**写入活跃 ContextManager——
    后续新增消息只叠加估算增量，百分比显著贴近真实。

    ★ 仅**主 Agent 对话轮次**计入：label 必须为 ``None``（非 TUI/缺省路径）
    或 ``"assistant"``（pipeline.py 约定）。SubAgent（"agent-N"/"sa-xxx"，
    其输入占用独立上下文）与内部工具调用（压缩摘要 ``"summarize"`` 等，
    其输入不是主对话）一律跳过，避免污染 ``main · N%``。

    Args:
        prompt_tokens: 服务端返回的真实输入 token（含系统提词 + 工具列表 +
            本次请求全部消息 + 模板开销）。
        label: 调用标签；仅 None/"assistant" 计入。
    """
    if label is not None and label != "assistant":
        return
    cm = _active_context_manager
    if cm is None:
        return
    try:
        cm.set_prompt_baseline(prompt_tokens)
    except Exception:
        _logger.debug("写入真实 prompt token 基线失败", exc_info=True)


def set_active_context_manager(cm: Optional["ContextManager"]) -> None:
    """注册当前活跃 ContextManager 实例（ContextManager.__init__ 调用）。

    流式管线（api 层）无实例引用，经 ``update_streaming_usage`` 访问此
    全局以触发实例级 refresh_usage() 重算全局百分比。
    """
    global _active_context_manager
    _active_context_manager = cm


def update_streaming_usage(delta_tokens: int, label: Optional[str] = None) -> None:
    """流式输出过程中实时刷新上下文使用率（api 流式管线调用入口）。

    仅主 Agent 流式计入（SubAgent 与内部调用跳过）：
      - SubAgent：label "agent-N" 前台 / "sa-xxx" 后台，其输出占用 SubAgent
        独立上下文，不影响主 Agent 百分比（★ 2026-08-20 修复：后台 subagent
        label 为 task_id "sa-xxx" 而非 "agent-" 前缀，修复前其流式增量被计入
        主 Agent 百分比）；
      - 内部调用：上下文压缩摘要（``STREAM_LABEL_SUMMARIZE``）等——输出写回
        检查点而非主消息列表，计入会把 ``main · N%`` 在压缩期间虚高。
    写入全局流式增量后触发活跃 ContextManager.refresh_usage()（缓存有效时
    O(1)，性能好）。

    Args:
        delta_tokens: 当前流式输出的**上下文增量**（ctx.streamed_output_tokens，
            content + 工具调用参数的整体估算；与消息追加后 MessageStatsCache
            同口径）。
        label: 流式调用标签；None/主 Agent（"assistant"）计入，SubAgent
            （"agent-N"/"sa-xxx"）与内部调用（"summarize"）跳过。
    """
    if _is_subagent_stream_label(label) or _is_internal_stream_label(label):
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
    调用 SandboxManager.shift_indices()/remap_indices()/fold_indices()
    （获取 SandboxManager.lock）。
    任何新的代码路径不得以相反顺序获取这两个锁。

    Args:
        messages: 消息列表引用（就地修改）
        model: 模型名称
        summarize_fn: 摘要生成函数，默认经 SyncModelBridge 走**流式**摘要调用
            （silent + 内部 label，见 core/adapters/model.py）
        on_messages_changed: 消息变更回调，接收事件字典：
            {"type": "insert", "index": int}
            {"type": "remove", "indices": list[int]}
            {"type": "fold", "indices": list[int], "insert_index": int | None}
            —— 消息被「折叠」（上下文压缩：折叠为一条摘要，``insert_index``
            为摘要位置；降级删除时为 None）而非删除失效：文件变更仍有效，
            沙盒须保留记录并把被折叠区间重挂到锚点（见 SandboxManager.
            fold_indices），不得按 remove 丢弃。
        strategies: 压缩策略列表（按优先级排序），
                    默认 [SummarizeStrategy, DropStrategy]
        config_port: 配置端口（max_context_chars 等读取）
        output_port: 输出端口
        tools: 当前工具 schemas（list[dict]）——上下文使用率统计的一部分
            （工具列表随系统提词一起发送给模型，须计入上下文占用）。
        pending_jobs_fn: 可选回调，返回当前未结束的后台任务清单
            （``[{"task_id", "kind", "detail", "status"}, ...]``）——压缩时
            注入摘要提词，确保未结束的后台 bash（``bg-xxx``）/ subagent
            （``sa-xxx``）task_id 逐字保留在检查点里（装配方通常指向 Agent
            的 ``_running_background_jobs``）；未提供时提词仅保留静态规则。
    """

    def __init__(self, messages, model, summarize_fn=None,
                 on_messages_changed=None,
                 strategies: Optional[list[CompressionStrategy]] = None,
                 config_port: Optional[ConfigPort] = None,
                 output_port: Optional[OutputPort] = None,
                 tools: Optional[list] = None,
                 event_port=None,
                 label: str = "main",
                 activate_global: bool = True,
                 pending_jobs_fn=None):
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
        # ★ 压缩显示事件端口（TUI 模式行/通知显示压缩状态）与 Agent label。
        self._event_port = event_port
        self.label = label or "main"
        # ★ 未结束后台任务清单回调（压缩提词注入 bg-xxx / sa-xxx，见类 docstring）。
        self._pending_jobs_fn = pending_jobs_fn
        # ★ 是否参与全局上下文使用率快照（主 Agent True；SubAgent False——
        #   子代理拥有独立上下文，写入全局会覆盖主 Agent 的百分比与流式
        #   增量目标实例）。
        self._publish_usage = bool(activate_global)

        # 增量统计缓存（惰性同步）
        self._cache = MessageStatsCache()

        # dsh 同款压缩引擎（惰性创建；配置禁用/不可解析时回退旧策略链）
        self._engine = None

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

        # 图片（视觉）token 估算缓存——流式输出期间每 ~0.1s 实时刷新上下文
        # 使用率（update_streaming_usage → refresh_usage）都会调用图片估算；
        # 指纹（len(messages) + 图片块 id 元组）不变时复用，避免重复解码
        # base64 / 读图像尺寸。
        self._image_tokens_cache: Optional[int] = None
        self._image_tokens_fp: tuple = ()

        # 真实 prompt token 基线（2026-10「main 上下文百分比统计不准」修复）：
        # (prompt_tokens, base_len, 前 base_len 条消息 id 元组)。由
        # update_real_prompt_usage → set_prompt_baseline 在收到服务端真实
        # usage 时写入：prompt_tokens 已含系统提词 + 工具列表 + 当时全部
        # 消息，故基线生效时不再重复叠加 tools/消息估算，只叠加此后新增的
        # 消息估算与流式增量。消息被移除/替换/工具或模型变化时自动失效，
        # 回退纯估算口径（见 _baseline_tail_locked）。
        self._prompt_baseline: Optional[tuple] = None

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
        # SubAgent（activate_global=False）不注册——避免覆盖主 Agent 的
        # 全局百分比快照与流式增量目标。
        if self._publish_usage:
            set_active_context_manager(self)
        self.refresh_usage()

    def update_model(self, model):
        """更新模型名称（模型变化 → 真实 prompt 基线失效，回退估算口径）。"""
        self.model = model
        self._prompt_baseline = None
        self.refresh_usage()

    def set_tools(self, tools: Optional[list]) -> None:
        """更新工具 schemas 并刷新上下文使用率（工具列表变化后调用）。"""
        self.tools = list(tools or [])
        self._tools_tokens_cache = None  # 工具列表变化 → 估算缓存失效
        self._tools_cache_fp = ()
        # 工具列表变化 → 旧真实 prompt 基线（含旧工具 schemas）失效
        self._prompt_baseline = None
        self.refresh_usage()

    def set_prompt_baseline(self, prompt_tokens: int) -> None:
        """写入真实 prompt token 基线（服务端 usage.prompt_tokens）。

        语义：``prompt_tokens`` 对应「此刻 messages 列表的全部内容 + 系统提词
        + 工具列表」的真实输入占用。基线生效期间百分比 = prompt_tokens +
        此后新增消息估算 + 流式增量，显著优于纯估算。

        线程安全：写入与快照（消息 id 元组）在锁内完成，避免与并发追加
        消息竞争产生错位基线；随后触发 refresh_usage 刷新全局快照。

        Args:
            prompt_tokens: 服务端真实输入 token（<=0 / 非法值忽略，保留旧基线）。
        """
        try:
            tokens = int(prompt_tokens or 0)
        except (TypeError, ValueError, OverflowError):
            return
        if tokens <= 0:
            return
        with self._lock:
            msgs = self.messages
            self._prompt_baseline = (tokens, len(msgs), tuple(id(m) for m in msgs))
        self.refresh_usage()

    def _baseline_tail_locked(self):
        """校验并返回真实基线 (base_tokens, tail_messages)；失效返回 None。

        失效条件（任一命中即清除基线并回退估算口径）：
          - 消息数少于基线长度（删除了基线期间的消息）；
          - 基线前缀消息对象身份变化（移除/替换/系统提词重建/压缩）。

        调用方须持有 ``self._lock``。
        """
        base = self._prompt_baseline
        if base is None:
            return None
        base_tokens, base_len, base_ids = base
        msgs = self.messages
        if base_len > len(msgs):
            self._prompt_baseline = None
            return None
        if base_len:
            for idx in range(base_len):
                if id(msgs[idx]) != base_ids[idx]:
                    self._prompt_baseline = None
                    return None
        return base_tokens, msgs[base_len:]

    @staticmethod
    def _estimate_messages_tokens(messages) -> int:
        """估算一批消息的上下文 token（文本 + 图片视觉），或 0。"""
        if not messages:
            return 0
        from .internal.shared._message_text import message_to_text
        total = 0
        for msg in messages:
            try:
                total += estimate_tokens(message_to_text(msg))
            except Exception:
                continue
        total += estimate_messages_image_tokens(messages)
        return total

    def _current_tokens_locked(self) -> int:
        """当前上下文 tokens（单一真源：真实基线优先 → 回退全量估算）。

        与上下文使用率显示（``refresh_usage`` / TUI 模式行 ``main · N%``）
        同源，保证「显示达到阈值」与「自动压缩判定触发」永不脱节：

          - 真实基线有效（``_baseline_tail_locked()``）：以服务端权威
            ``prompt_tokens`` 为底，叠加基线之后新增消息的估算（基线已含
            系统提词 + 工具列表 + 图片视觉，不重复叠加）；
          - 无基线 / 基线失效：全量估算 = 全部消息 + 工具列表 + 图片视觉。

        ★ 调用方须持有 ``self._lock``（``_baseline_tail_locked`` 与
        ``_cache`` 均在锁内访问）。不含流式瞬态增量
        （``_streaming_extra_tokens``）——该增量尚未落地为消息，由显示侧
        单独叠加，压缩判定只看已落地的上下文占用。
        """
        baseline = self._baseline_tail_locked()
        if baseline is not None:
            base_tokens, tail = baseline
            return base_tokens + self._estimate_messages_tokens(tail)
        return (self._cache.total_tokens + self._messages_image_tokens()
                + self._tools_tokens())

    # ── 缓存管理 ──────────────────────────────────────────

    def _ensure_cache(self):
        """确保缓存已与 messages 列表同步（惰性初始化 + 自动同步）。"""
        if not self._cache.is_synced(self.messages):
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

        优先走 dsh 同款压缩引擎（阈值 = ``min(W × thresholdRatio, W − O − B)``，
        保留尾部 = ``(W − O) × retainRatio``，结构化检查点摘要 + 工具结果剪枝）；
        引擎不可用（未配置 / 配置禁用 / 解析失败）时回退内置策略链。

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

            # ── dsh 同款压缩引擎优先 ──────────────────────────
            engine = self._get_engine()
            if engine is not None and engine.is_enabled():
                try:
                    from .compaction import CompactionTrigger

                    # ★ 自动全量压缩阈值（auto_force_compress_threshold，token
                    #   口径）在引擎路径同样生效：命中即以 force=True 调引擎
                    #   （不保留近期尾部＝全量压缩）。否则引擎只按 compaction
                    #   比例算出的压力阈值触发，该配置会被绕过（引擎启用时
                    #   回退策略链不执行）。
                    engine_force = force or self._auto_force_triggered()
                    engine.compact_if_needed(
                        trigger=CompactionTrigger.PRESSURE, force=engine_force,
                    )
                    return
                except Exception:
                    _logger.debug("压缩引擎执行失败，回退策略链", exc_info=True)

            # ── 回退：内置策略链 ──────────────────────────────
            total_chars_val = self._cache.total_chars
            # 图片视觉 token 计入压缩判断的 token 口径（字符口径不含图片）。
            total_tokens_val = self._cache.total_tokens + self._messages_image_tokens()

            force, should = self._should_compress(force, total_chars_val, total_tokens_val)
            if not should:
                return

            self._do_compress(force)

    # ── dsh 同款压缩引擎：构造与上下文测量/落地 ──────────────

    def _get_engine(self):
        """惰性构造压缩引擎（失败返回 None → 回退策略链）。"""
        if self._engine is not None:
            return self._engine
        try:
            from .compaction import CompactionEngine

            self._engine = CompactionEngine(
                self,
                self._summarize_fn,
                self._config_port,
                event_port=self._event_port,
                output_port=self._output_port,
                label=getattr(self, "label", "main"),
                pending_jobs_fn=getattr(self, "_pending_jobs_fn", None),
            )
        except Exception:
            _logger.debug("构造压缩引擎失败", exc_info=True)
            return None
        return self._engine

    def ensure_cache(self) -> None:
        """公开的缓存同步入口（供压缩引擎读取 token 口径）。"""
        with self._lock:
            self._ensure_cache()

    def measure_context(self) -> tuple[int, int]:
        """返回当前上下文的 (总字符, 总 token)。

        token 口径与上下文使用率显示（TUI 模式行 ``main · N%``）**完全一致**
        ——真实基线优先（服务端 ``prompt_tokens`` + 新增消息估算），无基线时
        回退全量估算（全部消息 + 工具列表 + 图片视觉）。该口径同时驱动
        自动全量压缩（``auto_force_compress_threshold``）与引擎压力判定，
        确保「用户看到已达阈值 → 实际必定触发压缩」，不再出现显示口径
        （真实）与判定口径（纯估算）脱节导致的「达阈值不压缩」。
        """
        with self._lock:
            self._ensure_cache()
            chars = self._cache.total_chars
            tokens = self._current_tokens_locked()
            return chars, tokens

    def message_token(self, index: int) -> tuple[int, int]:
        """返回单条消息的 (字符, 文本 token)（不含图片视觉 token）。"""
        with self._lock:
            self._ensure_cache()
            return self._cache.get_per_msg(index)

    def apply_replacement(self, start: int, end: int, message: dict) -> None:
        """把 ``[start, end]`` 的连续消息替换为单条消息（压缩落地）。

        同步更新增量缓存、通知沙盒索引折叠并刷新上下文使用率快照。

        ★ 沙盒一致性：压缩只折叠上下文（文件变更仍在磁盘生效），因此通知
        沙盒用 ``fold``（保留记录、被折叠区间重挂到摘要位置）而非
        ``remove``（删除即失效）——否则 ``/changes`` 与回滚丢失被压缩期间的
        文件历史，压缩后无法正常还原。
        """
        with self._lock:
            messages = self.messages
            removed = list(range(start, end + 1))
            for idx in sorted(removed, reverse=True):
                if 0 <= idx < len(messages):
                    messages.pop(idx)
            insert_at = min(max(0, start), len(messages))
            messages.insert(insert_at, message)
            if self._cache.is_valid:
                self._cache.on_remove(removed)
                self._cache.on_insert(insert_at, message)
                self._hint_chars = self._cache.total_chars
            else:
                self._hint_chars = 0
            self._notify_changed({
                "type": "fold", "indices": removed, "insert_index": insert_at,
            })
            self.refresh_usage()

    def compact_now(self):
        """显式压缩当前上下文一次（``/compact``）。

        Returns:
            CompactionResult，无可安全压缩范围时返回 None。

        Raises:
            CompactionError: 引擎未启用或摘要失败（见 ``.compaction``）。
        """
        from .compaction import ManualCompactionError

        engine = self._get_engine()
        if engine is None or not engine.is_enabled():
            raise ManualCompactionError("busy", "压缩功能不可用")
        with self._lock:
            if not self._has_compressible_messages(self.messages):
                return None
            self._ensure_cache()
            return engine.compact_now()

    def compact_for_overflow(self):
        """上下文溢出恢复：强制压缩并返回结果（供模型调用重试路径使用）。

        Returns:
            CompactionResult 或 None（未启用 / 无可压缩范围）。
        """
        engine = self._get_engine()
        if engine is None or not engine.is_enabled() or not engine.is_auto():
            return None
        from .compaction import CompactionTrigger

        with self._lock:
            if not self._has_compressible_messages(self.messages):
                return None
            try:
                return engine.compact_if_needed(
                    trigger=CompactionTrigger.CONTEXT_OVERFLOW, force=True,
                )
            except Exception:
                _logger.debug("溢出恢复压缩失败", exc_info=True)
                return None



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

    def _auto_force_triggered(self) -> bool:
        """自动全量压缩阈值判定（``auto_force_compress_threshold``，token 口径）。

        ``auto_force_compress_threshold`` 为「当前上下文 tokens 超过即强制
        全量压缩」的阈值（单位 token，默认 600k）。

        ★ 口径与 TUI 模式行 ``main · N%`` 显示**同源**（``measure_context``
        → ``_current_tokens_locked``：真实基线优先 → 全量估算），因此
        「用户看到已达阈值」与「判定触发压缩」不会脱节——修复前判定用纯
        估算（系统性低于服务端真实 prompt_tokens），出现过「显示已达阈值
        却未自动全量压缩」。

        引擎路径的常规压力阈值由 ``compaction`` 比例算出，本判定保证该配置
        在引擎启用时同样生效（命中 → 调用方以 ``force=True`` 调引擎）。
        """
        total_chars_val, total_tokens_val = self.measure_context()
        return selector.should_auto_force_values(
            total_chars_val, total_tokens_val,
            auto_force_threshold=self._config_port.get_auto_force_compress_threshold(),
            max_context_tokens=self._config_port.get_max_context_tokens(),
        )

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

            # ★ 沙盒一致性：会话消息数上限只丢上下文，磁盘文件变更仍有效——
            #   用 ``fold``（保留被删区间的记录并重挂到删除锚点）而非
            #   ``remove``（删除即失效），避免 /changes 与回滚丢失历史。
            self._notify_changed({
                "type": "fold", "indices": to_delete, "insert_index": None,
            })

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

    @staticmethod
    def _image_fp(messages) -> tuple:
        """图片块指纹：(消息条数, 图片块 id 元组)——不变则估算缓存可复用。"""
        ids = []
        for msg in messages:
            content = msg.get("content") if isinstance(msg, dict) else None
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") in ("image_url", "image"):
                        ids.append(id(block))
        return (len(messages), tuple(ids))

    def _messages_image_tokens(self) -> int:
        """messages 中图片的视觉 token 估算（含上传瘦身规则），带指纹缓存。

        图片在文本口径里只占 ``[图片]`` 占位，视觉 token 须单独估算并计入
        上下文占用与压缩判断（详见 ``core.image_tokens``）。指纹不变时复用
        缓存——流式每 0.1s 刷新路径 O(1)。
        """
        messages = self.messages
        fp = self._image_fp(messages)
        if self._image_tokens_cache is not None and self._image_tokens_fp == fp:
            return self._image_tokens_cache
        total = estimate_messages_image_tokens(messages)
        self._image_tokens_cache = total
        self._image_tokens_fp = fp
        return total

    def refresh_usage(self, force: bool = False) -> None:
        """刷新全局上下文使用率（动态刷新入口，2026-08-19 用户需求）。

        统计口径（两级，优先真实基线）：**系统提词 + 工具列表 + 全部消息**
        占**模型上下文窗口**（model_context_tokens，默认 1M tokens）的百分比——
          - 真实基线（2026-10 修复「统计不准」）：若最近一次请求已回传真实
            ``prompt_tokens``（``set_prompt_baseline``），则以该权威值为底，
            只叠加此后**新增消息**的估算（``_baseline_tail_locked`` 校验
            前缀消息未变）——彻底消除估算口径与服务端真实 token 的偏差；
          - 估算回退：无基线/基线失效时按 系统提词 + 工具列表 + 全部消息
            （MessageStatsCache，含 system）+ 图片视觉 token 估算；
          - 流式增量（2026-08-19「上下文百分比要实时刷新」）：模块级全局
            _streaming_extra_tokens——AI 流式生成期间当前已输出的估算
            tokens（content + 工具调用参数），经 update_streaming_usage
            每 ~0.1s 写入并触发本方法重算，行首 ``main · N%`` 随输出
            （含工具参数）实时上升；
          - 分母：get_model_context_tokens()（模型上下文窗口，默认 1M token）。
        计算一次性写入全局快照，TUI 渲染线程每帧 O(1) 无锁读取。

        Args:
            force: 强制全量 resync（默认 False 懒同步）。系统提词**内容**变化
                但消息条数不变时（如 Ctrl+B 空模式切换 rebuild_system_prompt
                ——system 消息数相同、内容替换）懒同步会命中旧缓存 → 百分比
                不更新；此类场景须传 force=True 强制重算（低频，O(n) 可接受）。
                消息对象被替换（同长度内容变更）由 ``MessageStatsCache.
                is_synced`` 的对象身份校验自动发现，无需调用方传 force。

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

        注：SubAgent（``activate_global=False``）不参与全局快照，本方法直接
        返回（子代理上下文独立，不覆盖主 Agent 的显示）。
        """
        if not getattr(self, "_publish_usage", True):
            return
        try:
            ctx_tokens = self._config_port.get_model_context_tokens()
            if ctx_tokens <= 0:
                set_context_usage_percent(None)
                return
            with self._lock:
                # 懒同步缓存（长度 + 对象身份双重校验；复用避免每帧重算）；
                # force=True（Ctrl+B 空模式切换等 system 内容变化场景）强制重算。
                if force or not self._cache.is_synced(self.messages):
                    self._cache.resync(self.messages)
                self._hint_chars = self._cache.total_chars
                # 单一真源（_current_tokens_locked：真实基线优先 → 全量估算），
                # 显示侧额外叠加流式瞬态增量（AI 生成中已输出但尚未落地的
                # tokens）——压缩判定（measure_context）不含该瞬态量。
                tokens = self._current_tokens_locked() + _streaming_extra_tokens
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
        self._prompt_baseline = None


# ── 用量刷新钩子注册（api 管线经钩子回调，避免 api→core 循环依赖）──
try:
    from ..api.stream._usage_hook import (
        register_usage_hook as _register_usage_hook,
        register_prompt_usage_hook as _register_prompt_usage_hook,
    )
    _register_usage_hook(update_streaming_usage)
    _register_prompt_usage_hook(update_real_prompt_usage)
except Exception:  # pragma: no cover — 导入失败时静默（实时刷新/真实校准降级）
    pass
