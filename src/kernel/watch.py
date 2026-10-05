"""插件文件热重载监听 — 文件变更后自动重载对应插件（run-time HMR）。

「一切皆插件」下插件可来自外部文件（``./plugins``、``.chat/runtime_plugins``、
用户 profile 目录）；本模块提供零依赖的轮询监听：按文件 mtime 检测变更，
重新导入模块并把其中的 Plugin 重启（复用 ``Fiber.restart``）或挂载（新插件）。

用法::

    stop = kernel.watch_file("plugins/my_plugin.py")   # 注册监听
    await kernel.watcher().start(interval=1.0)         # 后台轮询（asyncio）
    ...
    await kernel.watcher().stop()
    stop()                                             # 单独取消某文件

也支持手动一次：``await kernel.reload_file(path)`` / ``await watcher.flush()``。
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Callable, List, Optional

_logger = logging.getLogger(__name__)


def _mtime(path: str) -> Optional[float]:
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


class PluginWatcher:
    """插件文件监听器 — mtime 轮询 + 自动重载。"""

    def __init__(self, kernel) -> None:
        self._kernel = kernel
        self._mtimes: dict[str, Optional[float]] = {}
        self._task: Optional[asyncio.Task] = None
        self._interval: float = 1.0

    # ── 注册 ─────────────────────────────────────────────

    def watch_file(self, path: str) -> Callable[[], None]:
        """监听一个插件文件；返回停止监听的 disposer。"""
        if not path:
            raise ValueError("监听路径不能为空")
        target = os.path.abspath(path)
        self._mtimes[target] = _mtime(target)

        def _stop() -> None:
            self._mtimes.pop(target, None)

        return _stop

    def unwatch_file(self, path: str) -> bool:
        return self._mtimes.pop(os.path.abspath(path), None) is not None

    def watched(self) -> List[str]:
        return sorted(self._mtimes)

    def is_watching(self) -> bool:
        return self._task is not None and not self._task.done()

    # ── 变更检测 ─────────────────────────────────────────

    def pending(self) -> List[str]:
        """返回自上次检查以来发生变更的文件路径（并更新基线）。"""
        changed: List[str] = []
        for path in list(self._mtimes):
            current = _mtime(path)
            if current != self._mtimes[path]:
                self._mtimes[path] = current
                changed.append(path)
        return changed

    async def flush(self) -> List[str]:
        """重载全部已变更文件，返回受影响的插件名。"""
        affected: List[str] = []
        for path in self.pending():
            try:
                affected.extend(await self.reload_file(path))
            except Exception:
                _logger.exception("热重载失败: %s", path)
        return affected

    async def reload_file(self, path: str) -> List[str]:
        """重新导入文件并重启/挂载其中的插件，返回受影响的插件名。"""
        from .loader import load_module, plugins_from_module

        if not os.path.isfile(path):
            raise FileNotFoundError(f"插件文件不存在: {path}")
        module = load_module(path)
        plugins = plugins_from_module(module)
        if not plugins:
            return []
        names: List[str] = []
        for plugin in plugins:
            fiber = self._kernel.fiber(plugin.name)
            if fiber is not None:
                fiber.definition = plugin
                await fiber.restart()
            else:
                self._kernel.mount(plugin)
            names.append(plugin.name)
        await self._kernel.settle()
        return names

    # ── 后台轮询 ─────────────────────────────────────────

    async def start(self, interval: float = 1.0) -> None:
        """启动后台轮询任务（幂等）。"""
        if interval <= 0:
            raise ValueError(f"轮询间隔必须 > 0，得到 {interval}")
        self._interval = interval
        if self.is_watching():
            return
        self._task = asyncio.get_running_loop().create_task(self._loop())

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            try:
                await self.flush()
            except asyncio.CancelledError:
                raise
            except Exception:
                _logger.debug("插件监听轮询异常", exc_info=True)

    async def stop(self) -> None:
        """停止后台轮询任务（幂等）。"""
        task = self._task
        self._task = None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001 - 取消即结束
            pass


__all__ = ["PluginWatcher"]
