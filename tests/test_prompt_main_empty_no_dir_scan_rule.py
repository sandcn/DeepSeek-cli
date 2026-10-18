"""空模式提词：不再要求预先扫描目录，但改动前仍须认知调用链。"""

from __future__ import annotations

from tests._prompt_semantics import assert_contains, read_prompt


def test_no_prerequisite_directory_scan():
    text = read_prompt()
    assert "陌生项目" not in text
    assert "了解目录结构" not in text


def test_call_chain_and_impact_known_before_change():
    assert_contains(
        read_prompt(),
        "先读相关代码与文档",
        "`search`/`find`",
        "调用方、被调用方和影响范围",
        "禁止凭猜测修改",
    )
