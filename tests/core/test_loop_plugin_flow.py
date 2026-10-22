"""``LoopPlugin`` 端到端编排：批次数语义（批内无限续跑、false→批次数-1、0 结束）。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.core.commands.plugins.loop_plugin import LoopPlugin, _full_compact_context
from src.core.loop_prompt import (
    LOOP_PROMPT_MARKER,
    LoopPromptDecision,
    LoopPromptService,
    build_system_prompt,
)


class _FakeBottomBar:
    def __init__(self):
        self.enabled = 0
        self.disabled = 0
        self.reset = 0

    def enable_status(self):
        self.enabled += 1

    def disable_status(self):
        self.disabled += 1

    def reset_tool_count(self):
        self.reset += 1


class _FakeChatUI:
    def __init__(self):
        self.lines = []
        self.bottom_bar = _FakeBottomBar()
        self.opened = []
        self.statuses = []
        self.closed = 0

    def write_line(self, text):
        self.lines.append(str(text))

    def open_loop_floating_window(self, total, prompt, runs_per_round=1):
        self.opened.append((total, prompt, runs_per_round))

    def set_loop_status(self, **fields):
        self.statuses.append(fields)

    def close_loop_floating_window(self):
        self.closed += 1


class _FakeContextManager:
    """压缩入口替身：记录调用参数，可返回成功 / 失败结果或抛异常。"""

    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []
        self.on_call = None

    def check_and_compress(self, force=False):
        self.calls.append(force)
        if self.on_call is not None:
            self.on_call()
        if self.error is not None:
            raise self.error
        return self.result


class _FakeSession:
    def __init__(self, service, answers):
        self.loop_prompt_service = service
        self.model = "test-model"
        self.messages = []
        self.answers = list(answers)
        self.prompts = []
        self.cleared = 0
        self.context_manager = None
        self._service = service
        self.interrupted = False

    def clear_messages(self):
        self.cleared += 1
        self.messages = []

    async def run_round(self, prompt):
        self.prompts.append(prompt)
        if self.interrupted:
            return {"interrupted": True}
        self.messages = [
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": self.answers.pop(0)},
        ]
        # 模拟 Agent.run() 在等待后台任务前调用续跑提词钩子。
        await self._service.on_agent_round_end()
        return {"interrupted": False}


def _make_env(monkeypatch, responses, answers, *, context_manager=None):
    """构造 LoopPlugin 运行环境（fake UI / session / service）。"""
    ui = _FakeChatUI()
    pending = list(responses)

    def call_model(_messages):
        return pending.pop(0), ""

    def emit(event, **data):
        if event == "loop_prompt_generating":
            ui.set_loop_status(generating=data.get("generating"))
        elif event == "loop_prompt_generated":
            keep = data.get("should_continue") is not False and not data.get("error")
            ui.set_loop_status(
                generating=False,
                next_prompt=data.get("prompt", "") if keep else "",
            )

    service = LoopPromptService(
        messages_getter=lambda: session.messages,
        model_getter=lambda: "test-model",
        emit=emit,
        call_model=call_model,
        config_getter=lambda: {"retry_once": False},
    )
    session = _FakeSession(service, answers)
    session.context_manager = context_manager
    loop_state: dict = {}
    loop = SimpleNamespace(
        _chat_ui=ui,
        _loop_state=loop_state,
        _force_exit=SimpleNamespace(clear=lambda: None),
    )

    async def _no_snapshot(*_args, **_kwargs):
        return None

    monkeypatch.setattr("src.app_loop._save_loop_snapshot", _no_snapshot)
    monkeypatch.setattr(
        "src.core.interrupt_state.reset_interrupt_async", lambda *a, **k: None,
    )
    monkeypatch.setattr("src.core.stats.reset_token_speed", lambda *a, **k: None)

    plugin = LoopPlugin()
    plugin.bind_loop(loop)
    return ui, session, plugin, loop_state


@pytest.mark.asyncio
async def test_single_batch_runs_until_false(monkeypatch):
    """``/loop 1``：一批内 continue=true 无限续跑，false 后结束。"""
    ui, session, plugin, _state = _make_env(
        monkeypatch,
        [
            '{"continue": true, "prompt": "补充测试"}',
            '{"continue": true, "prompt": "继续验证"}',
            '{"continue": false, "prompt": ""}',
        ],
        ["一", "二", "三"],
    )
    await plugin.async_execute(SimpleNamespace(session=session, arg="1 实现功能"))

    assert len(session.prompts) == 3
    assert session.prompts[0] == "实现功能"
    assert session.prompts[1] == f"{LOOP_PROMPT_MARKER}补充测试"
    assert session.prompts[2] == f"{LOOP_PROMPT_MARKER}继续验证"
    assert session.cleared == 1
    assert ui.opened == [(1, "实现功能", 1)]
    assert ui.bottom_bar.enabled == 1
    assert ui.bottom_bar.disabled == 1
    assert ui.closed == 1

    text = "\n".join(ui.lines)
    assert "第 1/1 批开始" in text
    assert "第 1/1 批完成" in text
    assert "全部 1 批执行完毕" in text
    # 每轮继续即时显示；最后一轮（false）在「本批完成」之后显示。
    assert len([line for line in ui.lines if "LLM 输出" in line]) == 3
    # ★ 2026-10-22 用户需求：聊天区不再显示 LLM 输入 / 输出原文，
    #   只显示解析结果（continue 判定 / 提词）。
    assert "LLM 输入" not in text
    assert '{"continue"' not in text
    assert "解析:" in text
    done_idx = next(
        i for i, line in enumerate(ui.lines) if "第 1/1 批完成" in line
    )
    last_out_idx = max(
        i for i, line in enumerate(ui.lines) if "LLM 输出" in line
    )
    assert last_out_idx > done_idx


@pytest.mark.asyncio
async def test_multi_batch_resets_messages_and_prompt(monkeypatch):
    """多批：每批清空消息，并重新使用用户最初的提词。"""
    ui, session, plugin, _state = _make_env(
        monkeypatch,
        [
            '{"continue": true, "prompt": "A"}',
            '{"continue": false, "prompt": ""}',
            '{"continue": true, "prompt": "B"}',
            '{"continue": false, "prompt": ""}',
        ],
        ["一", "二", "三", "四"],
    )
    await plugin.async_execute(SimpleNamespace(session=session, arg="2 实现功能"))

    assert len(session.prompts) == 4
    assert session.cleared == 2
    assert session.prompts[0] == "实现功能"
    assert session.prompts[1] == f"{LOOP_PROMPT_MARKER}A"
    assert session.prompts[2] == "实现功能"  # 第二批重置为最初提词
    assert session.prompts[3] == f"{LOOP_PROMPT_MARKER}B"
    assert ui.opened == [(2, "实现功能", 1)]

    text = "\n".join(ui.lines)
    assert "第 1/2 批完成" in text
    assert "第 2/2 批完成" in text
    assert "全部 2 批执行完毕" in text
    # 两批各显示一次 false 输出（每批最后一块在「本批完成」后）。
    assert len([line for line in ui.lines if "LLM 输出" in line]) == 4


@pytest.mark.asyncio
async def test_takeover_single_batch_does_not_rerun_current_round(monkeypatch):
    """流式接管：当前轮计为第 1 批第 1 次运行，不清空、不重跑。"""
    ui, session, plugin, state = _make_env(
        monkeypatch,
        ['{"continue": false, "prompt": ""}'],
        [],
    )
    session.messages = [
        {"role": "system", "content": "系统"},
        {"role": "user", "content": "最初任务"},
        {"role": "assistant", "content": "当前轮回答"},
    ]
    state["_loop_takeover"] = {"count": 1, "prompt": ""}

    await plugin.async_execute(SimpleNamespace(session=session, arg="1"))

    assert session.prompts == []
    assert session.cleared == 0
    assert ui.opened == [(1, "最初任务", 1)]
    text = "\n".join(ui.lines)
    assert "接管当前流式轮（1 批）" in text
    assert "第 1 次运行（接管当前轮，不重跑）" in text
    assert "第 1/1 批完成（本批运行 1 次）" in text
    assert "全部 1 批执行完毕" in text
    assert "解析:" in text
    assert "continue=false" in text
    assert "第 1/1 批开始" not in text
    assert state.get("_loop_mode") is False


@pytest.mark.asyncio
async def test_takeover_multi_batch_reuses_first_human_message(monkeypatch):
    """接管后：第 1 批续跑；后续批次清空并复用接管提词。"""
    ui, session, plugin, state = _make_env(
        monkeypatch,
        [
            '{"continue": true, "prompt": "继续补充"}',
            '{"continue": false, "prompt": ""}',
            '{"continue": false, "prompt": ""}',
        ],
        ["二", "三"],
    )
    session.messages = [
        {"role": "system", "content": "系统"},
        {"role": "user", "content": "最初任务"},
        {"role": "assistant", "content": "当前轮回答"},
    ]
    state["_loop_takeover"] = {"count": 2, "prompt": ""}

    await plugin.async_execute(SimpleNamespace(session=session, arg="2"))

    assert session.prompts == [
        f"{LOOP_PROMPT_MARKER}继续补充",
        "最初任务",
    ]
    assert session.cleared == 1
    assert ui.opened == [(2, "最初任务", 1)]
    text = "\n".join(ui.lines)
    assert "第 1/2 批 · 第 1 次运行（接管当前轮，不重跑）" in text
    assert "第 1/2 批完成（本批运行 2 次），剩余 1 批" in text
    assert "第 2/2 批开始" in text
    assert "全部 2 批执行完毕" in text
    assert state.get("_loop_mode") is False


@pytest.mark.asyncio
async def test_takeover_without_human_message_cancels(monkeypatch):
    """接管时没有人类用户消息：不产生空提词，取消并回收浮窗。"""
    ui, session, plugin, state = _make_env(monkeypatch, [], [])
    session.messages = [{"role": "system", "content": "系统"}]
    state["_loop_takeover"] = {"count": 1, "prompt": ""}

    await plugin.async_execute(SimpleNamespace(session=session, arg="1"))

    assert session.prompts == []
    assert session.cleared == 0
    assert ui.closed == 1
    assert state.get("_loop_mode") is False
    text = "\n".join(ui.lines)
    assert "取消接管：当前会话没有可用于接管的人类用户消息" in text


@pytest.mark.asyncio
async def test_requires_explicit_positive_batch_count(monkeypatch):
    """省略批次数：报错并提示用法，不执行任何一轮。"""
    ui, session, plugin, _state = _make_env(monkeypatch, [], [])
    await plugin.async_execute(SimpleNamespace(session=session, arg="实现功能"))

    assert session.prompts == []
    assert session.cleared == 0
    assert ui.opened == []
    assert any("用法: /loop <批次数> <提词>" in line for line in ui.lines)


@pytest.mark.asyncio
async def test_zero_batch_count_errors(monkeypatch):
    ui, session, plugin, _state = _make_env(monkeypatch, [], [])
    await plugin.async_execute(SimpleNamespace(session=session, arg="0 实现功能"))

    assert session.prompts == []
    assert any("批次数必须是正整数" in line for line in ui.lines)


@pytest.mark.asyncio
async def test_user_input_stops_entire_loop(monkeypatch):
    """批内检测到用户输入 → 立即终止整个 loop（不进入下一批）。"""
    ui, session, plugin, state = _make_env(
        monkeypatch,
        ['{"continue": true, "prompt": "继续"}'],
        ["一"],
    )
    original_run = session.run_round

    async def run_and_queue(prompt):
        result = await original_run(prompt)
        if len(session.prompts) == 1:
            state["queued_input"] = "用户新消息"
        return result

    session.run_round = run_and_queue  # type: ignore[assignment]

    await plugin.async_execute(SimpleNamespace(session=session, arg="2 实现功能"))

    assert len(session.prompts) == 1
    text = "\n".join(ui.lines)
    assert "检测到用户输入" in text
    assert "提前结束整个循环" in text
    assert "第 2/2 批开始" not in text


@pytest.mark.asyncio
async def test_parse_failure_stops_entire_loop(monkeypatch):
    ui, session, plugin, _state = _make_env(monkeypatch, ["not json"], ["完成"])
    await plugin.async_execute(SimpleNamespace(session=session, arg="1 实现功能"))

    text = "\n".join(ui.lines)
    assert "提词生成失败" in text
    assert "解析: 失败" in text
    assert "未返回有效 JSON" in text
    # 模型原始输出（原文）不再上屏；只显示失败原因。
    assert "not json" not in text


@pytest.mark.asyncio
async def test_esc_stops_entire_loop(monkeypatch):
    ui, session, plugin, _state = _make_env(monkeypatch, [], ["一"])
    session.interrupted = True
    await plugin.async_execute(SimpleNamespace(session=session, arg="2 实现功能"))

    text = "\n".join(ui.lines)
    assert "ESC 中断" in text
    assert "提前结束整个循环" in text
    assert "第 1 次 LLM 输出" in text
    assert "解析: 失败" in text


@pytest.mark.asyncio
async def test_llm_input_and_output_raw_not_displayed(monkeypatch):
    """LLM 输入（system + user）与输出原文都不再上屏（2026-10-22 用户需求）。

    只保留解析结果与提词；发给模型的完整输入、模型返回的原始 JSON 均不出现在
    聊天区。
    """
    long_answer = "乙" * 2000
    ui, session, plugin, _state = _make_env(
        monkeypatch,
        ['{"continue": false, "prompt": ""}'],
        [long_answer],
    )
    await plugin.async_execute(SimpleNamespace(session=session, arg="1 实现功能"))

    text = "\n".join(ui.lines)
    # 输入块整体消失：没有标题、没有系统提词、没有用户消息原文全文。
    assert "LLM 输入" not in text
    assert build_system_prompt() not in text
    assert long_answer not in text
    # 输出原文（JSON）消失，但解析结果仍在。
    assert '{"continue": false' not in text
    assert "第 1 次 LLM 输出" in text
    assert "解析:" in text
    assert "continue=false" in text


@pytest.mark.asyncio
async def test_batch_timer_not_reset_by_runs(monkeypatch):
    """本批计时锚点只在批开始时写入，批内运行不重置（浮窗「本批」时长）。"""
    ui, session, plugin, _state = _make_env(
        monkeypatch,
        [
            '{"continue": true, "prompt": "继续"}',
            '{"continue": false, "prompt": ""}',
        ],
        ["一", "二"],
    )
    await plugin.async_execute(SimpleNamespace(session=session, arg="1 实现功能"))

    batch_status = next(s for s in ui.statuses if "round" in s)
    assert "new_batch" not in batch_status  # 控制标记不写入 UI 状态
    assert "batch_started_at" in batch_status
    assert "round_started_at" in batch_status

    run_status = next(
        s for s in ui.statuses if s.get("run") == 1 and "round" not in s
    )
    assert "run_started_at" in run_status
    # 批内运行不写本批锚点 → 浮窗的「本批」计时从批开始算起。
    assert "batch_started_at" not in run_status
    assert "round_started_at" not in run_status


# ── compact=true → 全量上下文压缩（2026-10-23 用户需求） ──


def _compact_result(*, success=True, count=3, saved=900, error=""):
    return SimpleNamespace(
        success=success,
        shadowed_count=count,
        saved_tokens=saved,
        stats={"error": error} if error else {},
    )


@pytest.mark.asyncio
async def test_compact_true_runs_full_compression_before_next_run(monkeypatch):
    """compact=true：force=True 全量压缩，且发生在下一轮运行之前。"""
    context_manager = _FakeContextManager(result=_compact_result())
    ui, session, plugin, _state = _make_env(
        monkeypatch,
        [
            '{"continue": true, "prompt": "继续", "compact": true}',
            '{"continue": false, "prompt": ""}',
        ],
        ["一", "二"],
        context_manager=context_manager,
    )
    seen_prompts = []
    context_manager.on_call = lambda: seen_prompts.append(len(session.prompts))

    await plugin.async_execute(SimpleNamespace(session=session, arg="1 实现功能"))

    assert context_manager.calls == [True]
    assert seen_prompts == [1]
    assert len(session.prompts) == 2
    text = "\n".join(ui.lines)
    assert "continue=true compact=true" in text
    assert "compact=true" in text
    assert "全量上下文压缩完成" in text
    assert "3 条历史" in text
    assert "节省 ~900t" in text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "compact_json",
    [
        '{"continue": true, "prompt": "继续", "compact": false}',
        # 字段缺失 / 非法 → False（兼容旧模型输出，不触发压缩）。
        '{"continue": true, "prompt": "继续"}',
        '{"continue": true, "prompt": "继续", "compact": "maybe"}',
    ],
)
async def test_compact_false_or_missing_does_not_compress(monkeypatch, compact_json):
    context_manager = _FakeContextManager(result=_compact_result())
    ui, session, plugin, _state = _make_env(
        monkeypatch,
        [compact_json, '{"continue": false, "prompt": ""}'],
        ["一", "二"],
        context_manager=context_manager,
    )

    await plugin.async_execute(SimpleNamespace(session=session, arg="1 实现功能"))

    assert context_manager.calls == []
    assert len(session.prompts) == 2
    assert "全量上下文压缩" not in "\n".join(ui.lines)


@pytest.mark.asyncio
async def test_compact_true_compresses_even_when_continue_false(monkeypatch):
    """判定 compact=true 即压缩：continue=false 结束本批时同样执行。"""
    context_manager = _FakeContextManager(result=_compact_result())
    ui, session, plugin, _state = _make_env(
        monkeypatch,
        ['{"continue": false, "prompt": "", "compact": true}'],
        ["一"],
        context_manager=context_manager,
    )

    await plugin.async_execute(SimpleNamespace(session=session, arg="1 实现功能"))

    assert context_manager.calls == [True]
    assert len(session.prompts) == 1
    assert "第 1/1 批完成" in "\n".join(ui.lines)


@pytest.mark.asyncio
async def test_compact_failure_does_not_stop_loop(monkeypatch):
    """压缩抛异常：提示后继续循环，不终止 /loop。"""
    context_manager = _FakeContextManager(error=RuntimeError("summary down"))
    ui, session, plugin, _state = _make_env(
        monkeypatch,
        [
            '{"continue": true, "prompt": "继续", "compact": true}',
            '{"continue": false, "prompt": ""}',
        ],
        ["一", "二"],
        context_manager=context_manager,
    )

    await plugin.async_execute(SimpleNamespace(session=session, arg="1 实现功能"))

    assert context_manager.calls == [True]
    assert len(session.prompts) == 2
    text = "\n".join(ui.lines)
    assert "全量上下文压缩失败" in text
    assert "继续循环" in text


@pytest.mark.asyncio
async def test_compact_none_result_continues(monkeypatch):
    """无可安全压缩范围（result=None）：提示跳过并继续。"""
    context_manager = _FakeContextManager(result=None)
    ui, session, plugin, _state = _make_env(
        monkeypatch,
        [
            '{"continue": true, "prompt": "继续", "compact": true}',
            '{"continue": false, "prompt": ""}',
        ],
        ["一", "二"],
        context_manager=context_manager,
    )

    await plugin.async_execute(SimpleNamespace(session=session, arg="1 实现功能"))

    assert context_manager.calls == [True]
    assert len(session.prompts) == 2
    assert "暂无可压缩的历史" in "\n".join(ui.lines)


@pytest.mark.asyncio
async def test_compact_unsuccessful_result_continues(monkeypatch):
    """压缩引擎返回 success=False：显示原因并继续。"""
    context_manager = _FakeContextManager(
        result=_compact_result(success=False, error="摘要未比被遮蔽内容更小"),
    )
    ui, session, plugin, _state = _make_env(
        monkeypatch,
        [
            '{"continue": true, "prompt": "继续", "compact": true}',
            '{"continue": false, "prompt": ""}',
        ],
        ["一", "二"],
        context_manager=context_manager,
    )

    await plugin.async_execute(SimpleNamespace(session=session, arg="1 实现功能"))

    assert len(session.prompts) == 2
    text = "\n".join(ui.lines)
    assert "全量上下文压缩未完成" in text
    assert "摘要未比被遮蔽内容更小" in text


@pytest.mark.asyncio
async def test_compact_unavailable_still_continues(monkeypatch):
    """上下文管理器未初始化：提示不可用，循环继续。"""
    ui, session, plugin, _state = _make_env(
        monkeypatch,
        [
            '{"continue": true, "prompt": "继续", "compact": true}',
            '{"continue": false, "prompt": ""}',
        ],
        ["一", "二"],
        context_manager=None,
    )

    await plugin.async_execute(SimpleNamespace(session=session, arg="1 实现功能"))

    assert len(session.prompts) == 2
    text = "\n".join(ui.lines)
    assert "全量上下文压缩不可用" in text
    assert "继续循环" in text


@pytest.mark.asyncio
async def test_full_compact_context_uses_real_compaction_engine():
    """真实 ContextManager：compact=true 走引擎全量压缩，系统提词原样保留。"""
    from src.core.compaction import is_checkpoint_message
    from src.core.context_manager import ContextManager

    messages = [{"role": "system", "content": "系统提词"}]
    for i in range(12):
        messages.append(
            {"role": "user", "content": f"任务 {i} " + "内容" * 200},
        )
        messages.append(
            {"role": "assistant", "content": f"完成 {i} " + "结果" * 200},
        )

    def summarize_fn(msgs, model=None):
        return "", "## 检查点\n- 已完成部分实现，剩余测试与验证", {"input": 10, "output": 10}, []

    context_manager = ContextManager(
        messages=messages,
        model="test-model",
        summarize_fn=summarize_fn,
        activate_global=False,
    )
    ui = _FakeChatUI()
    session = SimpleNamespace(context_manager=context_manager)
    decision = LoopPromptDecision(
        should_continue=True, prompt="继续", raw="", compact=True,
    )

    compacted = await _full_compact_context(ui, session, decision)

    assert compacted is True
    assert messages[0] == {"role": "system", "content": "系统提词"}
    assert any(is_checkpoint_message(m) for m in messages)
    assert "全量上下文压缩完成" in "\n".join(ui.lines)


@pytest.mark.asyncio
async def test_takeover_compact_true_runs_full_compression(monkeypatch):
    """流式接管轮判定 compact=true 时同样执行全量压缩。"""
    context_manager = _FakeContextManager(result=_compact_result())
    ui, session, plugin, state = _make_env(
        monkeypatch,
        [
            '{"continue": true, "prompt": "继续补充", "compact": true}',
            '{"continue": false, "prompt": ""}',
        ],
        ["二"],
        context_manager=context_manager,
    )
    session.messages = [
        {"role": "system", "content": "系统"},
        {"role": "user", "content": "最初任务"},
        {"role": "assistant", "content": "当前轮回答"},
    ]
    state["_loop_takeover"] = {"count": 1, "prompt": ""}

    await plugin.async_execute(SimpleNamespace(session=session, arg="1"))

    assert context_manager.calls == [True]
    assert session.prompts == [f"{LOOP_PROMPT_MARKER}继续补充"]
    assert "全量上下文压缩完成" in "\n".join(ui.lines)
