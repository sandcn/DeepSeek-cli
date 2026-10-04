"""运行时不变量 — 断言插件树自身的运行期关系（对应 dsh 的 invariant 插件）。

每个不变量是一个 ``(kernel) -> None`` 检查函数：通过即返回；失败时抛出异常
或返回非空字符串（作为失败原因）。不变量由插件注册（注册即副作用，随 Fiber
卸载撤销），由 ``ctx.invariants`` 服务在运行时检查。
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional

Check = Callable[[object], Optional[str]]


class InvariantError(Exception):
    """运行时不变量被违反。"""


class InvariantRegistry:
    """不变量注册表（零业务依赖）。"""

    def __init__(self) -> None:
        self._checks: Dict[str, Check] = {}

    def register(self, name: str, check: Check) -> Callable[[], None]:
        if not isinstance(name, str) or not name:
            raise ValueError(f"不变量名必须是非空字符串: {name!r}")
        if not callable(check):
            raise TypeError(f"不变量检查必须可调用: {check!r}")
        self._checks[name] = check
        removed = False

        def _dispose() -> None:
            nonlocal removed
            if removed:
                return
            removed = True
            self._checks.pop(name, None)

        return _dispose

    def unregister(self, name: str) -> bool:
        return self._checks.pop(name, None) is not None

    def names(self) -> List[str]:
        return sorted(self._checks)

    def clear(self) -> None:
        self._checks.clear()

    def check_all(self, kernel: object) -> List[str]:
        """运行全部不变量，返回失败消息列表（空表示全部通过）。"""
        failures: List[str] = []
        for name in self.names():
            try:
                result = self._checks[name](kernel)
            except Exception as exc:  # noqa: BLE001 - 失败即记录
                failures.append(f"{name}: {exc}")
                continue
            if isinstance(result, str) and result:
                failures.append(f"{name}: {result}")
        return failures

    def check_one(self, name: str, kernel: object) -> List[str]:
        check = self._checks.get(name)
        if check is None:
            return [f"{name}: 未注册"]
        try:
            result = check(kernel)
        except Exception as exc:  # noqa: BLE001
            return [f"{name}: {exc}"]
        if isinstance(result, str) and result:
            return [f"{name}: {result}"]
        return []


__all__ = ["InvariantError", "InvariantRegistry", "Check"]
