"""语法高亮语言注册表 — CodeBlock 高亮语言的单一来源（一切皆插件）。

「一切皆插件」：``_syntax`` 的内置语言（关键字表 / 别名 / 行注释前缀）不再是
硬编码字典，而是注册到本模块的规格表；每个语言由清单中的**独立插件条目**
（``syntax_language``）显式注册，因而可被 Profile/Bundle 声明，也可被
Patch/Overlay 按 id 单独禁用、覆盖（改关键字/注释/别名）或替换，或由外部
插件新增语言。

**清单接管**：``syntax`` 聚合插件收到组合根注入的 ``managed_syntax_languages``
（清单已接管的 id，含被禁用的）时经 ``set_managed_builtin_languages`` 声明
这些 id 由清单条目负责——对应内置项不再走默认装配；被禁用（未挂载）的条目
因此真正缺席。无清单（单元测试、独立调用）时无接管，全部内置项默认生效。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

_lock = threading.RLock()
_ABSENT = object()


@dataclass(frozen=True)
class SyntaxLanguage:
    """一个高亮语言规格。"""

    id: str
    keywords: Tuple[str, ...] = ()
    aliases: Tuple[str, ...] = ()
    line_comment: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "keywords": sorted(self.keywords),
            "aliases": list(self.aliases),
            "line_comment": self.line_comment,
        }


def _lang(lang_id: str, keywords, aliases=(), line_comment=None) -> SyntaxLanguage:
    return SyntaxLanguage(
        id=lang_id,
        keywords=tuple(keywords),
        aliases=tuple(aliases),
        line_comment=line_comment,
    )


_PY = {
    "and", "as", "assert", "async", "await", "break", "class", "continue",
    "def", "del", "elif", "else", "except", "finally", "for", "from",
    "global", "if", "import", "in", "is", "lambda", "nonlocal", "not",
    "or", "pass", "raise", "return", "try", "while", "with", "yield",
    "True", "False", "None", "self", "cls",
}
_JS = {
    "var", "let", "const", "function", "return", "if", "else", "for",
    "while", "do", "switch", "case", "break", "continue", "new", "this",
    "class", "extends", "super", "import", "export", "default", "from",
    "async", "await", "try", "catch", "finally", "throw", "typeof",
    "instanceof", "delete", "in", "of", "null", "undefined", "true",
    "false", "yield", "static", "get", "set",
}
_TS = {
    "var", "let", "const", "function", "return", "if", "else", "for",
    "while", "switch", "case", "break", "continue", "new", "this",
    "class", "extends", "implements", "interface", "type", "enum",
    "import", "export", "default", "from", "async", "await", "try",
    "catch", "finally", "throw", "typeof", "instanceof", "in", "of",
    "null", "undefined", "true", "false", "public", "private",
    "protected", "readonly", "namespace", "declare", "as",
}
_GO = {
    "break", "case", "chan", "const", "continue", "default", "defer",
    "else", "fallthrough", "for", "func", "go", "goto", "if", "import",
    "interface", "map", "package", "range", "return", "select", "struct",
    "switch", "type", "var", "nil", "true", "false",
}
_RUST = {
    "as", "async", "await", "break", "const", "continue", "crate", "dyn",
    "else", "enum", "extern", "false", "fn", "for", "if", "impl", "in",
    "let", "loop", "match", "mod", "move", "mut", "pub", "ref", "return",
    "self", "struct", "super", "trait", "true", "type", "unsafe", "use",
    "where", "while", "Some", "None", "Ok", "Err",
}
_JAVA = {
    "abstract", "assert", "boolean", "break", "byte", "case", "catch",
    "char", "class", "const", "continue", "default", "do", "double",
    "else", "enum", "extends", "final", "finally", "float", "for", "if",
    "implements", "import", "instanceof", "int", "interface", "long",
    "native", "new", "package", "private", "protected", "public",
    "return", "short", "static", "super", "switch", "synchronized",
    "this", "throw", "throws", "transient", "try", "void", "volatile",
    "while", "true", "false", "null",
}
_C = {
    "auto", "break", "case", "char", "const", "continue", "default",
    "do", "double", "else", "enum", "extern", "float", "for", "goto",
    "if", "int", "long", "register", "return", "short", "signed",
    "sizeof", "static", "struct", "switch", "typedef", "union",
    "unsigned", "void", "volatile", "while", "NULL",
}
_CPP = {
    "auto", "break", "case", "catch", "class", "const", "constexpr",
    "continue", "default", "delete", "do", "double", "else", "enum",
    "explicit", "extern", "false", "float", "for", "friend", "if",
    "inline", "int", "long", "namespace", "new", "nullptr", "operator",
    "override", "private", "protected", "public", "return", "short",
    "signed", "sizeof", "static", "struct", "switch", "template",
    "this", "throw", "true", "try", "typedef", "typename", "union",
    "unsigned", "using", "virtual", "void", "volatile", "while",
}
_RUBY = {
    "alias", "and", "begin", "break", "case", "class", "def", "do",
    "else", "elsif", "end", "ensure", "false", "for", "if", "in", "module",
    "next", "nil", "not", "or", "redo", "rescue", "retry", "return",
    "self", "super", "then", "true", "undef", "unless", "until", "when",
    "while", "yield", "require",
}
_SHELL = {
    "if", "then", "else", "elif", "fi", "for", "while", "do", "done",
    "case", "esac", "function", "return", "export", "local", "echo",
    "cd", "set", "unset", "source", "in", "select",
}
_SQL = {
    "select", "from", "where", "insert", "into", "values", "update",
    "set", "delete", "create", "table", "drop", "alter", "join", "left",
    "right", "inner", "outer", "on", "group", "by", "order", "having",
    "limit", "offset", "union", "all", "distinct", "as", "and", "or",
    "not", "null", "primary", "key", "foreign", "references", "index",
}
_YAML = {"true", "false", "null", "yes", "no", "on", "off"}
_JSON = {"true", "false", "null"}
_CSS = {"important", "media", "import", "keyframes", "from", "to"}

#: 内置语言声明（声明顺序即展示顺序）
_BUILTIN_SPECS: Tuple[SyntaxLanguage, ...] = (
    _lang("python", _PY, ("py", "python3"), "#"),
    _lang("javascript", _JS, ("js", "jsx", "node"), "//"),
    _lang("typescript", _TS, ("ts", "tsx"), "//"),
    _lang("go", _GO, ("golang",), "//"),
    _lang("rust", _RUST, ("rs",), "//"),
    _lang("java", _JAVA, (), "//"),
    _lang("c", _C, (), "//"),
    _lang("cpp", _CPP, ("c++",), "//"),
    _lang("ruby", _RUBY, (), "#"),
    _lang("shell", _SHELL, ("sh", "bash", "zsh", "console"), "#"),
    _lang("sql", _SQL, (), "--"),
    _lang("yaml", _YAML, ("yml",), "#"),
    _lang("json", _JSON, (), None),
    _lang("css", _CSS, (), None),
    _lang("html", set(), ("htm",), None),
)

#: 别名（不构成独立语言）→ 规范语言 id；``csharp`` 无关键字表（沿用旧行为）
_EXTRA_ALIASES: Dict[str, str] = {"cs": "csharp"}

_builtin_specs: Dict[str, SyntaxLanguage] = {spec.id: spec for spec in _BUILTIN_SPECS}

_registered_builtin: Dict[str, SyntaxLanguage] = {}
_managed_builtin: set = set()
_disabled_builtin: set = set()
_extension: Dict[str, SyntaxLanguage] = {}

_cache: Optional[dict] = None


def _normalize_ids(ids) -> List[str]:
    if isinstance(ids, str):
        ids = [ids]
    selected: List[str] = []
    for item in ids or ():
        if item not in _builtin_specs:
            raise KeyError(f"未知内置语言: {item!r}（可用: {list(_builtin_specs)}）")
        selected.append(item)
    return selected


def builtin_language_ids() -> list[str]:
    """全部内置语言 id（按声明顺序）。"""
    return list(_builtin_specs)


def default_language(lang_id: str) -> SyntaxLanguage:
    try:
        return _builtin_specs[lang_id]
    except KeyError:
        raise KeyError(f"未知内置语言: {lang_id!r}（可用: {list(_builtin_specs)}）") from None


def active_languages() -> Dict[str, SyntaxLanguage]:
    """当前生效的内置语言（``id → 规格``；按声明顺序）。"""
    with _lock:
        result: Dict[str, SyntaxLanguage] = {}
        for lang_id, default in _builtin_specs.items():
            if lang_id in _disabled_builtin:
                continue
            override = _registered_builtin.get(lang_id)
            if override is not None:
                result[lang_id] = override
                continue
            if lang_id in _managed_builtin:
                continue
            result[lang_id] = default
        return result


def _invalidate() -> None:
    global _cache
    _cache = None


def register_builtin_language(lang_id: str, spec: Optional[SyntaxLanguage] = None) -> Callable[[], None]:
    """注册/覆盖一个内置语言（``spec=None`` 用默认规格）；返回幂等撤销。"""
    if lang_id not in _builtin_specs:
        raise KeyError(f"未知内置语言: {lang_id!r}（可用: {list(_builtin_specs)}）")
    with _lock:
        previous = _registered_builtin.get(lang_id, _ABSENT)
        _registered_builtin[lang_id] = spec if spec is not None else _builtin_specs[lang_id]
        _invalidate()

    def _undo() -> None:
        with _lock:
            if previous is _ABSENT:
                _registered_builtin.pop(lang_id, None)
            else:
                _registered_builtin[lang_id] = previous
            _invalidate()

    return _undo


def unregister_builtin_language(lang_id: str) -> bool:
    with _lock:
        removed = _registered_builtin.pop(lang_id, None) is not None
        if removed:
            _invalidate()
    return removed


def set_managed_builtin_languages(ids) -> Callable[[], None]:
    """声明这些内置语言 id 由清单条目负责（默认装配被抑制）；返回撤销。"""
    selected = _normalize_ids(ids)
    with _lock:
        added = [item for item in selected if item not in _managed_builtin]
        _managed_builtin.update(added)
        _invalidate()

    def _undo() -> None:
        with _lock:
            for item in added:
                _managed_builtin.discard(item)
            _invalidate()

    return _undo


def managed_language_ids() -> list[str]:
    with _lock:
        return sorted(_managed_builtin)


def disable_builtin_languages(ids) -> Callable[[], None]:
    """禁用一个或多个内置语言（返回幂等撤销）。"""
    selected = _normalize_ids(ids)
    with _lock:
        added = [item for item in selected if item not in _disabled_builtin]
        _disabled_builtin.update(added)
        _invalidate()

    def _undo() -> None:
        with _lock:
            for item in added:
                _disabled_builtin.discard(item)
            _invalidate()

    return _undo


def register_language(spec: SyntaxLanguage) -> Callable[[], None]:
    """注册一个扩展语言（id 覆盖内置 / 新增）；返回幂等撤销。"""
    if not isinstance(spec, SyntaxLanguage):
        raise TypeError(f"扩展语言必须是 SyntaxLanguage: {spec!r}")
    with _lock:
        previous = _extension.get(spec.id, _ABSENT)
        _extension[spec.id] = spec
        _invalidate()

    def _undo() -> None:
        with _lock:
            if previous is _ABSENT:
                _extension.pop(spec.id, None)
            else:
                _extension[spec.id] = previous
            _invalidate()

    return _undo


def unregister_language(lang_id: str) -> bool:
    with _lock:
        removed = _extension.pop(lang_id, None) is not None
        if removed:
            _invalidate()
    return removed


def extension_languages() -> Dict[str, SyntaxLanguage]:
    with _lock:
        return dict(_extension)


def _active_maps() -> dict:
    global _cache
    with _lock:
        if _cache is not None:
            return _cache
        keywords: Dict[str, set] = {}
        comments: Dict[str, Optional[str]] = {}
        aliases: Dict[str, str] = dict(_EXTRA_ALIASES)

        def _put(spec: SyntaxLanguage) -> None:
            keywords[spec.id] = set(spec.keywords)
            comments[spec.id] = spec.line_comment
            for alias in spec.aliases:
                aliases.setdefault(alias, spec.id)

        for spec in active_languages().values():
            _put(spec)
        for spec in _extension.values():
            _put(spec)
        _cache = {"keywords": keywords, "comments": comments, "aliases": aliases}
        return _cache


def supported_language_ids() -> list:
    """当前生效（有高亮支持）的语言 id。"""
    return sorted(_active_maps()["keywords"])


def normalize_language(language) -> str:
    """语言名归一化（别名 → 规范名；未知 → 小写原名）。"""
    if not language:
        return ""
    name = str(language).strip().lower()
    return _active_maps()["aliases"].get(name, name)


def language_keywords(lang_id: str) -> set:
    return set(_active_maps()["keywords"].get(lang_id, set()))


def language_line_comment(lang_id: str) -> Optional[str]:
    return _active_maps()["comments"].get(lang_id)


def clear() -> None:
    """清空扩展项与清单注册（测试用；不影响内置默认与禁用状态）。"""
    with _lock:
        _extension.clear()
        _registered_builtin.clear()
        _invalidate()


def reset() -> None:
    """重置全部状态到「无清单、无禁用、全部默认」（测试隔离用）。"""
    with _lock:
        _extension.clear()
        _registered_builtin.clear()
        _managed_builtin.clear()
        _disabled_builtin.clear()
        _invalidate()


__all__ = [
    "SyntaxLanguage",
    "builtin_language_ids",
    "default_language",
    "active_languages",
    "register_builtin_language",
    "unregister_builtin_language",
    "set_managed_builtin_languages",
    "managed_language_ids",
    "disable_builtin_languages",
    "register_language",
    "unregister_language",
    "extension_languages",
    "supported_language_ids",
    "normalize_language",
    "language_keywords",
    "language_line_comment",
    "clear",
    "reset",
]
