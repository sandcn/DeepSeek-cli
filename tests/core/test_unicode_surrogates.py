"""孤立 UTF-16 代理项（surrogate）清洗 — 会话保存链路回归测试。

用户报障（2026-10）：

    ✖ src.core.internal.session._session_persistence_manager:
      自动保存会话失败: 'utf-8' codec can't encode character '\\ud83d'
      in position 83015: surrogates not allowed

根因：Python ``str`` 可含孤立代理项码点（U+D800-U+DFFF），但 Unicode 禁止把
代理项编码为 UTF-8——``json.dumps(ensure_ascii=False)`` 会原样带出该码点，
随后 ``write_text(encoding="utf-8")`` 抛 ``UnicodeEncodeError``（非 OSError，
调用方未捕获），整次落盘失败（会话文件未生成）。

本测试覆盖：
  - ``_unicode_text`` 清洗函数（单值 / 深度 / 悬挂高代理项拆分）；
  - ``chat_msgs.save_session`` / ``rename_session`` / ``export_session`` /
    会话摘要索引（头部扫描的代理对转义）；
  - ``checkpoint.save_checkpoint`` / ``load_checkpoint``；
  - ``SessionPersistenceManager.auto_save``（报障的端到端路径）与 ``load``；
  - ``BaseAgent`` 消息追加边界（用户消息 / 助手消息 / 工具结果）。
"""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path

import pytest

import src.chat_msgs as cm
import src.checkpoint as cp_mod
from src._unicode_text import (
    REPLACEMENT_CHARACTER,
    has_surrogate,
    sanitize_surrogates,
    sanitize_surrogates_deep,
    split_trailing_high_surrogate,
)

#: 孤立高代理项（emoji 😀 U+1F600 的 UTF-16 前半）——报障信息里的字符。
LONE_HIGH = "\ud83d"
#: 孤立低代理项（😀 的后半）。
LONE_LOW = "\ude00"
#: 完整 emoji（补充平面，UTF-8 可编码）。
EMOJI = "\U0001F600"


# ═══════════════════════════════════════════════════════════
# 1. _unicode_text：清洗函数
# ═══════════════════════════════════════════════════════════

class TestSanitizeSurrogates:
    def test_has_surrogate(self):
        assert has_surrogate(LONE_HIGH)
        assert has_surrogate(f"a{LONE_LOW}b")
        assert has_surrogate(EMOJI) is False
        assert has_surrogate("普通文本") is False
        assert has_surrogate("") is False

    def test_normal_text_is_returned_unchanged(self):
        text = "普通文本 emoji 😀 tab\t"
        assert sanitize_surrogates(text) is text  # 无代理项：零拷贝直通

    def test_lone_high_becomes_replacement_character(self):
        assert sanitize_surrogates(f"a{LONE_HIGH}b") == f"a{REPLACEMENT_CHARACTER}b"

    def test_lone_low_becomes_replacement_character(self):
        assert sanitize_surrogates(f"a{LONE_LOW}b") == f"a{REPLACEMENT_CHARACTER}b"

    def test_adjacent_pair_is_restored(self):
        """跨 delta 被拆开的代理对，拼接后相邻 → 无损还原为完整 emoji。"""
        split = LONE_HIGH + LONE_LOW
        assert split != EMOJI  # 两个码点
        assert sanitize_surrogates(split) == EMOJI

    def test_pair_among_text(self):
        assert sanitize_surrogates(f"开头{LONE_HIGH}{LONE_LOW}结尾") == f"开头{EMOJI}结尾"

    def test_mixed_pairs_and_lone(self):
        text = f"{LONE_HIGH}{LONE_LOW}-{LONE_HIGH}-{LONE_LOW}"
        expected = f"{EMOJI}-{REPLACEMENT_CHARACTER}-{REPLACEMENT_CHARACTER}"
        assert sanitize_surrogates(text) == expected

    def test_result_is_utf8_encodable(self):
        text = sanitize_surrogates(f"{LONE_HIGH}{LONE_LOW}{LONE_HIGH}x")
        assert text.encode("utf-8")  # 不再抛 UnicodeEncodeError

    def test_non_string_is_returned_as_is(self):
        assert sanitize_surrogates(None) is None


class TestSanitizeSurrogatesDeep:
    def test_nested_structures(self):
        data = {
            "messages": [
                {"role": "user", "content": f"hi{LONE_HIGH}"},
                {"role": "assistant", "content": f"{LONE_HIGH}{LONE_LOW}"},
            ],
            "n": 1,
        }
        cleaned = sanitize_surrogates_deep(data)
        assert cleaned["messages"][0]["content"] == f"hi{REPLACEMENT_CHARACTER}"
        assert cleaned["messages"][1]["content"] == EMOJI
        assert cleaned["n"] == 1

    def test_keys_are_sanitized(self):
        cleaned = sanitize_surrogates_deep({f"k{LONE_HIGH}": "v"})
        assert list(cleaned) == [f"k{REPLACEMENT_CHARACTER}"]

    def test_tuple_preserved(self):
        cleaned = sanitize_surrogates_deep((f"a{LONE_HIGH}", "b"))
        assert isinstance(cleaned, tuple)
        assert cleaned == (f"a{REPLACEMENT_CHARACTER}", "b")

    def test_input_not_mutated(self):
        content = f"x{LONE_HIGH}"
        data = {"messages": [{"role": "user", "content": content}]}
        sanitize_surrogates_deep(data)
        assert data["messages"][0]["content"] == content  # 原对象不变

    def test_non_container_passthrough(self):
        assert sanitize_surrogates_deep(3) == 3
        assert sanitize_surrogates_deep(None) is None
        assert sanitize_surrogates_deep(True) is True


class TestSplitTrailingHighSurrogate:
    def test_trailing_high_is_split_out(self):
        assert split_trailing_high_surrogate(f"ab{LONE_HIGH}") == ("ab", LONE_HIGH)

    def test_no_trailing_high(self):
        assert split_trailing_high_surrogate("ab") == ("ab", "")
        assert split_trailing_high_surrogate(f"ab{LONE_LOW}") == (f"ab{LONE_LOW}", "")

    def test_empty(self):
        assert split_trailing_high_surrogate("") == ("", "")


# ═══════════════════════════════════════════════════════════
# 2. chat_msgs：保存 / 重命名 / 导出 / 摘要索引
# ═══════════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def msgs_dir(tmp_path: Path, monkeypatch):
    """会话目录重定向到临时目录（与 test_chat_msgs 同一隔离口径）。

    子目录 ``msg_list`` + 父目录放摘要索引，与真实布局一致（``_index_path``
    取 ``CHAT_MSGS_DIR.parent``）。
    """
    target = tmp_path / "msg_list"
    target.mkdir()
    monkeypatch.setattr(cm, "CHAT_MSGS_DIR", target)
    monkeypatch.setattr(cm, "ensure_chat_msgs_dir", lambda: None)
    monkeypatch.setattr(
        cm, "get_token_stats", lambda: {"input": 0, "output": 0, "total": 0},
    )
    monkeypatch.setattr(cm, "_sync_terminal_title", lambda title: None)
    # 会话列表缓存按模块全局保存（TTL 30s）——测试之间必须复位，否则前一个
    # 用例写入的摘要会被后一个用例命中。
    monkeypatch.setattr(cm, "_session_cache", None)
    monkeypatch.setattr(cm, "_session_cache_mtime", 0.0)
    return target


def _read_raw(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class TestSaveSessionSurrogates:
    def test_lone_surrogate_no_longer_breaks_save(self, msgs_dir):
        """报障路径：消息含 ``\\ud83d`` 时保存不再抛 UnicodeEncodeError。"""
        messages = [
            {"role": "user", "content": f"看看这个 {LONE_HIGH}"},
            {"role": "assistant", "content": f"好的{LONE_LOW}"},
        ]
        sid = cm.save_session(messages, "deepseek-chat")

        path = msgs_dir / f"{sid}.json"
        assert path.exists()
        data = _read_raw(path)
        assert data["messages"][0]["content"] == f"看看这个 {REPLACEMENT_CHARACTER}"
        assert data["messages"][1]["content"] == f"好的{REPLACEMENT_CHARACTER}"

    def test_split_pair_restored_in_saved_file(self, msgs_dir):
        """被拆到两个增量帧的 emoji（两个代理项码点）落盘时还原为完整码点。"""
        sid = cm.save_session(
            [{"role": "assistant", "content": f"完成 {LONE_HIGH}{LONE_LOW} 了"}],
            "m",
        )
        data = _read_raw(msgs_dir / f"{sid}.json")
        assert data["messages"][0]["content"] == f"完成 {EMOJI} 了"

    def test_title_with_surrogate_is_sanitized(self, msgs_dir):
        sid = cm.save_session(
            [{"role": "user", "content": f"{LONE_HIGH}标题"}], "m",
        )
        data = _read_raw(msgs_dir / f"{sid}.json")
        assert data["title"] == f"{REPLACEMENT_CHARACTER}标题"

    def test_session_log_snapshot_with_surrogate(self, msgs_dir):
        sid = cm.save_session(
            [{"role": "user", "content": "hi"}],
            "m",
            session_log=[{
                "type": "user_message",
                "seq": 1,
                "data": {"content": f"日志{LONE_HIGH}"},
            }],
        )
        data = _read_raw(msgs_dir / f"{sid}.json")
        assert data["session_log"][0]["data"]["content"] == (
            f"日志{REPLACEMENT_CHARACTER}"
        )

    def test_subagents_with_surrogate(self, msgs_dir):
        sid = cm.save_session(
            [{"role": "user", "content": "hi"}],
            "m",
            subagents=[{"id": "sa-1", "result": f"{LONE_HIGH}{LONE_LOW}{LONE_LOW}"}],
        )
        data = _read_raw(msgs_dir / f"{sid}.json")
        assert data["subagents"][0]["result"] == f"{EMOJI}{REPLACEMENT_CHARACTER}"

    def test_load_roundtrip_after_sanitize(self, msgs_dir):
        sid = cm.save_session([{"role": "user", "content": f"x{LONE_HIGH}"}], "m")
        loaded = cm.load_session(sid)
        assert loaded["messages"][0]["content"] == f"x{REPLACEMENT_CHARACTER}"


class TestRenameSessionSurrogates:
    def _write_session(self, msgs_dir: Path, sid: str, messages: list) -> Path:
        path = msgs_dir / f"{sid}.json"
        # 用 ensure_ascii=True 写出 ``\ud83d`` 转义（旧版/外部工具产物形态）
        path.write_text(
            json.dumps(
                {
                    "id": sid,
                    "title": "旧标题",
                    "model": "m",
                    "saved_at": "2026-10-20T00:00:00",
                    "message_count": len(messages),
                    "messages": messages,
                    "subagents": [],
                },
                ensure_ascii=True,
            ),
            encoding="utf-8",
        )
        return path

    def test_rename_session_with_surrogate_in_messages(self, msgs_dir):
        """会话文件里含孤立代理项转义时，重命名（整文件重写）不再失败。"""
        sid = "abc123"
        path = self._write_session(
            msgs_dir, sid, [{"role": "user", "content": f"a{LONE_HIGH}"}],
        )
        assert cm.rename_session(sid, "新标题") is True

        data = _read_raw(path)
        assert data["title"] == "新标题"
        assert data["messages"][0]["content"] == f"a{REPLACEMENT_CHARACTER}"

    def test_rename_session_title_with_surrogate(self, msgs_dir):
        sid = "abc124"
        path = self._write_session(msgs_dir, sid, [{"role": "user", "content": "x"}])
        assert cm.rename_session(sid, f"新{LONE_HIGH}标题") is True
        assert _read_raw(path)["title"] == f"新{REPLACEMENT_CHARACTER}标题"


class TestExportSessionSurrogates:
    def test_export_returns_encodable_json(self, msgs_dir):
        sid = "exp0001"
        path = msgs_dir / f"{sid}.json"
        path.write_text(
            json.dumps(
                {
                    "id": sid,
                    "title": "t",
                    "model": "m",
                    "saved_at": "",
                    "message_count": 1,
                    "messages": [{"role": "user", "content": f"a{LONE_HIGH}"}],
                    "subagents": [],
                },
                ensure_ascii=True,
            ),
            encoding="utf-8",
        )
        out = cm.export_session(sid)
        assert out is not None
        assert LONE_HIGH not in out
        assert REPLACEMENT_CHARACTER in out

    def test_export_to_file(self, msgs_dir):
        sid = cm.save_session([{"role": "user", "content": f"a{LONE_HIGH}"}], "m")
        target = Path.cwd() / f"_surrogate_export_{uuid.uuid4().hex}.json"
        try:
            written = cm.export_session(sid, output=str(target))
            assert written == str(target)
            assert target.exists()
            json.loads(target.read_text(encoding="utf-8"))
        finally:
            target.unlink(missing_ok=True)


class TestSessionSummarySurrogates:
    def test_head_scan_joins_surrogate_escape_pair(self):
        """头部扫描必须与 json.loads 一致：``\\ud83d\\ude00`` → 一个 emoji。"""
        value, end = cm._scan_json_string('"\\ud83d\\ude00x"', 0)
        assert value == f"{EMOJI}x"
        assert end == 15

    def test_head_scan_keeps_lone_escape_as_single_code_point(self):
        value, _end = cm._scan_json_string('"\\ud83d"', 0)
        assert value == LONE_HIGH

    def test_list_sessions_title_with_escaped_emoji(self, msgs_dir):
        """标题含代理对转义的会话：列表标题正常（不产生孤立代理项），索引可写。"""
        sid = "sum0001"
        (msgs_dir / f"{sid}.json").write_text(
            json.dumps(
                {
                    "id": sid,
                    "title": f"{LONE_HIGH}{LONE_LOW} 会话",
                    "model": "m",
                    "saved_at": "2026-10-20T00:00:00",
                    "message_count": 1,
                    "messages": [],
                },
                ensure_ascii=True,
            ),
            encoding="utf-8",
        )
        sessions = {s["id"]: s for s in cm.list_sessions()}
        assert sessions[sid]["title"] == f"{EMOJI} 会话"
        # 索引写盘成功（清洗后才写，不会抛 UnicodeEncodeError）
        assert cm._index_path().exists()

    def test_iter_sessions_survives_surrogate_title(self, msgs_dir):
        sid = "sum0002"
        (msgs_dir / f"{sid}.json").write_text(
            json.dumps(
                {
                    "id": sid,
                    "title": f"坏{LONE_HIGH}",
                    "model": "m",
                    "saved_at": "",
                    "message_count": 1,
                    "messages": [],
                },
                ensure_ascii=True,
            ),
            encoding="utf-8",
        )
        items = list(cm.iter_sessions())
        assert [item["id"] for item in items] == [sid]
        assert items[0]["title"] == f"坏{REPLACEMENT_CHARACTER}"


# ═══════════════════════════════════════════════════════════
# 3. checkpoint：断点保存 / 加载
# ═══════════════════════════════════════════════════════════

class TestCheckpointSurrogates:
    @pytest.fixture(autouse=True)
    def ckpt_file(self, tmp_path: Path, monkeypatch):
        target = tmp_path / ".chat" / "_checkpoint.json"
        monkeypatch.setattr(cp_mod, "CHECKPOINT_FILE", target)
        return target

    def test_save_with_lone_surrogate(self, ckpt_file: Path):
        cp_mod.save_checkpoint(
            [{"role": "user", "content": f"任务{LONE_HIGH}"}], "m",
        )
        data = json.loads(ckpt_file.read_text(encoding="utf-8"))
        assert data["messages"][0]["content"] == f"任务{REPLACEMENT_CHARACTER}"

    def test_save_with_split_pair(self, ckpt_file: Path):
        cp_mod.save_checkpoint(
            [{"role": "user", "content": f"{LONE_HIGH}{LONE_LOW}"}], "m",
        )
        data = json.loads(ckpt_file.read_text(encoding="utf-8"))
        assert data["messages"][0]["content"] == EMOJI

    def test_load_sanitizes_legacy_file(self, ckpt_file: Path):
        ckpt_file.parent.mkdir(parents=True, exist_ok=True)
        ckpt_file.write_text(
            json.dumps(
                {"messages": [{"role": "user", "content": f"x{LONE_HIGH}"}]},
                ensure_ascii=True,
            ),
            encoding="utf-8",
        )
        data = cp_mod.load_checkpoint()
        assert data["messages"][0]["content"] == f"x{REPLACEMENT_CHARACTER}"


# ═══════════════════════════════════════════════════════════
# 4. SessionPersistenceManager：autosave（报障端到端）/ load
# ═══════════════════════════════════════════════════════════

class _FakeStateMachine:
    def can(self, action):
        return False


class _FakeObservability:
    def gauge(self, name, value):
        pass


def _make_manager(messages, session_id="session-1"):
    from src.core.adapters.persistence import JsonFilePersistence
    from src.core.internal.session._session_persistence_manager import (
        SessionPersistenceManager,
    )

    return SessionPersistenceManager(
        messages_getter=lambda: messages,
        model_getter=lambda: "test-model",
        model_setter=lambda value: None,
        session_id_getter=lambda: session_id,
        session_id_setter=lambda value: None,
        persistence_port=JsonFilePersistence(),
        checkpoint_port=None,
        state_machine=_FakeStateMachine(),
        emit_fn=lambda *args, **kwargs: None,
        observability_port=_FakeObservability(),
        subagents_getter=lambda: [],
        subagents_setter=lambda value: None,
        log_getter=None,
        log_setter=lambda value: None,
    )


def test_auto_save_with_lone_surrogate_writes_session_file(msgs_dir: Path):
    """报障场景端到端：auto_save 不再因孤立代理项失败，会话文件正常落盘。"""
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": f"帮我修这个 {LONE_HIGH}"},
    ]
    manager = _make_manager(messages, session_id=None)

    sid = asyncio.run(manager.auto_save())

    assert sid
    path = msgs_dir / f"{sid}.json"
    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["messages"][0]["content"] == (
        f"帮我修这个 {REPLACEMENT_CHARACTER}"
    )


def test_load_sanitizes_session_file(msgs_dir: Path):
    """旧会话文件含孤立代理项转义 → 加载后消息列表已清洗（不再污染运行期）。"""
    sid = "legacy01"
    (msgs_dir / f"{sid}.json").write_text(
        json.dumps(
            {
                "id": sid,
                "title": "t",
                "model": "m",
                "saved_at": "",
                "message_count": 1,
                "messages": [{"role": "user", "content": f"x{LONE_HIGH}"}],
                "subagents": [],
                "session_log": [],
            },
            ensure_ascii=True,
        ),
        encoding="utf-8",
    )
    messages: list = []
    manager = _make_manager(messages, session_id=sid)

    data = manager.load(sid)

    assert data is not None
    assert messages[-1]["content"] == f"x{REPLACEMENT_CHARACTER}"


# ═══════════════════════════════════════════════════════════
# 5. BaseAgent：消息追加边界
# ═══════════════════════════════════════════════════════════

class TestBaseAgentMessageBoundaries:
    def test_user_message_sanitized(self):
        from src.core.base_agent import BaseAgent

        agent = BaseAgent()
        agent.add_user_message(f"问题{LONE_HIGH}")
        assert agent.messages[-1]["content"] == f"问题{REPLACEMENT_CHARACTER}"

    def test_assistant_message_sanitized(self):
        from src.core.base_agent import BaseAgent

        agent = BaseAgent()
        agent._append_assistant_message(
            f"回答{LONE_HIGH}{LONE_LOW}", reasoning_content=f"思考{LONE_HIGH}",
        )
        msg = agent.messages[-1]
        assert msg["content"] == f"回答{EMOJI}"
        assert msg["reasoning_content"] == f"思考{REPLACEMENT_CHARACTER}"
        assert msg["content"].encode("utf-8")

    def test_assistant_tool_call_arguments_sanitized(self):
        from src.core.base_agent import BaseAgent

        agent = BaseAgent()
        agent._append_assistant_message(
            None,
            tool_calls=[{
                "id": "c1",
                "name": "bash",
                "arguments": {"cmd": f"echo {LONE_HIGH}"},
            }],
        )
        msg = agent.messages[-1]
        args = msg["tool_calls"][0]["function"]["arguments"]
        assert LONE_HIGH not in args
        assert REPLACEMENT_CHARACTER in args

    def test_tool_result_sanitized(self):
        from src.core.base_agent import BaseAgent

        agent = BaseAgent()
        agent._append_tool_result("c1", f"stdout{LONE_HIGH}")
        assert agent.messages[-1]["content"] == f"stdout{REPLACEMENT_CHARACTER}"

    def test_tool_result_blocks_sanitized(self):
        from src.core.base_agent import BaseAgent

        agent = BaseAgent()
        agent._append_tool_result(
            "c1",
            [{"type": "text", "text": f"块{LONE_HIGH}"}],
        )
        assert agent.messages[-1]["content"] == [
            {"type": "text", "text": f"块{REPLACEMENT_CHARACTER}"},
        ]
