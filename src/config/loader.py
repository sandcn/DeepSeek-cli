"""配置包 — 配置加载和持久化"""

import json
import logging
import os
import sys

from ..ui._lock import locked_print

from .defaults import CONFIG_DIR, LOG_FILE, RC_FILE, DEFAULTS, PROVIDERS
from .schema import _validate_rc


_RC = None
_RC_LOADED = False


def _ensure_config_dir():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def _safe_merge(defaults: dict, overrides: dict) -> dict:
    """安全合并，保留 defaults 中已有的键并合并 overrides 值。

    同时保留 overrides 中 defaults 不存在的顶层键（如 "performance"），
    信任 _validate_rc 做校验和回退。
    """
    result = dict(defaults)
    for key in result:
        if key in overrides:
            result[key] = overrides[key]
    # 保留 overrides 中 defaults 不存在的顶层键（如 "performance"），
    # 由 _validate_rc 做校验和回退
    for key in overrides:
        if key not in result:
            result[key] = overrides[key]
    return result


def _load_rc():
    _ensure_config_dir()
    if RC_FILE.exists():
        try:
            raw = _safe_merge(DEFAULTS, json.loads(RC_FILE.read_text(encoding="utf-8")))
            return _validate_rc(raw)
        except json.JSONDecodeError as e:
            logging.warning("配置文件 %s 解析失败: %s，使用默认配置", RC_FILE, e)
        except (PermissionError, OSError) as e:
            logging.warning("无法读取配置文件 %s: %s，使用默认配置", RC_FILE, e)
    return _validate_rc(dict(DEFAULTS))


def get_rc():
    global _RC, _RC_LOADED
    if not _RC_LOADED:
        _RC = _load_rc()
        _RC_LOADED = True
    return _RC


def update_config(key: str, value) -> None:
    rc = get_rc()
    rc[key] = value
    _ensure_config_dir()
    try:
        RC_FILE.write_text(
            json.dumps(rc, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as e:
        locked_print(f"警告: 无法写入配置文件 {RC_FILE}: {e}", file=sys.stderr)
    else:
        from . import _clear_value_cache
        _clear_value_cache()


def get_base_url(provider=None):
    rc = get_rc()
    if provider is None:
        provider = rc.get("provider", DEFAULTS["provider"])
    env_url = os.getenv("CHAT_BASE_URL")
    if env_url:
        return env_url
    rc_url = rc.get("base_url", "")
    if rc_url:
        return rc_url
    if provider in PROVIDERS:
        provider_config = PROVIDERS[provider]
        provider_url = provider_config.get("base_url", "")
        if not provider_url and provider != "custom":
            locked_print(f"警告: provider '{provider}' 的 API 格式与 OpenAI 不兼容，当前客户端不支持，请使用支持的 provider。",
                  file=sys.stderr)
        if provider_url:
            return provider_url
    return "https://api.deepseek.com/v1/chat/completions"


def get_audit_logger():
    _ensure_config_dir()
    audit_logger = logging.getLogger("chat_audit")
    audit_logger.setLevel(logging.INFO)
    if not audit_logger.handlers:
        _handler = logging.FileHandler(str(LOG_FILE), encoding="utf-8")
        _handler.setFormatter(logging.Formatter("%(asctime)s | %(message)s"))
        audit_logger.addHandler(_handler)
    return audit_logger


def _get_performance_config() -> dict:
    """获取性能配置，从 RC 文件中读取 performance 节点。"""
    rc = get_rc()
    return rc.get("performance", {})


def _get_config(env_var, default):
    """从环境变量读取配置，未设置时返回默认值。"""
    val = os.getenv(env_var)
    if val is not None:
        return val
    return default
