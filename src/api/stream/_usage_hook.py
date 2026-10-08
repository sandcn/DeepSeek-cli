"""流式用量刷新钩子 — api 层依赖倒置入口

api 流式管线需要在 AI 生成期间实时刷新「上下文使用率百分比」，但该能力
由 core.context_manager 提供。为避免 ``api.stream.pipeline_async →
core.context_manager`` 形成循环依赖，本模块提供注册式钩子：

    - core.context_manager 在导入时 ``register_usage_hook(update_streaming_usage)``
      与 ``register_prompt_usage_hook(update_real_prompt_usage)``；
    - api 流式管线调用 ``notify_streaming_usage(delta, label)``（流式增量）；
    - api 流式/非流式管线在收到真实 usage 时调用
      ``notify_prompt_usage(prompt_tokens, label)``（真实输入 token 基线）；
    - 未注册时静默无操作（不影响流式主流程）。

依赖方向：api.stream.pipeline_async → api/stream/_usage_hook ← core.context_manager。
"""

from __future__ import annotations

from typing import Callable, Optional

_usage_hook: Optional[Callable[[int, Optional[str]], None]] = None
_prompt_hook: Optional[Callable[[int, Optional[str]], None]] = None


def register_usage_hook(fn: Optional[Callable[[int, Optional[str]], None]]) -> None:
    """注册流式用量刷新实现（None 表示注销）。"""
    global _usage_hook
    _usage_hook = fn


def notify_streaming_usage(delta_tokens: int, label: Optional[str] = None) -> None:
    """通知流式用量变更（转发给已注册实现；未注册时静默忽略）。"""
    hook = _usage_hook
    if hook is not None:
        hook(delta_tokens, label)


def register_prompt_usage_hook(fn: Optional[Callable[[int, Optional[str]], None]]) -> None:
    """注册真实 prompt（输入）token 刷新实现（None 表示注销）。

    接收一次真实 API 调用的 ``prompt_tokens``（服务端计费的输入 token，
    含系统提词/工具列表/全部消息与模板开销），供 core 侧作为上下文使用率
    的权威基线校准估算值。
    """
    global _prompt_hook
    _prompt_hook = fn


def notify_prompt_usage(prompt_tokens: int, label: Optional[str] = None) -> None:
    """通知真实 prompt token（转发给已注册实现；未注册时静默忽略）。"""
    hook = _prompt_hook
    if hook is not None:
        hook(prompt_tokens, label)


__all__ = [
    "register_usage_hook",
    "notify_streaming_usage",
    "register_prompt_usage_hook",
    "notify_prompt_usage",
]
