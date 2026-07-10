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
