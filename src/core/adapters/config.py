"""配置端口适配器 — DefaultConfigAdapter、MockConfigAdapter"""
from __future__ import annotations

from typing import Any

from ..ports.config import ConfigPort


class DefaultConfigAdapter(ConfigPort):
    """默认配置适配器 — 包装 src/config 模块

    所有对 src/config 模块的依赖均在方法体内延迟导入，
    避免模块加载时的副作用。
    """

    def get(self, key: str, default: Any = None) -> Any:
        from ...config import config
        return config.get(key, default)

    def set(self, key: str, value: Any) -> None:
        from ...config import update_config
        update_config(key, value)

    def get_model(self) -> str:
        from ...config import MODEL
        return MODEL

    def get_base_url(self) -> str:
        from ...config import get_base_url
        return get_base_url()

    def get_token_prices(self) -> dict:
        from ...config import TOKEN_PRICES
        return TOKEN_PRICES

    def get_models(self) -> list[str]:
        from ...config import MODELS
        return MODELS

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
        from ...config import MAX_CONTEXT_CHARS
        return MAX_CONTEXT_CHARS

    def get_max_context_tokens(self) -> int:
        from ...config import MAX_CONTEXT_TOKENS
        return MAX_CONTEXT_TOKENS

    def get_max_session_messages(self) -> int:
        from ...config import MAX_SESSION_MESSAGES
        return MAX_SESSION_MESSAGES

    def get_keep_recent_messages(self) -> int:
        from ...config import KEEP_RECENT_MESSAGES
        return KEEP_RECENT_MESSAGES

    def get_auto_force_compress_threshold(self) -> int:
        from ...config import AUTO_FORCE_COMPRESS_THRESHOLD
        return AUTO_FORCE_COMPRESS_THRESHOLD

    def get_summary_token_budget(self) -> int:
        from ...config import SUMMARY_TOKEN_BUDGET
        return SUMMARY_TOKEN_BUDGET

    # ── 并行执行配置 ──────────────────────────────────

    def get_stagger_min_delay(self) -> float:
        from ...config import STAGGER_MIN_DELAY
        return STAGGER_MIN_DELAY

    def get_stagger_max_delay(self) -> float:
        from ...config import STAGGER_MAX_DELAY
        return STAGGER_MAX_DELAY


class MockConfigAdapter(ConfigPort):
    """模拟配置适配器 — 用于测试

    内部使用 dict 存储配置，支持初始数据注入。
    记录 set 调用次数和参数，便于测试断言。
    """

    def __init__(self, initial: dict | None = None) -> None:
        self._data: dict = dict(initial) if initial else {}
        self.set_count: int = 0
        self.last_set_key: str | None = None
        self.last_set_value: Any = None

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value
        self.set_count += 1
        self.last_set_key = key
        self.last_set_value = value

    def get_model(self) -> str:
        return str(self._data.get("model", ""))

    def get_base_url(self) -> str:
        return str(self._data.get("base_url", ""))

    def get_token_prices(self) -> dict:
        result = self._data.get("token_prices", {})
        return dict(result) if isinstance(result, dict) else {}

    def get_models(self) -> list[str]:
        result = self._data.get("models", [])
        return list(result) if isinstance(result, (list, tuple)) else []

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
        return int(self._data.get("max_context_chars", 60000))

    def get_max_context_tokens(self) -> int:
        return int(self._data.get("max_context_tokens", 60000))

    def get_max_session_messages(self) -> int:
        return int(self._data.get("max_session_messages", 0))

    def get_keep_recent_messages(self) -> int:
        return int(self._data.get("keep_recent_messages", 0))

    def get_auto_force_compress_threshold(self) -> int:
        return int(self._data.get("auto_force_compress_threshold", 60000))

    def get_summary_token_budget(self) -> int:
        return int(self._data.get("summary_token_budget", 2000))

    # ── 并行执行配置 ──────────────────────────────────

    def get_stagger_min_delay(self) -> float:
        return float(self._data.get("stagger_min_delay", 0.1))

    def get_stagger_max_delay(self) -> float:
        return float(self._data.get("stagger_max_delay", 0.5))
