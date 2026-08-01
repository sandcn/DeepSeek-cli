"""工具执行上下文 — 当前 tool_id 的 contextvar 传递。

工具输出事件链路的 tool_id 贯穿（Bug A 修复核心）：
- 工具执行期间（``_tool_callbacks._run_tool_method`` / ``SubAgent.run_method``）
  通过 ``set_current_tool_id`` 将当前 tool_call_id 写入 contextvar；
- ``tools/base.print_to_terminal`` / ``Func._publish_tool_text`` /
  ``SharedCapture.write`` 读取 ``get_current_tool_id`` 决定输出归属，
  避免并发工具输出事件错路由（O(N²) 广播放大 + 错误累积到「最后打开的块」）。

asyncio 隔离语义：
ToolScheduler 经 ``asyncio.gather`` 并发执行多个工具，每个工具在各自独立的
task/context 中运行，contextvar 天然隔离——并发工具各自读写自己的 tool_id，
互不干扰。

Layer 0 — 零外部依赖，可被 tools/base / _capture_manager / _tool_callbacks
/ subagent 引用。
"""

from __future__ import annotations

from contextvars import ContextVar, Token

__all__ = [
    "set_current_tool_id",
    "reset_current_tool_id",
    "get_current_tool_id",
    "run_with_tool_context",
]

#: 当前正在执行的工具调用唯一 ID（tool_call_id）；空字符串表示无工具上下文。
_CURRENT_TOOL_ID: ContextVar[str] = ContextVar("current_tool_id", default="")


def set_current_tool_id(tool_id: str) -> Token:
    """设置当前工具 ID，返回 reset token（须与 reset_current_tool_id 成对使用）。

    Args:
        tool_id: 工具调用唯一 ID（tool_call_id）；空字符串表示无归属。
    """
    return _CURRENT_TOOL_ID.set(tool_id)


def reset_current_tool_id(token: Token) -> None:
    """重置当前工具 ID 到调用前状态（须在 finally 中调用）。

    遗漏 reset 会导致 contextvar 泄漏，后续工具错误归属——调用方必须
    保证 set/reset 成对（推荐经 run_with_tool_context 统一管理）。
    """
    _CURRENT_TOOL_ID.reset(token)


def get_current_tool_id() -> str:
    """返回当前工具 ID（无上下文时返回空字符串）。"""
    return _CURRENT_TOOL_ID.get()


async def run_with_tool_context(tool_id: str, coro):
    """在指定 tool_id 上下文中执行协程，finally 保证 reset。

    模板方法骨架：设置-执行-重置，覆盖正常/异常/取消路径，避免复制
    try/finally 模板。工具执行入口（_run_tool_method / SubAgent.run_method）
    统一使用此辅助函数管理 contextvar 生命周期。

    Args:
        tool_id: 工具调用唯一 ID（tool_call_id）。
        coro: 待执行的 awaitable（工具执行协程）。
    """
    token = _CURRENT_TOOL_ID.set(tool_id)
    try:
        return await coro
    finally:
        _CURRENT_TOOL_ID.reset(token)
