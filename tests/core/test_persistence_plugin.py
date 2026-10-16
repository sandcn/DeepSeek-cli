"""持久化 / 断点插件测试 — ctx.persistence / ctx.checkpoint 服务。

覆盖：
- 服务提供默认 JSON 文件 provider；
- set_provider 替换与转发；
- ChatSession 默认经内核服务解析端口；
- 数据命令端口解析（内核优先 / 无内核回退）。
"""

from __future__ import annotations

import pytest

from src.kernel import set_current_kernel
from src.plugins.bootstrap import build_kernel, shutdown_kernel


@pytest.fixture
async def cli_kernel():
    kernel = await build_kernel("cli")
    try:
        yield kernel
    finally:
        await shutdown_kernel(kernel)


class _FakePersistence:
    def __init__(self):
        self.saved = []
        self.loaded = []

    def save_session(self, messages, model, session_id=None, subagents=None, session_log=None):
        self.saved.append((messages, model, session_id))
        return "fake-id"

    def load_session(self, session_id):
        self.loaded.append(session_id)
        return {"id": session_id}

    def list_sessions(self):
        return [{"id": "fake-id"}]

    def delete_session(self, session_id):
        return True

    def get_recover_cmd(self, session_id):
        return f"chat --load {session_id}"


class _FakeCheckpoint:
    def __init__(self):
        self.saves = []

    def save(self, messages, model, task_description=""):
        self.saves.append((messages, model, task_description))

    def load(self):
        return {"messages": []}

    def clear(self):
        pass

    def exists(self):
        return True

    def get_info(self):
        return {"message_count": 0}


async def test_services_present(cli_kernel):
    from src.core.adapters.persistence import JsonFileCheckpoint, JsonFilePersistence

    assert isinstance(cli_kernel.resolve_service("persistence").port(), JsonFilePersistence)
    assert isinstance(cli_kernel.resolve_service("checkpoint").port(), JsonFileCheckpoint)


async def test_persistence_set_provider_and_forward(cli_kernel):
    service = cli_kernel.resolve_service("persistence")
    fake = _FakePersistence()
    previous = service.set_provider(fake)
    try:
        assert service.save_session([{"role": "user"}], "m") == "fake-id"
        assert fake.saved == [([{"role": "user"}], "m", None)]
        assert service.load_session("x") == {"id": "x"}
        assert service.list_sessions() == [{"id": "fake-id"}]
        assert service.get_recover_cmd("x") == "chat --load x"
        assert service.port() is fake
    finally:
        service.set_provider(previous)


async def test_checkpoint_set_provider_and_forward(cli_kernel):
    service = cli_kernel.resolve_service("checkpoint")
    fake = _FakeCheckpoint()
    previous = service.set_provider(fake)
    try:
        service.save([{"role": "user"}], "m", "task")
        assert fake.saves == [([{"role": "user"}], "m", "task")]
        assert service.load() == {"messages": []}
        assert service.exists() is True
        assert service.get_info() == {"message_count": 0}
    finally:
        service.set_provider(previous)


async def test_session_uses_kernel_ports(cli_kernel):
    from src.core.session import ChatSession

    session = ChatSession()
    assert session._persistence_port is cli_kernel.resolve_service("persistence").port()
    assert session._checkpoint_port is cli_kernel.resolve_service("checkpoint").port()


async def test_data_cmd_resolves_kernel_port(cli_kernel):
    from src.core.commands._data_cmd import _resolve_persistence

    class _Ctx:
        persistence_port = None

    assert _resolve_persistence(_Ctx()) is cli_kernel.resolve_service("persistence").port()


def test_data_cmd_fallback_without_kernel():
    set_current_kernel(None)
    from src.core.adapters.persistence import JsonFilePersistence
    from src.core.commands._data_cmd import _resolve_persistence

    class _Ctx:
        persistence_port = None

    assert isinstance(_resolve_persistence(_Ctx()), JsonFilePersistence)


def test_session_fallback_without_kernel():
    set_current_kernel(None)
    from src.core.adapters.persistence import JsonFileCheckpoint, JsonFilePersistence
    from src.core.session import ChatSession

    session = ChatSession()
    assert isinstance(session._persistence_port, JsonFilePersistence)
    assert isinstance(session._checkpoint_port, JsonFileCheckpoint)
