"""后台任务端口 — 能力接缝（Definition）。

对应 DeepSeek Harness 的 ``ctx.jobs``：管理后台任务；``job_*`` 工具读取或
停止任务。
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Protocol, runtime_checkable


@runtime_checkable
class JobsPort(Protocol):
    """后台任务接缝协议。"""

    def start(self, job_id: str, task: Any, *, kind: str = "generic", meta: Optional[dict] = None) -> Dict[str, Any]:
        """登记一个后台任务（task 为 awaitable / 可调用对象 / 句柄）。"""
        ...

    def stop(self, job_id: str) -> bool:
        """停止并移除后台任务。"""
        ...

    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        """获取任务状态。"""
        ...

    def list(self) -> List[Dict[str, Any]]:
        """列出全部后台任务。"""
        ...


__all__ = ["JobsPort"]
