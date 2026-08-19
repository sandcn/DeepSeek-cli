"""配置包 — 配置加载和持久化"""

import json
import logging
import os
import sys

from .defaults import CONFIG_DIR, LOG_FILE, RC_FILE, DEFAULTS, PROVIDERS, CONFIG_KEYS
from .schema import _validate_rc


_RC = None
_RC_LOADED = False


def _ensure_config_dir():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def _safe_merge(defaults: dict, overrides: dict) -> dict:
    """安全合并，保留 defaults 中已有的键并合并 overrides 值。

    同时保留 overrides 中 defaults 不存在的顶层键（如 "performance"），
    信任 _validate_rc 做校验和回退。

    注（review P3）：浅合并——RC 只配置复合键部分子键（如 skills 只写
    {"enabled": false}）时会整体替换 defaults 的 skills。读取方有 .get
    默认值兜底不崩溃，但用户配置会部分丢失；深合并复合键需按类型递归，
    当前复合键消费方均容忍，暂不实施。
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
    """更新配置键并持久化到 RC 文件。

    注（review P3）：无并发锁——多线程同时写 RC 文件存在写坏风险。
    调用频率低（命令式配置修改）且写为原子 write_text（整文件重写），
    实际风险可控；如需并发安全需加 threading.Lock 保护整个读改写序列。
    """
    rc = get_rc()
    # 使用 CONFIG_KEYS 中的 rc_path 进行键名映射
    if key in CONFIG_KEYS:
        path = CONFIG_KEYS[key]["rc_path"]
        if path:
            assert all(isinstance(p, str) and p for p in path), (
                f"CONFIG_KEYS['{key}']['rc_path'] 包含无效路径段: {path}"
            )
            target = rc
            for part in path[:-1]:
                target = target.setdefault(part, {})
            target[path[-1]] = value
        else:
            rc[key] = value
    else:
        rc[key] = value
    _ensure_config_dir()
    try:
        RC_FILE.write_text(
            json.dumps(rc, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as e:
        sys.__stderr__.write(f"警告: 无法写入配置文件 {RC_FILE}: {e}\n")
    else:
        from . import _clear_value_cache
        _clear_value_cache()
        # multimodal 模型判定缓存联动失效：RC 配置 multimodal_models 变更后
        # 清除 is_multimodal_model 的结果缓存（延迟导入避免 config ↔ api 循环）
        try:
            from ..api.multimodal import clear_multimodal_cache
            clear_multimodal_cache()
        except Exception:
            pass


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
            sys.__stderr__.write(f"警告: provider '{provider}' 的 API 格式与 OpenAI 不兼容，当前客户端不支持，请使用支持的 provider。\n")
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
