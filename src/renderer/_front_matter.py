"""_front_matter — Front Matter（文档头元信息块）解析与共享常量。

两条渲染路径（Rich / ANSI）共用：仅做纯文本解析，不依赖任何渲染库。
支持 YAML（``---``）、TOML（``+++``）、JSON（``{``）三种格式。
"""

from __future__ import annotations

#: 起始定界符 → 格式名
FRONT_MATTER_DELIMS: dict[str, str] = {
    '---': 'yaml',
    '+++': 'toml',
    '{': 'json',
}

#: 支持的格式名集合
FRONT_MATTER_FORMATS: frozenset[str] = frozenset({'yaml', 'toml', 'json'})


def front_matter_delim(stripped: str) -> str | None:
    """判断行是否为 Front Matter 起始定界符（是则返回定界符，否则 None）。"""
    return stripped if stripped in FRONT_MATTER_DELIMS else None


def front_matter_format(delim: str) -> str:
    """定界符 → 格式名（未知时回退 ``yaml``）。"""
    return FRONT_MATTER_DELIMS.get(delim, 'yaml')


def is_front_matter_close(stripped: str, delim: str) -> bool:
    """判断行是否为 Front Matter 结束行。

    YAML 支持 ``---`` 与 ``...`` 两种结束；TOML 为 ``+++``；JSON 起始 ``{``
    以 ``}`` 结束。
    """
    if delim == '---':
        return stripped == '---' or stripped == '...'
    if delim == '{':
        return stripped == '}'
    return stripped == delim


def fm_split(line: str, fmt: str) -> tuple[str, str]:
    """Front Matter 单行 → ``(key, value)``（无分隔符时 key 为空）。"""
    if fmt == 'toml':
        idx = line.find('=')
        if idx > 0:
            return line[:idx].strip(), line[idx + 1:].strip()
        return line.strip(), ''
    idx = line.find(':')
    if idx > 0:
        return line[:idx].strip(), line[idx + 1:].strip()
    return line.strip(), ''


def fm_value_text(value) -> str:
    """JSON 值 → 单行文本（容器用紧凑 JSON 表示）。"""
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if value is None:
        return 'null'
    if isinstance(value, (int, float)):
        return str(value)
    try:
        import json
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return str(value)


def parse_front_matter_items(text: str, fmt: str) -> list[tuple[str, str]]:
    """解析 Front Matter 文本 → ``[(key, value), ...]``。

    YAML ``key: value`` / TOML ``key = value`` / JSON 对象；``#`` 注释忽略；
    ``- item`` 续行并入上一项的值（以 ``· `` 连接）。解析失败时返回空列表
    （调用方回退为整段原样文本渲染）。
    """
    if fmt == 'json':
        try:
            import json
            data = json.loads(text)
        except Exception:
            return []
        if isinstance(data, dict):
            return [(str(k), fm_value_text(v)) for k, v in data.items()]
        return [('', fm_value_text(data))]

    items: list[tuple[str, list[str]]] = []
    for raw in (text or '').split('\n'):
        line = raw.rstrip()
        if not line.strip():
            continue
        stripped = line.lstrip()
        if stripped.startswith('#'):
            continue
        if stripped.startswith('- ') and items:
            items[-1][1].append('\u00b7 ' + stripped[2:].strip())
            continue
        key, value = fm_split(stripped, fmt)
        items.append((key, [value] if value else []))
    return [(k, '\n'.join(v)) for k, v in items]


__all__ = [
    "FRONT_MATTER_DELIMS",
    "FRONT_MATTER_FORMATS",
    "front_matter_delim",
    "front_matter_format",
    "is_front_matter_close",
    "fm_split",
    "fm_value_text",
    "parse_front_matter_items",
]
