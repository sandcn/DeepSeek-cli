"""Token 估算端口 — TokensPort

核心层通过此接口估算文本 token 数，
不直接依赖 src/api/tokens。
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class TokensPort(ABC):
    """抽象 Token 估算端口"""

    @abstractmethod
    def estimate_tokens(self, text: str) -> int:
        """估算文本 token 数"""
        ...


class DefaultTokensAdapter(TokensPort):
    """默认 Token 估算适配器 — 包装 src/api/tokens.estimate_tokens"""

    def estimate_tokens(self, text: str) -> int:
        from ...api.tokens import estimate_tokens
        return estimate_tokens(text)
