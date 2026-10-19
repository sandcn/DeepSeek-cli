"""Unicode 代理项（surrogate）清洗 — 全项目单一真源（Layer 0，零依赖）。

背景（2026-10 修复）：定时自动保存会话报

    ✖ 自动保存会话失败: 'utf-8' codec can't encode character '\\ud83d'
      in position 83015: surrogates not allowed

Python 的 ``str`` 允许存在**孤立代理项**码点（U+D800-U+DFFF），但 Unicode
标准禁止把代理项编码为 UTF-8（它们只在中转 UTF-16 时成对出现）——任何
``json.dumps(..., ensure_ascii=False)`` 之后再 ``write_text(..., encoding=
"utf-8")`` 的保存路径都会因此抛 ``UnicodeEncodeError``，整次落盘失败
（会话文件没写成，用户以为已自动保存）。

孤立代理项进入对话内容的渠道都在「外部世界 → 本项目」的边界上：
  - 模型 SSE 流把 emoji 的 UTF-16 代理对拆到两个 delta 帧（两帧 JSON 里
    分别是 ``\\ud83d`` / ``\\ude00`` 转义），``json.loads`` 后各自是孤立
    代理项；
  - 上游 / 代理服务端直接给出损坏的转义序列；
  - TUI 粘贴、工具输出、旧会话文件等边界数据。

本模块把清洗口径收敛为唯一实现：
  - ``sanitize_surrogates``：**相邻**的高代理 + 低代理还原为完整码点
    （跨 delta 被拆开的 emoji 因此无损恢复），其余孤立代理项替换为
    U+FFFD——与 ``bytes.decode(errors="replace")`` 的既定口径一致；
  - ``sanitize_surrogates_deep``：按 JSON 数据结构（dict / list / tuple /
    str）递归清洗，供持久化、消息追加等边界一次性覆盖任意嵌套字段；
  - ``has_surrogate``：是否需要清洗的快速判定（无代理项时零拷贝直通）；
  - ``split_trailing_high_surrogate``：流式渲染按增量清洗时，把行尾的
    悬挂高代理项先挂起（下一个增量可能带来它的低代理项）。

依赖约束：本模块为 Layer 0，仅依赖标准库（``re``），可被任何层导入。
"""

from __future__ import annotations

import re
from typing import Any

#: Unicode 替换字符（孤立代理项的替换目标）。
REPLACEMENT_CHARACTER = "\ufffd"

#: 代理项码点区间（UTF-16 代理区）。
HIGH_SURROGATE_START = 0xD800
HIGH_SURROGATE_END = 0xDBFF
LOW_SURROGATE_START = 0xDC00
LOW_SURROGATE_END = 0xDFFF

#: 任意代理项码点。
_SURROGATE_RE = re.compile("[\ud800-\udfff]")
#: 相邻的高代理 + 低代理（UTF-16 代理对以两个码点形式出现 → 可还原为完整码点）。
_SURROGATE_PAIR_RE = re.compile("([\ud800-\udbff])([\udc00-\udfff])")


def has_surrogate(text: str) -> bool:
    """文本是否含代理项码点（孤立或成对）——清洗前的快速判定。"""
    if not text:
        return False
    return _SURROGATE_RE.search(text) is not None


def _join_surrogate_pair(match: "re.Match[str]") -> str:
    """把「高代理 + 低代理」两个码点还原为单个完整码点。"""
    high = ord(match.group(1)) - HIGH_SURROGATE_START
    low = ord(match.group(2)) - LOW_SURROGATE_START
    return chr(0x10000 + (high << 10) + low)


def sanitize_surrogates(text: str) -> str:
    """清洗文本中的代理项，返回可安全以 UTF-8 编码的字符串。

    规则：
      - ``高代理 + 低代理`` 相邻出现 → 还原为一个完整码点（emoji 等补充平面
        字符被拆成两个 delta 帧时，拼接后即相邻，此处无损还原）；
      - 其余孤立代理项（高代理无后继低代理 / 只出现低代理）→ U+FFFD。

    无代理项时原样返回同一对象（零拷贝，热路径无额外开销）。

    Args:
        text: 任意文本（``None`` 视为空串）。

    Returns:
        清洗后的文本。
    """
    if not isinstance(text, str) or not text:
        return text
    if _SURROGATE_RE.search(text) is None:
        return text
    text = _SURROGATE_PAIR_RE.sub(_join_surrogate_pair, text)
    return _SURROGATE_RE.sub(REPLACEMENT_CHARACTER, text)


def sanitize_surrogates_deep(value: Any) -> Any:
    """递归清洗 JSON 数据结构中的字符串（dict 的键与值、list / tuple 元素）。

    供持久化 / 消息追加等边界一次性覆盖任意嵌套字段（``messages`` 的
    content blocks、``tool_calls``、``session_log`` 事件、``subagents`` 记录
    等）。

    **写时复制**：只有确实发生清洗的容器才重新构造，干净数据原样返回同一
    对象——既避免无谓的内存拷贝（会话保存是高频路径），也保证调用方对
    「未含代理项的数据保持同一引用」的既有预期（如持久化端口返回值的
    身份语义）。

    Args:
        value: 任意数据结构。

    Returns:
        清洗后的同构数据（dict / list / tuple 类型保持；无需清洗时为原对象）。
    """
    if isinstance(value, str):
        return sanitize_surrogates(value)
    if isinstance(value, dict):
        cleaned: dict = {}
        changed = False
        for key, item in value.items():
            new_key = sanitize_surrogates(key) if isinstance(key, str) else key
            new_item = sanitize_surrogates_deep(item)
            if new_key is not key or new_item is not item:
                changed = True
            cleaned[new_key] = new_item
        return cleaned if changed else value
    if isinstance(value, list):
        cleaned_list = [sanitize_surrogates_deep(item) for item in value]
        if all(new is old for new, old in zip(cleaned_list, value)):
            return value
        return cleaned_list
    if isinstance(value, tuple):
        cleaned_tuple = tuple(sanitize_surrogates_deep(item) for item in value)
        if all(new is old for new, old in zip(cleaned_tuple, value)):
            return value
        return cleaned_tuple
    return value


def split_trailing_high_surrogate(text: str) -> tuple[str, str]:
    """把行尾的悬挂高代理项单独拆出。

    流式渲染按增量清洗时，增量末尾的高代理项可能与**下一个**增量的低代理
    项组成代理对——立即替换成 U+FFFD 会让被拆开的 emoji 渲染成 ``��``。调用
    方应把返回的悬挂部分暂存，与下一个增量拼接后再清洗。

    Args:
        text: 待拆分文本。

    Returns:
        ``(可安全使用的文本, 行尾悬挂高代理项或空串)``。
    """
    if not text:
        return text, ""
    last = ord(text[-1])
    if HIGH_SURROGATE_START <= last <= HIGH_SURROGATE_END:
        return text[:-1], text[-1]
    return text, ""


__all__ = [
    "REPLACEMENT_CHARACTER",
    "HIGH_SURROGATE_START",
    "HIGH_SURROGATE_END",
    "LOW_SURROGATE_START",
    "LOW_SURROGATE_END",
    "has_surrogate",
    "sanitize_surrogates",
    "sanitize_surrogates_deep",
    "split_trailing_high_surrogate",
]
