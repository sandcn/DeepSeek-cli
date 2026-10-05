"""内核诊断 — 依赖关系 / 阻塞原因 / 运行统计（运行时自省）。

「一切皆插件」的动态组合下，某个插件为何没有加载（依赖缺失？被禁用？加载
失败？）需要可解释。本模块提供纯函数：给定内核，产出逐插件的依赖诊断报告、
阻塞原因、服务提供者与运行统计。Kernel 的 ``dependency_report`` /
``why_blocked`` / ``service_providers`` / ``kernel_stats`` / ``diagnose``
方法委托本模块。
"""

from __future__ import annotations

from typing import Any, Dict, List

from .fiber import FiberState


def _definition_provide(fiber) -> List[str]:
    return [str(key) for key in (getattr(fiber.definition, "provide", ()) or ())]


def dependency_report(kernel: Any) -> List[dict]:
    """逐插件依赖诊断报告（不改状态）。"""
    report: List[dict] = []
    for fiber in kernel.fibers():
        report.append({
            "name": fiber.name,
            "state": fiber.state.value,
            "inject": list(fiber.inject),
            "missing": list(fiber.missing_dependencies()),
            "provide": _definition_provide(fiber),
            "source": getattr(fiber.definition, "source", "") or "",
            "disabled": bool(fiber.disabled),
            "error": repr(fiber.error) if fiber.error else "",
            "children": [getattr(child, "name", "?") for child in fiber._children],
        })
    return report


def service_providers(kernel: Any, key: str) -> List[str]:
    """提供某服务的插件名（栈序，最近提供的在前）。"""
    stack = kernel._services.get(key) or []
    providers: List[str] = []
    for _value, owner in reversed(stack):
        providers.append(owner.name if owner is not None else "root")
    return providers


def _possible_providers(kernel: Any, key: str) -> List[str]:
    """声明会提供某 key 的插件名（用于解释缺失依赖的潜在提供者）。"""
    names: List[str] = []
    for fiber in kernel.fibers():
        if key in _definition_provide(fiber):
            names.append(fiber.name)
    return names


def why_blocked(kernel: Any, name: str) -> List[str]:
    """解释某插件当前为何未处于 ACTIVE（或说明其健康）。"""
    fiber = kernel.fiber(name)
    if fiber is None:
        return [f"未找到插件: {name!r}"]
    reasons: List[str] = []
    if fiber.disabled:
        reasons.append("插件被运行时显式禁用（kernel.enable 可恢复）")
    if fiber.error is not None:
        reasons.append(f"上次加载失败: {fiber.error!r}")
    missing = fiber.missing_dependencies()
    for key in missing:
        possible = _possible_providers(kernel, key)
        hint = f"（可能由 {', '.join(possible)} 提供）" if possible else ""
        reasons.append(f"缺少依赖服务 {key!r}{hint}")
    if fiber.state is FiberState.PENDING and not missing:
        reasons.append("等待内核重规划（依赖已就绪）")
    if not reasons:
        reasons.append("无阻塞：依赖就绪" if fiber.active else f"当前状态: {fiber.state.value}")
    return reasons


def kernel_stats(kernel: Any) -> Dict[str, Any]:
    """内核运行统计（Fiber 状态计数 / 服务数 / 插件数 / 监听器数）。"""
    states: Dict[str, int] = {}
    disabled = 0
    for fiber in kernel.fibers():
        states[fiber.state.value] = states.get(fiber.state.value, 0) + 1
        if fiber.disabled:
            disabled += 1
    listeners = 0
    bus = getattr(kernel, "bus", None)
    if bus is not None:
        listeners = sum(len(handlers) for handlers in bus._listeners.values())
    return {
        "fibers": len(kernel.fibers()),
        "states": states,
        "services": len(kernel.service_keys()),
        "disabled": disabled,
        "listeners": listeners,
        "profile": getattr(kernel, "profile", "") or "",
    }


def format_diagnostics(kernel: Any, *, indent: int = 2) -> str:
    """人类可读的内核诊断文本（统计 + 逐插件依赖状态）。"""
    pad = " " * indent
    stats = kernel_stats(kernel)
    lines = [
        f"profile: {stats['profile'] or '(未记录)'}",
        f"fibers: {stats['fibers']}  services: {stats['services']}  listeners: {stats['listeners']}",
        f"states: {stats['states']}  disabled: {stats['disabled']}",
        "plugins:",
    ]
    for item in dependency_report(kernel):
        marker = " [disabled]" if item["disabled"] else ""
        line = f"{pad}- {item['name']}: {item['state']}{marker}"
        if item["missing"]:
            line += f"  missing={item['missing']}"
        if item["error"]:
            line += f"  error={item['error']}"
        lines.append(line)
    return "\n".join(lines)


__all__ = [
    "dependency_report",
    "service_providers",
    "why_blocked",
    "kernel_stats",
    "format_diagnostics",
]
