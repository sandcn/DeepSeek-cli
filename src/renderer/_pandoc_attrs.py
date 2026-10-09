"""_pandoc_attrs — Pandoc 属性语法（``{.class #id key=value}``）的共享解析。

单一真源：行内属性 span ``[文本]{.class #id}``（``_inline_links``）与块级
fenced div ``::: {.warning #id}``（``_block_parser``）共用同一套 token 切分与
属性归类逻辑，避免两处各写一份、行为漂移。

属性体（不含花括号）形如 ``.red #anchor lang=python title="a b"``：
  - ``.xxx``  → 类名（``classes`` 列表，去重保持顺序）；
  - ``#xxx``  → 锚点 id（``id``，后者覆盖前者，与 Pandoc 语义一致）；
  - ``k=v``   → 键值对（``attrs``，键小写、值去引号）；
  - 其它 token（如 ``color:red``）→ 视为**非本语法**（返回 ``None``，调用方
    回退既有花括号语义，如 ``{color:red}`` 着色文本）。

本模块无 ANSI/Rich 依赖，可被解析层与渲染层安全导入。
"""

from __future__ import annotations

#: 属性体最大长度（防御超长输入；超过即判定为非本语法）。
_MAX_ATTR_BODY = 512


def split_attr_tokens(body: str) -> list[str]:
    """属性体按空白分隔 token（引号内的空白不作为分隔符）。

    供 ``{.class #id key="a b"}`` 解析使用：``title="a b"`` 保持为单个 token。
    """
    tokens: list[str] = []
    buf: list[str] = []
    quote = ''
    for ch in body:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = ''
            continue
        if ch in '"\'':
            quote = ch
            buf.append(ch)
            continue
        if ch.isspace():
            if buf:
                tokens.append(''.join(buf))
                buf = []
            continue
        buf.append(ch)
    if buf:
        tokens.append(''.join(buf))
    return tokens


def parse_pandoc_attrs(body: str) -> dict | None:
    """解析 Pandoc 属性体（``{`` 与 ``}`` 之间的内容）。

    Args:
        body: 花括号内部文本（不含 ``{`` / ``}``）。

    Returns:
        ``{"classes": [...], "id": str, "attrs": {...}}``；体为空、超长或含
        非属性 token（如 ``color:red``）时返回 ``None``（调用方回退其它语义）。
    """
    if not body or len(body) > _MAX_ATTR_BODY:
        return None
    if not body.strip():
        return None
    classes: list[str] = []
    ident = ''
    attrs: dict = {}
    for token in split_attr_tokens(body):
        if not token:
            continue
        if token.startswith('.'):
            cls = token[1:]
            if cls and cls not in classes:
                classes.append(cls)
        elif token.startswith('#'):
            ident = token[1:]
        elif '=' in token:
            key, _, value = token.partition('=')
            key = key.strip().lower()
            value = value.strip().strip('"\'')
            if key:
                attrs[key] = value
        else:
            # 非属性 token（如 ``color:red``）→ 非本语法
            return None
    if not (classes or ident or attrs):
        return None
    return {"classes": classes, "id": ident, "attrs": attrs}


def parse_braced_attrs(text: str) -> tuple[dict | None, int]:
    """解析以 ``{`` 开头的完整属性块，返回 ``(attrs, 消耗字符数)``。

    ``text`` 为从 ``{`` 起的字符串；成功时 ``消耗字符数`` 为闭合 ``}`` 之后的
    长度（``len("{...}")``）；失败返回 ``(None, 0)``（调用方不前进位置）。

    供块级解析（fenced div 标题行）使用；行内解析器直接用位置版逻辑。
    """
    if not text or text[0] != '{':
        return None, 0
    end = text.find('}')
    if end < 0:
        return None, 0
    attrs = parse_pandoc_attrs(text[1:end])
    if attrs is None:
        return None, 0
    return attrs, end + 1


def pandoc_type_from_attrs(attrs: dict | None) -> str:
    """从属性块取「类型名」（首个类名，大写；无则空串）。

    用于 fenced div：``::: {.warning #id}`` → ``"WARNING"``（与 ``::: warning``
    同语义）；``::: {#id}``（无类名）→ 空串（调用方回落默认类型）。
    """
    if not attrs:
        return ''
    classes = attrs.get("classes") or []
    return str(classes[0]).upper() if classes else ''


__all__ = [
    "split_attr_tokens",
    "parse_pandoc_attrs",
    "parse_braced_attrs",
    "pandoc_type_from_attrs",
]
