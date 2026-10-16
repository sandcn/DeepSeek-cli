"""技能 frontmatter 解析测试 — 覆盖 src/skills/frontmatter.py。

验证 YAML frontmatter 块提取与最小 YAML 子集标量解析。
"""

import pytest

from src.skills.frontmatter import (
    _extract_frontmatter_block,
    _parse_scalar,
    _strip_trailing_comment,
    parse_frontmatter,
)


# ── parse_frontmatter ─────────────────────────────────────

def test_parse_frontmatter_basic():
    raw = "---\nname: my-skill\n---\nbody text"
    data, body = parse_frontmatter(raw)
    assert data["name"] == "my-skill"
    assert body == "body text"


def test_parse_frontmatter_no_frontmatter():
    assert parse_frontmatter("just text") is None


def test_parse_frontmatter_description():
    raw = "---\nname: x\ndescription: 描述\n---\nbody"
    data, _ = parse_frontmatter(raw)
    assert data["description"] == "描述"


# ── _extract_frontmatter_block ────────────────────────────

def test_extract_frontmatter_block():
    header, body = _extract_frontmatter_block("---\nname: x\n---\nrest")
    assert "name: x" in header
    assert body == "rest"


def test_extract_frontmatter_block_none():
    assert _extract_frontmatter_block("no --- here") is None


# ── _parse_scalar ─────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("true", True),
    ("false", False),
    ("yes", True),
    ("no", False),
    ("42", 42),
    ("3.14", 3.14),
    ("null", None),
    ("plain", "plain"),
])
def test_parse_scalar(text, expected):
    assert _parse_scalar(text) == expected


def test_parse_scalar_quoted_string():
    assert _parse_scalar('"hello"') == "hello"


def test_parse_scalar_single_quoted():
    assert _parse_scalar("'hello'") == "hello"


def test_parse_scalar_negative_int():
    assert _parse_scalar("-7") == -7


# ── _strip_trailing_comment ───────────────────────────────

def test_strip_trailing_comment():
    assert _strip_trailing_comment("value # comment") == "value"


def test_strip_trailing_comment_no_hash():
    assert _strip_trailing_comment("value") == "value"
