"""流式用量刷新钩子 — api 层依赖倒置入口

api 流式管线需要在 AI 生成期间实时刷新「上下文使用率百分比」，但该能力
由 core.context_manager 提供。为避免 ``api.stream.pipeline_async →
core.context_manager`` 形成循环依赖，本模块提供注册式钩子：

    - core.context_manager 在导入时 ``register_usage_hook(update_streaming_usage)``；
    - api 流式管线调用 ``notify_streaming_usage(delta, label)``；
    - 未注册时静默无操作（不影响流式主流程）。

依赖方向：api.stream.pipeline_async → api/stream/_usage_hook ← core.context_manager。
"""

from __future__ import annotations

from typing import Callable, Optional

_usage_hook: Optional[Callable[[int, Optional[str]], None]] = None


def register_usage_hook(fn: Optional[Callable[[int, Optional[str]], None]]) -> None:
    """注册流式用量刷新实现（None 表示注销）。"""
    global _usage_hook
    _usage_hook = fn


def notify_streaming_usage(delta_tokens: int, label: Optional[str] = None) -> None:
    """通知流式用量变更（转发给已注册实现；未注册时静默忽略）。"""
    hook = _usage_hook
    if hook is not None:
        hook(delta_tokens, label)


__all__ = ["register_usage_hook", "notify_streaming_usage"]
