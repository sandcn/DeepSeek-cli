"""会话消息工具测试 — 覆盖 src/chat_msgs.py。

验证会话 ID 校验、唯一 ID 生成与恢复命令。
"""

import pytest

from src.chat_msgs import _validate_session_id, generate_id, get_recover_cmd


def test_validate_session_id_valid():
    assert _validate_session_id("abc123") == "abc123"
    assert _validate_session_id("my-session_1") == "my-session_1"


def test_validate_session_id_strips_json():
    assert _validate_session_id("abc.json") == "abc"


def test_validate_session_id_path_traversal():
    assert _validate_session_id("../etc/passwd") is None
    assert _validate_session_id("a/b") is None


def test_validate_session_id_empty():
    assert _validate_session_id("") is None


def test_generate_id_hex():
    sid = generate_id()
    assert len(sid) == 32
    int(sid, 16)  # 十六进制


def test_generate_id_unique():
    assert generate_id() != generate_id()


def test_get_recover_cmd():
    assert get_recover_cmd("sid123") == "python chat.py --load sid123"


def test_get_recover_cmd_custom_script():
    assert get_recover_cmd("sid", script="run.py") == "python run.py --load sid"
