"""压缩配置解析 — 对齐 dsh ``compaction-basic`` 的加载期校验与路由策略解析。

设上下文窗口为 ``W``、生效请求输出预留为 ``O``、额外压力余量为 ``B``
（``headroomTokens``，默认 65,536），则：

- 消息预算 ``messageBudget = W - O``；
- 压力预算 ``pressureBudget = messageBudget - B``；
- 触发阈值 ``thresholdTokens = floor(min(W * thresholdRatio, pressureBudget))``；
- 逐字保留的近期尾部 ``retainTokens = floor(messageBudget * retainRatio)``
  （或显式 ``retainTokens``）；
- 且必须 ``retainTokens < thresholdTokens``。

``modelPolicies`` 提供逐 ``provider/model`` 的精确覆盖；校验失败在加载期
快速暴露（未知键、重复覆盖、非法 token 数、两种保留形式同时出现、保留比例
不小于阈值比例）。
"""

from __future__ import annotations

import math
from dataclasses import field
from typing import Any

from src._compat import dataclass

# ── 默认值（与 dsh compaction-basic 对齐） ──────────────────

DEFAULT_THRESHOLD_RATIO = 0.8
DEFAULT_RETAIN_RATIO = 0.16
DEFAULT_HEADROOM_TOKENS = 65_536
DEFAULT_COMPACTION_RETRIES = 1
DEFAULT_MAX_OVERFLOW_RETRIES = 1

#: 工具结果剪枝默认预算（与 dsh compaction-tool-result-pruner 对齐）
DEFAULT_PRUNE_THRESHOLD_CHARS = 8192
DEFAULT_PRUNE_HEAD_CHARS = 4096
DEFAULT_PRUNE_TAIL_CHARS = 1024

_POLICY_KEYS = frozenset({
    "threshold_ratio", "headroom_tokens", "retain_ratio", "retain_tokens",
    "summarization_provider", "summarization_model", "max_tokens",
    "compaction_retries", "max_overflow_retries",
})

_CONFIG_KEYS = _POLICY_KEYS | frozenset({
    "enabled", "auto", "model_policies", "reserved_tokens",
    "prune_enabled", "prune_threshold_chars", "prune_head_chars", "prune_tail_chars",
})

_MODEL_POLICY_KEYS = _POLICY_KEYS | frozenset({"provider", "model"})


class TargetPressureConfigError(Exception):
    """目标特定的压力配置失败（可对同一目标键做告警抑制）。"""

    def __init__(self, target_key: str, message: str) -> None:
        super().__init__(message)
        self.target_key = target_key
        self.message = message


@dataclass(slots=True)
class CompactionPolicy:
    """合并（默认 + 精确覆盖）后的目标策略。"""

    threshold_ratio: float = DEFAULT_THRESHOLD_RATIO
    headroom_tokens: int = DEFAULT_HEADROOM_TOKENS
    retain_ratio: float | None = DEFAULT_RETAIN_RATIO
    retain_tokens: int | None = None
    summarization_provider: str = ""
    summarization_model: str = ""
    max_tokens: int = DEFAULT_HEADROOM_TOKENS
    compaction_retries: int = DEFAULT_COMPACTION_RETRIES
    max_overflow_retries: int = DEFAULT_MAX_OVERFLOW_RETRIES


@dataclass(slots=True)
class ResolvedCompactSpec:
    """按模型容量折算出的具体 token 预算。"""

    context_window: int = 0
    threshold_tokens: int = 0
    retain_tokens: int = 0
    max_tokens: int = DEFAULT_HEADROOM_TOKENS
    compaction_retries: int = DEFAULT_COMPACTION_RETRIES
    max_overflow_retries: int = DEFAULT_MAX_OVERFLOW_RETRIES


@dataclass(slots=True)
class CompactionConfig:
    """解析并校验后的完整配置。"""

    enabled: bool = True
    auto: bool = True
    threshold_ratio: float = DEFAULT_THRESHOLD_RATIO
    headroom_tokens: int = DEFAULT_HEADROOM_TOKENS
    retain_ratio: float | None = DEFAULT_RETAIN_RATIO
    retain_tokens: int | None = None
    summarization_provider: str = ""
    summarization_model: str = ""
    max_tokens: int = DEFAULT_HEADROOM_TOKENS
    compaction_retries: int = DEFAULT_COMPACTION_RETRIES
    max_overflow_retries: int = DEFAULT_MAX_OVERFLOW_RETRIES
    reserved_tokens: int = 0
    prune_enabled: bool = True
    prune_threshold_chars: int = DEFAULT_PRUNE_THRESHOLD_CHARS
    prune_head_chars: int = DEFAULT_PRUNE_HEAD_CHARS
    prune_tail_chars: int = DEFAULT_PRUNE_TAIL_CHARS
    model_policies: tuple = field(default_factory=tuple)

    def __post_init__(self) -> None:
        self.model_policies = tuple(self.model_policies or ())


# ── 校验原语 ────────────────────────────────────────────────


def _assert_ratio(name: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} ({value!r}) 必须为 (0, 1] 区间的数值")
    value = float(value)
    if not math.isfinite(value) or value <= 0 or value > 1:
        raise ValueError(f"{name} ({value}) 必须为 (0, 1] 区间的数值")
    return value


def _assert_positive_int(name: str, value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} ({value!r}) 必须为正整数")
    return value


def _assert_non_negative_int(name: str, value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} ({value!r}) 必须为非负整数")
    return value


def _validate_summarization_pair(raw: dict, name: str) -> None:
    provider = raw.get("summarization_provider")
    model = raw.get("summarization_model")
    if provider is not None and not isinstance(provider, str):
        raise ValueError(f"{name}.summarization_provider 必须为字符串")
    if model is not None and not isinstance(model, str):
        raise ValueError(f"{name}.summarization_model 必须为字符串")
    if provider is None and model is None:
        return
    if provider is None or model is None:
        raise ValueError(
            f"{name}: summarization_provider 与 summarization_model 必须成对设置"
        )
    if (len(provider) == 0) != (len(model) == 0):
        raise ValueError(
            f"{name}: summarization_provider 与 summarization_model 必须同时为空或同时非空"
        )


def _validate_policy(raw: dict, name: str) -> None:
    if "threshold_ratio" in raw:
        _assert_ratio(f"{name}.threshold_ratio", raw["threshold_ratio"])
    if "headroom_tokens" in raw:
        _assert_non_negative_int(f"{name}.headroom_tokens", raw["headroom_tokens"])
    if "retain_ratio" in raw:
        _assert_ratio(f"{name}.retain_ratio", raw["retain_ratio"])
    if "retain_tokens" in raw:
        _assert_non_negative_int(f"{name}.retain_tokens", raw["retain_tokens"])
    if "retain_ratio" in raw and "retain_tokens" in raw:
        raise ValueError(f"{name}: retain_ratio 与 retain_tokens 互斥")
    if "max_tokens" in raw:
        _assert_positive_int(f"{name}.max_tokens", raw["max_tokens"])
    if "compaction_retries" in raw:
        _assert_non_negative_int(f"{name}.compaction_retries", raw["compaction_retries"])
    if "max_overflow_retries" in raw:
        _assert_non_negative_int(f"{name}.max_overflow_retries", raw["max_overflow_retries"])
    _validate_summarization_pair(raw, name)


def _validate_ratio_retention(threshold_ratio: float, retain_ratio: float | None, name: str) -> None:
    if retain_ratio is not None and retain_ratio >= threshold_ratio:
        raise ValueError(
            f"{name}: retain_ratio ({retain_ratio}) 必须小于 threshold_ratio ({threshold_ratio})"
        )


def _normalize_policy(raw: Any, name: str) -> dict:
    if not isinstance(raw, dict):
        raise ValueError(f"{name} 必须为 dict")
    unknown = set(raw) - _POLICY_KEYS
    if unknown:
        raise ValueError(f"{name}: 未知键 {sorted(unknown)}")
    _validate_policy(raw, name)
    return {
        "threshold_ratio": float(raw.get("threshold_ratio", DEFAULT_THRESHOLD_RATIO)),
        "headroom_tokens": int(raw.get("headroom_tokens", DEFAULT_HEADROOM_TOKENS)),
        "retain_ratio": (float(raw["retain_ratio"]) if "retain_ratio" in raw else None),
        "retain_tokens": (int(raw["retain_tokens"]) if "retain_tokens" in raw else None),
        "summarization_provider": str(raw.get("summarization_provider", "")),
        "summarization_model": str(raw.get("summarization_model", "")),
        "max_tokens": int(raw.get("max_tokens", raw.get("headroom_tokens", DEFAULT_HEADROOM_TOKENS))),
        "compaction_retries": int(raw.get("compaction_retries", DEFAULT_COMPACTION_RETRIES)),
        "max_overflow_retries": int(raw.get("max_overflow_retries", DEFAULT_MAX_OVERFLOW_RETRIES)),
    }


# ── 解析入口 ────────────────────────────────────────────────


def resolve_config(raw: dict | None = None) -> CompactionConfig:
    """校验并分离默认值 + 精确目标覆盖，返回不可变配置。

    Args:
        raw: 未受信配置（来自 ConfigPort.get_compaction_config()）。

    Returns:
        CompactionConfig。
    """
    raw = dict(raw or {})
    unknown = set(raw) - _CONFIG_KEYS
    if unknown:
        raise ValueError(f"CompactionConfig: 未知键 {sorted(unknown)}")

    _validate_policy(raw, "CompactionConfig")
    base = _normalize_policy(
        {key: value for key, value in raw.items() if key in _POLICY_KEYS},
        "CompactionConfig",
    )
    # 默认保留形式：两者都未配置时使用 retainRatio 默认值（与 dsh 一致）。
    if base["retain_ratio"] is None and base["retain_tokens"] is None:
        base["retain_ratio"] = DEFAULT_RETAIN_RATIO
    _validate_ratio_retention(base["threshold_ratio"], base["retain_ratio"], "CompactionConfig")

    if "enabled" in raw and not isinstance(raw["enabled"], bool):
        raise ValueError("CompactionConfig.enabled 必须为布尔值")
    if "auto" in raw and not isinstance(raw["auto"], bool):
        raise ValueError("CompactionConfig.auto 必须为布尔值")
    if "prune_enabled" in raw and not isinstance(raw["prune_enabled"], bool):
        raise ValueError("CompactionConfig.prune_enabled 必须为布尔值")

    prune_threshold = _assert_positive_int(
        "CompactionConfig.prune_threshold_chars",
        int(raw.get("prune_threshold_chars", DEFAULT_PRUNE_THRESHOLD_CHARS)),
    )
    prune_head = _assert_non_negative_int(
        "CompactionConfig.prune_head_chars",
        int(raw.get("prune_head_chars", DEFAULT_PRUNE_HEAD_CHARS)),
    )
    prune_tail = _assert_non_negative_int(
        "CompactionConfig.prune_tail_chars",
        int(raw.get("prune_tail_chars", DEFAULT_PRUNE_TAIL_CHARS)),
    )
    reserved = _assert_non_negative_int(
        "CompactionConfig.reserved_tokens", int(raw.get("reserved_tokens", 0) or 0),
    )

    policies = _resolve_model_policies(raw.get("model_policies"))

    return CompactionConfig(
        enabled=bool(raw.get("enabled", True)),
        auto=bool(raw.get("auto", True)),
        threshold_ratio=base["threshold_ratio"],
        headroom_tokens=base["headroom_tokens"],
        retain_ratio=base["retain_ratio"],
        retain_tokens=base["retain_tokens"],
        summarization_provider=base["summarization_provider"],
        summarization_model=base["summarization_model"],
        max_tokens=base["max_tokens"],
        compaction_retries=base["compaction_retries"],
        max_overflow_retries=base["max_overflow_retries"],
        reserved_tokens=reserved,
        prune_enabled=bool(raw.get("prune_enabled", True)),
        prune_threshold_chars=prune_threshold,
        prune_head_chars=prune_head,
        prune_tail_chars=prune_tail,
        model_policies=policies,
    )


def _resolve_model_policies(configured: Any) -> tuple:
    if configured is None:
        return ()
    if not isinstance(configured, (list, tuple)):
        raise ValueError("CompactionConfig.model_policies 必须为列表")
    seen: set = set()
    resolved = []
    for index, source in enumerate(configured):
        name = f"CompactionConfig.model_policies[{index}]"
        if not isinstance(source, dict):
            raise ValueError(f"{name} 必须为 dict")
        unknown = set(source) - _MODEL_POLICY_KEYS
        if unknown:
            raise ValueError(f"{name}: 未知键 {sorted(unknown)}")
        provider = source.get("provider")
        model = source.get("model")
        if not isinstance(provider, str) or not provider:
            raise ValueError(f"{name}.provider 必须为非空字符串")
        if not isinstance(model, str) or not model:
            raise ValueError(f"{name}.model 必须为非空字符串")
        key = (provider, model)
        if key in seen:
            raise ValueError(f"{name}: 重复的模型覆盖 {provider}/{model}")
        seen.add(key)
        _validate_policy(source, name)
        policy = _normalize_policy(
            {key: value for key, value in source.items() if key in _POLICY_KEYS}, name,
        )
        policy["provider"] = provider
        policy["model"] = model
        _validate_ratio_retention(policy["threshold_ratio"], policy["retain_ratio"], name)
        resolved.append(policy)
    return tuple(resolved)


def _retention_from(raw_policy: dict, fallback: tuple[float | None, int | None]) -> tuple[float | None, int | None]:
    """取一份策略的保留形式，未设置时继承 fallback。"""
    if raw_policy.get("retain_tokens") is not None:
        return None, int(raw_policy["retain_tokens"])
    if raw_policy.get("retain_ratio") is not None:
        return float(raw_policy["retain_ratio"]), None
    return fallback


def resolve_target_policy(config: CompactionConfig, provider: str, model: str) -> CompactionPolicy:
    """把精确 ``provider/model`` 覆盖合并到默认策略之上。"""
    override = next(
        (p for p in config.model_policies if p.get("provider") == provider and p.get("model") == model),
        None,
    )
    inherit = (config.retain_ratio, config.retain_tokens)
    if override is None:
        retained = inherit
        source = {
            "threshold_ratio": config.threshold_ratio,
            "headroom_tokens": config.headroom_tokens,
            "summarization_provider": config.summarization_provider,
            "summarization_model": config.summarization_model,
            "max_tokens": config.max_tokens,
            "compaction_retries": config.compaction_retries,
            "max_overflow_retries": config.max_overflow_retries,
        }
    else:
        retained = _retention_from(override, inherit)
        source = {
            "threshold_ratio": override["threshold_ratio"],
            "headroom_tokens": override["headroom_tokens"],
            "summarization_provider": override["summarization_provider"],
            "summarization_model": override["summarization_model"],
            "max_tokens": override["max_tokens"],
            "compaction_retries": override["compaction_retries"],
            "max_overflow_retries": override["max_overflow_retries"],
        }
    return CompactionPolicy(
        threshold_ratio=source["threshold_ratio"],
        headroom_tokens=source["headroom_tokens"],
        retain_ratio=retained[0],
        retain_tokens=retained[1],
        summarization_provider=source["summarization_provider"],
        summarization_model=source["summarization_model"],
        max_tokens=source["max_tokens"],
        compaction_retries=source["compaction_retries"],
        max_overflow_retries=source["max_overflow_retries"],
    )


def resolve_compact_spec(
    policy: CompactionPolicy,
    context_window: int,
    reserved_completion_tokens: int,
) -> ResolvedCompactSpec:
    """按模型容量把目标策略折算为具体 token 预算。

    Args:
        policy: 合并后的目标策略。
        context_window: 该目标的模型上下文窗口（正整数）。
        reserved_completion_tokens: 一次请求预留的输出 token（非负整数）。

    Returns:
        ResolvedCompactSpec。

    Raises:
        TargetPressureConfigError: 容量非法 / 预算耗尽 / 保留不小于阈值。
    """
    target_key = f"{policy.summarization_provider or 'routed'}/{policy.summarization_model or 'model'}"
    if not isinstance(context_window, int) or context_window <= 0:
        raise TargetPressureConfigError(
            target_key, f"context_window ({context_window}) 必须为正整数"
        )
    if not isinstance(reserved_completion_tokens, int) or reserved_completion_tokens < 0:
        raise TargetPressureConfigError(
            target_key,
            f"reserved_completion_tokens ({reserved_completion_tokens}) 必须为非负整数",
        )

    message_budget = context_window - reserved_completion_tokens
    if message_budget <= 0:
        raise TargetPressureConfigError(
            target_key,
            f"{target_key} 预留 {reserved_completion_tokens} 输出 token 后，"
            f"{context_window} 上下文窗口已无消息预算；请提高模型上下文窗口或降低输出预留",
        )
    pressure_budget = message_budget - policy.headroom_tokens
    if pressure_budget <= 0:
        raise TargetPressureConfigError(
            target_key,
            f"{target_key} 预留 {reserved_completion_tokens} 输出 + {policy.headroom_tokens} "
            f"余量后，{context_window} 上下文窗口已无压力预算；请降低输出预留或余量，"
            "或配置更大的模型上下文窗口",
        )

    threshold_tokens = math.floor(min(context_window * policy.threshold_ratio, pressure_budget))
    if policy.retain_tokens is not None:
        retain_tokens = int(policy.retain_tokens)
    else:
        ratio = policy.retain_ratio if policy.retain_ratio is not None else DEFAULT_RETAIN_RATIO
        retain_tokens = math.floor(message_budget * ratio)
    if retain_tokens >= threshold_tokens:
        raise TargetPressureConfigError(
            target_key,
            f"{target_key} retain_tokens ({retain_tokens}) 必须小于 threshold_tokens ({threshold_tokens})",
        )

    return ResolvedCompactSpec(
        context_window=context_window,
        threshold_tokens=threshold_tokens,
        retain_tokens=retain_tokens,
        max_tokens=policy.max_tokens,
        compaction_retries=policy.compaction_retries,
        max_overflow_retries=policy.max_overflow_retries,
    )


__all__ = [
    "DEFAULT_THRESHOLD_RATIO",
    "DEFAULT_RETAIN_RATIO",
    "DEFAULT_HEADROOM_TOKENS",
    "DEFAULT_COMPACTION_RETRIES",
    "DEFAULT_MAX_OVERFLOW_RETRIES",
    "DEFAULT_PRUNE_THRESHOLD_CHARS",
    "DEFAULT_PRUNE_HEAD_CHARS",
    "DEFAULT_PRUNE_TAIL_CHARS",
    "TargetPressureConfigError",
    "CompactionPolicy",
    "ResolvedCompactSpec",
    "CompactionConfig",
    "resolve_config",
    "resolve_target_policy",
    "resolve_compact_spec",
]
