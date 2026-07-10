"""HTML 实体映射与解码。"""

from __future__ import annotations


# HTML 实体映射表（完整版）
_HTML_ENTITIES: dict[str, str] = {
    '&amp;': '&', '&lt;': '<', '&gt;': '>',
    '&quot;': '"', '&apos;': "'", '&nbsp;': '\u00A0',
    '&cent;': '\u00A2', '&pound;': '\u00A3', '&yen;': '\u00A5',
    '&euro;': '\u20AC', '&copy;': '\u00A9', '&reg;': '\u00AE',
    '&trade;': '\u2122', '&mdash;': '\u2014', '&ndash;': '\u2013',
    '&hellip;': '\u2026', '&laquo;': '\u00AB', '&raquo;': '\u00BB',
    '&lsquo;': '\u2018', '&rsquo;': '\u2019', '&ldquo;': '\u201C', '&rdquo;': '\u201D',
    '&bull;': '\u2022', '&deg;': '\u00B0', '&plusmn;': '\u00B1',
    '&times;': '\u00D7', '&divide;': '\u00F7', '&micro;': '\u00B5',
    '&sect;': '\u00A7', '&para;': '\u00B6',
}


def decode_html_entities(text: str) -> str:
    """HTML 实体解码为 Unicode。

    支持命名实体（如 &amp;）和数字实体（如 &#169; / &#x00A9;）。

    Args:
        text: 含 HTML 实体的文本

    Returns:
        解码后的文本
    """
    if '&' not in text:
        return text

    result: list[str] = []
    i = 0
    while i < len(text):
        amp = text.find('&', i)
        if amp == -1:
            result.append(text[i:])
            break
        result.append(text[i:amp])
        semicolon = text.find(';', amp)
        if semicolon == -1:
            result.append(text[amp:])
            break

        entity = text[amp:semicolon + 1]
        if entity in _HTML_ENTITIES:
            result.append(_HTML_ENTITIES[entity])
            i = semicolon + 1
            continue

        if entity.startswith('&#') and len(entity) > 3:
            try:
                num_str = entity[2:-1]
                cp: int = int(num_str[1:], 16) if num_str.startswith(('x', 'X')) else int(num_str)
                result.append(chr(cp))
                i = semicolon + 1
                continue
            except (ValueError, OverflowError):
                pass

        result.append(entity)
        i = semicolon + 1

    return ''.join(result)
