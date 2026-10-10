"""通用异步数据源 — 后台线程加载 + 键级缓存 + 就绪通知。

补全引擎（``/sessions`` / ``/load`` 参数补全）与全屏视图（``/sessions``）的
初始化数据加载经本模块异步化：渲染线程只做非阻塞读取
（``peek``），未就绪时触发后台加载并立即返回「未就绪」，加载完成后经监听器
通知界面刷新（动态更新）。

设计要点：
  - **单工作线程**串行执行 fetcher（IO 任务，避免并发风暴与重复读盘）；
  - 同一 key 不重复入队（``_pending`` 去重）；TTL 过期后下次读取重新加载；
  - 监听器在**工作线程**调用（调用方负责线程安全，通常仅请求重绘）；
  - ``prefetch`` 强制后台加载（启动预热）；``peek(sync_fallback=True)``
    保持同步语义——未启用异步的调用方与单元测试行为不变。
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Any, Callable, Iterable

_logger = logging.getLogger(__name__)

#: 默认 TTL（秒）——fetcher 未显式指定时使用。
DEFAULT_TTL = 60.0
#: 流式数据源监听器通知节流间隔（秒）——emit 频繁时合并通知（首个 emit 立即
#: 通知、流结束兜底通知一次），避免每次 emit 都触发界面同步重绘。
STREAM_NOTIFY_INTERVAL = 0.1


class AsyncSource:
    """键 → 值的异步数据源（单工作线程 + 键级缓存 + 就绪监听）。"""

    def __init__(self, name: str = "async-source") -> None:
        self._name = name
        self._lock = threading.RLock()
        self._fetchers: dict[str, tuple[Callable[[], Any], float]] = {}
        self._streams: set[str] = set()
        self._values: dict[str, Any] = {}
        self._expires: dict[str, float] = {}
        self._loaded: set[str] = set()
        self._pending: set[str] = set()
        self._queue: deque[str] = deque()
        self._listeners: list[Callable[[str], None]] = []
        self._wakeup = threading.Event()
        self._thread: threading.Thread | None = None
        self._thread_lock = threading.Lock()
        self._closed = False

    # ── 注册 / 生命周期 ─────────────────────────────────

    def register(self, key: str, fetcher: Callable[[], Any],
                 ttl: float = DEFAULT_TTL) -> None:
        """注册一个键的加载函数（重复注册覆盖 fetcher，保留已缓存值）。"""
        with self._lock:
            self._fetchers[key] = (fetcher, float(ttl))
            self._streams.discard(key)

    def register_stream(self, key: str, producer: Callable[[Callable[[Any], None]], None],
                        ttl: float = DEFAULT_TTL) -> None:
        """注册**流式**数据源：``producer(emit)`` 可多次 ``emit(value)`` 增量产出。

        每次 emit 把传入值作为**最新累积值**写入缓存（立即可读），并按
        ``STREAM_NOTIFY_INTERVAL`` 节流通知监听器（首个 emit 立即通知、流结束
        兜底通知一次）——界面可随加载进度**逐条/逐批**增加内容。
        """
        with self._lock:
            self._fetchers[key] = (producer, float(ttl))
            self._streams.add(key)

    def unregister(self, key: str) -> None:
        """移除键（fetcher + 缓存值 + 待加载标记）。"""
        with self._lock:
            self._fetchers.pop(key, None)
            self._streams.discard(key)
            self._values.pop(key, None)
            self._expires.pop(key, None)
            self._loaded.discard(key)
            self._pending.discard(key)

    def add_listener(self, listener: Callable[[str], None]) -> Callable[[], None]:
        """注册「数据就绪」监听器（工作线程调用）；返回注销函数。"""
        with self._lock:
            self._listeners.append(listener)

        def _undo() -> None:
            with self._lock:
                try:
                    self._listeners.remove(listener)
                except ValueError:
                    pass

        return _undo

    def close(self) -> None:
        """关闭（停止工作线程；幂等）。"""
        with self._lock:
            self._closed = True
        self._wakeup.set()

    # ── 读取 ───────────────────────────────────────────

    def is_ready(self, key: str) -> bool:
        """该键当前是否已有未过期缓存值。"""
        with self._lock:
            return key in self._loaded and not self._expired_locked(key)

    def peek(self, key: str, *, sync_fallback: bool = True) -> tuple[bool, Any]:
        """读取缓存值。

        Args:
            key: 已注册的键。
            sync_fallback: True（同步模式）时未命中直接在**调用线程**执行
                fetcher（行为与同步缓存一致）；False（异步模式）时未命中
                仅触发后台加载并返回 ``(False, None)``。

        Returns:
            ``(ready, value)``；ready=False 表示数据未就绪（已触发后台加载）。
        """
        with self._lock:
            spec = self._fetchers.get(key)
        if spec is None:
            return (False, None)
        if self.is_ready(key):
            with self._lock:
                return (True, self._values.get(key))
        if sync_fallback:
            return (True, self._execute(key, spec))
        self.prefetch((key,))
        return (False, None)

    def prefetch(self, keys: Iterable[str]) -> None:
        """强制后台加载（预热）；已就绪未过期或已在加载中的键跳过。"""
        added = False
        with self._lock:
            if self._closed:
                return
            for key in keys:
                if key not in self._fetchers:
                    continue
                if key in self._loaded and not self._expired_locked(key):
                    continue
                if key in self._pending:
                    continue
                self._pending.add(key)
                self._queue.append(key)
                added = True
        if added:
            self._ensure_worker()
            self._wakeup.set()

    def invalidate(self, key: str | None = None) -> None:
        """使缓存失效（key 为 None 时清全部）；下次读取重新加载。"""
        with self._lock:
            if key is None:
                self._loaded.clear()
                self._values.clear()
                self._expires.clear()
                return
            self._loaded.discard(key)
            self._values.pop(key, None)
            self._expires.pop(key, None)

    def pending_keys(self) -> list[str]:
        """当前正在后台加载的键（诊断/测试用）。"""
        with self._lock:
            return sorted(self._pending)

    # ── 内部 ───────────────────────────────────────────

    def _expired_locked(self, key: str) -> bool:
        return time.monotonic() >= self._expires.get(key, 0.0)

    def _execute(self, key: str, spec: tuple[Callable[[], Any], float]) -> Any:
        """同步执行 fetcher（流式源完整跑一遍，取最后一次 emit 的值）。"""
        fetcher, ttl = spec
        with self._lock:
            streaming = key in self._streams
        if streaming:
            value: Any = None

            def _emit(item: Any) -> None:
                nonlocal value
                value = item

            try:
                fetcher(_emit)
            except Exception:
                _logger.debug("同步流式加载失败 key=%s", key, exc_info=True)
            self._store(key, value, ttl)
            return value
        try:
            value = fetcher()
        except Exception:
            _logger.debug("同步加载失败 key=%s", key, exc_info=True)
            value = None
        self._store(key, value, ttl)
        return value

    def _store(self, key: str, value: Any, ttl: float) -> None:
        with self._lock:
            self._values[key] = value
            self._expires[key] = time.monotonic() + float(ttl)
            self._loaded.add(key)

    def _ensure_worker(self) -> None:
        with self._thread_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(
                target=self._worker_loop, name=f"{self._name}-loader", daemon=True,
            )
            self._thread.start()

    def _worker_loop(self) -> None:
        while True:
            self._wakeup.wait()
            with self._lock:
                if not self._queue:
                    self._wakeup.clear()
                    if self._closed:
                        return
                    continue
                key = self._queue.popleft()
            self._run_fetch(key)
            if self._closed:
                with self._lock:
                    if not self._queue:
                        return

    def _run_fetch(self, key: str) -> None:
        with self._lock:
            spec = self._fetchers.get(key)
            streaming = key in self._streams
        if spec is None:
            with self._lock:
                self._pending.discard(key)
            return
        fetcher, ttl = spec
        if streaming:
            self._run_stream(key, fetcher, ttl)
            return
        try:
            value = fetcher()
        except Exception:
            _logger.debug("异步加载失败 key=%s", key, exc_info=True)
            with self._lock:
                self._pending.discard(key)
            return
        self._store(key, value, ttl)
        with self._lock:
            self._pending.discard(key)
        self._notify(key)

    def _run_stream(self, key: str, producer: Callable, ttl: float) -> None:
        """后台执行流式 producer（逐条 emit → 增量缓存 + 节流通知）。"""
        state = {"last": 0.0, "first": True, "missed": False}

        def _emit(value: Any) -> None:
            self._store(key, value, ttl)
            now = time.monotonic()
            if state["first"] or now - state["last"] >= STREAM_NOTIFY_INTERVAL:
                state["first"] = False
                state["last"] = now
                state["missed"] = False
                self._notify(key)
            else:
                state["missed"] = True

        try:
            producer(_emit)
        except Exception:
            _logger.debug("流式加载失败 key=%s", key, exc_info=True)
        finally:
            with self._lock:
                self._pending.discard(key)
        # 流结束兜底通知：最后一次 emit 若落在节流窗口内未通知，此处补一次。
        if state["missed"]:
            self._notify(key)

    def _notify(self, key: str) -> None:
        with self._lock:
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                listener(key)
            except Exception:
                _logger.debug("就绪监听器异常 key=%s", key, exc_info=True)


__all__ = ["AsyncSource", "DEFAULT_TTL", "STREAM_NOTIFY_INTERVAL"]
