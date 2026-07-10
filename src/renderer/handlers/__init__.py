"""Handler 注册表 — 统一注册和调度"""
from .base import HandlerRegistry, TokenHandler
from ._box_base import BaseBoxMixin
from .inline import InlineHandler
from .code import CodeHandler
from .math import MathHandler
from .mermaid import MermaidHandler
from .details import DetailsHandler
from .admonition import AdmonitionHandler
from .html_block import HtmlBlockHandler
from .fenced_div import FencedDivHandler
from .table import TableHandler

__all__ = [
    "HandlerRegistry",
    "TokenHandler",
    "BaseBoxMixin",
    "InlineHandler", "CodeHandler", "MathHandler",
    "MermaidHandler", "DetailsHandler", "AdmonitionHandler",
    "HtmlBlockHandler", "FencedDivHandler", "TableHandler",
]
