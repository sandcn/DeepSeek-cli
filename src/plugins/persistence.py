"""持久化 / 断点插件 — 提供 ``ctx.persistence`` 与 ``ctx.checkpoint``。

「一切皆插件」：会话持久化与任务断点存储作为可替换的 provider 坐在内核
之上。默认 provider 分别是 ``JsonFilePersistence``（``.chat/msg_list/*.json``）
与 ``JsonFileCheckpoint``（``.chat/msg_list/_checkpoint.json``）；外部插件可
经 ``ctx.persistence.set_provider(...)`` / ``ctx.checkpoint.set_provider(...)``
整体替换存储后端（数据库、对象存储、远端服务……）。

``ChatSession`` 默认经 ``active_persistence_port()`` /
``active_checkpoint_port()`` 解析本服务的 provider（内核缺失时回退
既有 JSON 文件实现），因此替换 provider 即改变会话存储位置。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from ..kernel import Service, plugin

_logger = logging.getLogger(__name__)


class PersistenceService(Service):
    """会话持久化服务 — 占据 ``ctx.persistence``。"""

    provide = "persistence"
    name = "persistence"

    def __init__(self, ctx, config=None):
        super().__init__(ctx, config)
        from ..core.adapters.persistence import JsonFilePersistence

        self._provider: Any = JsonFilePersistence()
        ctx.effect(lambda: self._on_unload)

    def _on_unload(self) -> None:
        self._provider = None

    @property
    def provider(self):
        return self._provider

    def port(self):
        """供 Session 注入的 PersistencePort（未加载时返回 None）。"""
        return self._provider

    def set_provider(self, provider) -> Any:
        previous = self._provider
        self._provider = provider
        return previous

    # ── 便捷转发 ─────────────────────────────────────────

    def save_session(self, messages, model, session_id=None, subagents=None, session_log=None):
        return self._provider.save_session(
            messages, model, session_id, subagents, session_log
        )

    def load_session(self, session_id: str):
        return self._provider.load_session(session_id)

    def list_sessions(self):
        return self._provider.list_sessions()

    def delete_session(self, session_id: str) -> bool:
        return self._provider.delete_session(session_id)

    def get_recover_cmd(self, session_id: str) -> str:
        return self._provider.get_recover_cmd(session_id)


class CheckpointService(Service):
    """断点管理服务 — 占据 ``ctx.checkpoint``。"""

    provide = "checkpoint"
    name = "checkpoint"

    def __init__(self, ctx, config=None):
        super().__init__(ctx, config)
        from ..core.adapters.persistence import JsonFileCheckpoint

        self._provider: Any = JsonFileCheckpoint()
        ctx.effect(lambda: self._on_unload)

    def _on_unload(self) -> None:
        self._provider = None

    @property
    def provider(self):
        return self._provider

    def port(self):
        """供 Session 注入的 CheckpointPort（未加载时返回 None）。"""
        return self._provider

    def set_provider(self, provider) -> Any:
        previous = self._provider
        self._provider = provider
        return previous

    # ── 便捷转发 ─────────────────────────────────────────

    def save(self, messages, model, task_description: str = "") -> None:
        self._provider.save(messages, model, task_description)

    def load(self):
        return self._provider.load()

    def clear(self) -> None:
        self._provider.clear()

    def exists(self) -> bool:
        return self._provider.exists()

    def get_info(self):
        return self._provider.get_info()


@plugin("persistence", provide=["persistence"])
def apply_persistence(ctx):
    return PersistenceService(ctx)


@plugin("checkpoint", provide=["checkpoint"])
def apply_checkpoint(ctx):
    return CheckpointService(ctx)


__all__ = ["PersistenceService", "CheckpointService", "apply_persistence", "apply_checkpoint"]
