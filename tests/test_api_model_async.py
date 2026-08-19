"""src/api/model_async — 异步/同步模型调用公开接口单元测试。

覆盖：
  - call_model_async / call_model_sync_async：适配器解析、消息深拷贝与重试包装
  - _call_sync_async：非流式完整路径（请求构造/中断/解析/统计/工具解析耗时）
  - 中断路径（调用前/调用后）
  - call_model_sync / call_model 同步包装（持久化事件循环）
所有网络层均被 mock，不产生真实请求。
"""

from __future__ import annotations

import asyncio

import pytest

import src.api.model_async as ma
from src.api.model_async import _call_sync_async, call_model, call_model_async, call_model_sync, call_model_sync_async


class _FakeAdapter:
    """记录调用的假适配器。"""

    def __init__(self, response=None):
        self.provider_name = "fake"
        self._protocol = ""
        self._base_url = "http://fake"
        self.prepare_calls = []
        self.response = response or {
            "content": "你好",
            "reasoning_content": "思考中",
            "usage": {"input": 10, "output": 20},
            "tool_calls": [],
        }

    def prepare_messages(self, messages, model):
        self.prepare_calls.append((messages, model))
        return [dict(m, _prepared=True) for m in messages]

    def is_reasoner_model(self, model):
        return "reasoner" in model

    def build_request_kwargs(self, messages, model, tools=None, stream=False,
                             stream_options=None):
        return {"messages": messages, "model": model, "tools": tools, "stream": stream}

    def parse_response(self, response):
        return response


@pytest.fixture(autouse=True)
def patch_model_async_deps(monkeypatch):
    """统一替换 model_async 依赖，避免真实网络/全局统计污染。"""
    monkeypatch.setattr(ma, "get_adapter", lambda model: _current_adapter())
    monkeypatch.setattr(ma, "is_interrupted_async", _interrupted)
    monkeypatch.setattr(ma, "accumulate_usage", lambda usage: None)
    monkeypatch.setattr(ma, "set_tool_parse_elapsed", lambda e: None)
    monkeypatch.setattr(ma, "set_stream_speed", lambda s: None)
    monkeypatch.setattr(ma, "add_token_size", lambda n: None)
    monkeypatch.setattr(ma, "estimate_tokens", lambda s: 5)
    monkeypatch.setattr(ma, "chat_completions_async", _fake_chat_completions)
    monkeypatch.setattr(ma, "chat_completions_async_anthropic", _fake_chat_completions_anthropic)


_current = None
_interrupt_flag = False
_chat_calls = []


def _current_adapter():
    assert _current is not None, "测试未设置假适配器"
    return _current


async def _interrupted():
    return _interrupt_flag


async def _fake_chat_completions(**kwargs):
    _chat_calls.append(("openai", kwargs))
    return _current.response


async def _fake_chat_completions_anthropic(base_url=None, **kwargs):
    _chat_calls.append(("anthropic", {"base_url": base_url, **kwargs}))
    return _current.response


@pytest.fixture(autouse=True)
def reset_globals():
    global _current, _interrupt_flag
    _chat_calls.clear()
    _current = None
    _interrupt_flag = False
    yield
    _chat_calls.clear()
    _current = None
    _interrupt_flag = False


# ── call_model_async：流式路径 ────────────────────────────

def test_call_model_async_streams_via_retry(monkeypatch):
    global _current
    _current = _FakeAdapter()

    captured = {}

    async def fake_retry(api_func, **kwargs):
        captured["silent"] = kwargs["silent"]
        captured["api_args"] = kwargs["api_args"]
        return ("r", "c", {"input": 1}, [])

    monkeypatch.setattr(ma, "retry_on_parse_failure_async", fake_retry)

    result = asyncio.run(call_model_async(
        [{"role": "user", "content": "hi"}], model="deepseek-chat",
    ))
    assert result == ("r", "c", {"input": 1}, [])
    # 适配器 prepare_messages 被调用且消息被深拷贝
    assert _current.prepare_calls[0][1] == "deepseek-chat"
    msg = _current.prepare_calls[0][0][0]
    assert msg["content"] == "hi"
    assert "_prepared" in captured["api_args"][0][0]
    # stream_call_async 的参数顺序: (messages, model, is_reasoner, tools, display, label, silent)
    args = captured["api_args"]
    assert args[1] == "deepseek-chat"
    assert args[2] is False  # 非 reasoner
    assert args[6] is False  # silent


def test_call_model_async_reasoner_detection(monkeypatch):
    global _current
    _current = _FakeAdapter()

    captured = {}

    async def fake_retry(api_func, **kwargs):
        captured["api_args"] = kwargs["api_args"]
        return ("", "", {}, [])

    monkeypatch.setattr(ma, "retry_on_parse_failure_async", fake_retry)

    asyncio.run(call_model_async(
        [{"role": "user", "content": "hi"}], model="deepseek-reasoner",
    ))
    assert captured["api_args"][2] is True  # is_reasoner


def test_call_model_async_default_model(monkeypatch):
    """model=None 时使用全局 MODEL。"""
    global _current
    _current = _FakeAdapter()

    captured = {}

    async def fake_retry(api_func, **kwargs):
        captured["api_args"] = kwargs["api_args"]
        return ("", "", {}, [])

    monkeypatch.setattr(ma, "retry_on_parse_failure_async", fake_retry)
    monkeypatch.setattr(ma, "MODEL", "deepseek-default")

    asyncio.run(call_model_async([{"role": "user", "content": "hi"}]))
    assert captured["api_args"][1] == "deepseek-default"


def test_call_model_async_passes_retry_overrides(monkeypatch):
    global _current
    _current = _FakeAdapter()
    captured = {}

    async def fake_retry(api_func, **kwargs):
        captured["kwargs"] = kwargs
        return ("", "", {}, [])

    monkeypatch.setattr(ma, "retry_on_parse_failure_async", fake_retry)

    asyncio.run(call_model_async(
        [{"role": "user", "content": "hi"}],
        override_max_retries=1, fixed_delay_sec=0.0,
    ))
    assert captured["kwargs"]["override_max_retries"] == 1
    assert captured["kwargs"]["fixed_delay_sec"] == 0.0


# ── call_model_sync_async：非流式路径 ─────────────────────

def test_call_model_sync_async_routes_to_sync_impl(monkeypatch):
    global _current
    _current = _FakeAdapter()

    captured = {}

    async def fake_retry(api_func, **kwargs):
        captured["func"] = api_func
        captured["kwargs"] = kwargs
        return ("r", "c", {"input": 1}, [])

    monkeypatch.setattr(ma, "retry_on_parse_failure_async", fake_retry)

    asyncio.run(call_model_sync_async([{"role": "user", "content": "hi"}], model="m"))
    assert captured["func"] is _call_sync_async
    assert captured["kwargs"]["silent"] is True
    assert captured["kwargs"]["api_args"][1] == "m"


# ── _call_sync_async：非流式实现 ──────────────────────────

def test_call_sync_async_full_path(monkeypatch):
    global _current
    _current = _FakeAdapter()
    monkeypatch.setattr(ma.time, "time", lambda: 1.0)  # duration=0 → speed 0

    result = asyncio.run(_call_sync_async(
        [{"role": "user", "content": "hi"}], "m", None,
    ))
    reasoning, content, usage, tool_calls = result
    assert reasoning == "思考中"
    assert content == "你好"
    assert usage["input"] == 10
    assert usage["output"] == 20
    assert usage["speed"] == 0.0  # api_duration 模拟为 0 → 无速度
    assert tool_calls == []
    assert _chat_calls[0][0] == "openai"


def test_call_sync_async_anthropic_protocol():
    global _current
    _current = _FakeAdapter()
    _current._protocol = "anthropic"

    asyncio.run(_call_sync_async([{"role": "user", "content": "hi"}], "m", None))
    kind, kwargs = _chat_calls[0]
    assert kind == "anthropic"
    assert kwargs["base_url"] == "http://fake"


def test_call_sync_async_interrupted_before_call():
    global _current, _interrupt_flag
    _current = _FakeAdapter()
    _interrupt_flag = True

    result = asyncio.run(_call_sync_async([{"role": "user", "content": "hi"}], "m", None))
    assert result[1] == "(已中断)"
    assert _chat_calls == []  # 未发起请求


def test_call_sync_async_interrupted_after_call():
    global _current, _interrupt_flag
    _current = _FakeAdapter()
    calls = {"n": 0}

    async def _interrupted_twice():
        calls["n"] += 1
        return calls["n"] >= 2  # 第一次 False，第二次 True

    import src.api.model_async as ma2

    ma2.is_interrupted_async = _interrupted_twice  # type: ignore[assignment]
    try:
        result = asyncio.run(_call_sync_async([{"role": "user", "content": "hi"}], "m", None))
    finally:
        ma2.is_interrupted_async = _interrupted
    assert result[1] == "(已中断)"
    assert _chat_calls  # 请求已发出


def test_call_sync_async_tool_calls_set_parse_elapsed(monkeypatch):
    global _current
    _current = _FakeAdapter(response={
        "content": "",
        "reasoning_content": "",
        "usage": {"input": 5, "output": 10},
        "tool_calls": [{"id": "t1", "name": "read_file", "arguments": {"path": "x.py"}}],
    })
    seen = {}
    monkeypatch.setattr(ma, "set_tool_parse_elapsed", lambda e: seen.setdefault("elapsed", e))
    monkeypatch.setattr(ma.time, "time", lambda: 1.0)  # 固定时间 → elapsed 0.0

    result = asyncio.run(_call_sync_async([{"role": "user", "content": "hi"}], "m", []))
    assert result[3][0]["name"] == "read_file"
    assert result[2]["tool_parse_elapsed"] == 0.0
    assert "elapsed" in seen


def test_call_sync_async_speed_calculation(monkeypatch):
    """output>0 且耗时>0 时计算速度并写入 usage/set_stream_speed。"""
    global _current
    _current = _FakeAdapter(response={
        "content": "x",
        "reasoning_content": "",
        "usage": {"input": 5, "output": 50},
        "tool_calls": [],
    })
    times = iter([10.0, 20.0])  # duration = 10s
    monkeypatch.setattr(ma.time, "time", lambda: next(times))
    speeds = []
    monkeypatch.setattr(ma, "set_stream_speed", speeds.append)

    result = asyncio.run(_call_sync_async([{"role": "user", "content": "hi"}], "m", None))
    assert result[2]["speed"] == pytest.approx(5.0)
    assert speeds == [5.0]


def test_call_sync_async_reasoning_only_becomes_content():
    """仅 reasoning_content 时转置为 content（V4 兼容行为）。"""
    global _current
    _current = _FakeAdapter(response={
        "content": "",
        "reasoning_content": "只有推理",
        "usage": {"input": 1, "output": 1},
        "tool_calls": [],
    })
    result = asyncio.run(_call_sync_async([{"role": "user", "content": "hi"}], "m", None))
    assert result[0] == "只有推理"
    assert result[1] == "只有推理"


def test_call_sync_async_display_update(monkeypatch):
    """有 display + label 且存在 tool_calls 时调用 update_parse_info。"""
    global _current
    _current = _FakeAdapter(response={
        "content": "",
        "reasoning_content": "",
        "usage": {"input": 1, "output": 1},
        "tool_calls": [{"id": "t", "name": "bash", "arguments": {"cmd": "ls"}}],
    })
    seen = {}
    display = type("D", (), {"update_parse_info": lambda self, label, name, toks, elapsed: seen.update(
        {"label": label, "name": name, "tokens": toks, "elapsed": elapsed})})()
    monkeypatch.setattr(ma.time, "time", lambda: 1.0)

    asyncio.run(_call_sync_async([{"role": "user", "content": "hi"}], "m", [], display=display, label="L"))
    assert seen["label"] == "L"
    assert seen["name"] == "bash"
    assert seen["tokens"] == 5


def test_call_sync_async_display_error_silent(monkeypatch):
    """display.update_parse_info 抛异常时静默降级。"""
    global _current
    _current = _FakeAdapter(response={
        "content": "",
        "reasoning_content": "",
        "usage": {"input": 1, "output": 1},
        "tool_calls": [{"id": "t", "name": "bash", "arguments": {}}],
    })
    display = type("D", (), {"update_parse_info": lambda self, *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))})()
    monkeypatch.setattr(ma.time, "time", lambda: 1.0)

    result = asyncio.run(_call_sync_async([{"role": "user", "content": "hi"}], "m", [], display=display, label="L"))
    assert result[3][0]["name"] == "bash"  # 不抛异常


# ── 同步包装（持久化事件循环） ────────────────────────────

def test_call_model_sync_wrapper(monkeypatch):
    async def fake_sync_async(*args, **kwargs):
        return ("r", "c", {"input": 1}, [])

    monkeypatch.setattr(ma, "call_model_sync_async", fake_sync_async)
    result = call_model_sync([{"role": "user", "content": "hi"}], model="m")
    assert result == ("r", "c", {"input": 1}, [])


def test_call_model_wrapper(monkeypatch):
    async def fake_async(*args, **kwargs):
        return ("r2", "c2", {"input": 2}, [])

    monkeypatch.setattr(ma, "call_model_async", fake_async)
    result = call_model([{"role": "user", "content": "hi"}], model="m")
    assert result == ("r2", "c2", {"input": 2}, [])
