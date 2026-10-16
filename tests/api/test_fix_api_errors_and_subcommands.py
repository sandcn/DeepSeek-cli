"""修复回归测试 — HTTP 错误提示兜底表 + CLI 子命令 handler 防护。

覆盖：
  1. ``errors._STATUS_HINTS`` 兜底快照与数据注册表键类型不一致（int vs
     ``str(status_code)``）→ 注册表不可用时所有 HTTP 错误提示静默消失；
  2. ``Subcommand.handler`` 可为 ``None`` 却在命中时被直接调用 →
     "NoneType object is not callable"（修复：matches/分派/注册三处防护）。
"""

from __future__ import annotations

import pytest


# ── 1. HTTP 错误提示兜底 ──────────────────────────────────────


def test_status_hints_snapshot_uses_str_keys():
    from src.api import errors as E

    assert E._STATUS_HINTS
    assert all(isinstance(key, str) for key in E._STATUS_HINTS)


def test_format_user_error_keeps_hint_when_registry_unavailable():
    """数据注册表不可用（回退兜底快照）时提示仍在。"""
    from src.api import errors as E

    original = E._status_hints
    E._status_hints = lambda: dict(E._STATUS_HINTS)
    try:
        msg = E.format_user_error(E.AuthError(401, "bad key"))
        assert "API 密钥无效" in msg
        msg429 = E.format_user_error(E.RateLimitError("too many"))
        assert "请求频率超限" in msg429
    finally:
        E._status_hints = original


def test_status_hints_normalizes_int_keys(monkeypatch):
    """扩展注册表若用 int 键，查询仍须命中（内部归一化为 str）。"""
    from src.api import errors as E
    import src.presentation_data as pd

    monkeypatch.setattr(pd, "http_error_hints", lambda: {429: "自定义 429 提示"})
    assert "自定义 429 提示" in E.format_user_error(E.RateLimitError("x"))


def test_status_hints_uses_str_keyed_registry(monkeypatch):
    from src.api import errors as E
    import src.presentation_data as pd

    monkeypatch.setattr(pd, "http_error_hints", lambda: {"404": "自定义 404 提示"})
    assert "自定义 404 提示" in E.format_user_error(E.NotFoundError(404, "x"))


# ── 2. CLI 子命令 handler 防护 ────────────────────────────────


def test_subcommand_matches_false_without_handler():
    from src.app_init.subcommands import Subcommand

    cmd = Subcommand("probe", matcher=lambda args: True, handler=None)
    assert cmd.matches(object()) is False


def test_subcommand_matches_false_without_matcher():
    from src.app_init.subcommands import Subcommand

    cmd = Subcommand("probe", matcher=None, handler=lambda app, args: None)
    assert cmd.matches(object()) is False


def test_subcommand_matches_true_when_complete():
    from src.app_init.subcommands import Subcommand

    cmd = Subcommand("probe", matcher=lambda args: True, handler=lambda app, args: None)
    assert cmd.matches(object()) is True


def test_register_subcommand_without_handler_never_matches(caplog):
    """注册成功（不抛错）但永不命中——避免分派处 NoneType 调用。"""
    from src.app_init import subcommands as S

    cmd = S.Subcommand("probe-no-handler", matcher=lambda args: True, handler=None)
    undo = S.register_subcommand(cmd)
    try:
        assert cmd.matches(object()) is False
    finally:
        undo()


@pytest.mark.asyncio
async def test_run_subcommand_phase_skips_missing_handler(monkeypatch):
    """分派侧双保险：命中但 handler 为空时跳过，不抛 TypeError。"""
    from src.app_init import subcommands as S
    from src.plugins.app import AppService

    calls: list = []

    async def _handler(app, args):
        calls.append("called")

    cmd_missing = S.Subcommand("probe-missing", matcher=lambda args: True, handler=None)
    cmd_ok = S.Subcommand(
        "probe-ok", matcher=lambda args: True, handler=_handler, order=100,
    )
    monkeypatch.setattr(
        S, "subcommands_for_phase", lambda phase: [cmd_missing, cmd_ok],
    )

    class _FakeApp:
        _run_subcommand_phase = AppService._run_subcommand_phase

    handled = await _FakeApp()._run_subcommand_phase(object(), "pre")
    assert handled is True
    assert calls == ["called"]
