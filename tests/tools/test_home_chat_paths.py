"""运行时数据目录 ``~/.chat`` 迁移回归测试。

覆盖：
- ``src.paths`` 全部派生目录都位于 ``Path.home() / ".chat"``；
- plan 白名单目录来自 ``paths.CHAT_DIR``，而不是当前工作目录；
- 路径工具对 ``~`` 的展开（write/edit/mkdir/read/glob/grep/ls）；
- 会话保存/加载实际落到 ``CHAT_MSGS_DIR``。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from src import chat_msgs, paths
from src.tools import file_ops
from src.tools.base import Func
from src.tools.edit import EditFunc
from src.tools.file_base import PathSecurityError
from src.tools.glob import GlobFunc
from src.tools.grep import GrepFunc
from src.tools.ls import LsFunc
from src.tools.mkdir import MkdirFunc
from src.tools.read import ReadFunc
from src.tools.write import WriteFunc


def test_chat_dirs_live_under_home_chat() -> None:
    home_chat = Path.home() / ".chat"
    assert paths.CHAT_DIR == home_chat
    assert paths.CHAT_MSGS_DIR == home_chat / "msg_list"
    assert paths.CHAT_MEMORY_DIR == home_chat / "memory"
    assert paths.RUNTIME_PLUGINS_DIR == home_chat / "runtime_plugins"
    assert paths.CHAT_ATTACHMENTS_DIR == home_chat / "attachments"


def test_plan_allowed_dir_uses_chat_dir(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(paths, "CHAT_DIR", tmp_path / ".chat")

    expected = os.path.realpath(str(tmp_path / ".chat" / "plan"))
    assert file_ops.get_plan_allowed_dir() == expected


def test_is_path_within_dir_expands_tilde(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(paths, "CHAT_DIR", tmp_path / ".chat")
    allowed = file_ops.get_plan_allowed_dir()

    assert file_ops.is_path_within_dir("~/.chat/plan/a.md", allowed) is True
    assert file_ops.is_path_within_dir("~/.chat/plan/../../etc/passwd", allowed) is False
    assert file_ops.is_path_within_dir(str(tmp_path / "elsewhere" / "a.md"), allowed) is False


def test_file_tools_expand_tilde(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    plan_file = tmp_path / ".chat" / "plan" / "a.md"

    assert WriteFunc("~/.chat/plan/a.md", "x").path == str(plan_file)
    assert EditFunc("~/.chat/plan/a.md", "a", "b").path == str(plan_file)
    assert MkdirFunc("~/.chat/plan", parents=True).path == str(tmp_path / ".chat" / "plan")
    assert ReadFunc("~/.chat/memory/index.md").path == str(
        tmp_path / ".chat" / "memory" / "index.md"
    )
    assert GlobFunc("*", path="~/.chat/memory").root_path == str(
        tmp_path / ".chat" / "memory"
    )
    assert GrepFunc("x", path="~/.chat/memory").path == str(tmp_path / ".chat" / "memory")
    assert LsFunc("~/.chat").target_path == str(tmp_path / ".chat")


def test_plan_whitelist_accepts_home_chat_tilde_path(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(paths, "CHAT_DIR", tmp_path / ".chat")

    allowed, _ = Func.can_use("write", "plan", "~/.chat/plan/a.md")
    denied, reason = Func.can_use("write", "plan", "~/.chat/memory/a.md")
    assert allowed is True
    assert denied is False
    assert "plan agent" in (reason or "")

    write = WriteFunc("~/.chat/plan/a.md", "x")
    write.agent_type = "plan"
    write._validate_path_and_size()  # 不抛异常即通过

    outside = WriteFunc("~/.chat/memory/a.md", "x")
    outside.agent_type = "plan"
    with pytest.raises(PathSecurityError):
        outside._validate_path_and_size()


def test_save_and_load_session_use_chat_msgs_dir(monkeypatch, tmp_path) -> None:
    msgs_dir = tmp_path / ".chat" / "msg_list"
    msgs_dir.mkdir(parents=True)
    monkeypatch.setattr(chat_msgs, "CHAT_MSGS_DIR", msgs_dir)
    monkeypatch.setattr(chat_msgs, "ensure_chat_msgs_dir", lambda: None)

    sid = chat_msgs.save_session([{"role": "user", "content": "hello"}], "test-model")

    assert (msgs_dir / f"{sid}.json").exists()
    data = chat_msgs.load_session(sid)
    assert data is not None
    assert data["messages"][0]["content"] == "hello"
