"""配置端口 — 核心层与配置系统的接口"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ConfigPort(ABC):
    """抽象配置端口

    核心层通过此接口读取和写入配置。
    具体实现可包装文件配置、环境变量或内存字典。
    """

    @abstractmethod
    def get(self, key: str, default: Any = None) -> Any:
        """读取配置项"""
        ...

    @abstractmethod
    def set(self, key: str, value: Any) -> None:
        """写入配置项并持久化"""
        ...

    @abstractmethod
    def get_model(self) -> str:
        """获取当前模型名称"""
        ...

    @abstractmethod
    def get_base_url(self) -> str:
        """获取 API base URL"""
        ...

    @abstractmethod
    def get_token_prices(self) -> dict:
        """获取 token 价格配置"""
        ...

    @abstractmethod
    def get_models(self) -> list[str]:
        """获取可用模型列表"""
        ...

    @abstractmethod
    def get_int(self, key: str, default: int = 0) -> int:
        """读取整数配置项"""
        ...

    @abstractmethod
    def get_float(self, key: str, default: float = 0.0) -> float:
        """读取浮点配置项"""
        ...

    @abstractmethod
    def get_bool(self, key: str, default: bool = False) -> bool:
        """读取布尔配置项"""
        ...


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
