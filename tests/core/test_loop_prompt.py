"""``/loop`` LLM 续跑提词：提示词构建、JSON 解析与服务调度测试。"""

from __future__ import annotations

import json

import pytest

from src.core.loop_prompt import (
    LOOP_PROMPT_MARKER,
    LoopPromptDecision,
    LoopPromptService,
    build_loop_prompt_messages,
    human_user_texts,
    last_assistant_text,
    parse_loop_prompt_response,
    select_user_messages_within_budget,
)
from src.core.commands.plugins.loop_plugin import parse_loop_args
from src.config.schema import _clean_loop_prompt


# ── 参数解析 ─────────────────────────────────────────────


@pytest.mark.parametrize(
    ("arg", "expected"),
    [
        ("3 完成登录页", (3, "完成登录页")),
        ("0 无限循环", (None, "无限循环")),
        ("-1 负数按无限", (None, "-1 负数按无限")),
        ("3", (None, "3")),
        ("继续完成所有", (None, "继续完成所有")),
        ("   ", (None, "")),
        ("", (None, "")),
        ("12   多空格", (12, "多空格")),
    ],
)
def test_parse_loop_args(arg, expected):
    assert parse_loop_args(arg) == expected


# ── 提示词输入 ───────────────────────────────────────────


def test_human_user_texts_filters_machine_messages():
    messages = [
        {"role": "system", "content": "系统"},
        {"role": "user", "content": "实现登录页"},
        {"role": "assistant", "content": "好的"},
        {"role": "user", "content": "<skill_content>技能正文"},
        {"role": "user", "content": "[对话摘要] 摘要"},
        {"role": "user", "content": "【继续】网络错误已恢复"},
        {"role": "user", "content": f"{LOOP_PROMPT_MARKER}继续修复"},
        {"role": "user", "content": json.dumps({"task_id": "bg-1", "stdout": "ok"})},
        {"role": "user", "content": json.dumps({"stdout": "ok", "returncode": 0})},
        {"role": "user", "content": "[图片]"},
        {"role": "user", "content": "再补测试"},
    ]
    assert human_user_texts(messages) == ["实现登录页", "再补测试"]


def test_last_assistant_text_skips_tool_call_only_messages():
    messages = [
        {"role": "assistant", "content": "第一版实现"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "1"}]},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "2"}]},
        {"role": "tool", "content": "工具结果"},
    ]
    assert last_assistant_text(messages) == "第一版实现"


def test_select_user_messages_keeps_latest_first():
    messages = ["旧" * 100, "中间" * 20, "最新一条"]
    selected, omitted = select_user_messages_within_budget(messages, 80)
    assert selected == ["最新一条"]
    assert omitted == 2


def test_select_user_messages_truncates_oversized_latest():
    messages = ["x" * 500]
    selected, omitted = select_user_messages_within_budget(messages, 100)
    assert omitted == 0
    assert len(selected) == 1
    assert len(selected[0].encode("utf-8")) <= 100


def test_select_user_messages_unlimited_budget():
    messages = ["a", "b"]
    assert select_user_messages_within_budget(messages, 0) == (messages, 0)


def test_build_loop_prompt_messages_respects_input_budget():
    messages = ["需求" * 50, "最后的需求"]
    result = build_loop_prompt_messages(
        messages,
        "最后回答" * 100,
        run_index=2,
        max_input_bytes=600,
        max_prompt_bytes=512,
    )
    assert [item["role"] for item in result] == ["system", "user"]
    assert "continue" in result[0]["content"]
    payload = json.loads(result[1]["content"])
    assert payload["run"] == 2
    assert payload["lastAssistant"]
    assert len(result[1]["content"].encode("utf-8")) <= 600


# ── 输出解析 ─────────────────────────────────────────────


def test_parse_plain_json():
    decision = parse_loop_prompt_response(
        '{"continue": true, "prompt": "继续完成测试"}'
    )
    assert decision == LoopPromptDecision(True, "继续完成测试", decision.raw)


def test_parse_fenced_json_and_marker_and_quotes():
    raw = '```json\n{"continue": true, "prompt": "【自动继续】修复 P1"}\n```'
    decision = parse_loop_prompt_response(raw)
    assert decision is not None
    assert decision.should_continue is True
    assert decision.prompt == "修复 P1"


def test_parse_chinese_aliases_and_string_bool():
    decision = parse_loop_prompt_response(
        '说明文字 {"是否继续": "否", "继续提词": "不需要"} 结尾'
    )
    assert decision is not None
    assert decision.should_continue is False
    assert decision.prompt == ""


def test_parse_false_clears_prompt():
    decision = parse_loop_prompt_response(
        '{"should_continue": false, "next_prompt": "不该保留"}'
    )
    assert decision is not None
    assert decision.should_continue is False
    assert decision.prompt == ""


def test_parse_true_without_prompt_is_invalid():
    assert parse_loop_prompt_response('{"continue": true, "prompt": "   "}') is None


@pytest.mark.parametrize(
    "raw",
    ["", "not json", "[]", '{"prompt": "缺少 continue"}', '{"continue": "maybe"}'],
)
def test_parse_invalid_outputs(raw):
    assert parse_loop_prompt_response(raw) is None


# ── 配置清洗 ─────────────────────────────────────────────


def test_clean_loop_prompt_defaults_and_clamps():
    cleaned = _clean_loop_prompt(
        {
            "enabled": "false",
            "timeout_ms": 1,
            "max_input_bytes": "512",
            "max_prompt_bytes": 10_000_000,
            "retry_once": 0,
        }
    )
    assert cleaned["enabled"] is False
    assert cleaned["timeout_ms"] == 500
    assert cleaned["max_input_bytes"] == 512
    assert cleaned["max_prompt_bytes"] == 100_000
    assert cleaned["retry_once"] is False


# ── 服务调度 ─────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _no_interrupt(monkeypatch):
    async def _false():
        return False

    monkeypatch.setattr(LoopPromptService, "_interrupted", staticmethod(_false))


def _make_service(responses, *, emit=None, config_getter=None):
    calls = []

    def call_model(messages):
        calls.append(messages)
        item = responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item, ""

    service = LoopPromptService(
        messages_getter=lambda: [
            {"role": "user", "content": "实现功能"},
            {"role": "assistant", "content": "已完成一部分，还剩测试"},
        ],
        model_getter=lambda: "test-model",
        emit=emit,
        config_getter=config_getter,
        call_model=call_model,
    )
    return service, calls


@pytest.mark.asyncio
async def test_not_armed_does_not_call_model():
    events = []
    service, calls = _make_service([], emit=lambda e, **kw: events.append((e, kw)))
    await service.on_agent_round_end()
    assert calls == []
    assert events == []


@pytest.mark.asyncio
async def test_armed_generates_and_emits_events():
    events = []
    service, calls = _make_service(
        ['{"continue": true, "prompt": "补测试"}'],
        emit=lambda e, **kw: events.append((e, kw)),
    )
    service.begin_loop()
    service.arm()
    await service.on_agent_round_end()

    decision = service.take_result()
    assert decision is not None
    assert decision.should_continue is True
    assert decision.prompt == "补测试"
    assert len(calls) == 1
    assert [name for name, _ in events] == [
        "loop_prompt_generating",
        "loop_prompt_generated",
        "loop_prompt_generating",
    ]
    assert events[0][1] == {"generating": True}
    assert events[-1][1] == {"generating": False}
    assert events[1][1]["should_continue"] is True
    assert events[1][1]["prompt"] == "补测试"
    assert events[1][1]["error"] == ""


@pytest.mark.asyncio
async def test_generation_retries_once_then_succeeds():
    events = []
    service, calls = _make_service(
        [RuntimeError("network"), '{"continue": true, "prompt": "继续"}'],
        emit=lambda e, **kw: events.append((e, kw)),
    )
    service.begin_loop()
    service.arm()
    await service.on_agent_round_end()

    assert len(calls) == 2
    assert service.last_error == ""
    decision = service.take_result()
    assert decision is not None
    assert decision.prompt == "继续"


@pytest.mark.asyncio
async def test_generation_failure_sets_error_and_no_result():
    events = []
    service, calls = _make_service(
        [RuntimeError("down"), "not json"],
        emit=lambda e, **kw: events.append((e, kw)),
    )
    service.begin_loop()
    service.arm()
    await service.on_agent_round_end()

    assert len(calls) == 2
    assert service.take_result() is None
    assert "未返回有效 JSON" in service.last_error
    assert events[-2][0] == "loop_prompt_generated"
    assert events[-2][1]["should_continue"] is None
    assert events[-2][1]["error"]
    assert events[-1] == ("loop_prompt_generating", {"generating": False})


@pytest.mark.asyncio
async def test_disabled_config_skips_model_call():
    service, calls = _make_service(
        [], config_getter=lambda: {"enabled": False},
    )
    service.begin_loop()
    service.arm()
    await service.on_agent_round_end()

    assert calls == []
    assert "禁用" in service.last_error


@pytest.mark.asyncio
async def test_stale_revision_result_is_discarded():
    service = None

    def call_model(_messages):
        # 生成期间新一次 loop 开始 → 旧结果必须丢弃。
        service.begin_loop()
        return '{"continue": true, "prompt": "陈旧"}', ""

    service = LoopPromptService(
        messages_getter=lambda: [
            {"role": "user", "content": "实现功能"},
            {"role": "assistant", "content": "还有测试"},
        ],
        model_getter=lambda: "test-model",
        call_model=call_model,
    )
    service.begin_loop()
    service.arm()
    await service.on_agent_round_end()

    assert service.take_result() is None


@pytest.mark.asyncio
async def test_take_result_clears_buffer():
    service, _calls = _make_service(
        ['{"continue": true, "prompt": "继续"}', '{"continue": false}'],
    )
    service.begin_loop()
    service.arm()
    await service.on_agent_round_end()
    assert service.take_result() is not None
    assert service.take_result() is None

    # 第二次 arm 使用同一条响应队列的下一项。
    service.arm()
    await service.on_agent_round_end()
    decision = service.take_result()
    assert decision is not None
    assert decision.should_continue is False
    assert decision.prompt == ""


@pytest.mark.asyncio
async def test_disarm_prevents_generation():
    service, calls = _make_service([])
    service.begin_loop()
    service.arm()
    service.disarm()
    await service.on_agent_round_end()
    assert calls == []


def test_service_status_snapshot():
    service, _calls = _make_service([])
    service.begin_loop()
    service.arm()
    status = service.status()
    assert status["armed"] is True
    assert status["generating"] is False
    assert status["has_result"] is False
    assert status["last_error"] == ""


def test_marker_not_human():
    messages = [{"role": "user", "content": f"{LOOP_PROMPT_MARKER}继续"}]
    assert human_user_texts(messages) == []
