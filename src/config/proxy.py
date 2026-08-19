"""ConfigProxy — 为 config 包提供完整的 @property 类型声明

使 IDE 和静态类型检查器可获得准确的类型提示。
运行时行为完全依赖 config/__init__.py 的 __getattr__ 机制。

实现 ConfigPort 接口，提供方法式访问（get/set/get_model/get_base_url 等）。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import src.config as _config
from ..core.ports.config import ConfigPort


class ConfigProxy(ConfigPort):
    """配置代理 — 所有属性通过 @property 委托到 config/__init__.py 的 __getattr__

    同时实现 ConfigPort 接口，提供方法式配置访问。
    
    使用方式:
        from src.config.proxy import config
        print(config.MODEL)
        print(config.TOKEN_PRICES)
        config.set("MODEL", "deepseek-r1")
    """

    # ── ConfigPort 方法实现 ─────────────────────────────

    def set(self, key: str, value: Any) -> None:
        """设置配置项并保存"""
        from src.config.loader import update_config
        update_config(key, value)

    def get_model(self) -> str:
        return _config.MODEL

    def get_low_model(self) -> str:
        return _config.LOW_MODEL

    def get_base_url(self) -> str:
        return _config.BASE_URL

    def get_token_prices(self) -> dict:
        return _config.TOKEN_PRICES

    def get_models(self) -> list[str]:
        return _config.MODELS

    def get_reasoning_effort(self) -> str:
        """获取推理等级（low/medium/high/max）。"""
        return _config.REASONING_EFFORT

    def get_temperature(self) -> float:
        """获取大模型温度（0.0~2.0）。"""
        return float(_config.TEMPERATURE)

    def get_int(self, key: str, default: int = 0) -> int:
        return int(self.get(key, default))

    def get_float(self, key: str, default: float = 0.0) -> float:
        return float(self.get(key, default))

    def get_bool(self, key: str, default: bool = False) -> bool:
        value = self.get(key, default)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.lower() in ("true", "1", "yes", "on")
        return bool(value)

    # ── 上下文压缩配置 ──────────────────────────────────

    def get_max_context_chars(self) -> int:
        return _config.MAX_CONTEXT_CHARS

    def get_max_context_tokens(self) -> int:
        return _config.MAX_CONTEXT_TOKENS

    def get_model_context_tokens(self) -> int:
        """获取模型上下文窗口（tokens）——上下文使用率百分比的分母。"""
        return _config.MODEL_CONTEXT_TOKENS

    def get_max_session_messages(self) -> int:
        return _config.MAX_SESSION_MESSAGES

    def get_keep_recent_messages(self) -> int:
        return _config.KEEP_RECENT_MESSAGES

    def get_auto_force_compress_threshold(self) -> int:
        return _config.AUTO_FORCE_COMPRESS_THRESHOLD

    def get_summary_token_budget(self) -> int:
        return _config.SUMMARY_TOKEN_BUDGET

    # ── 并行执行配置 ──────────────────────────────────

    def get_stagger_min_delay(self) -> float:
        return _config.STAGGER_MIN_DELAY

    def get_stagger_max_delay(self) -> float:
        return _config.STAGGER_MAX_DELAY

    # ── @property 属性（向后兼容） ──────────────────────

    @property
    def MODEL(self) -> str:
        return _config.MODEL

    @property
    def LOW_MODEL(self) -> str:
        return _config.LOW_MODEL

    @property
    def API_KEY(self) -> str:
        return _config.API_KEY

    @property
    def BASE_URL(self) -> str:
        return _config.BASE_URL

    @property
    def MODELS(self) -> list[str]:
        return _config.MODELS

    @property
    def REASONING_EFFORT(self) -> str:
        return _config.REASONING_EFFORT

    @property
    def TEMPERATURE(self) -> float:
        return _config.TEMPERATURE

    @property
    def THEME(self) -> str:
        return _config.THEME

    @property
    def TOKEN_PRICES(self) -> dict:
        return _config.TOKEN_PRICES

    @property
    def MAX_CONTEXT_CHARS(self) -> int:
        return _config.MAX_CONTEXT_CHARS

    @property
    def MAX_OUTPUT_CHARS(self) -> int:
        return _config.MAX_OUTPUT_CHARS

    @property
    def MAX_RETRIES(self) -> int:
        return _config.MAX_RETRIES

    @property
    def RETRY_BASE_SEC(self) -> float:
        return _config.RETRY_BASE_SEC

    @property
    def MAX_SESSION_MESSAGES(self) -> int:
        return _config.MAX_SESSION_MESSAGES

    @property
    def KEEP_RECENT_MESSAGES(self) -> int:
        return _config.KEEP_RECENT_MESSAGES

    @property
    def MAX_CONTEXT_TOKENS(self) -> int:
        return _config.MAX_CONTEXT_TOKENS

    @property
    def MODEL_CONTEXT_TOKENS(self) -> int:
        return _config.MODEL_CONTEXT_TOKENS

    @property
    def SUMMARY_TOKEN_BUDGET(self) -> int:
        return _config.SUMMARY_TOKEN_BUDGET

    @property
    def AUTO_FORCE_COMPRESS_THRESHOLD(self) -> int:
        return _config.AUTO_FORCE_COMPRESS_THRESHOLD

    @property
    def ENABLE_NOTIFICATIONS(self) -> bool:
        return _config.ENABLE_NOTIFICATIONS

    @property
    def NOTIFY_ON_CHAT_COMPLETION(self) -> bool:
        return _config.NOTIFY_ON_CHAT_COMPLETION

    @property
    def CONFIG_DIR(self) -> Path:
        return _config.CONFIG_DIR

    @property
    def LOG_FILE(self) -> Path:
        return _config.LOG_FILE

    @property
    def RC_FILE(self) -> Path:
        return _config.RC_FILE

    @property
    def INPUT_HISTORY_FILE(self) -> Path:
        return _config.INPUT_HISTORY_FILE

    @property
    def PROVIDERS(self) -> dict:
        return _config.PROVIDERS

    @property
    def DEFAULTS(self) -> dict:
        return _config.DEFAULTS

    @property
    def CONFIG_KEYS(self) -> dict:
        return _config.CONFIG_KEYS

    @property
    def STAGGER_MIN_DELAY(self) -> float:
        return _config.STAGGER_MIN_DELAY

    @property
    def STAGGER_MAX_DELAY(self) -> float:
        return _config.STAGGER_MAX_DELAY

    @property
    def HTTP_CONNECT_TIMEOUT(self) -> int:
        return _config.HTTP_CONNECT_TIMEOUT

    @property
    def HTTP_READ_TIMEOUT(self) -> int:
        return _config.HTTP_READ_TIMEOUT

    @property
    def HTTP_WRITE_TIMEOUT(self) -> int:
        return _config.HTTP_WRITE_TIMEOUT

    @property
    def HTTP_MAX_CONNECTIONS(self) -> int:
        return _config.HTTP_MAX_CONNECTIONS

    @property
    def HTTP_MAX_CONNECTIONS_PER_HOST(self) -> int:
        return _config.HTTP_MAX_CONNECTIONS_PER_HOST

    @property
    def HTTP_KEEP_ALIVE_TIMEOUT(self) -> int:
        return _config.HTTP_KEEP_ALIVE_TIMEOUT

    @property
    def HTTP_ENABLE_POOL(self) -> bool:
        return _config.HTTP_ENABLE_POOL

    @property
    def HTTP_ENABLE_HTTP2(self) -> bool:
        return _config.HTTP_ENABLE_HTTP2

    @property
    def audit_logger(self) -> logging.Logger:
        return _config.audit_logger

    @property
    def config(self) -> dict:
        """完整 RC 配置字典"""
        # 直接调用内部函数避免模块级 config 属性被 ConfigProxy 实例覆盖导致循环引用
        from src.config.loader import get_rc
        from src.config import _resolve_rc_key
        return _resolve_rc_key("config", get_rc())

    def get(self, key: str, default: Any = None) -> Any:
        """字典式访问 — 向后兼容，委托到 self.config.get()
        
        提供与 RC 字典相同的 .get() 接口，使 ''from src.config import config''
        的使用方无需修改即可迁移到 ConfigProxy。
        """
        return self.config.get(key, default)


# 全局单例
config = ConfigProxy()
