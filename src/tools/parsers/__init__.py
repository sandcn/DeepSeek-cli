"""搜索解析器模块——为各搜索引擎提供 HTML → 结构化结果 的解析能力。

每个解析器实现 BaseParser 抽象类，提供 _parse_soup（BS4）和 _parse_regex（正则回退）双路径。
"""

from .base import BaseParser
from .baidu import BaiduParser, parse as baidu_parse
from .bing import BingParser, parse as bing_parse
from .generic import parse as generic_parse
from .github import GitHubParser, parse as github_parse

__all__ = [
    "BaseParser",
    "BaiduParser", "baidu_parse",
    "BingParser", "bing_parse",
    "generic_parse",
    "GitHubParser", "github_parse",
]
