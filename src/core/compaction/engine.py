"""压缩引擎 — 对齐 dsh ``compaction-basic`` 的三种入口。

- ``compact_if_needed(trigger, force)``：自动压力压缩 / 上下文溢出恢复；
- ``compact_now()``：显式空闲会话压缩（``/compact``）；
- ``compact_region(start, end)``：把一段连续历史替换为一条检查点。

引擎持有所在 ``ContextManager`` 的引用，统一经它读取消息/缓存/工具口径并
落地替换（保持沙盒索引与使用率快照一致），因此主 Agent 与全部 SubAgent
共用同一套实现。
"""

from __future__ import annotations

import logging
import time

from ..image_tokens import estimate_messages_image_tokens
from ..internal.shared._message_text import message_to_text
from ..tokens import estimate_tokens
from .checkpoint import build_checkpoint_message, is_checkpoint_message
from .config import (
    CompactionConfig,
    ResolvedCompactSpec,
    TargetPressureConfigError,
    resolve_compact_spec,
    resolve_config,
    resolve_target_policy,
)
from .events import publish_compaction_event
from .pruner import PruneConfig, prune_messages
from .region import select_compactable_range
from .summarizer import SummaryError, summarize_region
from .types import (
    CompactionResult,
    CompactionTrigger,
    ManualCompactionError,
)

_logger = logging.getLogger(__name__)


class CompactionEngine:
    """上下文压缩引擎（与 dsh ``BasicCompactionEngine`` 语义对齐）。"""

    def __init__(
        self,
        context_manager,
        summarize_fn,
        config_port,
        event_port=None,
        output_port=None,
        label: str = "main",
    ) -> None:
        self._cm = context_manager
        self._summarize_fn = summarize_fn
        self._config_port = config_port
        self._event_port = event_port
        self._output_port = output_port
        self.label = label or "main"

    # ── 配置 ────────────────────────────────────────────

    def load_config(self) -> CompactionConfig:
        """从 ConfigPort 读取并解析压缩配置（配置非法时告警并回退默认值）。"""
        raw = None
        try:
            getter = getattr(self._config_port, "get_compaction_config", None)
            if callable(getter):
                raw = getter()
        except Exception:
            _logger.debug("读取压缩配置失败，使用默认值", exc_info=True)
        raw = raw or {}
        try:
            return resolve_config(raw)
        except (ValueError, TypeError) as exc:
            _logger.warning("压缩配置无效（%s），回退默认配置", exc)
            return resolve_config({})

    def is_enabled(self) -> bool:
        return bool(self.load_config().enabled)

    def is_auto(self) -> bool:
        return bool(self.load_config().auto)

    # ── 测量 ────────────────────────────────────────────

    def _measure(self) -> tuple[int, int]:
        """返回 (总字符, 总 token)（token 含图片与工具列表）。"""
        self._cm.ensure_cache()
        return self._cm.measure_context()

    def _tokens_of(self, index: int) -> int:
        """单条消息的估算 token（文本 + 图片）。"""
        _chars, tokens = self._cm.message_token(index)
        try:
            message = self._cm.messages[index]
            tokens += estimate_messages_image_tokens([message])
        except Exception:
            pass
        return max(0, int(tokens))

    def _max_context_chars(self) -> int:
        """字符口径上限（``max_context_chars``）；0/负值表示不设字符阈值。"""
        try:
            return int(self._config_port.get_max_context_chars() or 0)
        except Exception:
            _logger.debug("读取 max_context_chars 失败，按不设字符阈值处理", exc_info=True)
            return 0

    def _pressure_exceeded(self, spec: ResolvedCompactSpec, max_context_chars: int) -> bool:
        """常规压力判定：token 达阈值，或字符达 ``max_context_chars`` 上限。

        token 口径（``threshold_tokens``）是主判据；字符口径让纯 ASCII 会话
        在 token 估算偏低时也能及时触发压缩，避免字符已贴近窗口而上限未达。
        """
        chars, tokens = self._measure()
        if tokens >= spec.threshold_tokens:
            return True
        return max_context_chars > 0 and chars > max_context_chars

    def _current_target(self) -> tuple[str, str]:
        provider = ""
        try:
            provider = str(self._config_port.get("provider", "") or "")
        except Exception:
            provider = ""
        return provider, str(self._cm.model or "")

    def _resolve_spec(self, config: CompactionConfig, *, overflow: bool = False) -> ResolvedCompactSpec:
        """按当前目标与模型容量解析阈值/保留预算。"""
        provider, model = self._current_target()
        policy = resolve_target_policy(config, provider, model)
        context_window = int(self._config_port.get_model_context_tokens() or 0)
        reserved = int(config.reserved_tokens or 0)
        spec = resolve_compact_spec(policy, context_window, reserved)
        if overflow:
            return ResolvedCompactSpec(
                context_window=spec.context_window,
                threshold_tokens=spec.threshold_tokens,
                retain_tokens=0,
                max_tokens=spec.max_tokens,
                compaction_retries=0,
                max_overflow_retries=spec.max_overflow_retries,
            )
        return spec

    # ── 显示 ────────────────────────────────────────────

    def _emit(self, phase: str, **kwargs) -> None:
        publish_compaction_event(
            self._event_port, self.label, phase, source=self.label, **kwargs,
        )

    def _notify(self, text: str) -> None:
        port = self._output_port
        if port is None:
            return
        try:
            port.write(text, level="raw", source="context")
        except Exception:
            _logger.debug("压缩进度输出失败", exc_info=True)

    # ── 入口：自动 ──────────────────────────────────────

    def compact_if_needed(
        self,
        trigger: CompactionTrigger | str = CompactionTrigger.PRESSURE,
        force: bool = False,
    ) -> CompactionResult | None:
        """按触发来源执行自动压缩；无需压缩时返回 None。

        Args:
            trigger: ``pressure``（常规压力）或 ``context-overflow``（溢出恢复）。
            force: 强制压缩（跳过阈值判定，尽量多压缩）。

        Returns:
            CompactionResult（含仅剪枝的中性结果）或 None。

        Raises:
            TargetPressureConfigError: 目标压力配置无效（调用方负责告警/跳过）。
        """
        config = self.load_config()
        if not config.enabled:
            return None
        trigger_value = trigger.value if isinstance(trigger, CompactionTrigger) else str(trigger)
        overflow = trigger_value == CompactionTrigger.CONTEXT_OVERFLOW.value

        pruned_count = 0
        pruned_chars = 0
        try:
            spec = self._resolve_spec(config, overflow=overflow)
        except TargetPressureConfigError:
            if overflow:
                # 溢出恢复绕过常规阈值：容量缺失时退化为「不设阈值」的尽力压缩。
                spec = ResolvedCompactSpec(
                    context_window=0, threshold_tokens=0, retain_tokens=0,
                    max_tokens=config.max_tokens, compaction_retries=0,
                    max_overflow_retries=config.max_overflow_retries,
                )
            else:
                raise

        max_context_chars = self._max_context_chars()
        if not force and not overflow and not self._pressure_exceeded(spec, max_context_chars):
            return None

        # 剪枝先于摘要：无模型的确定性缩减可能完全省去一次模型调用。
        if config.prune_enabled:
            pruned_count, pruned_chars = self.prune(config)
            if pruned_count:
                if not force and not overflow and not self._pressure_exceeded(spec, max_context_chars):
                    self._notify(
                        f"+ 工具结果剪枝 {pruned_count} 条，释放 {pruned_chars} 字符"
                    )
                    return CompactionResult(
                        success=True,
                        trigger=trigger_value,
                        pruned_count=pruned_count,
                        pruned_chars=pruned_chars,
                        stats={"mode": "prune_only"},
                    )

        attempts = 1 if (overflow or force) else (spec.compaction_retries + 1)
        # 溢出恢复与强制压缩（手动 /compact、compact_context(force=True)）
        # 不保留近期尾部（dsh compactNow / context-overflow 语义：尽量多压缩）。
        retain_tokens = 0 if (overflow or force) else spec.retain_tokens
        result: CompactionResult | None = None
        for _ in range(max(1, attempts)):
            selected = select_compactable_range(
                self._cm.messages,
                retain_tokens,
                self._tokens_of,
            )
            if selected is None:
                # 无可安全压缩范围（全为系统提词 / 首条即 pinned / 工具组
                # 无法平衡切割）：本次不压缩——记录以便诊断「达阈值未压缩」。
                _logger.debug(
                    "压缩跳过（%s, force=%s）：无可安全压缩范围", trigger_value, force,
                )
                return result
            result = self.compact_region(
                selected[0], selected[1], trigger_value,
                pruned_count=pruned_count, pruned_chars=pruned_chars,
            )
            if not result.success:
                return result
            if overflow or force:
                return result
            if not self._pressure_exceeded(spec, max_context_chars):
                return result
        return result

    # ── 入口：手动 ──────────────────────────────────────

    def compact_now(self) -> CompactionResult | None:
        """显式压缩一次（``/compact``）；无可安全压缩范围时返回 None。

        Raises:
            ManualCompactionError: 摘要失败或摘要未缩小内容。
        """
        config = self.load_config()
        if not config.enabled:
            raise ManualCompactionError("busy", "压缩功能已在配置中禁用")
        selected = select_compactable_range(self._cm.messages, 0, self._tokens_of)
        if selected is None:
            return None
        pruned_count = 0
        pruned_chars = 0
        if config.prune_enabled:
            pruned_count, pruned_chars = self.prune(config)
        result = self.compact_region(
            selected[0], selected[1], "manual",
            pruned_count=pruned_count, pruned_chars=pruned_chars,
        )
        if not result.success:
            raise ManualCompactionError(
                "summary", result.stats.get("error") or "摘要压缩失败",
                cause=result.stats.get("exception"),
            )
        return result

    # ── 剪枝 ────────────────────────────────────────────

    def prune(self, config: CompactionConfig | None = None) -> tuple[int, int]:
        """对当前消息列表中超预算的工具结果做确定性剪枝。

        Returns:
            ``(剪枝条数, 移除字符数)``。
        """
        if config is None:
            config = self.load_config()
        prune_config = PruneConfig(
            threshold_chars=config.prune_threshold_chars,
            head_chars=config.prune_head_chars,
            tail_chars=config.prune_tail_chars,
        )
        outcome = prune_messages(self._cm.messages, prune_config)
        if outcome.pruned:
            self._cm.invalidate_cache()
        return len(outcome.pruned), outcome.chars_removed

    # ── 范围压缩 ────────────────────────────────────────

    def compact_region(
        self,
        start: int,
        end: int,
        trigger: str = CompactionTrigger.PRESSURE.value,
        *,
        pruned_count: int = 0,
        pruned_chars: int = 0,
    ) -> CompactionResult:
        """把 ``[start, end]`` 的连续历史替换为一条检查点消息。

        摘要必须比被遮蔽内容更小；失败不落地任何替换，并通过显示事件上报
        失败原因。
        """
        messages = self._cm.messages
        total = len(messages)
        if start < 0 or end < start or end >= total:
            return CompactionResult(
                success=False, trigger=trigger,
                stats={"error": f"无效压缩范围 [{start}, {end}]"},
            )

        region = list(messages[start:end + 1])
        region_tokens = sum(self._tokens_of(i) for i in range(start, end + 1))
        # 回放前缀：顶部系统提词（可能多个 parts）。
        head_system = [
            m for m in messages[:start]
            if isinstance(m, dict) and m.get("role") == "system"
        ]
        has_prior = any(is_checkpoint_message(m) for m in region)

        self._emit("started", count=len(region), tokens=region_tokens,
                   detail=f"{start}-{end}")
        started = time.time()
        try:
            summary_result = summarize_region(
                head_system, region, self._summarize_fn, self._cm.model,
                max_tokens=0, has_prior=has_prior,
            )
            summary = summary_result.summary
            checkpoint = build_checkpoint_message(summary)
            checkpoint_tokens = self._tokens_of_message(checkpoint)
            if checkpoint_tokens >= region_tokens:
                raise SummaryError(
                    f"摘要未比被遮蔽内容更小（{checkpoint_tokens} >= {region_tokens}）"
                )
            removed = list(range(start, end + 1))
            self._cm.apply_replacement(start, end, checkpoint)
            elapsed = time.time() - started
            result = CompactionResult(
                success=True,
                trigger=trigger,
                removed_indices=removed,
                inserted_index=start,
                summary=summary,
                shadowed_count=len(region),
                shadowed_tokens=region_tokens,
                summary_tokens=checkpoint_tokens,
                saved_tokens=max(0, region_tokens - checkpoint_tokens),
                chars_saved=max(0, self._estimate_chars(region) - self._estimate_chars([checkpoint])),
                pruned_count=pruned_count,
                pruned_chars=pruned_chars,
                elapsed=elapsed,
                usage=summary_result.usage,
            )
            self._emit(
                "finished", count=result.shadowed_count, tokens=result.saved_tokens,
                detail=f"节省 ~{result.saved_tokens}t",
            )
            self._notify(
                f"+ 压缩 {result.shadowed_count} 条消息，节省 ~{result.saved_tokens}t"
            )
            _logger.info(
                "压缩成功 (%s): 遮蔽 %d 条 (~%dt)，检查点 ~%dt",
                trigger, result.shadowed_count, result.shadowed_tokens, checkpoint_tokens,
            )
            return result
        except Exception as exc:
            error_text = str(exc)
            self._emit("failed", count=len(region), detail=error_text)
            _logger.warning("压缩失败 (%s): %s", trigger, error_text)
            return CompactionResult(
                success=False,
                trigger=trigger,
                shadowed_count=len(region),
                shadowed_tokens=region_tokens,
                pruned_count=pruned_count,
                pruned_chars=pruned_chars,
                elapsed=time.time() - started,
                stats={"error": error_text, "exception": exc},
            )

    # ── 内部辅助 ────────────────────────────────────────

    @staticmethod
    def _estimate_chars(messages: list) -> int:
        return sum(len(message_to_text(m)) for m in messages if isinstance(m, dict))

    def _tokens_of_message(self, message: dict) -> int:
        tokens = estimate_tokens(message_to_text(message))
        tokens += estimate_messages_image_tokens([message])
        return max(0, int(tokens))


__all__ = ["CompactionEngine"]
