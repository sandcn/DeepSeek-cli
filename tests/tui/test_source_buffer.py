"""块源文本分块缓冲（流式 O(n²) 修复）回归。"""
from __future__ import annotations

from types import SimpleNamespace

from src.tui.app._source_buffer import append_source, join_source


def _block():
    return SimpleNamespace(source_text="", extra={})


def test_small_writes_stay_immediately_readable():
    block = _block()
    append_source(block, "abc", 1000)
    append_source(block, "def", 1000)
    assert block.source_text == "abcdef"
    assert join_source(block) == "abcdef"


def test_large_writes_use_pending_parts_and_join_exactly():
    block = _block()
    first = "a" * 70_000
    second = "b" * 5_000
    append_source(block, first, 200_000)
    assert block.source_text == first
    append_source(block, second, 200_000)
    # 第二个分块进入暂存列表（尚未达到合并步长），完整读取时合并
    assert block.extra.get("_source_parts")
    assert join_source(block) == first + second
    assert block.source_text == first + second
    assert not block.extra.get("_source_parts")


def test_source_cap_truncates_and_marks():
    block = _block()
    append_source(block, "a" * 60, 100)
    append_source(block, "b" * 60, 100)
    assert block.source_text == "a" * 60 + "b" * 40
    assert block.extra.get("source_truncated") is True
    append_source(block, "c", 100)
    assert block.source_text == "a" * 60 + "b" * 40


def test_join_source_without_parts_is_zero_copy():
    block = _block()
    append_source(block, "hello", 100)
    assert join_source(block) == "hello"
