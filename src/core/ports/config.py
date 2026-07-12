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
    def get_low_model(self) -> str:
        """获取低优先级模型名称（由 CHAT_LOW_MODEL 环境变量设置）

        返回空字符串表示未设置低模型，此时应使用 get_model() 的返回值。
        """
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

    # ── 上下文压缩配置 ──────────────────────────────────

    @abstractmethod
    def get_max_context_chars(self) -> int:
        """获取 max_context_chars 配置值"""
        ...

    @abstractmethod
    def get_max_context_tokens(self) -> int:
        """获取 max_context_tokens 配置值"""
        ...

    @abstractmethod
    def get_max_session_messages(self) -> int:
        """获取 max_session_messages 配置值（0=无限制）"""
        ...

    @abstractmethod
    def get_keep_recent_messages(self) -> int:
        """获取 keep_recent_messages 配置值"""
        ...

    @abstractmethod
    def get_auto_force_compress_threshold(self) -> int:
        """获取 auto_force_compress_threshold 配置值"""
        ...

    @abstractmethod
    def get_summary_token_budget(self) -> int:
        """获取 summary_token_budget 配置值"""
        ...

    # ── 并行执行配置 ──────────────────────────────────

    @abstractmethod
    def get_stagger_min_delay(self) -> float:
        """获取 stagger_min_delay 配置值（秒）"""
        ...

    @abstractmethod
    def get_stagger_max_delay(self) -> float:
        """获取 stagger_max_delay 配置值（秒）"""
        ...
