"""emoji_map — Emoji 短代码 → Unicode 映射表（真源下沉到表现层数据注册表）。

「一切皆插件」：Emoji 表的真源已下沉到 ``src.presentation_data`` 注册表
（清单条目 ``presentation_data``，id=``emoji``，可 patch/overlay 覆盖/禁用）。
本模块保留旧调用面：``EMOJI_MAP``（实时映射视图，overlay 变更即时可见）与
``resolve_emoji``。
"""

from __future__ import annotations

from src.presentation_data import LiveMapping

#: Emoji 短代码映射（实时视图——底层取注册表当前生效表）
EMOJI_MAP: "LiveMapping" = LiveMapping("emoji")


def resolve_emoji(text: str) -> str:
    """将文本中的 Emoji 短代码替换为实际 Emoji 字符。

    字符级扫描，无正则表达式：逐字符遍历 text，
    遇到 : 时收集后续合法名称字符，若 :name: 在 EMOJI_MAP 中则替换。
    """
    result: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        if text[i] == ':':
            j = i + 1
            name_start = j
            while j < n and (text[j].isalnum() or text[j] in '_-+'):
                j += 1
            if j > name_start and j < n and text[j] == ':':
                full = text[i:j + 1]
                if full in EMOJI_MAP:
                    result.append(EMOJI_MAP[full])
                    i = j + 1
                    continue
        result.append(text[i])
        i += 1
    return ''.join(result)


__all__ = ["EMOJI_MAP", "resolve_emoji"]
