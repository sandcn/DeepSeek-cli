"""ClawBot /stop 命令测试 — 直接终止大模型流式输出。

覆盖：
- /stop 注册在帮助文本中
- AI 空闲时 /stop → 提示「当前没有正在生成的输出」
- AI 生成期间微信 /stop → 实时触发全局中断 + 微信确认回复 + 消息不入队
- AI 生成期间本地 TUI /stop → 实时触发全局中断
- 未授权用户 /stop → 不触发中断（走正常配对/入队流程）
- _ai_chat 期间 _ai_running 标志维护（生成中 True，结束/异常复位）
- run_round 返回 interrupted → 回复含「⏹ 已停止生成」停止提示
"""
from __future__ import annotations

import asyncio

import pytest

from src.clawbot.commands import HELP_TEXT
from src.clawbot.runner import ClawBotRunner
from src.api.interrupt_async import (
    is_interrupted,
    request_interrupt_async,
    reset_interrupt_async,
)


@pytest.fixture(autouse=True)
def _clean_interrupt():
    """每个测试结束后清除全局中断标志，避免跨测试污染。"""
    yield
    reset_interrupt_async()


class FakeClient:
    def __init__(self):
        self.auth = None
        self.sent: list = []

    async def get_updates(self, buf):
        await asyncio.sleep(3600)
        return {"msgs": [], "get_updates_buf": buf}

    async def send_message(self, to_user_id, context_token, text):
        self.sent.append((to_user_id, context_token, text))
        return {}

    async def get_config(self, ilink_user_id, context_token):
        return {}

    async def send_typing(self, ilink_user_id, typing_ticket, status):
        return {}

    async def aclose(self):
        pass

    def set_auth(self, token, base_url):
        self.auth = (token, base_url)


class StopClient(FakeClient):
    """首次 get_updates 返回一条 /stop 微信消息，之后长轮询挂起。"""

    def __init__(self):
        super().__init__()
        self.calls = 0

    async def get_updates(self, buf):
        self.calls += 1
        if self.calls == 1:
            return {
                "msgs": [{
                    "message_type": 1,
                    "from_user_id": "u1",
                    "context_token": "ctx1",
                    "item_list": [{"type": 1, "text_item": {"text": "/stop"}}],
                }],
                "get_updates_buf": "buf1",
            }
        await asyncio.sleep(3600)
        return {"msgs": [], "get_updates_buf": buf}


class FakeSession:
    def __init__(self, model: str = ""):
        self.model = model
        self.messages: list = []
        self.run_round_result = {
            "interrupted": False,
            "session_id": None,
            "delta": {"input": 0, "output": 0, "calls": 0},
            "elapsed": 0.0,
        }
        self.run_round_texts: list = []

    def initialize(self):
        pass

    async def run_round(self, text):
        self.run_round_texts.append(text)
        self.messages.append({"role": "user", "content": text})
        self.messages.append({"role": "assistant", "content": "你好"})
        return self.run_round_result


def _make_runner(client=None, session_factory=None, tui=False, print_fn=None):
    return ClawBotRunner(
        client=client or FakeClient(),
        session_factory=session_factory or (lambda m="": FakeSession(m)),
        tui=tui,
        print_fn=print_fn or (lambda *a: None),
    )


# ── 帮助文本 ──────────────────────────────────────────

def test_help_contains_stop():
    assert "/stop" in HELP_TEXT


# ── AI 空闲时 /stop ───────────────────────────────────

def test_stop_when_idle():
    client = FakeClient()
    runner = _make_runner(client=client)
    runner._allowed_users.add("u1")
    asyncio.run(runner._dispatch_cmd("u1", "ctx", "/stop"))
    assert client.sent and client.sent[0][2] == "当前没有正在生成的输出"
    assert is_interrupted() is False


# ── AI 生成期间微信 /stop（_try_stop_ai） ─────────────

def test_stop_while_ai_running_wechat():
    client = FakeClient()
    runner = _make_runner(client=client)
    runner._allowed_users.add("u1")
    runner._ai_running = True
    try:
        handled = asyncio.run(runner._try_stop_ai("u1", "ctx", "/stop"))
        assert handled is True
        assert is_interrupted() is True
        assert client.sent and client.sent[0][2] == "⏹ 已停止当前生成"
    finally:
        reset_interrupt_async()


def test_stop_unauthorized_wechat():
    """未授权用户发 /stop：不触发中断、不回复、不拦截入队。"""
    client = FakeClient()
    runner = _make_runner(client=client)
    runner._ai_running = True
    handled = asyncio.run(runner._try_stop_ai("unknown", "ctx", "/stop"))
    assert handled is False
    assert client.sent == []
    assert is_interrupted() is False


def test_stop_ai_idle_wechat_not_handled():
    """AI 空闲时微信 /stop：特判不拦截，交队列走 _cmd_stop 提示。"""
    client = FakeClient()
    runner = _make_runner(client=client)
    runner._allowed_users.add("u1")
    handled = asyncio.run(runner._try_stop_ai("u1", "ctx", "/stop"))
    assert handled is False
    assert client.sent == []


# ── AI 生成期间本地 TUI /stop（_try_stop_local） ──────

def test_stop_while_ai_running_local():
    runner = _make_runner()
    runner._ai_running = True
    try:
        assert runner._try_stop_local("/stop") is True
        assert is_interrupted() is True
    finally:
        reset_interrupt_async()
    runner._ai_running = False
    assert runner._try_stop_local("/stop") is False


# ── _cmd_stop 直接调用（AI 运行中） ───────────────────

def test_cmd_stop_while_running():
    client = FakeClient()
    runner = _make_runner(client=client)
    runner._allowed_users.add("u1")
    runner._ai_running = True
    try:
        asyncio.run(runner._cmd_stop("u1", "ctx"))
        assert is_interrupted() is True
        assert client.sent and client.sent[0][2] == "⏹ 已停止当前生成"
    finally:
        reset_interrupt_async()


# ── _ai_running 标志维护 ──────────────────────────────

def test_ai_running_flag_maintained():
    session = FakeSession()
    runner = _make_runner(session_factory=lambda m="": session)
    runner._allowed_users.add("u1")

    async def _run_round(text):
        assert runner._ai_running is True
        session.messages.append({"role": "user", "content": text})
        session.messages.append({"role": "assistant", "content": "ok"})
        return {"interrupted": False, "session_id": None,
                "delta": {"input": 0, "output": 0, "calls": 0}, "elapsed": 0.0}

    session.run_round = _run_round
    asyncio.run(runner._ai_chat("u1", "ctx", "hello"))
    assert runner._ai_running is False


def test_ai_running_flag_reset_on_error():
    session = FakeSession()
    runner = _make_runner(session_factory=lambda m="": session)
    runner._allowed_users.add("u1")

    async def _run_round(text):
        raise RuntimeError("模拟网络错误")

    session.run_round = _run_round
    asyncio.run(runner._ai_chat("u1", "ctx", "hello"))
    assert runner._ai_running is False


# ── run_round 中断后的回复 ────────────────────────────

def test_ai_interrupted_reply():
    session = FakeSession()
    session.run_round_result = {
        "interrupted": True,
        "session_id": None,
        "delta": {"input": 0, "output": 0, "calls": 0},
        "elapsed": 0.0,
    }
    client = FakeClient()
    runner = _make_runner(client=client, session_factory=lambda m="": session)
    runner._allowed_users.add("u1")
    asyncio.run(runner._ai_chat("u1", "ctx", "hello"))
    assert any("⏹ 已停止生成" in text for _, _, text in client.sent)


# ── _poll_loop 实时拦截：/stop 不入队 ─────────────────

def test_poll_loop_stop_not_enqueue():
    client = StopClient()
    runner = _make_runner(client=client)
    runner._allowed_users.add("u1")
    runner._ai_running = True

    async def _run():
        # ★ 2026-08-20（稳定性修复）：asyncio.Queue() 在 asyncio.run 外构造
        #   依赖已关闭的事件循环（Python 3.9 get_event_loop 抛 RuntimeError，
        #   xdist 并行下偶发）——移入运行中的循环内创建；等待 /stop 已被实时
        #   拦截处理（sent 已写）再取消，替代固定 0.3s sleep（更快更稳）。
        queue: asyncio.Queue = asyncio.Queue()
        task = asyncio.create_task(runner._poll_loop(queue))
        for _ in range(100):
            if client.sent:
                break
            await asyncio.sleep(0.01)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return queue

    try:
        queue = asyncio.run(_run())
        assert queue.empty()  # /stop 被实时拦截，未进入消息队列
        assert client.sent and client.sent[0][2] == "⏹ 已停止当前生成"
    finally:
        reset_interrupt_async()


def test_poll_loop_regular_msg_still_enqueued():
    """非 /stop 消息仍正常入队（不破坏既有微信消息流程）。"""

    class RegularClient(FakeClient):
        def __init__(self):
            super().__init__()
            self.calls = 0

        async def get_updates(self, buf):
            self.calls += 1
            if self.calls == 1:
                return {
                    "msgs": [{
                        "message_type": 1,
                        "from_user_id": "u1",
                        "context_token": "ctx1",
                        "item_list": [{"type": 1, "text_item": {"text": "你好"}}],
                    }],
                    "get_updates_buf": "buf1",
                }
            await asyncio.sleep(3600)
            return {"msgs": [], "get_updates_buf": buf}

    client = RegularClient()
    runner = _make_runner(client=client)
    runner._allowed_users.add("u1")
    runner._ai_running = True

    async def _run():
        # ★ 2026-08-20（稳定性修复）：queue 移入运行中的循环内创建（避免
        #   asyncio.run 后残留无循环状态下构造 Queue 抛 RuntimeError）；
        #   等待消息已入队再取消，替代固定 0.3s sleep（更快更稳）。
        queue: asyncio.Queue = asyncio.Queue()
        task = asyncio.create_task(runner._poll_loop(queue))
        for _ in range(100):
            if queue.qsize() >= 1:
                break
            await asyncio.sleep(0.01)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return queue

    queue = asyncio.run(_run())
    assert queue.qsize() == 1
    item = queue.get_nowait()
    assert item["source"] == "wechat"
    assert item["text"] == "你好"
    assert client.sent == []
