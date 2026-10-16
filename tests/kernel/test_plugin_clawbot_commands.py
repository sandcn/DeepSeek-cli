"""ClawBot 命令条目化测试 — 每个远程指令是清单中的独立插件条目。

覆盖：
- 清单为每个内置指令声明独立条目；
- runtime bundle 引入 clawbot bundle（且不再直接内联 clawbot 聚合条目）；
- 默认 profile 下指令分派经注册表；
- overlay 禁用单个指令条目真正生效（未知指令提示）；
- 动态帮助文本随注册表变化；
- 条目 config 覆盖 usage/order/description；
- 注册表接管 / 禁用 / 扩展 API（含 /stop 实时特判随条目禁用失效）。
"""

from __future__ import annotations

import asyncio

import pytest

from src.kernel import Kernel, materialize
from src.plugins.bootstrap import (
    inject_managed_config,
    resolve_entries,
)
from src.plugins.manifest import CLAWBOT_COMMAND_ENTRIES, build_config_tree


class FakeClient:
    def __init__(self):
        self.sent: list = []

    async def send_message(self, to_user_id, context_token, text):
        self.sent.append((to_user_id, context_token, text))
        return {}

    async def get_config(self, ilink_user_id, context_token):
        return {}

    async def send_typing(self, ilink_user_id, typing_ticket, status):
        return {}


class FakeSession:
    def __init__(self, model: str = ""):
        self.model = model
        self.messages: list = []


def _make_runner():
    from src.clawbot.runner import ClawBotRunner

    return ClawBotRunner(
        client=FakeClient(),
        session_factory=lambda m="": FakeSession(m),
        tui=False,
        print_fn=lambda *a: None,
    )


def _resolved(profile="cli"):
    return materialize(resolve_entries(profile, discover_external=False))


def test_manifest_declares_each_command():
    from src.clawbot.command_registry import builtin_clawbot_command_ids

    names, ids = [], []
    for entry, plug in _resolved():
        if getattr(plug, "name", "") == "clawbot_command":
            names.append((entry.config or {}).get("name"))
            ids.append(entry.id)
    assert set(names) == set(builtin_clawbot_command_ids())
    assert len(ids) == len(set(ids))
    assert all(i.startswith("clawbot::clawbot_command_") for i in ids)


def test_manifest_entries_match_registry_declaration():
    from src.clawbot.command_registry import builtin_clawbot_command_ids

    declared = {(e.get("config") or {}).get("name") for e in CLAWBOT_COMMAND_ENTRIES}
    assert declared == set(builtin_clawbot_command_ids())


def test_tree_declares_clawbot_bundle():
    tree = build_config_tree()
    assert "clawbot" in tree.bundles()
    assert "clawbot" in tree.bundle("runtime").includes
    runtime_ids = [spec.id for spec in tree.bundle("runtime").plugins]
    assert "clawbot" not in runtime_ids


async def _build_with_disable(ids):
    from src.kernel.overlay import apply_overlay

    entries = apply_overlay(resolve_entries("cli", discover_external=False), {"disable": list(ids)})
    resolved = materialize(entries)
    inject_managed_config(resolved)
    kernel = Kernel(name="t", profile="cli")
    for entry, plug in resolved:
        if entry.disabled:
            continue
        kernel.mount(plug, config=entry.config)
    await kernel.settle()
    return kernel


def test_dispatch_uses_registry_defaults():
    from src.clawbot.command_registry import build_help_text

    runner = _make_runner()
    runner._allowed_users.add("u1")
    asyncio.run(runner._dispatch_cmd("u1", "ctx", "/help"))
    assert runner._client.sent
    assert "/shell" in runner._client.sent[0][2]
    assert "/stop" in build_help_text()


async def test_unknown_command_when_disabled():
    kernel = await _build_with_disable(["clawbot::clawbot_command_shell"])
    try:
        from src.clawbot.command_registry import resolve_clawbot_command

        assert resolve_clawbot_command("shell") is None
        runner = _make_runner()
        runner._allowed_users.add("u1")
        await runner._dispatch_cmd("u1", "ctx", "/shell ls")
        assert runner._client.sent
        assert runner._client.sent[0][2].startswith("未知指令 /shell")
    finally:
        await kernel.dispose()


async def test_help_text_reflects_active_commands():
    kernel = await _build_with_disable(["clawbot::clawbot_command_model"])
    try:
        from src.clawbot.command_registry import build_help_text

        text = build_help_text()
        assert "/shell" in text
        assert "/model" not in text
    finally:
        await kernel.dispose()


async def test_stop_command_disabled_disables_realtime_guard():
    kernel = await _build_with_disable(["clawbot::clawbot_command_stop"])
    try:
        from src.api.interrupt_async import reset_interrupt_async

        runner = _make_runner()
        runner._ai_running = True
        try:
            assert runner._is_stop_command("/stop") is False
            assert runner._try_stop_local("/stop") is False
        finally:
            reset_interrupt_async()
    finally:
        await kernel.dispose()


def test_registry_api_roundtrip():
    from src.clawbot import command_registry as reg

    reg.reset()
    try:
        from src.clawbot.command_registry import ClawbotCommand

        assert set(reg.builtin_clawbot_command_ids()) == {
            "help", "shell", "clear", "new", "time", "status", "model", "stop",
        }
        undo = reg.set_managed_builtin_clawbot_commands(["model"])
        assert reg.resolve_clawbot_command("model") is None
        undo()
        assert reg.resolve_clawbot_command("model") is not None

        undo2 = reg.disable_builtin_clawbot_commands(["time"])
        assert reg.resolve_clawbot_command("time") is None
        undo2()

        async def _noop(runner, from_id, ctx, arg):
            return None

        undo3 = reg.register_clawbot_command(
            ClawbotCommand("echo", usage="/echo", description="回显", aliases=("e",), order=99, handler=_noop)
        )
        assert reg.resolve_clawbot_command("e").name == "echo"
        assert reg.unregister_clawbot_command("echo") is True
        undo3()
        assert reg.resolve_clawbot_command("echo") is None
    finally:
        reg.reset()


async def test_entry_config_override():
    from src.plugins.config import apply as config_apply
    from src.plugins.clawbot_commands import apply_clawbot_command

    kernel = Kernel(name="t")
    kernel.mount(config_apply)
    kernel.mount(apply_clawbot_command, config={
        "name": "shell", "usage": "/sh", "description": "自定义", "order": 42,
    })
    await kernel.settle()
    try:
        from src.clawbot.command_registry import resolve_clawbot_command

        cmd = resolve_clawbot_command("shell")
        assert cmd.usage == "/sh"
        assert cmd.description == "自定义"
        assert cmd.order == 42
    finally:
        await kernel.dispose()
