"""自动链接化：裸 URL / Email → 链接样式 Rich Text。"""

from __future__ import annotations

from rich.text import Text
from rich.style import Style

from ._url_email_scanner import _scan_next_url, _scan_next_email


def auto_linkify(text: str) -> Text:
    """裸 URL → 链接样式 Text。

    自动将文本中的 URL 和 Email 地址转换为带链接样式的 Rich Text。
    字符级扫描，无正则表达式。

    Args:
        text: 纯文本

    Returns:
        带链接样式的 Rich Text
    """
    result = Text()
    pos = 0

    while pos < len(text):
        url_info = _scan_next_url(text, pos)
        if url_info is None:
            break
        url_start, url_end, url_text = url_info
        # 在 URL 之前的部分中扫描 Email
        if url_start > pos:
            result.append_text(_auto_linkify_emails(text[pos:url_start]))
        result.append(url_text, style=Style(color="cyan", underline=True))
        pos = url_end

    # 剩余文本中扫描 Email
    if pos < len(text):
        result.append_text(_auto_linkify_emails(text[pos:]))

    return result


def _auto_linkify_emails(text: str) -> Text:
    """Email 地址 → 链接样式 Text。字符级扫描，无正则表达式。"""
    if not text:
        return Text()

    result = Text()
    pos = 0

    while pos < len(text):
        email_info = _scan_next_email(text, pos)
        if email_info is None:
            break
        e_start, e_end, email_text = email_info
        if e_start > pos:
            result.append(text[pos:e_start])
        result.append(email_text, style=Style(color="cyan", underline=True, italic=True))
        pos = e_end

    if pos < len(text):
        result.append(text[pos:])

    return result
