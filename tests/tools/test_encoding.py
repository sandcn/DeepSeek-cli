"""src/tools/encoding — 编码检测模块单元测试。

覆盖：
  - BOM 检测（utf-8-sig / utf-16-le / utf-32-be 等）
  - UTF-8 快速路径
  - chardet 高置信度路径（含 windows-1252 映射、别名映射、iso-8859-5 特判）
  - 低置信度回退常见编码 / 空字节 / 无路径
  - pick_best_decoding 评分（通吃编码降分、替代字符扣分、兜底）
  - _validate_decoding_quality
"""

from __future__ import annotations

import pytest

import src.tools.encoding as enc


# ── BOM / UTF-8 快速路径 ────────────────────────────────

@pytest.mark.parametrize("data,expected", [
    (b"\xef\xbb\xbfhello", "utf-8-sig"),
    (b"\xff\xfeh\x00i\x00", "utf-16-le"),
    (b"\xfe\xff\x00h\x00i", "utf-16-be"),
    (b"\x00\x00\xfe\xff\x00\x00\x00h", "utf-32-be"),
    (b"\xff\xfe\x00\x00h\x00\x00\x00", "utf-32-le"),
    (b"plain ascii text", "utf-8"),
    ("中文内容".encode("utf-8"), "utf-8"),
])
def test_detect_encoding_bom_and_utf8(data, expected):
    assert enc.detect_encoding(raw_bytes=data) == expected


def test_detect_encoding_empty_bytes():
    assert enc.detect_encoding(raw_bytes=b"") == "utf-8"


def test_detect_encoding_no_path_no_bytes():
    assert enc.detect_encoding() == "utf-8"


# ── chardet 路径 ─────────────────────────────────────────

def test_detect_encoding_chardet_high_confidence(monkeypatch):
    monkeypatch.setattr(enc, "CHARDET_AVAILABLE", True)
    monkeypatch.setattr(enc.chardet, "detect", lambda b: {"encoding": "utf-8", "confidence": 0.99})
    assert enc.detect_encoding(raw_bytes=b"\xc3\xa9") == "utf-8"


def test_detect_encoding_windows1252_mapped_to_latin1(monkeypatch):
    monkeypatch.setattr(enc, "CHARDET_AVAILABLE", True)
    monkeypatch.setattr(
        enc.chardet, "detect",
        lambda b: {"encoding": "windows-1252", "confidence": 0.9},
    )
    seen = {}

    def _spy_validate(raw, e):
        seen["enc"] = e
        return "validated"

    monkeypatch.setattr(enc, "_validate_decoding_quality", _spy_validate)
    assert enc.detect_encoding(raw_bytes=b"\x81\x82 bad bytes") == "validated"
    assert seen["enc"] == "latin-1"  # windows-1252 已映射为 latin-1


def test_detect_encoding_iso8859_5_gbk_preferred(monkeypatch):
    monkeypatch.setattr(enc, "CHARDET_AVAILABLE", True)
    monkeypatch.setattr(
        enc.chardet, "detect",
        lambda b: {"encoding": "iso-8859-5", "confidence": 0.9},
    )
    # GBK 可完美解码的字节 → 返回 gbk
    assert enc.detect_encoding(raw_bytes="中文".encode("gbk")) == "gbk"


def test_detect_encoding_iso8859_5_fallback(monkeypatch):
    monkeypatch.setattr(enc, "CHARDET_AVAILABLE", True)
    monkeypatch.setattr(
        enc.chardet, "detect",
        lambda b: {"encoding": "iso-8859-5", "confidence": 0.9},
    )
    # \x80 不是合法 GBK 首字节 → GBK replace 含 \ufffd → 保留 iso-8859-5
    assert enc.detect_encoding(raw_bytes=b"\x80\x81") == "iso-8859-5"


def test_detect_encoding_chardet_low_confidence_falls_back(monkeypatch):
    monkeypatch.setattr(enc, "CHARDET_AVAILABLE", True)
    monkeypatch.setattr(
        enc.chardet, "detect",
        lambda b: {"encoding": "utf-16", "confidence": 0.3},
    )
    # 低置信度 → 尝试 COMMON_ENCODINGS
    assert isinstance(enc.detect_encoding(raw_bytes=b"some bytes"), str)


def test_detect_encoding_file_path(monkeypatch, tmp_path):
    f = tmp_path / "test.txt"
    f.write_bytes(b"hello world")
    assert enc.detect_encoding(str(f)) == "utf-8"


def test_detect_encoding_exception_falls_back_utf8(monkeypatch):
    monkeypatch.setattr(enc, "_read_bytes", lambda p: (_ for _ in ()).throw(OSError("denied")))
    assert enc.detect_encoding("/nonexistent/path") == "utf-8"


# ── pick_best_decoding ───────────────────────────────────

def test_pick_best_decoding_returns_first_clean_non_catchall():
    raw = "中文内容".encode("utf-8")
    enc_name, content = enc.pick_best_decoding(raw, ["utf-8", "latin-1"])
    assert enc_name == "utf-8"
    assert content == "中文内容"


def test_pick_best_decoding_catchall_not_preferred():
    """latin-1 通吃编码不应压过真实编码（评分 60 < 70+）。"""
    raw = b"\xe4\xb8\xad"  # 非 latin-1 语义的字节
    enc_name, _ = enc.pick_best_decoding(raw, ["latin-1", "gbk"])
    # gbk strict 失败 → replace 评分；latin-1 完美解码但降分
    assert enc_name == "gbk"


def test_pick_best_decoding_ultimate_fallback():
    raw = b"\x00\x01\x02"
    enc_name, content = enc.pick_best_decoding(raw, ["utf-16-le"])
    assert enc_name == "utf-16-le"
    assert content == raw.decode("utf-16-le", errors="replace")


def test_pick_best_decoding_deduplicates():
    raw = b"abc"
    enc_name, _ = enc.pick_best_decoding(raw, ["ascii", "ascii", "utf-8"])
    assert enc_name == "ascii"


# ── _validate_decoding_quality ───────────────────────────

def test_validate_quality_clean_returns_same():
    raw = "hello".encode("utf-8")
    assert enc._validate_decoding_quality(raw, "utf-8") == "utf-8"


def test_validate_quality_catchall_reconsidered():
    """检测结果为通吃编码 → 走 fallback 候选择优。"""
    raw = "中文内容".encode("utf-8")
    best = enc._validate_decoding_quality(raw, "latin-1")
    assert best != "latin-1"


def test_validate_quality_empty_bytes():
    assert enc._validate_decoding_quality(b"", "utf-8") == "utf-8"


# ── async 入口 ───────────────────────────────────────────

def test_async_detect_encoding():
    import asyncio

    result = asyncio.run(enc.async_detect_encoding(raw_bytes="hi".encode()))
    assert result == "utf-8"
