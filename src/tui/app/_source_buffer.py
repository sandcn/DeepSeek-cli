"""块源文本缓冲 — resize 重渲染用的增量累积（避免流式 O(n²) 拼接）。

``ChatBlock.source_text`` 仅用于终端 resize 时按新宽度整块重渲染。流式通道
原先每收到一个 chunk 就 ``source_text = source_text + delta``——长回答（1MB
上限）下每次复制整段已累积文本，累计 O(n²)（50KB 回答逐 8 字符写入约
150MB 拷贝）。

本模块把拼接改为「常见长度即时拼接 + 大文本分块暂存」：

  - 累计长度不超过 ``_DIRECT_MAX``：直接拼接（常见回答 / 测试语义完全不变）；
  - 超过后：新 chunk 暂存到 ``block.extra["_source_parts"]``，每累计
    ``_JOIN_STEP`` 字符才做一次 join——拷贝次数降为 O(n / step)；
  - ``join_source`` 在需要完整源文本时（resize / 前向引用重渲染 / 关闭块）
    合并暂存分块，保证消费方始终看到完整内容；
  - 达到 ``max_chars`` 上限后截断并标记 ``source_truncated``（既有契约）。
"""

from __future__ import annotations

#: 暂存分块合并步长（字符）：大文本每累积该长度才拼接一次。
_JOIN_STEP = 16_384

#: 直接拼接阈值（字符）：常见回答（数十 KB）仍走 ``source_text += delta``
#: 的零额外开销路径；超过后才启用分块暂存，避免逐 chunk 复制整段文本。
_DIRECT_MAX = 65_536

#: ``block.extra`` 中暂存分块列表 / 累计长度的键。
_PARTS_KEY = "_source_parts"
_LEN_KEY = "_source_len"


def _pending(block) -> list[str]:
    parts = block.extra.get(_PARTS_KEY)
    if not isinstance(parts, list):
        parts = []
        block.extra[_PARTS_KEY] = parts
    return parts


def _current_len(block, parts: list[str]) -> int:
    """当前累计长度（含暂存分块；以 ``_source_len`` 缓存避免逐块求和）。"""
    total = block.extra.get(_LEN_KEY)
    if isinstance(total, int) and total >= 0:
        return total
    total = len(getattr(block, "source_text", "") or "") + sum(
        len(p) for p in parts)
    block.extra[_LEN_KEY] = total
    return total


def _join_pending(block) -> str:
    """把暂存分块并入 ``source_text`` 并返回完整源文本。"""
    parts = block.extra.get(_PARTS_KEY)
    if not parts:
        return getattr(block, "source_text", "") or ""
    joined = (getattr(block, "source_text", "") or "") + "".join(parts)
    parts.clear()
    block.source_text = joined
    block.extra[_LEN_KEY] = len(joined)
    return joined


def append_source(block, delta: str, max_chars: int) -> None:
    """向块追加源文本（增量、上限截断；见模块 docstring）。"""
    if not delta or max_chars <= 0:
        return
    extra = block.extra
    if extra.get("source_truncated"):
        return
    # ★ 热路径（常见回答）：尚未跨越直接拼接阈值时只做一次字符串拼接，
    #   避免每个流式 chunk 的字典查询/长度缓存维护开销。
    cur = getattr(block, "source_text", "") or ""
    if (len(cur) + len(delta) <= _DIRECT_MAX
            and len(cur) + len(delta) <= max_chars
            and not extra.get(_PARTS_KEY)):
        block.source_text = cur + delta
        extra[_LEN_KEY] = len(block.source_text)
        return
    parts = _pending(block)
    total = _current_len(block, parts)
    if total >= max_chars:
        extra["source_truncated"] = True
        return
    new_total = total + len(delta)
    if new_total > max_chars:
        joined = _join_pending(block) + delta
        block.source_text = joined[:max_chars]
        extra[_LEN_KEY] = max_chars
        extra["source_truncated"] = True
        return
    # 常见长度：直接拼接（保持既有「随时可读 source_text」语义与低开销）
    if new_total <= _DIRECT_MAX:
        block.source_text = (getattr(block, "source_text", "") or "") + delta
        extra[_LEN_KEY] = new_total
        return
    if total < _DIRECT_MAX:
        # 首次跨越阈值：把暂存分块一次性并入（此后走分块路径）
        block.source_text = (
            (getattr(block, "source_text", "") or "")
            + "".join(parts) + delta
        )
        parts.clear()
        extra[_LEN_KEY] = new_total
        return
    parts.append(delta)
    extra[_LEN_KEY] = new_total
    if new_total - len(getattr(block, "source_text", "") or "") >= _JOIN_STEP:
        _join_pending(block)


def join_source(block) -> str:
    """返回完整源文本（合并暂存分块；无分块时零成本）。"""
    return _join_pending(block)


__all__ = ["append_source", "join_source"]
