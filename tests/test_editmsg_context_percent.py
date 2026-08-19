"""editmsg / deitmsg 编辑后上下文使用百分比刷新（2026-08-19 用户需求修复）单元测试。

修复背景：用户报告「/editmsg 按回车后不更新上下文百分比」。
根因：Edit/Delete/Resume 命令经 ``_truncate_messages`` 直接操作
``agent.messages`` 列表（``del messages[keep_idx:]``），**未触发**
ContextManager 缓存同步点（refresh_usage）——模式行行首 ``main · N%``
保持旧值直至下一次消息追加（用户看到的：按回车确认编辑后百分比不变）。

修复：``_truncate_messages`` 截断消息后调用 ``agent._refresh_context_usage()``
（BaseAgent 方法，getattr 防御——SubAgent/测试桩无 context_manager 或
方法时静默跳过）→ ``refresh_usage`` 按 len 变化自动 resync 重算 → 全局
快照即时更新 → input_area snap_key（ctx_percent）变化 → 模式行即时刷新
（snap_key 刷新链路已由 test_bg_task_mode_line.TestInputSnapKeyBgCount 覆盖）。

覆盖路径：/editmsg（Edit/Delete/Resume）、/deitmsg（直接截断）共用
``_truncate_messages``——单点修复全路径覆盖。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.core.internal.commands._command_core import CommandContext
from src.tui.pipeline.message_editor import (
    DeleteCommand,
    EditCommand,
    ResumeCommand,
    _truncate_messages,
)


# ── 构造 / 隔离 ────────────────────────────────────────

def _make_agent_with_cm(messages, ctx_tokens: int = 10000):
    """构造带 context_manager 的 agent（主 Agent 形态，与 session 初始化同构）。"""
    from src.core.adapters.config import MockConfigAdapter
    from src.core.base_agent import BaseAgent
    from src.core.context_manager import ContextManager

    agent = BaseAgent()
    agent.messages = messages
    cfg = MockConfigAdapter({"model_context_tokens": ctx_tokens})
    agent.context_manager = ContextManager(agent.messages, "m", config_port=cfg)
    return agent


def _pct():
    from src.core.context_manager import get_context_usage_percent
    return get_context_usage_percent()


@pytest.fixture(autouse=True)
def _isolate_global(monkeypatch):
    """每个测试：隔离全局上下文使用率快照 + 沙盒管理器（无外部状态干扰）。"""
    from src.core.context_manager import set_context_usage_percent
    set_context_usage_percent(None)
    # _truncate_messages 依赖的沙盒管理器置空（无沙盒状态，走跳过分支）
    monkeypatch.setattr(
        "src.tui.pipeline.message_editor._get_sandbox_manager", lambda: None,
    )
    yield
    set_context_usage_percent(None)


# ── 核心修复：_truncate_messages 截断后刷新 ─────────────

def test_truncate_messages_refreshes_context_percent_down():
    """截断消息（edit 语义）→ 全局上下文百分比即时下降（核心修复）。"""
    messages = [
        {"role": "system", "content": "s" * 3000},      # 900 tok
        {"role": "user", "content": "a" * 3000},        # 900 tok
        {"role": "assistant", "content": "b" * 3000},   # 900 tok
        {"role": "user", "content": "c" * 3000},        # 900 tok
    ]
    agent = _make_agent_with_cm(messages)  # init 即刷新 → 3600/10000 = 36.0%
    assert _pct() == 36.0
    _truncate_messages(agent, 2)
    assert len(messages) == 2               # 截断生效
    assert _pct() == 18.0                   # 1800/10000 —— 即时下降


def test_truncate_messages_no_op_keeps_percent():
    """keep_idx 在末尾（无可截断）→ 百分比不变化（幂等）。"""
    messages = [
        {"role": "system", "content": "s" * 3000},
        {"role": "user", "content": "a" * 3000},
    ]
    agent = _make_agent_with_cm(messages)   # 1800/10000 = 18.0%
    assert _pct() == 18.0
    _truncate_messages(agent, len(messages))
    assert len(messages) == 2               # 无删除
    assert _pct() == 18.0                   # 不变（懒同步仍精确）


def test_truncate_messages_agent_without_cm_no_crash():
    """agent 无 context_manager（SimpleNamespace 测试桩）→ 不崩溃（兼容既有路径）。"""
    messages = [{"role": "user", "content": "hi"}]
    agent = SimpleNamespace(messages=messages)
    _truncate_messages(agent, 0)            # 无 _refresh_context_usage → 跳过
    assert messages == []


def test_truncate_messages_with_cm_method_raising_safe():
    """_refresh_context_usage 抛异常 → 静默降级（不中断截断流程）。"""
    messages = [
        {"role": "system", "content": "s" * 3000},
        {"role": "user", "content": "a" * 3000},
    ]

    class _RaisingAgent(SimpleNamespace):
        def _refresh_context_usage(self):
            raise RuntimeError("boom")

    agent = _RaisingAgent(messages=messages)
    _truncate_messages(agent, 1)            # 异常被吞，截断仍完成
    assert len(messages) == 1


# ── 命令级：Edit/Delete/Resume 均触发刷新 ──────────────

def test_edit_command_refreshes_context_percent():
    """EditCommand 编辑（截断到光标消息）→ 百分比即时下降。"""
    messages = [
        {"role": "system", "content": "s" * 3000},
        {"role": "user", "content": "a" * 3000},
        {"role": "assistant", "content": "b" * 3000},
        {"role": "user", "content": "c" * 3000},
    ]
    agent = _make_agent_with_cm(messages)
    assert _pct() == 36.0
    state = {}
    assert EditCommand(agent, 2).execute(state) is True
    assert len(messages) == 2
    assert state["prefill"] == "b" * 3000   # 预填被编辑消息
    assert _pct() == 18.0


def test_delete_command_refreshes_context_percent():
    """DeleteCommand 删除（截断到光标消息）→ 百分比即时下降。"""
    messages = [
        {"role": "system", "content": "s" * 3000},
        {"role": "user", "content": "a" * 3000},
        {"role": "assistant", "content": "b" * 3000},
        {"role": "user", "content": "c" * 3000},
    ]
    agent = _make_agent_with_cm(messages)
    assert _pct() == 36.0
    assert DeleteCommand(agent, 2).execute({}) is True
    assert len(messages) == 2
    assert _pct() == 18.0


def test_resume_command_refreshes_context_percent():
    """ResumeCommand 恢复（截断到光标消息之后）→ 百分比即时下降。"""
    messages = [
        {"role": "system", "content": "s" * 3000},
        {"role": "user", "content": "a" * 3000},
        {"role": "assistant", "content": "b" * 3000},
        {"role": "user", "content": "c" * 3000},
    ]
    agent = _make_agent_with_cm(messages)
    assert _pct() == 36.0
    state = {}
    assert ResumeCommand(agent, 2).execute(state) is True
    assert len(messages) == 3               # 保留 index 2（含）
    assert _pct() == 27.0                   # 2700/10000


def test_deitmsg_plugin_path_refreshes_context_percent():
    """/deitmsg 直接截断（复用 _truncate_messages）→ 百分比即时下降。"""
    messages = [
        {"role": "system", "content": "s" * 3000},
        {"role": "user", "content": "a" * 3000},
        {"role": "assistant", "content": "b" * 3000},
        {"role": "user", "content": "c" * 3000},
    ]
    agent = _make_agent_with_cm(messages)
    assert _pct() == 36.0
    # deitmsg 定位最后一条 user（index 3）→ _truncate_messages(agent, 3)
    _truncate_messages(agent, 3)
    assert len(messages) == 3
    assert _pct() == 27.0


# ── 端到端：编辑 → 全局快照 → 模式行显示 ────────────────

def test_edit_then_mode_line_shows_updated_percent():
    """编辑生效后模式行行首显示新百分比（_build_lines 集成链路）。"""
    from src.tui.app.input_area import _build_lines

    messages = [
        {"role": "system", "content": "s" * 3000},
        {"role": "user", "content": "a" * 3000},
        {"role": "assistant", "content": "b" * 3000},
        {"role": "user", "content": "c" * 3000},
    ]
    agent = _make_agent_with_cm(messages)
    assert _pct() == 36.0

    fiber = SimpleNamespace(
        props={"text": "", "cursor_pos": 0, "completion": None,
               "status_active": False, "cpu": 3, "mem": 12, "width": 80,
               "bg_bash_count": 0, "bg_subagent_count": 0},
        layout_box=SimpleNamespace(w=80, x=0, y=0),
    )
    before = "".join(r.text for r in _build_lines(fiber)[-1].runs)
    assert "main \u00b7 36.0%" in before

    # 编辑（截断）→ 全局快照下降 → 同一 fiber 重渲染模式行即时更新
    EditCommand(agent, 2).execute({})
    assert _pct() == 18.0
    after = "".join(r.text for r in _build_lines(fiber)[-1].runs)
    assert "main \u00b7 18.0%" in after
    assert "main \u00b7 36.0%" not in after


# ── /undo /retry /edit /clear 命令路径（同根因修复） ──────

def _cmd_ctx(messages, cm):
    """构造 CommandContext（命令插件路径，TUI _handle_command_msg 同构）。"""
    from src.core.internal.commands._command_core import CommandContext
    return CommandContext(
        messages=messages, state={"model": "", "retry": False, "prefill": ""},
        arg="",
        build_system_prompt=lambda: [],
        get_user_input=lambda prompt="": "",
        context_manager=cm,
    )


def test_cmd_undo_refreshes_context_percent():
    """/undo 撤销上一轮（移除 assistant + 对应 user）→ 百分比即时下降。"""
    from src.core.commands._session_cmd import _cmd_undo
    messages = [
        {"role": "system", "content": "s" * 3000},
        {"role": "user", "content": "a" * 3000},
        {"role": "assistant", "content": "b" * 3000},
    ]
    agent = _make_agent_with_cm(messages)
    assert _pct() == 27.0
    ctx = _cmd_ctx(messages, agent.context_manager)
    _cmd_undo(ctx)
    assert len(messages) == 1               # 撤销 assistant + user → 仅 system
    assert _pct() == 9.0


def test_cmd_undo_last_user_only():
    """/undo 末尾为 user（未回答）→ 仅撤销该 user。"""
    from src.core.commands._session_cmd import _cmd_undo
    messages = [
        {"role": "system", "content": "s" * 3000},
        {"role": "user", "content": "a" * 3000},
        {"role": "assistant", "content": "b" * 3000},
        {"role": "user", "content": "c" * 3000},
    ]
    agent = _make_agent_with_cm(messages)
    assert _pct() == 36.0
    ctx = _cmd_ctx(messages, agent.context_manager)
    _cmd_undo(ctx)
    assert len(messages) == 3               # 仅 pop 末尾 user
    assert _pct() == 27.0


def test_cmd_undo_no_removal_no_refresh_crash():
    """/undo 无消息可撤销 → 百分比不变且不崩溃。"""
    from src.core.commands._session_cmd import _cmd_undo
    messages = [{"role": "system", "content": "s" * 3000}]
    agent = _make_agent_with_cm(messages)
    assert _pct() == 9.0
    ctx = _cmd_ctx(messages, agent.context_manager)
    _cmd_undo(ctx)                          # 无可 pop → removed=0 → 不刷新
    assert _pct() == 9.0


def test_cmd_undo_without_cm_no_crash():
    """/undo 的 context_manager 为 None → 不崩溃（非 TUI/测试桩路径）。"""
    from src.core.commands._session_cmd import _cmd_undo
    messages = [
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "b"},
    ]
    ctx = _cmd_ctx(messages, None)
    _cmd_undo(ctx)
    assert len(messages) == 1               # 无 system 时 pop user 条件 len>1 不满足


def test_cmd_retry_refreshes_context_percent():
    """/retry 移除回答 → 百分比即时下降（随后重新生成时再上升）。"""
    from src.core.commands._session_cmd import _cmd_retry
    messages = [
        {"role": "system", "content": "s" * 3000},
        {"role": "user", "content": "a" * 3000},
        {"role": "assistant", "content": "b" * 3000},
    ]
    agent = _make_agent_with_cm(messages)
    assert _pct() == 27.0
    ctx = _cmd_ctx(messages, agent.context_manager)
    _cmd_retry(ctx)
    assert len(messages) == 2               # 仅移除 assistant
    assert _pct() == 18.0


def test_cmd_edit_refreshes_context_percent():
    """/edit 截断重发（截断最后一条 user + 追加新 user）→ 百分比更新。"""
    from src.core.commands._session_cmd import _cmd_edit
    messages = [
        {"role": "system", "content": "s" * 3000},
        {"role": "user", "content": "a" * 3000},
        {"role": "assistant", "content": "b" * 3000},
        {"role": "user", "content": "c" * 3000},
    ]
    agent = _make_agent_with_cm(messages)
    assert _pct() == 36.0
    ctx = CommandContext(
        messages=messages, state={"model": "", "retry": False, "prefill": ""},
        arg="",
        build_system_prompt=lambda: [],
        get_user_input=lambda prompt="": "新内容" * 100,   # 300 tok
        context_manager=agent.context_manager,
    )
    _cmd_edit(ctx)
    # system + user(a) + assistant(b) + user(新内容×100)
    # = 900*3 + 750（300 个中文字符 × 2.5）= 3450 tok → 34.5%
    assert len(messages) == 4
    assert messages[-1]["content"] == "新内容" * 100
    assert _pct() == 34.5


def test_cmd_clear_refreshes_context_percent():
    """/clear 清空（保留 system）→ 百分比下降（force 重算）。"""
    from src.core.commands._session_cmd import _cmd_clear
    messages = [
        {"role": "system", "content": "s" * 3000},
        {"role": "user", "content": "a" * 3000},
        {"role": "assistant", "content": "b" * 3000},
        {"role": "user", "content": "c" * 3000},
    ]
    agent = _make_agent_with_cm(messages)
    assert _pct() == 36.0
    ctx = CommandContext(
        messages=messages, state={}, arg="",
        build_system_prompt=lambda: ["s" * 3000],
        get_user_input=lambda prompt="": "",
        context_manager=agent.context_manager,
    )
    _cmd_clear(ctx)
    assert len(messages) == 1               # 仅 system 保留
    assert _pct() == 9.0


def test_session_mgr_undo_refreshes_context_percent():
    """SessionMessagingManager.undo_last_round 撤销 → 百分比即时下降。"""
    from src.core.internal.session._session_messaging_manager import (
        SessionMessagingManager,
    )
    messages = [
        {"role": "system", "content": "s" * 3000},
        {"role": "user", "content": "a" * 3000},
        {"role": "assistant", "content": "b" * 3000},
    ]
    agent = _make_agent_with_cm(messages)
    assert _pct() == 27.0
    obs = SimpleNamespace(gauge=lambda *a, **k: None)
    mgr = SessionMessagingManager(
        messages=messages,
        model_getter=lambda: "m",
        context_manager_getter=lambda: agent.context_manager,
        context_manager_setter=lambda v: None,
        sandbox_getter=lambda: None,
        state_machine=None,
        emit_fn=lambda *a, **k: None,
        observability_port=obs,
        retry_pending_getter=lambda: False,
        retry_pending_setter=lambda v: None,
    )
    removed = mgr.undo_last_round()
    assert removed == 2                     # assistant + user
    assert len(messages) == 1
    assert _pct() == 9.0


def test_session_mgr_undo_without_cm_no_crash():
    """undo_last_round 的 context_manager 为 None → 不崩溃（getter 防御）。"""
    from src.core.internal.session._session_messaging_manager import (
        SessionMessagingManager,
    )
    messages = [
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "b"},
    ]
    obs = SimpleNamespace(gauge=lambda *a, **k: None)
    mgr = SessionMessagingManager(
        messages=messages,
        model_getter=lambda: "m",
        context_manager_getter=lambda: None,
        context_manager_setter=lambda v: None,
        sandbox_getter=lambda: None,
        state_machine=None,
        emit_fn=lambda *a, **k: None,
        observability_port=obs,
        retry_pending_getter=lambda: False,
        retry_pending_setter=lambda v: None,
    )
    removed = mgr.undo_last_round()
    assert removed == 2
    assert messages == []
