"""共享常量与工具函数测试 — 覆盖 src/core/constants.py。

验证 token/文件大小格式化与消息过滤工具。
"""

import pytest

from src.core.constants import (
    filter_non_system,
    filter_non_system_indices,
    filter_system,
    format_token_k,
    human_size,
)


# ── format_token_k ────────────────────────────────────────

def test_format_token_k_below_threshold():
    assert format_token_k(0) == "0"
    assert format_token_k(500) == "500"
    assert format_token_k(999) == "999"


def test_format_token_k_above_threshold():
    assert format_token_k(1000) == "1.0k"
    assert format_token_k(1500) == "1.5k"


# ── human_size ────────────────────────────────────────────

def test_human_size_bytes():
    assert human_size(0) == "0"
    assert human_size(512) == "512"


def test_human_size_kilobytes():
    assert human_size(1536) == "1.5K"
    assert human_size(2048) == "2.0K"


def test_human_size_megabytes():
    assert human_size(1048576) == "1.0M"


def test_human_size_gigabytes():
    assert human_size(1073741824) == "1.0G"


# ── 消息过滤 ──────────────────────────────────────────────

def test_filter_system():
    messages = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
    ]
    assert filter_system(messages) == [{"role": "system", "content": "s"}]


def test_filter_non_system():
    messages = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
        {"role": "assistant", "content": "a"},
    ]
    result = filter_non_system(messages)
    assert len(result) == 2
    assert result[0]["role"] == "user"


def test_filter_non_system_indices():
    messages = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
        {"role": "assistant", "content": "a"},
    ]
    assert filter_non_system_indices(messages) == [1, 2]
