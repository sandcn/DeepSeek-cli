"""拖放 / 粘贴文件路径解析与规范化（``src/core/path_tokens.py``）单元测试。

覆盖终端拖放注入的各种形态：反斜杠转义、单/双引号包裹、file:// URI
（含百分号编码）、Windows 原生路径与 UNC；以及「非拖放文本不改写」的
零回归判定、规模上限与引号输出规则。
"""

from __future__ import annotations

import pytest

from src.core import path_tokens as pt


def _ALWAYS(_path: str) -> bool:
    """存在性判定桩（恒真——仅用于形似路径/挂载探测场景）。"""
    return True


# ── 1. 切分与引号 / 转义还原 ─────────────────────────────

def test_single_quoted_path_unquoted():
    assert pt.normalize_dropped_paths("'/home/me/a b.txt'") == \
        '"/home/me/a b.txt"'


def test_double_quoted_path_unquoted():
    assert pt.normalize_dropped_paths('"/home/me/a b.txt"') == \
        '"/home/me/a b.txt"'


def test_backslash_escaped_space_unescaped():
    assert pt.normalize_dropped_paths("/home/me/a\\ b.txt") == \
        '"/home/me/a b.txt"'


def test_plain_path_without_space_not_quoted():
    assert pt.normalize_dropped_paths("/home/me/a.txt") == "/home/me/a.txt"


def test_multiple_paths_each_line():
    text = "'/a b.txt' /c.txt"
    assert pt.normalize_dropped_paths(text) == '"/a b.txt"\n/c.txt'


def test_multiple_paths_space_separated_when_multiline_off():
    text = "'/a b.txt' /c.txt"
    assert pt.normalize_dropped_paths(text, multiline=False) == \
        '"/a b.txt" /c.txt'


def test_unclosed_quote_returns_none():
    assert pt.normalize_dropped_paths("'/a b.txt") is None


# ── 2. file:// URI ──────────────────────────────────────

def test_file_uri_posix():
    assert pt.normalize_dropped_paths("file:///home/me/a%20b.txt") == \
        '"/home/me/a b.txt"'


def test_file_uri_windows_drive():
    assert pt.file_uri_to_path("file:///C:/Users/me/a%20b.txt") == \
        "C:/Users/me/a b.txt"


def test_file_uri_unc_host():
    assert pt.file_uri_to_path("file://server/share/a.txt") == \
        "//server/share/a.txt"


def test_non_file_scheme_returns_empty():
    assert pt.file_uri_to_path("https://example.com/a.txt") == ""


# ── 3. Windows 路径 → POSIX（仅 Cygwin/MSYS） ────────────

def test_windows_drive_converted_on_cygwin():
    assert pt.to_posix_path(
        "C:\\Users\\me\\a.txt", exists=_ALWAYS, platform="cygwin",
    ) == "/cygdrive/c/Users/me/a.txt"


def test_windows_drive_msys_mount():
    assert pt.to_posix_path(
        "D:\\work\\a.txt", exists=_ALWAYS, platform="msys",
    ) == "/d/work/a.txt"


def test_windows_drive_untouched_on_linux():
    assert pt.to_posix_path(
        "C:\\Users\\me\\a.txt", exists=_ALWAYS, platform="linux",
    ) == "C:\\Users\\me\\a.txt"


def test_windows_unc_converted():
    assert pt.to_posix_path(
        "\\\\server\\share\\a.txt", exists=_ALWAYS, platform="cygwin",
    ) == "//server/share/a.txt"


def test_windows_drive_escaped_space():
    assert pt.to_posix_path(
        "C:\\Program\\ Files\\a.txt", exists=_ALWAYS, platform="cygwin",
    ) == "/cygdrive/c/Program Files/a.txt"


# ── 4. 判定（非拖放文本零改写） ──────────────────────────

def test_natural_language_not_path_returns_none(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("x", encoding="utf-8")
    assert pt.normalize_dropped_paths("请读取 %s" % f) is None


def test_multiline_code_not_path_returns_none():
    assert pt.normalize_dropped_paths("def f():\n    return 1") is None


def test_empty_text_returns_none():
    assert pt.normalize_dropped_paths("") is None
    assert pt.normalize_dropped_paths("   ") is None


def test_path_without_abs_prefix_needs_existence():
    assert pt.looks_like_path("a.txt", exists=lambda _p: False) is False
    assert pt.normalize_dropped_paths(
        "a.txt", exists=lambda p: p == "a.txt",
    ) == "a.txt"


def test_tilde_path_recognized():
    assert pt.looks_like_path("~/a.txt", exists=lambda _p: False) is True


def test_option_like_token_rejected():
    assert pt.looks_like_path("-v", exists=_ALWAYS) is False


def test_oversized_text_returns_none():
    big = "/a.txt " * 2000
    assert pt.normalize_dropped_paths(big, exists=_ALWAYS) is None


def test_too_many_tokens_returns_none():
    text = " ".join("/f%d.txt" % i for i in range(pt._MAX_DROP_TOKENS + 1))
    assert pt.normalize_dropped_paths(text, exists=_ALWAYS) is None


# ── 5. 输出引号规则 / 辅助函数 ───────────────────────────

def test_quote_rules():
    assert pt.quote_path("/a/b.txt") == "/a/b.txt"
    assert pt.quote_path("/a b.txt") == '"/a b.txt"'
    assert pt.quote_path('/a"b c.txt') == "'/a\"b c.txt'"
    assert pt.quote_path('/a"b\' c.txt') == '"/a\\"b\' c.txt"'


def test_strip_trailing_punct():
    assert pt.strip_trailing_punct("/a/b.png，") == "/a/b.png"
    assert pt.strip_trailing_punct("/a/b.png") == "/a/b.png"


def test_iter_path_tokens_spans():
    tokens = pt.iter_path_tokens("aa '/b c.txt' dd")
    assert [(t.text, t.quoted, t.start, t.end) for t in tokens] == [
        ("aa", False, 0, 1),
        ("/b c.txt", True, 3, 12),
        ("dd", False, 14, 15),
    ]


def test_unc_backslashes_preserved_in_token():
    tokens = pt.iter_path_tokens("\\\\server\\share\\a.txt")
    assert tokens[0].text == "\\\\server\\share\\a.txt"


def test_real_file_exists(tmp_path):
    f = tmp_path / "a b.txt"
    f.write_text("x", encoding="utf-8")
    assert pt.normalize_dropped_paths("'%s'" % f) == '"%s"' % f


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__]))
