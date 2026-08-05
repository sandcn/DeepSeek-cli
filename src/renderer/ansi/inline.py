"""行内格式 — 粗体/斜体/行内码/链接 → Run 序列。

轻量行内 Markdown 解析器（Rich-free），支持：
  **bold** / __bold__
  *italic* / _italic_
  `code`
  ~~strike~~
  [text](url)
  <https://link> / 裸 URL（简单链接化）

解析失败时原样返回纯文本（兜底不丢内容）。
"""

from __future__ import annotations

from .style import Style
from .helpers import Run


# ── 行内语法标记 ──────────────────────────────────────────
_BOLD = ("**", "__")
_ITALIC = ("*", "_")
_CODE = "`"
_STRIKE = "~~"


def _find_closing(text: str, opener: str, start: int) -> int:
    """在 text[start:] 中查找 opener 的闭合标记（opener 长度≥2 或单字符）。"""
    if len(opener) == 1:
        return text.find(opener, start)
    return text.find(opener, start)


def _render_inline_impl(text: str, base: Style) -> list[Run]:
    """递归解析行内语法为 Run 序列。"""
    runs: list[Run] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        # 行内代码 `code`
        if ch == _CODE:
            end = text.find(_CODE, i + 1)
            if end != -1:
                runs.append(Run(text[i + 1:end], base.merge(Style(fg=46, bold=True))))
                i = end + 1
                continue
        # 粗体 ** / __
        if text.startswith(_BOLD[0], i) or text.startswith(_BOLD[1], i):
            opener = _BOLD[0] if text.startswith(_BOLD[0], i) else _BOLD[1]
            end = _find_closing(text, opener, i + 2)
            if end != -1:
                inner = _render_inline_impl(text[i + 2:end], base.merge(Style(bold=True)))
                runs.extend(inner)
                i = end + 2
                continue
        # 删除线 ~~
        if text.startswith(_STRIKE, i):
            end = text.find(_STRIKE, i + 2)
            if end != -1:
                runs.append(Run(text[i + 2:end], base.merge(Style(dim=True))))
                i = end + 2
                continue
        # 斜体 * / _
        if ch in ("*", "_"):
            # 避免与粗体混淆：`*` 后跟 `*` 的跳过（已在粗体分支处理）
            if i + 1 < n and text[i + 1] == ch:
                i += 1
                continue
            end = text.find(ch, i + 1)
            if end != -1:
                inner = _render_inline_impl(text[i + 1:end], base.merge(Style(italic=True)))
                runs.extend(inner)
                i = end + 1
                continue
        # 链接 [text](url)
        if ch == "[":
            close_bracket = text.find("]", i + 1)
            if close_bracket != -1 and close_bracket + 1 < n and text[close_bracket + 1] == "(":
                close_paren = text.find(")", close_bracket + 2)
                if close_paren != -1:
                    label = text[i + 1:close_bracket]
                    runs.append(Run(
                        label,
                        base.merge(Style(fg=45, underline=True)),
                    ))
                    i = close_paren + 1
                    continue
        # 裸链接 <url> / https://...
        if ch == "<":
            end = text.find(">", i + 1)
            if end != -1 and ("://" in text[i + 1:end] or text[i + 1:end].startswith("mailto:")):
                runs.append(Run(text[i + 1:end], base.merge(Style(fg=45, underline=True))))
                i = end + 1
                continue
        # 普通字符（累积连续纯文本以合并 Run）
        j = i
        buf = ""
        while j < n:
            c = text[j]
            if c in ("*", "_", "`", "[", "<") or text.startswith("~~", j):
                break
            buf += c
            j += 1
        if buf:
            runs.append(Run(buf, base))
            i = j
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
