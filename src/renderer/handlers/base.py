"""Handler 基类和注册表"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any
from ..types import Token, TokenType

_logger = logging.getLogger(__name__)


class TokenHandler(ABC):
    """Token handler 基类

    每个 handler 负责 1~N 个相关 TokenType 的渲染。
    """

    @abstractmethod
    def get_token_types(self) -> set[TokenType]:
        """返回此 handler 负责的 TokenType 集合"""
        ...

    def get_method_map(self) -> dict[TokenType, callable]:
        """子类覆写：返回 TokenType → handler 方法 的映射表"""
        return {}

    def handle(self, token: Token, engine: Any) -> None:
        """渲染单个 Token（基类默认实现 — 按 get_method_map() 分发）

        Args:
            token: 要渲染的 Token
            engine: RenderEngine 实例引用（用于访问 output/_render_inline 等）
        """
        method_map = self.get_method_map()
        handler = method_map.get(token.type)
        if handler:
            handler(token, engine)
        else:
            _logger.warning("%s 未处理 TokenType: %s", type(self).__name__, token.type.name)


class HandlerRegistry:
    """Handler 注册表 — 管理 TokenType → Handler 的映射"""

    def __init__(self, strict: bool = False):
        self._handlers: dict[TokenType, TokenHandler] = {}
        self._strict: bool = strict

    def register(self, handler: TokenHandler) -> None:
        """注册一个 handler"""
        for token_type in handler.get_token_types():
            existing = self._handlers.get(token_type)
            if existing is not None and existing is not handler:
                _logger.warning(
                    "Handler 冲突: TokenType %s 已被 %s 注册，现被 %s 覆盖",
                    token_type.name,
                    type(existing).__name__,
                    type(handler).__name__,
                )
                if self._strict:
                    raise ValueError(
                        f"Handler 冲突: TokenType {token_type.name} "
                        f"已被 {type(existing).__name__} 注册，不允许覆盖"
                    )
            self._handlers[token_type] = handler

    def get(self, token_type: TokenType) -> TokenHandler | None:
        """获取处理指定 TokenType 的 handler"""
        return self._handlers.get(token_type)
