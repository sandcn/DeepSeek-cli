"""``/loop`` LLM 续跑提词：提示词构建、JSON 解析与服务调度测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.core.loop_prompt import (
    LOOP_PROMPT_MARKER,
    LoopPromptDecision,
    LoopPromptLoadError,
    LoopPromptService,
    build_loop_prompt_messages,
    build_system_prompt,
    human_user_texts,
    last_assistant_text,
    parse_loop_prompt_response,
)
from src.core.loop_prompt import provider as loop_provider
from src.core.commands.plugins.loop_plugin import parse_loop_args
from src.config.schema import _clean_loop_prompt


# ── 参数解析 ─────────────────────────────────────────────


@pytest.mark.parametrize(
    ("arg", "expected"),
    [
        ("3 完成登录页", (3, "完成登录页")),
        ("1 任务", (1, "任务")),
        ("0 无效批次数", (0, "无效批次数")),
        ("-1 负数按无效", (0, "-1 负数按无效")),
        ("3", (0, "3")),
        ("继续完成所有", (0, "继续完成所有")),
        ("   ", (0, "")),
        ("", (0, "")),
        ("12   多空格", (12, "多空格")),
        ("1 ", (0, "1")),
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


def test_build_loop_prompt_messages_sends_full_input_without_limits():
    """输入不做大小限制：全部用户消息与完整回答原样发送（2026-10-22 需求）。"""
    long_answer = "最后回答" * 100
    messages = ["需求" * 5000, "最后的需求"]
    result = build_loop_prompt_messages(
        messages,
        long_answer,
        run_index=2,
    )
    assert [item["role"] for item in result] == ["system", "user"]
    system = result[0]["content"]
    assert "continue" in system
    assert "长度不超过" not in system
    payload = json.loads(result[1]["content"])
    assert payload["run"] == 2
    assert payload["userMessages"] == messages
    assert payload["lastAssistant"] == long_answer
    assert "omittedMessages" not in payload


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


def test_clean_loop_prompt_defaults_and_clears_legacy_limits():
    raw = {
        "enabled": "false",
        "timeout_ms": 1,
        "max_input_bytes": "512",
        "max_prompt_bytes": 10_000_000,
        "retry_once": 0,
    }
    cleaned = _clean_loop_prompt(raw)
    assert cleaned["enabled"] is False
    assert cleaned["timeout_ms"] == 500
    assert cleaned["retry_once"] is False
    # 旧版字节上限字段已移除并清理（2026-10-22：输入/输出不做大小限制）。
    assert "max_input_bytes" not in cleaned
    assert "max_prompt_bytes" not in cleaned
    assert raw == {"enabled": "false", "timeout_ms": 1, "retry_once": 0}


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
async def test_generated_prompt_is_not_truncated():
    """输出不做大小限制：长提词完整保留（2026-10-22 需求）。"""
    long_prompt = "继续完成剩余工作" * 2000
    service, _calls = _make_service(
        ['{"continue": true, "prompt": "' + long_prompt + '"}'],
    )
    service.begin_loop()
    service.arm()
    await service.on_agent_round_end()

    decision = service.take_result()
    assert decision is not None
    assert decision.should_continue is True
    assert decision.prompt == long_prompt


@pytest.mark.asyncio
async def test_peek_result_does_not_clear_and_keeps_last_raw():
    """peek 不改变结果状态；last_raw 保留模型原始输出（2026-10-22 起仅调试用）。"""
    service, _calls = _make_service(
        ['{"continue": true, "prompt": "继续"}'],
    )
    service.begin_loop()
    service.arm()
    await service.on_agent_round_end()

    assert service.peek_result() is not None
    assert service.peek_result().prompt == "继续"
    assert service.take_result().prompt == "继续"
    assert service.peek_result() is None
    assert service.last_raw == '{"continue": true, "prompt": "继续"}'


@pytest.mark.asyncio
async def test_last_raw_saved_when_parse_fails():
    """解析失败时仍保留原始输出，供 /loop 展示失败原因与原文。"""
    service, _calls = _make_service(
        ["not json"], config_getter=lambda: {"retry_once": False},
    )
    service.begin_loop()
    service.arm()
    await service.on_agent_round_end()

    assert service.peek_result() is None
    assert service.last_raw == "not json"
    assert "未返回有效 JSON" in service.last_error


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
async def test_call_requests_unlimited_output(monkeypatch):
    """输出不做大小限制：生产调用路径传 max_tokens=0（0 = 不限制）。"""
    captured = {}

    async def fake_call_model_async(messages, **kwargs):
        captured["messages"] = messages
        captured.update(kwargs)
        return "", '{"continue": false, "prompt": ""}', {"input": 0, "output": 0}, []

    monkeypatch.setattr(
        "src.api.model_async.call_model_async", fake_call_model_async,
    )
    service = LoopPromptService(
        messages_getter=lambda: [
            {"role": "user", "content": "实现功能"},
            {"role": "assistant", "content": "已完成"},
        ],
        model_getter=lambda: "test-model",
    )
    service.begin_loop()
    service.arm()
    await service.on_agent_round_end()

    assert captured["max_tokens"] == 0
    assert captured["silent"] is True
    assert captured["label"] == "loop-prompt"
    assert service.take_result() is not None


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


# ── 系统提词文件（prompts/prompts_loop_prompt.md） ──────────


def test_system_prompt_loads_from_prompts_dir():
    prompt = build_system_prompt()
    path = (
        Path(__file__).resolve().parents[2]
        / "prompts" / "prompts_loop_prompt.md"
    )
    assert path.is_file()
    assert prompt == path.read_text(encoding="utf-8").strip()
    assert "JSON" in prompt


def test_system_prompt_missing_file_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(loop_provider, "_PROMPTS_DIR", tmp_path)
    with pytest.raises(LoopPromptLoadError):
        build_system_prompt()
    with pytest.raises(LoopPromptLoadError):
        build_loop_prompt_messages(["任务"], "回答", run_index=1)


def test_system_prompt_empty_file_raises(tmp_path, monkeypatch):
    (tmp_path / "prompts_loop_prompt.md").write_text("   \n", encoding="utf-8")
    monkeypatch.setattr(loop_provider, "_PROMPTS_DIR", tmp_path)
    with pytest.raises(LoopPromptLoadError):
        build_system_prompt()


def test_system_prompt_hot_reload(tmp_path, monkeypatch):
    path = tmp_path / "prompts_loop_prompt.md"
    path.write_text("第一版提词", encoding="utf-8")
    monkeypatch.setattr(loop_provider, "_PROMPTS_DIR", tmp_path)
    assert build_system_prompt() == "第一版提词"

    path.write_text("第二版提词", encoding="utf-8")
    assert build_system_prompt() == "第二版提词"


@pytest.mark.asyncio
async def test_service_reports_prompt_load_error(tmp_path, monkeypatch):
    """系统提词缺失时：不发送请求，记录错误并由 /loop 按失败策略结束。"""
    monkeypatch.setattr(loop_provider, "_PROMPTS_DIR", tmp_path)
    service, calls = _make_service(['{"continue": true, "prompt": "继续"}'])
    service.begin_loop()
    service.arm()
    await service.on_agent_round_end()

    assert calls == []
    assert service.take_result() is None
    assert "构建提词请求失败" in service.last_error


# ── 决策只认 LLM 返回的 JSON（无本地兜底） ───────────────


def test_system_prompt_matches_loaded_file():
    """系统提词按 prompts/prompts_loop_prompt.md 原文热加载。"""
    system = build_loop_prompt_messages(
        ["实现登录页"], "回答", run_index=1,
    )[0]["content"]
    assert system == build_system_prompt()
    assert "JSON" in system
    assert "continue=true" in system
    assert "continue=false" in system


@pytest.mark.asyncio
async def test_service_follows_llm_json_without_local_override():
    """LLM 返回 continue=false 时直接结束，即使回答里出现「未做事项/未改动」等措辞。"""
    service = LoopPromptService(
        messages_getter=lambda: [
            {"role": "user", "content": "改一下提示词"},
            {
                "role": "assistant",
                "content": (
                    "**未做事项**：未改动任何代码/配置/脚本，未执行构建与测试"
                    "（本轮只有普通文本文件改动，无实操类文件变更，"
                    "因此未触发 bash_opt 实操三查与单元测试）。"
                ),
            },
        ],
        model_getter=lambda: "test-model",
        call_model=lambda _messages: ('{"continue": false, "prompt": ""}', ""),
    )
    service.begin_loop()
    service.arm()
    await service.on_agent_round_end()

    decision = service.take_result()
    assert decision is not None
    assert decision.should_continue is False
    assert decision.prompt == ""
    assert service.last_error == ""


@pytest.mark.asyncio
async def test_service_uses_llm_prompt_when_continue_true():
    """LLM 返回 continue=true 时原样采用其提词，不做本地加工。"""
    service = LoopPromptService(
        messages_getter=lambda: [
            {"role": "user", "content": "实现登录页"},
            {"role": "assistant", "content": "时间不够，这次没有修改任何文件。"},
        ],
        model_getter=lambda: "test-model",
        call_model=lambda _messages: (
            '{"continue": true, "prompt": "继续实现登录页并补测试"}', "",
        ),
    )
    service.begin_loop()
    service.arm()
    await service.on_agent_round_end()

    decision = service.take_result()
    assert decision is not None
    assert decision.should_continue is True
    assert decision.prompt == "继续实现登录页并补测试"


# ── LLM 输入完整保留（2026-10-22 起不再上屏，供调试 / 兼容读取） ──


@pytest.mark.asyncio
async def test_service_keeps_full_request_for_display():
    """最近一次请求（system + user）完整保留，不做截断。"""
    long_answer = "甲" * 3000
    service = LoopPromptService(
        messages_getter=lambda: [
            {"role": "user", "content": "实现功能"},
            {"role": "assistant", "content": long_answer},
        ],
        model_getter=lambda: "test-model",
        call_model=lambda _messages: ('{"continue": true, "prompt": "继续"}', ""),
    )
    service.begin_loop()
    service.arm()
    await service.on_agent_round_end()

    request = service.last_request
    assert [item["role"] for item in request] == ["system", "user"]
    assert request[0]["content"] == build_system_prompt()
    payload = json.loads(request[1]["content"])
    assert payload["userMessages"] == ["实现功能"]
    assert payload["lastAssistant"] == long_answer
    assert len(payload["lastAssistant"]) == 3000


@pytest.mark.asyncio
async def test_service_request_cleared_on_arm_and_begin_loop():
    service = LoopPromptService(
        messages_getter=lambda: [
            {"role": "user", "content": "实现功能"},
            {"role": "assistant", "content": "完成"},
        ],
        model_getter=lambda: "test-model",
        call_model=lambda _messages: ('{"continue": false, "prompt": ""}', ""),
    )
    service.begin_loop()
    service.arm()
    await service.on_agent_round_end()
    assert service.last_request

    service.arm()
    assert service.last_request == []

    service.begin_loop()
    assert service.last_request == []
