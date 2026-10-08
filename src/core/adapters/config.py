"""配置端口适配器 — DefaultConfigAdapter、MockConfigAdapter"""
from __future__ import annotations

from typing import Any

from ..ports.config import ConfigPort
from ...config.defaults import DEFAULTS
from ...config.proxy import ConfigProxy


class DefaultConfigAdapter(ConfigProxy):
    """默认配置适配器 — 复用 ConfigProxy 的完整实现。

    ConfigProxy 已是 ConfigPort 的完整实现（包含全部 get_* 方法与
    @property 类型提示），DefaultConfigAdapter 作为 core 层端口适配器
    直接复用其实现，消除重复。

    依赖方向：适配器层 → config 包（具体实现），符合六边形架构中
    适配器依赖具体基础设施的规则。
    """


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
        """当前模型名（唯一来源：模型档案 ``model_profiles``）。"""
        from ...config.model_profiles import current_model

        try:
            return current_model(self._data)
        except Exception:
            return ""

    def get_base_url(self) -> str:
        """当前接口地址（唯一来源：当前模型档案 / 其提供商默认地址）。"""
        from ...config.model_profiles import current_base_url

        try:
            return current_base_url(self._data)
        except Exception:
            return ""

    def get_token_prices(self) -> dict:
        result = self._data.get("token_prices", {})
        return dict(result) if isinstance(result, dict) else {}

    def get_models(self) -> list[str]:
        """配置的模型列表（模型档案 ``model_profiles`` 的模型名）。

        RC 顶层 ``models`` 字段已移除——本方法语义 = 模型档案模型名，
        与 ``config.model_profiles.configured_models`` 一致。
        """
        from ...config.model_profiles import configured_models

        try:
            return configured_models(rc=self._data)
        except Exception:
            return []

    def get_reasoning_effort(self) -> str:
        return str(self._data.get("reasoning_effort", "max"))

    def get_temperature(self) -> float:
        return float(self._data.get("temperature", 0.2))

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
        return int(self._data.get("max_context_chars", DEFAULTS["max_context_chars"]))

    def get_max_context_tokens(self) -> int:
        return int(self._data.get("max_context_tokens", DEFAULTS["max_context_tokens"]))

    def get_model_context_tokens(self) -> int:
        return int(self._data.get("model_context_tokens", DEFAULTS["model_context_tokens"]))

    def get_max_session_messages(self) -> int:
        return int(self._data.get("max_session_messages", 0))

    def get_keep_recent_messages(self) -> int:
        return int(self._data.get("keep_recent_messages", 0))

    def get_auto_force_compress_threshold(self) -> int:
        """自动全量压缩阈值（token 口径，默认 400k）。"""
        return int(self._data.get(
            "auto_force_compress_threshold", DEFAULTS["auto_force_compress_threshold"],
        ))

    def get_summary_token_budget(self) -> int:
        return int(self._data.get("summary_token_budget", 2000))

    def get_compaction_config(self) -> dict:
        raw = self._data.get("compaction")
        return dict(raw) if isinstance(raw, dict) else {}

    # ── 并行执行配置 ──────────────────────────────────

    def get_stagger_min_delay(self) -> float:
        return float(self._data.get("stagger_min_delay", 0.1))

    def get_stagger_max_delay(self) -> float:
        return float(self._data.get("stagger_max_delay", 0.5))
