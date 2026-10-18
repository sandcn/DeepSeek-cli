"""Pygments 词法分析器缓存（渲染层共享，无第三方渲染依赖）。

ANSI 渲染路径（``src.renderer.ansi``）与其它代码高亮消费方统一调用
``get_lexer``，避免各自维护缓存。仅依赖 Pygments。
"""

from __future__ import annotations

import logging
import threading

_logger = logging.getLogger(__name__)

_LEXER_CACHE: dict[str, object] = {}
"""全局词法分析器缓存。键为语言名，值为 Pygments Lexer 实例。"""

_lexer_lock = threading.Lock()
"""保护 ``_LEXER_CACHE`` 写入的锁（读取无需持锁，Python GIL 保证 dict 基本操作安全）。"""


def get_lexer(lang: str) -> object:
    """获取/缓存 Pygments 词法分析器。

    Args:
        lang: 语言名称。

    Returns:
        Pygments Lexer 实例。失败时返回 None（失败结果一并缓存，避免重复重试）。
    """
    from pygments.lexers import get_lexer_by_name

    if lang not in _LEXER_CACHE:
        with _lexer_lock:
            # 双重检查：锁内再次判断，防止并发重复创建
            if lang not in _LEXER_CACHE:
                try:
                    _LEXER_CACHE[lang] = get_lexer_by_name(lang, stripnl=False)
                except Exception:
                    # 失败结果一并缓存（None）——避免未知语言在流式预览下
                    # 每帧重试并打印 warning。
                    _LEXER_CACHE[lang] = None
                    _logger.debug("词法分析器获取失败: lang=%s", lang, exc_info=True)
    return _LEXER_CACHE[lang]


__all__ = ["get_lexer"]
