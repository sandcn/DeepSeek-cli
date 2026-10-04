"""能力接缝测试 — 三段式 seam（Definition + Provider + Consumer）。

覆盖：
- 插件提供 ctx.fs / ctx.shell / ctx.subprocess / ctx.terminals / ctx.jobs /
  ctx.sandbox / ctx.seams；
- Provider 可整体替换（Consumer 无需改动）；
- Consumer（file_ops 读写、spawn_process）经接缝落到 Provider；
- 能力事件广播（fs/read、fs/write、shell/spawn 等）；
- 无内核时 Consumer 回退本地实现。
"""

from __future__ import annotations

import pytest

from src.kernel import Kernel, set_current_kernel
from src.plugins.bootstrap import build_kernel, shutdown_kernel


@pytest.fixture
async def cli_kernel():
    kernel = await build_kernel("cli")
    try:
        yield kernel
    finally:
        await shutdown_kernel(kernel)


async def test_seam_services_present(cli_kernel):
    for key in ("fs", "shell", "subprocess", "terminals", "jobs", "sandbox", "seams"):
        assert cli_kernel.has_service(key), key


async def test_fs_service_read_write(cli_kernel, tmp_path):
    fs = cli_kernel.resolve_service("fs")
    target = tmp_path / "a.txt"
    fs.write_text(str(target), "hello")
    assert fs.read_text(str(target)) == "hello"
    assert fs.exists(str(target)) is True
    assert fs.is_file(str(target)) is True
    assert fs.stat(str(target))["size"] == 5
    fs.move(str(target), str(tmp_path / "b.txt"))
    assert fs.exists(str(tmp_path / "b.txt"))
    fs.remove(str(tmp_path / "b.txt"))
    assert fs.exists(str(tmp_path / "b.txt")) is False


async def test_fs_emits_capability_events(cli_kernel, tmp_path):
    seen = []
    cli_kernel.bus.on("fs/read", lambda path: seen.append(("read", path)))
    cli_kernel.bus.on("fs/write", lambda path: seen.append(("write", path)))
    fs = cli_kernel.resolve_service("fs")
    target = tmp_path / "a.txt"
    fs.write_text(str(target), "x")
    fs.read_text(str(target))
    kinds = [kind for kind, _ in seen]
    assert kinds == ["write", "read"]


async def test_fs_provider_replacement(cli_kernel, tmp_path):
    class RecordingFs:
        def __init__(self):
            self.reads = []
            self.writes = []

        def exists(self, path):
            return False

        def is_file(self, path):
            return False

        def is_dir(self, path):
            return False

        def read_text(self, path, **kwargs):
            self.reads.append(path)
            return "remote"

        def read_bytes(self, path):
            return b"remote"

        def write_text(self, path, content, **kwargs):
            self.writes.append((path, content))

        def write_bytes(self, path, data):
            pass

        def remove(self, path, **kwargs):
            pass

        def move(self, src, dst):
            pass

        def mkdir(self, path, **kwargs):
            pass

        def list_dir(self, path):
            return []

        def walk_files(self, path):
            return []

        def stat(self, path):
            return {}

        def realpath(self, path):
            return path

        def size_mb(self, path):
            return 0.0

    fs = cli_kernel.resolve_service("fs")
    provider = RecordingFs()
    previous = fs.set_provider(provider)
    try:
        assert fs.read_text("/remote/x") == "remote"
        assert provider.reads == ["/remote/x"]
    finally:
        fs.set_provider(previous)


async def test_file_ops_consumer_uses_seam(cli_kernel, tmp_path):
    fs = cli_kernel.resolve_service("fs")

    class Marker:
        def __init__(self, inner):
            self.inner = inner
            self.calls = []

        def read_text(self, path, **kwargs):
            self.calls.append(("read", path))
            return self.inner.read_text(path, **kwargs)

        def write_text(self, path, content, **kwargs):
            self.calls.append(("write", path))
            return self.inner.write_text(path, content, **kwargs)

    from src.core.adapters.capabilities import LocalFsProvider

    marker = Marker(LocalFsProvider())
    previous = fs.set_provider(marker)
    try:
        from src.tools.file_ops import atomic_write_file, _sync_read_file

        target = tmp_path / "c.txt"
        atomic_write_file(str(target), "content")
        assert _sync_read_file(str(target)) == "content"
        assert [call[0] for call in marker.calls] == ["write", "read"]
    finally:
        fs.set_provider(previous)


async def test_shell_service_run(cli_kernel):
    shell = cli_kernel.resolve_service("shell")
    result = await shell.run("echo hello")
    assert "hello" in result.stdout
    assert result.returncode == 0


async def test_shell_emits_spawn_event(cli_kernel):
    seen = []
    cli_kernel.bus.on("shell/spawn", lambda command: seen.append(command))
    shell = cli_kernel.resolve_service("shell")
    await shell.run("true")
    assert seen == ["true"]


async def test_subprocess_service_spawn(cli_kernel):
    subprocess_service = cli_kernel.resolve_service("subprocess")
    result = await subprocess_service.spawn(["echo", "spawned"])
    assert "spawned" in result.stdout


async def test_jobs_service_lifecycle(cli_kernel):
    jobs = cli_kernel.resolve_service("jobs")
    jobs.start("j1", None, kind="demo")
    assert jobs.get("j1")["kind"] == "demo"
    assert [job["id"] for job in jobs.list()] == ["j1"]
    assert jobs.stop("j1") is True
    assert jobs.get("j1") is None


async def test_terminals_service(cli_kernel):
    terminals = cli_kernel.resolve_service("terminals")
    session_id = await terminals.open(["cat"])
    try:
        assert await terminals.write(session_id, "hi\n") is True
        assert isinstance(await terminals.read(session_id), str)
        assert any(item["id"] == session_id for item in terminals.list())
    finally:
        assert await terminals.close(session_id) is True


async def test_sandbox_service_rejects_unsafe_path(cli_kernel):
    sandbox = cli_kernel.resolve_service("sandbox")
    allowed, _ = sandbox.check_path("/tmp/ok.txt", operation="read")
    assert allowed is True
    denied, reason = sandbox.check_path("CON", operation="read")
    assert denied is False
    assert reason


async def test_seams_service_describe_and_replace(cli_kernel):
    seams = cli_kernel.resolve_service("seams")
    assert set(seams.keys()) == {"fs", "shell", "subprocess", "terminals", "jobs", "sandbox"}
    described = {item["key"]: item["provider"] for item in seams.describe()}
    assert described["fs"] == "LocalFsProvider"

    class FakeFs:
        pass

    previous = seams.replace("fs", FakeFs())
    try:
        assert seams.provider_of("fs").__class__.__name__ == "FakeFs"
    finally:
        seams.replace("fs", previous)


async def test_spawn_process_helper_uses_seam(cli_kernel):
    from src.core.adapters import kernel_runtime as kr

    process = await kr.spawn_process("echo seam", shell=True, stdout=-1, stderr=-1)
    out, _ = await process.communicate()
    assert b"seam" in out


def test_spawn_process_helper_without_kernel():
    import asyncio

    set_current_kernel(None)
    from src.core.adapters import kernel_runtime as kr

    async def _run():
        process = await kr.spawn_process("echo local", shell=True, stdout=-1, stderr=-1)
        out, _ = await process.communicate()
        return out

    assert b"local" in asyncio.run(_run())


def test_file_ops_fallback_without_kernel(tmp_path):
    set_current_kernel(None)
    from src.tools.file_ops import atomic_write_file, _sync_read_file

    target = tmp_path / "d.txt"
    atomic_write_file(str(target), "fallback")
    assert _sync_read_file(str(target)) == "fallback"
