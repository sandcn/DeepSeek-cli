"""行内格式 — 粗体/斜体/行内码/链接 → Run 序列。

轻量行内 Markdown 解析器（Rich-free），支持：
  **bold** / __bold__
  *italic* / _italic_
  `code`
  ~~strike~~
  [text](url)
  <https://link>（简单链接化）

解析失败时原样返回纯文本（兜底不丢内容）。

性能契约（超长单行）：普通文本段落用 ``str.find``（C 级）一次定位下一个
行内标记起点后整体切片，不逐字符累积——修复前 ``buf += c`` 逐字符拼接使
**单行**解析退化为 O(n²)（流式预览对活动行逐帧重解析，超长行每帧数十毫秒）。
"""

from __future__ import annotations

from .style import Style
from .helpers import Run


# ── 行内语法标记 ──────────────────────────────────────────
_BOLD = ("**", "__")
_ITALIC = ("*", "_")
_CODE = "`"
_STRIKE = "~~"

#: 会中断普通文本累积的标记首字符。``~`` 不单列——只有连续 ``~~`` 构成
#: 删除线，故按子串定位（单 ``~`` 属普通文本）。
_PLAIN_STOP_CHARS = ("*", "_", "`", "[", "<")

# ── 行内样式常量（避免每个 run 重建 frozen dataclass） ──
_STYLE_CODE = Style(fg=46, bold=True)
_STYLE_BOLD = Style(bold=True)
_STYLE_ITALIC = Style(italic=True)
_STRIKE_STYLE = Style(dim=True)
_STYLE_LINK = Style(fg=45, underline=True)

#: ``base.merge(style)`` 结果缓存：``base`` 取值集合有限（默认样式 / 标题 /
#: 表格单元格等），缓存后同一基础样式的合并结果跨帧复用（免每次构造 frozen
#: dataclass）。有界，超限清空。
_MERGE_CACHE: dict = {}
_MERGE_CACHE_MAX = 512


def _merge(base: Style, style: Style) -> Style:
    """``base.merge(style)`` 的缓存版本（样式对象不可变，可安全复用）。"""
    key = (base, style)
    merged = _MERGE_CACHE.get(key)
    if merged is None:
        merged = base.merge(style)
        if len(_MERGE_CACHE) >= _MERGE_CACHE_MAX:
            _MERGE_CACHE.clear()
        _MERGE_CACHE[key] = merged
    return merged


#: 行内标记字符（用于「子串是否需要递归解析」的快速判否）
_MARKER_CHARS = ("*", "_", "`", "[", "<")


def _has_inline_marker(text: str) -> bool:
    """文本是否可能含行内标记（快速判否，C 级子串搜索）。"""
    if "~~" in text:
        return True
    for ch in _MARKER_CHARS:
        if ch in text:
            return True
    return False


#: 参与「下一个特殊标记」定位的搜索项（顺序与位置缓存下标对应）。
_SCAN_TERMS = _PLAIN_STOP_CHARS + (_STRIKE,)


def _render_inline_impl(text: str, base: Style) -> list[Run]:
    """递归解析行内语法为 Run 序列。

    普通文本段用**单调前进的位置缓存 + C 级 ``str.find``** 定位下一个特殊
    标记，再整体切片追加 run——修复前逐字符 ``buf += c`` 使单行解析退化为
    O(n²)；仅「每个标记各 find 一次」后走缓存，超长纯文本段落成本 O(n) 且
    常数极小。
    """
    runs: list[Run] = []
    i = 0
    n = len(text)
    # 每个搜索项「下一次出现位置」缓存：扫描位置单调前进，同一标记在整段
    # 文本中只做一次 find（缺失的标记 -1 永久缓存，避免反复全段扫描）。
    scan_pos: list[int] = [-2] * len(_SCAN_TERMS)
    while i < n:
        # 普通文本批量跳转：一次定位 + 切片（不再逐字符累积）
        best = n
        for t_idx, term in enumerate(_SCAN_TERMS):
            k = scan_pos[t_idx]
            if k == -2 or (k >= 0 and k < i):
                k = text.find(term, i)
                scan_pos[t_idx] = k
            if 0 <= k < best:
                best = k
        j = best
        if j > i:
            runs.append(Run(text[i:j], base))
            i = j
            if i >= n:
                break
        ch = text[i]
        # 行内代码 `code`
        if ch == _CODE:
            end = text.find(_CODE, i + 1)
            if end != -1:
                runs.append(Run(text[i + 1:end], _merge(base, _STYLE_CODE)))
                i = end + 1
                continue
        # 粗体 ** / __（仅当首字符为 * 或 _ 时才可能成立）
        elif ch == "*" or ch == "_":
            if text.startswith(ch + ch, i):
                end = text.find(ch + ch, i + 2)
                if end != -1:
                    inner = text[i + 2:end]
                    merged = _merge(base, _STYLE_BOLD)
                    # 快路径：内容不含行内标记 → 直接产出单 run（免一次递归）
                    if _has_inline_marker(inner):
                        runs.extend(_render_inline_impl(inner, merged))
                    else:
                        runs.append(Run(inner, merged))
                    i = end + 2
                    continue
                # 未闭合：原样输出标记（修复前落到单 `*` 分支吞掉第二个字符，
                # 流式未完成标记会显示成 `*text` 而非 `**text`）
                runs.append(Run(ch + ch, base))
                i += 2
                continue
            # 避免与粗体混淆：`*` 后跟 `*` 的跳过（已在粗体分支处理）
            if i + 1 < n and text[i + 1] == ch:
                i += 1
                continue
            # 斜体 * / _
            end = text.find(ch, i + 1)
            if end != -1:
                inner = text[i + 1:end]
                merged = _merge(base, _STYLE_ITALIC)
                if _has_inline_marker(inner):
                    runs.extend(_render_inline_impl(inner, merged))
                else:
                    runs.append(Run(inner, merged))
                i = end + 1
                continue
        # 删除线 ~~
        elif ch == "~":
            end = text.find(_STRIKE, i + 2)
            if end != -1:
                runs.append(Run(text[i + 2:end], _merge(base, _STRIKE_STYLE)))
                i = end + 2
                continue
            # 未闭合：原样输出标记（同上）
            runs.append(Run(_STRIKE, base))
            i += 2
            continue
        # 链接 [text](url)
        elif ch == "[":
            close_bracket = text.find("]", i + 1)
            if close_bracket != -1 and close_bracket + 1 < n and text[close_bracket + 1] == "(":
                close_paren = text.find(")", close_bracket + 2)
                if close_paren != -1:
                    runs.append(Run(
                        text[i + 1:close_bracket],
                        _merge(base, _STYLE_LINK),
                    ))
                    i = close_paren + 1
                    continue
        # 裸链接 <url>
        elif ch == "<":
            end = text.find(">", i + 1)
            if end != -1 and ("://" in text[i + 1:end] or text[i + 1:end].startswith("mailto:")):
                runs.append(Run(text[i + 1:end], _merge(base, _STYLE_LINK)))
                i = end + 1
                continue
        runs.append(Run(ch, base))
        i += 1
    return runs


def render_inline(text: str, base_style: Style | None = None) -> list[Run]:
    """解析行内 Markdown 为 Run 序列。

    Args:
        text: 行内文本。
        base_style: 基础样式。

    Returns:
        Run 列表（解析失败时单 run 纯文本）。
    """
    if not text:
        return []
    base = base_style if base_style is not None else Style()
    try:
        return _render_inline_impl(text, base)
    except Exception:
        return [Run(text, base)]


__all__ = ["render_inline"]
