"""ANSI 样式测试 — 覆盖 src/renderer/ansi/style.py。

验证自包含 Style 的 ANSI 序列生成、合并、布尔语义，以及 RGB→256 色号转换与调色板构建。
"""

import pytest

from src.renderer.ansi.style import (
    Style,
    _build_palette,
    _color_ansi,
    rgb_to_256,
)


# ── Style 布尔语义 ────────────────────────────────────────

def test_style_default_falsy():
    assert not Style()


def test_style_bold_truthy():
    assert Style(bold=True)


def test_style_fg_truthy():
    assert Style(fg=1)


# ── to_ansi ───────────────────────────────────────────────

def test_to_ansi_empty():
    assert Style().to_ansi() == ""


def test_to_ansi_bold():
    assert Style(bold=True).to_ansi() == "\033[1m"


def test_to_ansi_256_color():
    assert Style(fg=42).to_ansi() == "\033[38;5;42m"


def test_to_ansi_rgb_color():
    assert Style(fg=(1, 2, 3)).to_ansi() == "\033[38;2;1;2;3m"


def test_to_ansi_bg_rgb():
    assert Style(bg=(10, 20, 30)).to_ansi() == "\033[48;2;10;20;30m"


# ── apply ─────────────────────────────────────────────────

def test_apply_no_style_returns_text():
    assert Style().apply("x") == "x"


def test_apply_bold_wraps_with_reset():
    assert Style(bold=True).apply("x") == "\033[1mx\033[0m"


# ── merge ─────────────────────────────────────────────────

def test_merge_other_overrides_fg():
    merged = Style(fg=1).merge(Style(fg=2))
    assert merged.fg == 2


def test_merge_keeps_fg_when_other_none():
    merged = Style(fg=1).merge(Style(bold=True))
    assert merged.fg == 1
    assert merged.bold is True


def test_merge_bold_boolean_or():
    merged = Style(bold=True).merge(Style(italic=True))
    assert merged.bold is True
    assert merged.italic is True


# ── _color_ansi ───────────────────────────────────────────

def test_color_ansi_256():
    assert _color_ansi(5, "38") == "\033[38;5;5m"


def test_color_ansi_rgb():
    assert _color_ansi((1, 2, 3), "38") == "\033[38;2;1;2;3m"


# ── rgb_to_256 / _build_palette ───────────────────────────

def test_rgb_to_256_exact_red():
    assert rgb_to_256(255, 0, 0) == 9


def test_rgb_to_256_black():
    assert rgb_to_256(0, 0, 0) == 0


def test_rgb_to_256_white():
    assert rgb_to_256(255, 255, 255) == 15


def test_rgb_to_256_returns_valid_index():
    idx = rgb_to_256(123, 45, 67)
    assert 0 <= idx < 256


def test_build_palette_has_256_colors():
    palette = _build_palette()
    assert len(palette) == 256
