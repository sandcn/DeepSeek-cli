"""内核错误类型 — 无业务依赖。"""

from __future__ import annotations


class KernelError(Exception):
    """内核基础异常。"""


class ServiceNotFound(KernelError):
    """请求的服务不存在。"""

    def __init__(self, key: str):
        super().__init__(f"服务未提供: {key}")
        self.key = key


class ServiceExists(KernelError):
    """同一作用域重复提供同名服务。"""

    def __init__(self, key: str):
        super().__init__(f"服务已提供: {key}（一个能力同一作用域只允许一个实现）")
        self.key = key


class DependencyError(KernelError):
    """服务依赖不满足。"""

    def __init__(self, key: str, missing: list[str]):
        super().__init__(f"服务 '{key}' 依赖未满足: {', '.join(missing)}")
        self.key = key
        self.missing = list(missing)


class PluginError(KernelError):
    """插件定义或加载错误。"""


class FiberStateError(KernelError):
    """Fiber 状态非法或状态机迁移错误。"""


__all__ = [
    "KernelError",
    "ServiceNotFound",
    "ServiceExists",
    "DependencyError",
    "PluginError",
    "FiberStateError",
]
