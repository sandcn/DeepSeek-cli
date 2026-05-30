"""配置包 — 配置校验逻辑"""

from .defaults import DEFAULTS, PROVIDERS


def _validate_rc(rc):
    """校验配置值类型，无效值回退到默认值"""
    int_fields = ["max_context_chars", "max_output_chars", "max_retries",
                  "max_session_messages",
                  "keep_recent_messages", "max_context_tokens", "summary_token_budget",
                  "auto_force_compress_threshold"]
    for field in int_fields:
        if field in rc:
            if isinstance(rc[field], bool):
                continue
            if not isinstance(rc[field], int):
                try:
                    rc[field] = int(rc[field])
                except (ValueError, TypeError):
                    rc[field] = DEFAULTS.get(field, 0)

    float_fields = ["retry_base_sec"]
    for field in float_fields:
        if field in rc:
            if not isinstance(rc[field], (int, float)):
                try:
                    rc[field] = float(rc[field])
                except (ValueError, TypeError):
                    rc[field] = DEFAULTS.get(field, 1.0)

    bool_fields = ["enable_notifications", "notify_on_chat_completion"]
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
                        cleaned[str(model)] = {
                            "input": float(prices["input"]),
                            "output": float(prices["output"])
                        }
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

    return rc
