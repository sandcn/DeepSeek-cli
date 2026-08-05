"""自动链接化：裸 URL / Email → 链接样式 Rich Text。"""

from __future__ import annotations

from rich.text import Text
from rich.style import Style

from ._url_email_scanner import _scan_next_email


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
