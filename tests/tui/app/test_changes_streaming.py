"""``/changes`` 流式输出期间：可打开实时刷新，回滚动作暂停。"""

from __future__ import annotations

from types import SimpleNamespace

from src.core.commands._session_cmd import ChangesCommand
from src.tui.app.changes_view import _ROLLBACK_BLOCKED_MESSAGE, _rollback_blocked


def _model(status_active: bool):
    return SimpleNamespace(
        status=SimpleNamespace(status_active=status_active),
        changes_view=None,
    )


def test_changes_command_is_direct_during_round():
    meta = ChangesCommand().meta
    assert meta.direct_during_round is True
    assert meta.queue_only is False
    assert "流式" in meta.description


def test_rollback_allowed_only_after_generation():
    assert _rollback_blocked(_model(True)) is True
    assert _rollback_blocked(_model(False)) is False
    assert _rollback_blocked(SimpleNamespace(status=None)) is False
    assert "生成结束" in _ROLLBACK_BLOCKED_MESSAGE
