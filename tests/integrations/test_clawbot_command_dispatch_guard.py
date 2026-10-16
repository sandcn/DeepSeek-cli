"""ClawBot 指令分派健壮性 —— handler 为空不得抛 TypeError（静默失效回归）。

★ 修复回归（2026-10）：``ClawbotCommand.handler`` 允许为空（扩展命令未提供
可调用体 / 条目被 patch 覆盖成 ``None``），而 ``_dispatch_cmd`` 直接
``await command.handler(...)`` → ``TypeError: 'NoneType' object is not
callable``，只落到消息消费者的兜底 ``except``（用户侧表现为「指令静默失效 +
日志异常」）。现分派侧显式判空并提示，注册侧同步告警（与
``app_init.subcommands.Subcommand`` 同一约定）。
"""

from __future__ import annotations

import logging

import pytest

from src.clawbot.command_registry import (
    ClawbotCommand,
    register_clawbot_command,
    reset,
    resolve_clawbot_command,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    reset()
    yield
    reset()


def _make_runner():
    from src.clawbot.runner import ClawBotRunner

    return ClawBotRunner(print_fn=lambda *a, **k: None)


@pytest.mark.asyncio
async def test_dispatch_without_handler_does_not_raise(monkeypatch):
    """handler 为空的指令：不抛异常，给出「未绑定处理器」提示。"""
    register_clawbot_command(
        ClawbotCommand("probe", usage="/probe", description="无处理器命令"),
    )
    assert resolve_clawbot_command("probe") is not None

    runner = _make_runner()
    sent: list = []

    async def _fake_send(from_id, ctx, text):
        sent.append((from_id, ctx, text))

    monkeypatch.setattr(runner, "_send", _fake_send)

    await runner._dispatch_cmd("user1", "ctx1", "/probe")  # 修复前：TypeError

    assert sent, "应当向用户回执提示"
    assert "未绑定处理器" in sent[-1][2]


@pytest.mark.asyncio
async def test_dispatch_with_handler_still_invoked(monkeypatch):
    """带 handler 的指令照常执行（回归保护）。"""
    calls: list = []

    async def _handler(runner, from_id, ctx, arg):
        calls.append((from_id, ctx, arg))

    register_clawbot_command(
        ClawbotCommand("probe2", usage="/probe2", handler=_handler),
    )
    runner = _make_runner()
    await runner._dispatch_cmd("u", "c", "/probe2 参数")
    assert calls == [("u", "c", "参数")]


def test_register_without_handler_logs_warning(caplog):
    """注册无 handler 命令 → 记录 warning（便于定位「指令不生效」）。"""
    with caplog.at_level(logging.WARNING, logger="src.clawbot.command_registry"):
        register_clawbot_command(ClawbotCommand("probe3"))
    assert any("probe3" in record.message for record in caplog.records)
