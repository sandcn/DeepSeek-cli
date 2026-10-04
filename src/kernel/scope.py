"""Scope — 按 key 划分的作用域注册原语（对应 dsh 的 core/scope）。

同一个进程里可以并存多个 Agent / 会话（如主 Agent 与各 SubAgent），每个都
可能注册自己的服务、事件监听或策略。Scope 把这类「注册」按 scope key 分区：

- 落在某个 scope 内的注册只对该 scope 可见，父级/其它 scope 不受影响；
- scope 可以继承父级解析，也可以显式隔离若干 key（这些 key 只解析 scope
  本地注册，不穿透父级/内核）；
- scope 关闭时撤销其全部注册（可逆副作用）。

本模块零业务依赖，仅建立在 ``Context`` 的空间可组合性之上，供插件与内核
消费者共同使用（库，无 ctx 键，与 dsh 的 core/scope 定位一致）。
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple


class Scope:
    """一个作用域：携带本地服务注册，独立于父级与其它 scope。"""

    def __init__(self, key: str, ctx: Any, *, isolated: Tuple[str, ...] = ()) -> None:
        self.key = key
        self._ctx = ctx
        self._isolated = tuple(isolated)
        self._closed = False
        self._disposers: List[Callable[[], Any]] = []

    # ── 属性 ─────────────────────────────────────────────

    @property
    def ctx(self) -> Any:
        """作用域上下文（可直接 ``.service`` / ``.plugin``）。"""
        return self._ctx

    @property
    def isolated(self) -> Tuple[str, ...]:
        return self._isolated

    @property
    def closed(self) -> bool:
        return self._closed

    # ── 注册（可逆副作用） ───────────────────────────────

    def provide(self, name: str, value: Any) -> Callable[[], Any]:
        """在作用域内注册服务，返回撤销函数（关闭 scope 时自动撤销）。"""
        self._ensure_open()
        release = self._ctx.provide(name, value)
        self._disposers.append(release)
        return release

    def on(self, event: str, handler: Callable, **kwargs: Any) -> Callable[[], Any]:
        """在作用域内注册事件监听（关闭 scope 时自动移除）。"""
        self._ensure_open()
        release = self._ctx.on(event, handler, **kwargs)
        self._disposers.append(release)
        return release

    def effect(self, callback: Callable[[], Any]) -> Callable[[], Any]:
        """在作用域内登记可逆副作用（关闭 scope 时自动撤销）。"""
        self._ensure_open()
        release = self._ctx.effect(callback)
        self._disposers.append(release)
        return release

    def plugin(self, target: Any, config: Optional[dict] = None) -> Any:
        """在作用域内挂载子插件（继承该作用域的解析）。"""
        self._ensure_open()
        fiber = self._ctx.plugin(target, config=config)
        self._disposers.append(lambda: fiber)
        return fiber

    # ── 解析 ─────────────────────────────────────────────

    def service(self, name: str, default: Any = None) -> Any:
        return self._ctx.service(name, default)

    def consume(self, name: str) -> Any:
        return self._ctx.consume(name)

    def has(self, name: str) -> bool:
        return self._ctx.has(name)

    # ── 派生 ─────────────────────────────────────────────

    def derive(self) -> "Scope":
        """派生一个继承当前作用域解析的新 scope（独立注册空间）。"""
        self._ensure_open()
        return Scope(self.key, self._ctx.extend(), isolated=self._isolated)

    def isolate(self, *names: str) -> "Scope":
        """在当前 scope 上追加隔离 key，返回继承解析的新 scope。"""
        self._ensure_open()
        merged = tuple(dict.fromkeys((*self._isolated, *names)))
        child_ctx = self._ctx.isolate_many(*merged) if merged else self._ctx.extend()
        return Scope(self.key, child_ctx, isolated=merged)

    # ── 关闭 ─────────────────────────────────────────────

    def close(self) -> None:
        """撤销作用域内全部注册（逆序，幂等）。"""
        if self._closed:
            return
        self._closed = True
        while self._disposers:
            disposer = self._disposers.pop()
            try:
                disposer()
            except Exception:
                import logging

                logging.getLogger(__name__).debug(
                    "scope 清理回调异常: %s", self.key, exc_info=True
                )

    def __enter__(self) -> "Scope":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError(f"scope 已关闭: {self.key!r}")

    def __repr__(self) -> str:  # pragma: no cover - 调试辅助
        state = "closed" if self._closed else "open"
        return f"<Scope {self.key!r} {state} isolated={list(self._isolated)}>"


class ScopeRegistry:
    """作用域注册表 — 按 key 打开 / 获取 / 关闭 Scope。"""

    def __init__(self, root_ctx: Any) -> None:
        self._root = root_ctx
        self._scopes: Dict[str, Scope] = {}

    def open(
        self,
        key: str,
        *,
        isolated: Iterable[str] = (),
        parent: Optional[Scope] = None,
    ) -> Scope:
        """打开（或返回已存在的）作用域。

        Args:
            key: 作用域标识（同 key 重复打开返回既有实例）。
            isolated: 需要隔离（只解析本地注册）的服务 key。
            parent: 父 scope；给定则在父 scope 上派生，否则从根上下文派生。
        """
        if not isinstance(key, str) or not key:
            raise ValueError(f"scope key 必须是非空字符串: {key!r}")
        existing = self._scopes.get(key)
        if existing is not None and not existing.closed:
            return existing
        base = parent or Scope(key, self._root)
        scope = base.isolate(*tuple(isolated))
        self._scopes[key] = scope
        return scope

    def of(self, key: str) -> Optional[Scope]:
        scope = self._scopes.get(key)
        if scope is None or scope.closed:
            return None
        return scope

    def require(self, key: str) -> Scope:
        scope = self.of(key)
        if scope is None:
            raise KeyError(f"未打开的 scope: {key!r}")
        return scope

    def close(self, key: str) -> bool:
        scope = self._scopes.pop(key, None)
        if scope is None:
            return False
        scope.close()
        return True

    def close_all(self) -> None:
        for key in list(self._scopes):
            self.close(key)

    def keys(self) -> List[str]:
        return sorted(self._scopes)

    def __contains__(self, key: str) -> bool:
        return self.of(key) is not None


__all__ = ["Scope", "ScopeRegistry"]
