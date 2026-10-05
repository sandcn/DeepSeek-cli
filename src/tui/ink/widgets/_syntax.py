"""_syntax — CodeBlock 轻量语法高亮（多语言，无第三方依赖）。

为 ``CodeBlock`` 控件提供内置高亮：按语言的关键字/字符串/注释/数字切分并
着色（256 色号），输出 ``StyledRun`` 列表序列（每行一段）。

「一切皆插件」：语言表（关键字/别名/行注释）的真源下沉到
``src.tui.ink.widgets._syntax_registry`` 注册表——每个语言一个清单插件条目
（``syntax_language``），可被 Patch/Overlay 覆盖/禁用/替换或外部插件新增。
本模块仅保留分词算法与颜色槽，语言数据经注册表实时查询。

设计取舍：本项目聊天渲染的 Markdown 代码块已由 Pygments 高亮；本模块只服务
TUI 控件库内的 ``CodeBlock``（无 Pygments 依赖、纯内置、可预测），并覆盖
更多语言的关键字表。
"""

from __future__ import annotations

from typing import Iterable

from src.tui.core.style import Style
from src.tui.ink.output import StyledRun

from ._syntax_registry import (
    language_keywords,
    language_line_comment,
    normalize_language,
    supported_language_ids,
)

__all__ = [
    "SUPPORTED_LANGUAGES",
    "highlight_lines",
    "tokenize_line",
    "normalize_language",
    "is_supported",
    "plain_runs",
]

#: 颜色槽（256 色号）
_C_KEYWORD = Style(fg=175, bold=True)
_C_STRING = Style(fg=114)
_C_COMMENT = Style(fg=242, italic=True)
_C_NUMBER = Style(fg=180)
_C_PLAIN = None

#: 内置语言快照（兼容 re-export；不随 overlay 变化）
SUPPORTED_LANGUAGES = tuple(supported_language_ids())

#: 字符串引号
_QUOTES = "'\"`"

_IDENT_START = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_")
_IDENT_BODY = _IDENT_START | set("0123456789")
_DIGITS = set("0123456789")


def _tokenize_line(line: str, keywords: set, comment: str | None) -> list[StyledRun]:
    """把单行代码切分为 StyledRun 序列（字符串/注释/数字/关键字/普通）。"""
    runs: list[StyledRun] = []
    buf: list[str] = []

    def _flush():
        if buf:
            runs.append(StyledRun("".join(buf), _C_PLAIN))
            buf.clear()

    i = 0
    n = len(line)
    while i < n:
        ch = line[i]
        # 行注释
        if comment and line.startswith(comment, i):
            _flush()
            runs.append(StyledRun(line[i:], _C_COMMENT))
            return runs
        # 字符串
        if ch in _QUOTES:
            _flush()
            j = i + 1
            while j < n:
                if line[j] == "\\":
                    j += 2
                    continue
                if line[j] == ch:
                    j += 1
                    break
                j += 1
            runs.append(StyledRun(line[i:j], _C_STRING))
            i = j
            continue
        # 数字
        if ch in _DIGITS:
            _flush()
            j = i
            while j < n and (line[j] in _DIGITS or line[j] == "."):
                j += 1
            runs.append(StyledRun(line[i:j], _C_NUMBER))
            i = j
            continue
        # 标识符/关键字
        if ch in _IDENT_START:
            j = i
            while j < n and line[j] in _IDENT_BODY:
                j += 1
            word = line[i:j]
            if word in keywords:
                _flush()
                runs.append(StyledRun(word, _C_KEYWORD))
            else:
                buf.append(word)
            i = j
            continue
        # 普通字符
        buf.append(ch)
        i += 1
    _flush()
    return runs or [StyledRun("", None)]


def highlight_lines(code: str, language: str) -> list[list[StyledRun]]:
    """按语言高亮整段代码，返回每行的 StyledRun 列表。"""
    lang = normalize_language(language)
    keywords = language_keywords(lang)
    comment = language_line_comment(lang)
    lines = code.split("\n") if code else [""]
    return [_tokenize_line(line, keywords, comment) for line in lines]


def tokenize_line(line: str, language: str) -> list[StyledRun]:
    """按语言高亮单行（CodeBlock 逐行渲染使用）。"""
    lang = normalize_language(language)
    return _tokenize_line(line, language_keywords(lang), language_line_comment(lang))


def is_supported(language: str) -> bool:
    """是否内置了该语言的关键字表。"""
    return normalize_language(language) in set(supported_language_ids())


def plain_runs(lines: Iterable[str]) -> list[list[StyledRun]]:
    """无高亮模式：逐行返回单 run。"""
    return [[StyledRun(str(line), None)] for line in lines]
