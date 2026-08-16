"""frontmatter 解析测试 — 零依赖最小 YAML 子集 + PyYAML 快路径。"""

from src.skills.frontmatter import parse_frontmatter


def test_basic_frontmatter():
    raw = """---
name: my-skill
description: 一句话描述
---
正文内容
"""
    data, body = parse_frontmatter(raw)
    assert data["name"] == "my-skill"
    assert data["description"] == "一句话描述"
    assert body == "正文内容\n"


def test_when_to_use_and_booleans():
    raw = """---
name: code-review
description: 审查
whenToUse: 用户要求审查时
disable-model-invocation: false
user-invocable: true
---
"""
    data, _ = parse_frontmatter(raw)
    assert data["whenToUse"] == "用户要求审查时"
    assert data["disable-model-invocation"] is False
    assert data["user-invocable"] is True


def test_scalar_types():
    raw = """---
name: t
description: d
count: 42
ratio: 3.14
enabled: yes
disabled: off
empty:
nothing: null
---
"""
    data, _ = parse_frontmatter(raw)
    assert data["count"] == 42
    assert data["ratio"] == 3.14
    assert data["enabled"] is True
    assert data["disabled"] is False
    assert data["empty"] is None
    assert data["nothing"] is None


def test_quoted_strings():
    raw = '---\nname: t\ndescription: "带 空格 和 # 井号"\nliteral: \'单引号\'\n---\n'
    data, _ = parse_frontmatter(raw)
    assert data["description"] == "带 空格 和 # 井号"
    assert data["literal"] == "单引号"


def test_inline_and_block_lists():
    raw = """---
name: t
description: d
tags: [a, b, c]
metadata:
  - x
  - y
---
"""
    data, _ = parse_frontmatter(raw)
    assert data["tags"] == ["a", "b", "c"]
    assert data["metadata"] == ["x", "y"]


def test_full_line_comments():
    raw = """---
# 这是注释
name: t
description: d
# 中间注释
whenToUse: w
---
"""
    data, _ = parse_frontmatter(raw)
    assert data == {"name": "t", "description": "d", "whenToUse": "w"}


def test_missing_frontmatter_returns_none():
    assert parse_frontmatter("# 没有 frontmatter\n正文") is None
    assert parse_frontmatter("---\n只有开头") is None
    assert parse_frontmatter("") is None


def test_crlf_line_endings():
    raw = "---\r\nname: t\r\ndescription: d\r\n---\r\n正文\r\n"
    data, body = parse_frontmatter(raw)
    assert data["name"] == "t"
    assert body == "正文\r\n"


def test_empty_frontmatter_block():
    raw = "---\n---\n正文"
    data, body = parse_frontmatter(raw)
    assert data == {}
    assert body == "正文"
