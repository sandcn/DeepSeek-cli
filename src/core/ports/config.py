"""配置端口 — 核心层与配置系统的接口

适配器实现已移至 src.core.adapters.config。
"""
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
