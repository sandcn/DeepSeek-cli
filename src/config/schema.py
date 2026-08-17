"""配置包 — 配置校验逻辑"""

import os
from typing import Optional

from .defaults import CONFIG_KEYS, DEFAULTS, PROVIDERS


def _detect_provider_from_api_key(api_key: str) -> tuple[Optional[str], Optional[str]]:
    """从 API Key 前缀推断 provider 和默认模型。

    Returns:
        (provider_name, default_model) 或 (None, None) 表示无法推断。
    """
    if not api_key:
        return None, None
    # Anthropic: sk-ant-api03-...
    if api_key.startswith("sk-ant-"):
        return "anthropic", "claude-sonnet-4-6"
    # 可在此扩展更多 key 前缀检测
    return None, None


def _derive_rc_fields(key_type):
    """从 CONFIG_KEYS 元数据中派生指定类型的扁平 RC 字段名列表。

    仅返回 rc_path 长度为 1（即 RC 文件顶层键）的条目，
    嵌套路径（如 HTTP 性能配置）由各自模块自行处理。
    """
    return sorted(
        entry["rc_path"][0]
        for entry in CONFIG_KEYS.values()
        if entry["type"] == key_type and len(entry["rc_path"]) == 1
    )


# 模块级派生常量（单次计算，避免 _validate_rc 每次调用重建列表）
_INT_FIELDS = _derive_rc_fields(int)
_FLOAT_FIELDS = _derive_rc_fields(float)
_BOOL_FIELDS = _derive_rc_fields(bool)


def _validate_rc(rc):
    """校验配置值类型，无效值回退到默认值"""
    int_fields = _INT_FIELDS
    for field in int_fields:
        if field in rc:
            if isinstance(rc[field], bool):
                rc[field] = DEFAULTS.get(field, 0)
                pass
            if not isinstance(rc[field], int):
                try:
                    rc[field] = int(rc[field])
                except (ValueError, TypeError):
                    rc[field] = DEFAULTS.get(field, 0)

    float_fields = _FLOAT_FIELDS
    for field in float_fields:
        if field in rc:
            if not isinstance(rc[field], (int, float)):
                try:
                    rc[field] = float(rc[field])
                except (ValueError, TypeError):
                    rc[field] = DEFAULTS.get(field, 1.0)

    bool_fields = _BOOL_FIELDS
    for field in bool_fields:
        if field in rc:
            if isinstance(rc[field], bool):
                pass
            elif isinstance(rc[field], str):
                rc[field] = rc[field].lower() in ("true", "1", "yes", "on")
            elif isinstance(rc[field], int):
                rc[field] = bool(rc[field])
            else:
                rc[field] = DEFAULTS.get(field, True)

    if "provider" in rc:
        if not isinstance(rc["provider"], str) or rc["provider"] not in PROVIDERS:
            rc["provider"] = DEFAULTS["provider"]

    if "base_url" in rc and not isinstance(rc["base_url"], str):
        rc["base_url"] = DEFAULTS["base_url"]
    if "api_key" in rc and not isinstance(rc["api_key"], str):
        rc["api_key"] = DEFAULTS["api_key"]

    # reasoning_effort 值域校验：非 str 或不在允许集合时回退默认值
    _REASONING_EFFORT_LEVELS = frozenset({"low", "medium", "high", "max"})
    if "reasoning_effort" in rc:
        effort = rc["reasoning_effort"]
        if not isinstance(effort, str) or effort.lower() not in _REASONING_EFFORT_LEVELS:
            rc["reasoning_effort"] = DEFAULTS.get("reasoning_effort", "max")
        else:
            rc["reasoning_effort"] = effort.lower()

    # temperature 值域校验：非法类型或超出 [0.0, 2.0] 时回退默认值
    if "temperature" in rc:
        temp = rc["temperature"]
        if isinstance(temp, bool) or not isinstance(temp, (int, float)):
            rc["temperature"] = DEFAULTS.get("temperature", 0.2)
        else:
            temp = float(temp)
            if not (0.0 <= temp <= 2.0):
                rc["temperature"] = DEFAULTS.get("temperature", 0.2)
            else:
                rc["temperature"] = temp

    if "models" in rc:
        if not isinstance(rc["models"], (list, tuple)):
            rc["models"] = DEFAULTS["models"]
        else:
            rc["models"] = [str(m) for m in rc["models"]]

    if "token_prices" in rc:
        if not isinstance(rc["token_prices"], dict):
            rc["token_prices"] = DEFAULTS["token_prices"]
        else:
            cleaned = {}
            for model, prices in rc["token_prices"].items():
                if isinstance(prices, dict) and "input" in prices and "output" in prices:
                    try:
                        entry = {
                            "input": float(prices["input"]),
                            "output": float(prices["output"])
                        }
                        # 保留可选缓存命中价格（缺失时 /cost 回退按 input 全价）
                        if "input_cache_hit" in prices:
                            entry["input_cache_hit"] = float(prices["input_cache_hit"])
                        cleaned[str(model)] = entry
                    except (ValueError, TypeError):
                        continue
            rc["token_prices"] = cleaned

    if rc.get("max_retries", 1) < 0:
        rc["max_retries"] = DEFAULTS["max_retries"]
    if rc.get("max_context_chars", 1) < 0:
        rc["max_context_chars"] = DEFAULTS["max_context_chars"]

    provider = rc.get("provider", DEFAULTS["provider"])
    if provider in PROVIDERS:
        provider_config = PROVIDERS[provider]
        if not rc.get("base_url"):
            rc["base_url"] = provider_config.get("base_url", "")
        if not rc.get("models"):
            rc["models"] = list(provider_config.get("models", []))
        if not rc.get("token_prices"):
            rc["token_prices"] = provider_config.get("token_prices", {})

    current_model = rc.get("model")
    if current_model and current_model not in rc.get("models", []):
        rc.setdefault("models", []).append(current_model)

    # ── API Key 自动探测 provider ─────────────────
    # 当 CHAT_API_KEY 已设置且 CHAT_MODEL / CHAT_BASE_URL 未被用户显式覆盖时：
    #   1. 尝试从 Key 前缀推断 provider，若与当前不同则完整切换
    #   2. 若无法推断但 RC 中存在残留的异 provider 配置（如 base_url 不匹配），
    #      则同步当前 provider 的内置 base_url/models/token_prices
    _api_key = os.getenv("CHAT_API_KEY", "")
    _env_model = os.getenv("CHAT_MODEL", "")
    _env_base_url = os.getenv("CHAT_BASE_URL", "")
    if _api_key and not _env_model:
        detected_provider, detected_model = _detect_provider_from_api_key(_api_key)
        current_provider = rc.get("provider", DEFAULTS["provider"])
        if detected_provider and detected_provider != current_provider:
            # 检测到不同 provider → 完整切换
            provider_config = PROVIDERS.get(detected_provider, {})
            rc["provider"] = detected_provider
            rc["model"] = detected_model
            rc["base_url"] = provider_config.get("base_url", "")
            rc["models"] = list(provider_config.get("models", []))
            rc["token_prices"] = provider_config.get("token_prices", {})
        elif not _env_base_url and current_provider in PROVIDERS:
            # 无法从 Key 推断不同 provider，但确保 RC 中的 provider 配置一致
            # 修复残留的异 provider 配置（如 base_url 指向其他服务）
            provider_config = PROVIDERS[current_provider]
            provider_base_url = provider_config.get("base_url", "")
            if provider_base_url and rc.get("base_url") != provider_base_url:
                rc["base_url"] = provider_base_url
                if not rc.get("models"):
                    rc["models"] = list(provider_config.get("models", []))
                if not rc.get("token_prices"):
                    rc["token_prices"] = provider_config.get("token_prices", {})

    return rc
