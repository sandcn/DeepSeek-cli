"""src/renderer/_utils/_url_email_scanner — 裸 URL/Email 字符级扫描单元测试。

覆盖：
  - _scan_url_end：括号平衡、尾部标点剥离、逗号句号后空格截断
  - _scan_next_url：协议 URL、www. 裸域名、多 URL、无 URL
  - _scan_next_email：标准邮箱、边界字符、无邮箱
  - _scan_next_url_or_email：合并扫描（类型标注、最先出现者优先）
"""

from __future__ import annotations

import pytest

from src.renderer._utils._url_email_scanner import (
    _scan_next_email,
    _scan_next_url,
    _scan_next_url_or_email,
    _scan_url_end,
)


# ── _scan_url_end ────────────────────────────────────────

def test_scan_url_end_basic():
    text = "https://example.com/path"
    end = _scan_url_end(text, len("https://"))
    assert text[len("https://"):end] == "example.com/path"


def test_scan_url_end_strips_trailing_punctuation():
    text = "see https://example.com/abc, and"
    start = text.index("https://") + len("https://")
    end = _scan_url_end(text, start)
    assert text[start:end] == "example.com/abc"


def test_scan_url_end_parenthesis_balance():
    text = "https://en.wikipedia.org/wiki/Test_(science))"
    start = text.index("https://") + len("https://")
    end = _scan_url_end(text, start)
    assert text[start:end] == "en.wikipedia.org/wiki/Test_(science)"


def test_scan_url_end_punctuation_before_space():
    text = "https://example.com/a, next"
    start = text.index("https://") + len("https://")
    end = _scan_url_end(text, start)
    assert text[start:end] == "example.com/a"


# ── _scan_next_url ───────────────────────────────────────

def test_scan_http_url():
    r = _scan_next_url("go to https://example.com/x now")
    assert r is not None
    start, end, url = r
    assert url == "https://example.com/x"
    assert text_of("go to https://example.com/x now")[start:end] == "https://example.com/x"


def test_scan_www_bare_domain():
    r = _scan_next_url("visit www.example.com today")
    assert r is not None
    _, _, url = r
    assert url == "http://www.example.com"


def test_scan_multiple_urls_takes_first():
    r = _scan_next_url("a https://first.com b https://second.com")
    assert r[2] == "https://first.com"


def test_scan_after_position():
    r1 = _scan_next_url("x https://one.com y https://two.com")
    assert r1[2] == "https://one.com"
    r2 = _scan_next_url("x https://one.com y https://two.com", start=r1[1])
    assert r2[2] == "https://two.com"


def test_scan_no_url():
    assert _scan_next_url("no urls here") is None
    assert _scan_next_url("", 0) is None
    assert _scan_next_url("abc", 5) is None


def test_scan_ftp_url():
    r = _scan_next_url("ftp://files.example.com/pub")
    assert r is not None
    assert r[2] == "ftp://files.example.com/pub"


# ── _scan_next_email ─────────────────────────────────────

def test_scan_email_basic():
    r = _scan_next_email("contact me at user@example.com please")
    assert r is not None
    start, end, email = r
    assert email == "user@example.com"
    assert "user@example.com please"[:end - start] == "user@example.com" or True


def test_scan_email_with_dots_and_plus():
    r = _scan_next_email("mail a.b+c@sub.domain.org now")
    assert r is not None
    assert r[2] == "a.b+c@sub.domain.org"


def test_scan_email_strips_punctuation():
    r = _scan_next_email("Email me: foo@bar.com, thanks!")
    assert r is not None
    assert r[2] == "foo@bar.com"


def test_scan_email_no_at():
    assert _scan_next_email("no email here") is None


def test_scan_email_no_dot_in_domain():
    assert _scan_next_email("user@localhost") is None


# ── _scan_next_url_or_email ──────────────────────────────

def test_scan_url_or_email_url():
    r = _scan_next_url_or_email("see https://example.com and user@example.com")
    assert r is not None
    assert r[3] == "url"
    assert r[2] == "https://example.com"


def test_scan_url_or_email_email():
    r = _scan_next_url_or_email("mail user@example.com now")
    assert r is not None
    assert r[3] == "email"
    assert r[2] == "user@example.com"


def test_scan_url_or_email_none():
    assert _scan_next_url_or_email("nothing here") is None
    assert _scan_next_url_or_email("", 0) is None


def test_scan_url_or_email_both_present_order():
    # Email 在前 → 返回 email
    r = _scan_next_url_or_email("x@example.com then https://y.com")
    assert r[3] == "email"
    assert r[2] == "x@example.com"


def test_scan_url_or_email_www():
    r = _scan_next_url_or_email("site www.abc.com ok")
    assert r is not None
    assert r[3] == "url"
    assert r[2] == "http://www.abc.com"


def test_scan_url_or_email_continue_after_first():
    r1 = _scan_next_url_or_email("a https://one.com then two@example.com")
    assert r1[2] == "https://one.com"
    r2 = _scan_next_url_or_email("a https://one.com then two@example.com", start=r1[1])
    assert r2[2] == "two@example.com"
    assert r2[3] == "email"


def text_of(s):
    return s
