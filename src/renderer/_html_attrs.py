"""_html_attrs — HTML 标签行/属性解析（解析层与渲染层共享，无正则）。

渲染路径需要从 HTML 标签行中取属性（``<div align="center">``、
``<progress value="70" max="100">``、``<pre class="language-python">``…）。
解析集中在本模块，避免解析器与 ANSI 渲染层各写一份属性扫描。
"""

from __future__ import annotations

from ._utils import decode_html_entities

#: 无值属性（布尔属性）集合——存在即为真（渲染层可用 ``bool_attr`` 判真值）
BOOL_ATTRS: frozenset = frozenset({
    "checked", "disabled", "selected", "readonly", "required",
    "multiple", "open", "hidden", "autofocus", "loop", "controls",
    "autoplay", "muted", "default", "novalidate", "reversed", "compact",
})

#: 原始文本标签（内容不按 Markdown 解析、渲染层以占位说明呈现）
RAW_TEXT_TAGS: frozenset = frozenset({
    "script", "style", "template", "canvas", "noscript", "svg", "math",
})


def parse_tag_name(line: str) -> str:
    """标签行 → 小写标签名（非标签行返回空串）。"""
    s = (line or "").lstrip()
    if len(s) < 2 or s[0] != "<":
        return ""
    i = 1
    if s[1] == "/":
        i = 2
    start = i
    while i < len(s) and (s[i].isalnum() or s[i] in "-:_"):
        i += 1
    return s[start:i].lower()


def parse_attrs(text: str) -> dict[str, str]:
    """解析标签属性文本 → ``{名: 值}``（无值属性值为空串；名称小写）。

    ``text`` 可含标签外壳（``<div align=center>``），函数只扫描属性区；
    引号内的 ``>`` 不会提前结束（与 HTML 词法一致）。值中的 HTML 实体解码。
    """
    attrs: dict[str, str] = {}
    s = text or ""
    n = len(s)
    i = 0
    # 跳过 "<" + 标签名（含可选 "/"）
    if i < n and s[i] == "<":
        i += 1
        if i < n and s[i] == "/":
            i += 1
        while i < n and (s[i].isalnum() or s[i] in "-:_"):
            i += 1
    while i < n:
        ch = s[i]
        if ch == ">":
            break
        if ch == "/" and i + 1 < n and s[i + 1] == ">":
            break
        if ch.isspace():
            i += 1
            continue
        start = i
        while i < n and not s[i].isspace() and s[i] not in "=/>":
            i += 1
        name = s[start:i].lower()
        if not name:
            i += 1
            continue
        # 跳过空白后判断是否有 "=值"
        j = i
        while j < n and s[j].isspace():
            j += 1
        if j >= n or s[j] != "=":
            attrs.setdefault(name, "")
            i = j
            continue
        j += 1
        while j < n and s[j].isspace():
            j += 1
        if j < n and s[j] in "\"'":
            quote = s[j]
            end = s.find(quote, j + 1)
            if end < 0:
                end = n - 1
            value = s[j + 1:end]
            i = end + 1
        else:
            end = j
            while end < n and not s[end].isspace() and s[end] != ">":
                end += 1
            value = s[j:end]
            i = end
        attrs[name] = decode_html_entities(value)
    return attrs


def parse_open_tag(line: str) -> tuple[str, dict[str, str]]:
    """标签行 → ``(标签名, 属性表)``（非标签行返回 ``("", {})``）。"""
    tag = parse_tag_name(line)
    if not tag:
        return "", {}
    return tag, parse_attrs(line)


def attr(attrs: dict[str, str], name: str, default: str = "") -> str:
    """取属性值（缺失或空串时返回 ``default``）。"""
    value = attrs.get(name)
    if value is None or value == "":
        return default
    return value


def has_attr(attrs: dict[str, str], name: str) -> bool:
    """属性是否存在（布尔属性存在即真）。"""
    return name.lower() in attrs


def align_of(attrs: dict[str, str]) -> str:
    """对齐方式（``left`` / ``center`` / ``right`` / ``justify``）。

    取 ``align`` 属性或 ``style`` 中的 ``text-align``；未知值返回空串。
    """
    value = attr(attrs, "align").strip().lower()
    if not value:
        value = _style_value(attrs, "text-align")
    if value in ("left", "center", "right", "justify"):
        return value
    if value in ("middle", "centre"):
        return "center"
    return ""


def _style_value(attrs: dict[str, str], prop: str) -> str:
    """从 ``style="a:b;c:d"`` 中取指定属性值（小写；无则空串）。"""
    style = attrs.get("style", "")
    if not style:
        return ""
    for decl in style.split(";"):
        if ":" not in decl:
            continue
        name, _, value = decl.partition(":")
        if name.strip().lower() == prop:
            return value.strip().lower()
    return ""


def style_value(attrs: dict[str, str], prop: str) -> str:
    """取 ``style`` 中的属性值（渲染层复用；无则空串）。"""
    return _style_value(attrs, prop)


def language_of(attrs: dict[str, str]) -> str:
    """代码语言（``class="language-python"`` / ``lang="py"`` / ``data-lang``）。

    支持 ``class="hljs language-py"``、``class="lang-py"``、
    ``class="brush: ruby"`` 等常见写法。
    """
    value = attr(attrs, "data-lang") or attr(attrs, "lang")
    if value:
        return value.strip()
    classes = attrs.get("class", "")
    tokens = [t.strip() for t in classes.replace("\t", " ").split(" ")]
    for i, token in enumerate(tokens):
        if not token:
            continue
        low = token.lower()
        for prefix in ("language-", "lang-", "highlight-"):
            if low.startswith(prefix) and len(low) > len(prefix):
                return token[len(prefix):]
        if low == "brush:":
            # ``class="brush: ruby"``：语言写在下一个词
            for nxt in tokens[i + 1:]:
                if nxt:
                    return nxt
    return ""


def number_of(attrs: dict[str, str], name: str) -> float | None:
    """取数值属性（``value="70.5"`` → 70.5；缺失/非法返回 ``None``）。"""
    raw = attrs.get(name)
    if raw is None or raw == "":
        return None
    text = raw.strip().rstrip("%")
    try:
        return float(text)
    except ValueError:
        return None


def bool_attr(attrs: dict[str, str], name: str) -> bool:
    """布尔属性真值（``checked`` 存在即真；``checked="checked"`` 也为真）。"""
    if name not in attrs:
        return False
    value = (attrs.get(name) or "").strip().lower()
    return value not in ("false", "0", "no")


__all__ = [
    "BOOL_ATTRS", "RAW_TEXT_TAGS", "parse_tag_name",
    "parse_attrs", "parse_open_tag", "attr", "has_attr", "align_of",
    "style_value", "language_of", "number_of", "bool_attr",
]
