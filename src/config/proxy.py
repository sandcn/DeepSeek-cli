"""ConfigProxy — 为 config 包提供完整的 @property 类型声明

使 IDE 和静态类型检查器可获得准确的类型提示。
运行时行为完全依赖 config/__init__.py 的 __getattr__ 机制。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import src.config as _config


class ConfigProxy:
    """配置代理 — 所有属性通过 @property 委托到 config/__init__.py 的 __getattr__
    
    使用方式:
        from src.config.proxy import config
        print(config.MODEL)
        print(config.TOKEN_PRICES)
    """

    @property
    def MODEL(self) -> str:
        return _config.MODEL

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
