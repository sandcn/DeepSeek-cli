"""能力接缝插件 — 提供 ``ctx.fs`` / ``ctx.shell`` / ``ctx.subprocess`` /
``ctx.terminals`` / ``ctx.jobs`` / ``ctx.sandbox``。

每项能力是**三段式 seam**（对应 DeepSeek Harness 的 capability seam）：

- **Service Definition**：``src.core.ports`` 中的协议（FsPort / ShellPort /
  SubprocessPort / TerminalsPort / JobsPort / SandboxPort）；
- **Service Provider**：默认本地实现（``src.core.adapters.capabilities``），
  可用 ``set_provider`` 整体替换（如指向远程沙箱）；
- **Consumer**：面向模型的工具（read_file / write_file / bash ...）经
  ``ctx.fs`` / ``ctx.shell`` 等使用能力。

替换一个 Provider 即可改变整个执行世界；每次操作同时广播能力事件
（``fs/*``、``shell/spawn``、``jobs/*`` ...），插件可据此附加策略与审计。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from ..core.adapters.capabilities import (
    DefaultSandboxProvider,
    LocalFsProvider,
    LocalJobsProvider,
    LocalShellProvider,
    LocalSubprocessProvider,
    LocalTerminalsProvider,
)
from ..kernel import Service, plugin

_logger = logging.getLogger(__name__)


class CapabilityService(Service):
    """能力接缝服务基类 — 持有可替换的 Provider。"""

    _provider_cls = None

    def __init__(self, ctx, config=None, provider=None):
        super().__init__(ctx, config)
        self._provider = provider if provider is not None else self._provider_cls()

    @property
    def provider(self):
        return self._provider

    def set_provider(self, provider):
        """替换 Provider（返回旧 Provider 以便回滚）。"""
        previous = self._provider
        self._provider = provider
        return previous

    def _notify(self, event: str, *args: Any) -> None:
        try:
            self.ctx.notify(event, *args)
        except Exception:
            _logger.debug("能力事件发布失败: %s", event, exc_info=True)


class FsService(CapabilityService):
    """文件系统接缝 — 占据 ``ctx.fs``。"""

    provide = "fs"
    name = "fs"
    _provider_cls = LocalFsProvider

    def exists(self, path: str) -> bool:
        return self.provider.exists(path)

    def is_file(self, path: str) -> bool:
        return self.provider.is_file(path)

    def is_dir(self, path: str) -> bool:
        return self.provider.is_dir(path)

    def read_text(self, path: str, **kwargs) -> str:
        self._notify("fs/read", path)
        return self.provider.read_text(path, **kwargs)

    def read_bytes(self, path: str) -> bytes:
        self._notify("fs/read", path)
        return self.provider.read_bytes(path)

    def write_text(self, path: str, content: str, **kwargs) -> None:
        self._notify("fs/write", path)
        return self.provider.write_text(path, content, **kwargs)

    def write_bytes(self, path: str, data: bytes) -> None:
        self._notify("fs/write", path)
        return self.provider.write_bytes(path, data)

    def remove(self, path: str, **kwargs) -> None:
        self._notify("fs/remove", path)
        return self.provider.remove(path, **kwargs)

    def move(self, src: str, dst: str) -> None:
        self._notify("fs/move", src, dst)
        return self.provider.move(src, dst)

    def mkdir(self, path: str, **kwargs) -> None:
        return self.provider.mkdir(path, **kwargs)

    def list_dir(self, path: str) -> list:
        self._notify("fs/list", path)
        return self.provider.list_dir(path)

    def walk_files(self, path: str) -> list:
        self._notify("fs/list", path)
        return self.provider.walk_files(path)

    def stat(self, path: str) -> dict:
        return self.provider.stat(path)

    def realpath(self, path: str) -> str:
        return self.provider.realpath(path)

    def size_mb(self, path: str) -> float:
        return self.provider.size_mb(path)


class SubprocessService(CapabilityService):
    """子进程接缝 — 占据 ``ctx.subprocess``。"""

    provide = "subprocess"
    name = "subprocess"
    _provider_cls = LocalSubprocessProvider

    async def spawn(self, argv, **kwargs):
        self._notify("subprocess/spawn", list(argv))
        return await self.provider.spawn(argv, **kwargs)

    async def spawn_background(self, argv, **kwargs):
        self._notify("subprocess/spawn", list(argv))
        return await self.provider.spawn_background(argv, **kwargs)


class ShellService(CapabilityService):
    """Shell 接缝 — 占据 ``ctx.shell``（默认经 subprocess 语义执行）。"""

    provide = "shell"
    name = "shell"
    _provider_cls = LocalShellProvider

    async def run(self, command: str, **kwargs):
        self._notify("shell/spawn", command)
        return await self.provider.run(command, **kwargs)

    async def spawn(self, command: str, **kwargs):
        self._notify("shell/spawn", command)
        return await self.provider.spawn(command, **kwargs)

    async def create_process(self, command, *, shell: bool = True, **kwargs):
        """创建原始进程（供需要流式 / pty 控制的 Consumer，如 bash 工具）。"""
        self._notify("shell/spawn", command if shell else list(command))
        return await self.provider.create_process(command, shell=shell, **kwargs)


class TerminalsService(CapabilityService):
    """持久终端接缝 — 占据 ``ctx.terminals``。"""

    provide = "terminals"
    name = "terminals"
    _provider_cls = LocalTerminalsProvider

    async def open(self, argv=None, **kwargs) -> str:
        self._notify("terminals/open", argv)
        return await self.provider.open(argv, **kwargs)

    async def write(self, session_id: str, data: str) -> bool:
        return await self.provider.write(session_id, data)

    async def read(self, session_id: str) -> str:
        return await self.provider.read(session_id)

    async def close(self, session_id: str) -> bool:
        self._notify("terminals/close", session_id)
        return await self.provider.close(session_id)

    def list(self) -> list:
        return self.provider.list()


class JobsService(CapabilityService):
    """后台任务接缝 — 占据 ``ctx.jobs``。"""

    provide = "jobs"
    name = "jobs"
    _provider_cls = LocalJobsProvider

    def start(self, job_id: str, task: Any, **kwargs) -> dict:
        self._notify("jobs/start", job_id)
        return self.provider.start(job_id, task, **kwargs)

    def stop(self, job_id: str) -> bool:
        self._notify("jobs/stop", job_id)
        return self.provider.stop(job_id)

    def get(self, job_id: str):
        return self.provider.get(job_id)

    def list(self) -> list:
        return self.provider.list()


class SandboxService(CapabilityService):
    """沙盒接缝 — 占据 ``ctx.sandbox``。"""

    provide = "sandbox"
    name = "sandbox"
    _provider_cls = DefaultSandboxProvider

    def check_path(self, path: str, *, operation: str = "read"):
        allowed, reason = self.provider.check_path(path, operation=operation)
        if not allowed:
            self._notify("sandbox/check", path, operation)
        return allowed, reason

    def check_argv(self, argv):
        allowed, reason = self.provider.check_argv(argv)
        if not allowed:
            self._notify("sandbox/check", list(argv), "execute")
        return allowed, reason

    def wrap_argv(self, argv):
        return self.provider.wrap_argv(argv)

    def wrap_env(self, env):
        return self.provider.wrap_env(env)


class SeamsService(Service):
    """能力接缝汇总 — 提供 ``ctx.seams``（自省与批量替换）。"""

    provide = "seams"
    name = "seams"
    inject = ("fs", "shell", "subprocess", "terminals", "jobs", "sandbox")

    _KEYS = ("fs", "shell", "subprocess", "terminals", "jobs", "sandbox")

    def keys(self) -> list:
        return list(self._KEYS)

    def services(self) -> dict:
        return {key: self.ctx.consume(key) for key in self._KEYS}

    def provider_of(self, key: str):
        return self.ctx.consume(key).provider

    def replace(self, key: str, provider) -> Any:
        """替换某项能力的 Provider，返回旧 Provider。"""
        if key not in self._KEYS:
            raise KeyError(f"未知能力接缝: {key!r}")
        return self.ctx.consume(key).set_provider(provider)

    def describe(self) -> list:
        return [
            {"key": key, "provider": type(self.provider_of(key)).__name__}
            for key in self._KEYS
        ]


@plugin("fs", provide=["fs"])
def apply_fs(ctx):
    return FsService(ctx)


@plugin("subprocess", provide=["subprocess"])
def apply_subprocess(ctx):
    return SubprocessService(ctx)


@plugin("shell", inject=["subprocess"], provide=["shell"])
def apply_shell(ctx):
    return ShellService(ctx)


@plugin("terminals", provide=["terminals"])
def apply_terminals(ctx):
    return TerminalsService(ctx)


@plugin("jobs", provide=["jobs"])
def apply_jobs(ctx):
    return JobsService(ctx)


@plugin("sandbox", provide=["sandbox"])
def apply_sandbox(ctx):
    return SandboxService(ctx)


@plugin("seams", inject=["fs", "shell", "subprocess", "terminals", "jobs", "sandbox"], provide=["seams"])
def apply_seams(ctx):
    return SeamsService(ctx)
