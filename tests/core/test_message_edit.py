"""消息编辑工具测试 — 覆盖 src/core/message_edit.py。

验证消息列表截断与清空重建 system prompt。
"""

import pytest

from src.core.message_edit import clear_all_messages, truncate_messages


def test_truncate_messages_keeps_system_and_recent():
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "u1"},
        {"role": "user", "content": "u2"},
        {"role": "user", "content": "u3"},
    ]
    deleted = truncate_messages(messages, keep_from_start=2)
    assert [m["content"] for m in messages] == ["sys", "u1", "u2"]
    assert [m["content"] for m in deleted] == ["u3"]


def test_truncate_messages_all_kept():
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "u1"},
    ]
    deleted = truncate_messages(messages, keep_from_start=5)
    assert len(messages) == 2
    assert deleted == []


def test_truncate_messages_no_system():
    messages = [
        {"role": "user", "content": "u1"},
        {"role": "user", "content": "u2"},
    ]
    deleted = truncate_messages(messages, keep_from_start=1)
    assert [m["content"] for m in messages] == ["u1"]
    assert [m["content"] for m in deleted] == ["u2"]


def test_clear_all_messages():
    messages = [{"role": "user", "content": "x"}]
    clear_all_messages(messages, lambda: ["sys1", "sys2"])
    assert messages == [
        {"role": "system", "content": "sys1"},
        {"role": "system", "content": "sys2"},
    ]
