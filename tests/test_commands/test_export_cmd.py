"""测试导出命令 — _export_cmd.py（/export 导出 markdown，含 SubAgent 聊天信息）

覆盖：
- build_markdown：主对话渲染（含 system 过滤、tool_calls、推理内容）、SubAgent 记录渲染
- _code_block：内容含反引号时的栅栏加长
- _cmd_export：默认文件名 / 指定路径 / 越界路径拒绝 / 目录路径拒绝
- _collect_subagent_records：有/无 session、无 records 的降级
- SubAgent._record_to_parent：记录挂到父 Agent、同 label 覆盖去重
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.core.commands._export_cmd import (
    _cmd_export,
    _collect_subagent_records,
    _code_block,
    build_markdown,
)
from src.core.internal.commands._command_core import CommandContext


# ── build_markdown ────────────────────────────────────

class TestBuildMarkdown:
    def _sample_records(self) -> list[dict]:
        return [{
            "label": "agent-1",
            "description": "解析 user.py 模块",
            "agent_type": "map",
            "prompt": "请读取 src/user.py 并总结",
            "status": "done",
            "result": "user.py 包含 User 类",
            "error": "",
            "tool_calls_count": 2,
            "messages": [
                {"role": "system", "content": "你是只读分析代理"},
                {"role": "user", "content": "请读取 src/user.py 并总结"},
                {"role": "assistant", "content": "总结完成。"},
            ],
        }]

    def test_basic_structure(self):
        messages = [{"role": "user", "content": "你好"}]
        md = build_markdown(messages, [], "deepseek-chat", "2026-08-03T00:00:00")
        assert md.startswith("# 对话导出")
        assert "deepseek-chat" in md
        assert "### 消息 1" in md
        assert "### 🤖 用户" in md
        assert "你好" in md

    def test_system_messages_filtered(self):
        messages = [
            {"role": "system", "content": "你是助手"},
            {"role": "user", "content": "你好"},
        ]
        md = build_markdown(messages, [], "m", "2026-08-03T00:00:00")
        assert "你是助手" not in md
        assert "主对话消息数 | 1" in md
        assert "你好" in md

    def test_assistant_reasoning_and_tool_calls(self):
        messages = [
            {"role": "assistant", "content": "我来处理",
             "reasoning_content": "先想想"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "c1", "type": "function",
                 "function": {"name": "read_file",
                              "arguments": '{"path": "a.py"}'}},
            ]},
            {"role": "tool", "tool_call_id": "c1", "content": "def a(): pass"},
        ]
        md = build_markdown(messages, [], "m", "2026-08-03T00:00:00")
        assert "助手（思考）" in md
        assert "先想想" in md
        assert "工具调用：read_file" in md
        assert '"path": "a.py"' in md
        assert "工具结果（id: c1）" in md
        assert "def a(): pass" in md

    def test_subagent_records_rendered(self):
        md = build_markdown([], self._sample_records(), "m", "2026-08-03T00:00:00")
        assert "## 🤖 SubAgent 任务详情" in md
        assert "解析 user.py 模块" in md
        assert "`map`" in md
        assert "任务指令" in md
        assert "请读取 src/user.py 并总结" in md
        assert "执行结果" in md
        assert "user.py 包含 User 类" in md
        assert "内部对话记录" in md
        assert "你是只读分析代理" in md
        assert "总结完成" in md

    def test_subagent_count_in_header(self):
        md = build_markdown([], self._sample_records(), "m", "2026-08-03T00:00:00")
        assert "SubAgent 任务数 | 1" in md

    def test_empty_messages(self):
        md = build_markdown([], [], "m", "2026-08-03T00:00:00")
        assert "（无对话消息）" in md


# ── _code_block 反引号防护 ────────────────────────────

class TestCodeBlock:
    def test_normal_text(self):
        assert _code_block("hello") == "```\nhello\n```"

    def test_triple_backtick_inside(self):
        text = "```python\nprint(1)\n```"
        block = _code_block(text)
        # 栅栏应加长到 4 个反引号，避免与内容冲突
        assert block.startswith("````")
        assert "hello" not in block  # 无内容泄漏

    def test_empty_text(self):
        assert _code_block("   ") == "(空)"

    def test_json_lang(self):
        assert _code_block('{"a": 1}', "json") == "```json\n{\"a\": 1}\n```"


# ── _cmd_export ───────────────────────────────────────

def _make_ctx(messages: list[dict], agent=None, model="deepseek-chat",
              arg: str = "") -> CommandContext:
    session = None
    if agent is not None:
        session = MagicMock()
        session.agent = agent
    return CommandContext(
        messages=messages,
        state={"model": model},
        arg=arg,
        build_system_prompt=None,
        get_user_input=None,
        context_manager=None,
        session=session,
    )


class TestCmdExport:
    def test_default_filename(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        messages = [{"role": "user", "content": "你好"}]
        ctx = _make_ctx(messages, agent=MagicMock())
        assert _cmd_export(ctx) is True
        files = list(tmp_path.glob("chat_export_*.md"))
        assert len(files) == 1
        assert "你好" in files[0].read_text(encoding="utf-8")

    def test_specified_path(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        messages = [{"role": "user", "content": "内容"}]
        ctx = _make_ctx(messages, arg="out.md")
        assert _cmd_export(ctx) is True
        assert (tmp_path / "out.md").exists()
        assert "内容" in (tmp_path / "out.md").read_text(encoding="utf-8")

    def test_path_outside_cwd_rejected(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        ctx = _make_ctx([{"role": "user", "content": "x"}],
                        arg=str(tmp_path.parent / "evil.md"))
        assert _cmd_export(ctx) is True
        # 越界文件不应被创建
        assert not (tmp_path.parent / "evil.md").exists()

    def test_directory_path_rejected(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "sub").mkdir()
        ctx = _make_ctx([{"role": "user", "content": "x"}],
                        arg="sub")
        assert _cmd_export(ctx) is True
        assert not (tmp_path / "sub" / "chat_export").exists()

    def test_includes_subagent_records(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        agent = MagicMock()
        agent._subagent_records = [{
            "label": "agent-1", "description": "并发任务", "agent_type": "execute",
            "prompt": "任务指令内容", "status": "done", "result": "结果内容",
            "error": "", "tool_calls_count": 1,
            "messages": [{"role": "user", "content": "子任务输入"}],
        }]
        ctx = _make_ctx([{"role": "user", "content": "主对话"}], agent=agent)
        assert _cmd_export(ctx) is True
        files = list(tmp_path.glob("chat_export_*.md"))
        content = files[0].read_text(encoding="utf-8")
        assert "并发任务" in content
        assert "任务指令内容" in content
        assert "子任务输入" in content

    def test_write_failure_reports_error(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        ctx = _make_ctx([{"role": "user", "content": "x"}], arg="out.md")
        with patch("pathlib.Path.write_text", side_effect=OSError("disk full")):
            assert _cmd_export(ctx) is True
        assert not (tmp_path / "out.md").exists()


# ── _collect_subagent_records ─────────────────────────

class TestCollectSubagentRecords:
    def test_with_session_and_records(self):
        agent = MagicMock()
        agent._subagent_records = [{"label": "a1"}]
        session = MagicMock()
        session.agent = agent
        ctx = _make_ctx([], agent=agent)
        records = _collect_subagent_records(ctx)
        assert len(records) == 1

    def test_no_session(self):
        ctx = _make_ctx([], agent=None)
        assert _collect_subagent_records(ctx) == []

    def test_session_but_no_agent(self):
        session = MagicMock()
        session.agent = None
        ctx = CommandContext(
            messages=[], state={}, arg="",
            build_system_prompt=None, get_user_input=None,
            context_manager=None, session=session,
        )
        assert _collect_subagent_records(ctx) == []

    def test_no_records(self):
        agent = MagicMock()
        del agent._subagent_records  # 模拟属性不存在
        session = MagicMock()
        session.agent = agent
        ctx = _make_ctx([], agent=agent)
        assert _collect_subagent_records(ctx) == []


# ── SubAgent._record_to_parent ────────────────────────

class TestSubAgentRecordToParent:
    class _FakeParent:
        """模拟父 Agent：真实对象（MagicMock 会干扰 getattr 初始化逻辑）。"""
        def __init__(self):
            self._subagent_records = None

    def _make_subagent(self, parent, label="agent-1", messages=None):
        from src.core.subagent import SubAgent
        sa = SubAgent.__new__(SubAgent)
        sa.label = label
        sa.description = "描述"
        sa.agent_type = "execute"
        sa.prompt = "指令"
        sa.messages = messages or [{"role": "user", "content": "指令"}]
        sa.result = "结果"
        sa.error = ""
        sa.tool_calls_count = 2
        sa.parent = parent
        return sa

    def test_records_to_parent(self):
        parent = self._FakeParent()
        sa = self._make_subagent(parent)
        sa._record_to_parent()
        assert len(parent._subagent_records) == 1
        rec = parent._subagent_records[0]
        assert rec["label"] == "agent-1"
        assert rec["description"] == "描述"
        assert rec["messages"][0]["content"] == "指令"
        assert rec["status"] == "done"

    def test_error_status(self):
        parent = self._FakeParent()
        sa = self._make_subagent(parent)
        sa.error = "boom"
        sa._record_to_parent()
        assert parent._subagent_records[0]["status"] == "error"

    def test_same_label_overwrites(self):
        parent = self._FakeParent()
        sa = self._make_subagent(parent)
        sa._record_to_parent()
        sa.result = "新结果"
        sa._record_to_parent()
        assert len(parent._subagent_records) == 1
        assert parent._subagent_records[0]["result"] == "新结果"

    def test_no_parent_noop(self):
        sa = self._make_subagent(None)
        sa._record_to_parent()  # 不应抛异常


# ── 会话切换时的 subagent 记录生命周期 ───────────────

class TestSubagentRecordLifecycle:
    """/clear 清空记录；/load 恢复保存的 subagent 记录。"""

    class _FakeAgent:
        def __init__(self):
            self._subagent_records = [{"label": "agent-1"}]

    class _FakeSession:
        def __init__(self, agent):
            self.agent = agent

    def _make_session(self):
        agent = self._FakeAgent()
        return agent, self._FakeSession(agent)

    def test_clear_clears_records(self):
        from src.core.commands import handle_command

        agent, session = self._make_session()
        messages = [{"role": "system", "content": "s"},
                    {"role": "user", "content": "hi"}]
        ok = handle_command("/clear", messages, {}, lambda: ["s"],
                            lambda s="": s, session=session)
        assert ok is True
        assert agent._subagent_records == []

    def test_load_restores_records(self, tmp_path, monkeypatch):
        from src.core.commands import handle_command

        monkeypatch.chdir(tmp_path)
        agent, session = self._make_session()
        messages = [{"role": "system", "content": "s"}]
        # 预置一个含 subagent 记录的会话文件
        from src.chat_msgs import save_session, delete_session
        records = [{
            "label": "agent-1", "description": "历史任务", "agent_type": "map",
            "prompt": "历史指令", "status": "done", "result": "历史结果",
            "error": "", "tool_calls_count": 1,
            "messages": [{"role": "user", "content": "历史指令"}],
        }]
        sid = save_session([{"role": "user", "content": "旧会话"}],
                           model="m", subagents=records)
        try:
            ok = handle_command(f"/load {sid}", messages, {}, lambda: [],
                                lambda s="": s, session=session)
            assert ok is True
            # 会话保存的 subagent 记录应恢复到 agent（供 /export 导出）
            assert agent._subagent_records == records
            assert any(m.get("content") == "旧会话" for m in messages)
        finally:
            delete_session(sid)

    def test_load_restores_empty_records(self, tmp_path, monkeypatch):
        """加载无 subagents 字段的旧会话 → agent 记录恢复为空。"""
        from src.core.commands import handle_command

        monkeypatch.chdir(tmp_path)
        agent, session = self._make_session()
        messages = [{"role": "system", "content": "s"}]
        from src.chat_msgs import save_session, delete_session
        sid = save_session([{"role": "user", "content": "旧会话"}], model="m")
        try:
            ok = handle_command(f"/load {sid}", messages, {}, lambda: [],
                                lambda s="": s, session=session)
            assert ok is True
            assert agent._subagent_records == []
        finally:
            delete_session(sid)


# ── SubAgent 记录持久化（保存到 json / 加载恢复） ─────

class TestSubagentPersistence:
    """save_session 将 subagent 记录写入 json，load_session 原样返回。"""

    def _records(self):
        return [{
            "label": "agent-1", "description": "并发任务", "agent_type": "execute",
            "prompt": "指令", "status": "done", "result": "结果",
            "error": "", "tool_calls_count": 1,
            "messages": [{"role": "user", "content": "指令"}],
        }]

    def test_save_session_writes_subagents(self):
        from src.chat_msgs import save_session, load_session, delete_session
        records = self._records()
        sid = save_session([{"role": "user", "content": "主对话"}],
                           model="m", subagents=records)
        try:
            data = load_session(sid)
            assert data is not None
            assert data["subagents"] == records
            # 无 subagents 时保存为空列表
            sid2 = save_session([{"role": "user", "content": "x"}], model="m")
            try:
                data2 = load_session(sid2)
                assert data2["subagents"] == []
            finally:
                delete_session(sid2)
        finally:
            delete_session(sid)

    def test_load_session_old_file_without_subagents(self):
        """旧会话文件（无 subagents 字段）加载时返回空列表（缺省兼容）。"""
        from src.chat_msgs import save_session, load_session, delete_session, CHAT_MSGS_DIR
        import json
        sid = save_session([{"role": "user", "content": "旧"}], model="m")
        try:
            # 模拟旧格式：手工移除 subagents 字段
            fp = CHAT_MSGS_DIR / f"{sid}.json"
            raw = json.loads(fp.read_text(encoding="utf-8"))
            raw.pop("subagents", None)
            fp.write_text(json.dumps(raw), encoding="utf-8")
            data = load_session(sid)
            assert data["subagents"] == []
        finally:
            delete_session(sid)

    def test_persistence_manager_roundtrip(self):
        """SessionPersistenceManager save/load 应保存并恢复 subagent 记录。"""
        from src.chat_msgs import delete_session
        from src.core.adapters.persistence import JsonFilePersistence
        from src.core.internal.session._session_persistence_manager import (
            SessionPersistenceManager,
        )
        from src.core.state_machine import SessionStateMachine

        class _Obs:
            def gauge(self, *a, **k):
                pass

        class _Agent:
            _subagent_records = self._records()

        agent = _Agent()
        state_machine = SessionStateMachine()

        def _make_mgr():
            return SessionPersistenceManager(
                messages_getter=lambda: [{"role": "user", "content": "主对话"}],
                model_getter=lambda: "deepseek-chat",
                model_setter=lambda v: None,
                session_id_getter=lambda: None,
                session_id_setter=lambda v: None,
                persistence_port=JsonFilePersistence(),
                checkpoint_port=JsonFilePersistence(),
                state_machine=state_machine,
                emit_fn=lambda *a, **k: None,
                observability_port=_Obs(),
                subagents_getter=lambda: list(getattr(agent, "_subagent_records", None) or []),
                subagents_setter=lambda v: setattr(agent, "_subagent_records", list(v or [])),
            )

        mgr = _make_mgr()
        sid = mgr.save()
        try:
            # 模拟新会话：清空记录后加载，应恢复
            agent._subagent_records = []
            data = _make_mgr().load(sid)
            assert data is not None
            assert agent._subagent_records == self._records()
        finally:
            delete_session(sid)
