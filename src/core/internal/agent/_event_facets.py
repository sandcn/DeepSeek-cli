"""Agent 事件切面 — turn/step 生命周期事件与 waterfall 拦截。

对应 DeepSeek Harness 的「轮次流程」（turn / step 事件切面）：

- ``turn/start`` / ``turn/end``：轮次打开与关闭。一个轮次（turn）包含零个
  或多个步骤（step）：它在领取首条输入之前打开，在不再欠下任何工作时关闭。
- ``agent/pre-step``（waterfall）：决定接纳的输入；监听器可改写或拒绝已领取
  消息，首次领取被拒绝或为空时可关闭不含步骤的轮次。
- ``step/start`` / ``step/end``：一次模型请求加上它调用的工具。
- ``agent/request``（waterfall）：请求前解析路由、提交系统提示词与用户消息。
- ``llm/stream``（waterfall）：环绕流式模型调用。
- ``agent/turn-stopping``（serial）：轮次停止判定，插件可声明仍欠工作。

所有切面经 ``core.adapters.kernel_runtime`` 与内核事件总线交互；无内核时
全部退化为 no-op（向后兼容单元测试与独立调用）。
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from ...adapters import kernel_runtime as _kr


def _kind(agent: Any) -> str:
    return getattr(agent, "agent_kind", "") or "main"


def _last_user_input(messages: List[dict]) -> str:
    for message in reversed(messages or []):
        if message.get("role") == "user":
            content = message.get("content", "")
            return content if isinstance(content, str) else str(content)
    return ""


# ── 轮次（turn） ────────────────────────────────────────


async def turn_start(agent: Any, *, session_id: Optional[str] = None, input: str = "") -> dict:
    """打开轮次：广播 ``turn/start``。"""
    envelope: Dict[str, Any] = {
        "agent_id": getattr(agent, "agent_id", ""),
        "kind": _kind(agent),
        "session_id": session_id,
        "input": input or _last_user_input(getattr(agent, "messages", []) or []),
    }
    await _kr.emit_event("turn/start", agent, envelope)
    return envelope


async def turn_end(agent: Any, *, interrupted: bool = False, session_id: Optional[str] = None, reason: str = "") -> dict:
    """关闭轮次：广播 ``turn/end``。"""
    envelope: Dict[str, Any] = {
        "agent_id": getattr(agent, "agent_id", ""),
        "kind": _kind(agent),
        "session_id": session_id,
        "interrupted": bool(interrupted),
        "reason": reason,
    }
    await _kr.emit_event("turn/end", agent, envelope)
    return envelope


async def pre_step(agent: Any, messages: List[dict]) -> Dict[str, Any]:
    """``agent/pre-step``（waterfall）：决定接纳的输入。

    监听器签名 ``(agent, decision, next)``；decision 形如
    ``{"messages": [...], "reject": False, "enter": True}``。返回最终 decision。
    """
    decision: Dict[str, Any] = {
        "messages": list(messages or []),
        "reject": False,
        "enter": True,
    }

    async def _base() -> Any:
        return decision

    result = await _kr.run_waterfall("agent/pre-step", agent, decision, base=_base)
    return result if isinstance(result, dict) else decision


async def step_start(agent: Any, index: int, *, messages: Optional[List[dict]] = None) -> None:
    await _kr.emit_event(
        "step/start",
        agent,
        {"agent_id": getattr(agent, "agent_id", ""), "index": index,
         "messages": messages or []},
    )


async def step_end(agent: Any, index: int, *, tool_calls: Optional[list] = None, interrupted: bool = False) -> None:
    await _kr.emit_event(
        "step/end",
        agent,
        {"agent_id": getattr(agent, "agent_id", ""), "index": index,
         "tool_calls": list(tool_calls or []), "interrupted": bool(interrupted)},
    )


# ── 请求（request）与流（stream） ───────────────────────


async def request(agent: Any, call: Dict[str, Any]) -> Dict[str, Any]:
    """``agent/request``（waterfall）：请求前解析路由、提交消息与工具。

    监听器签名 ``(agent, call, next)``；call 形如
    ``{"messages": [...], "model": str, "tools": [...]}``。返回最终 call。
    """
    async def _base() -> Any:
        return call

    result = await _kr.run_waterfall("agent/request", agent, call, base=_base)
    return result if isinstance(result, dict) else call


async def llm_stream(agent: Any, call: Dict[str, Any], base: Callable[[], Any]) -> Any:
    """``llm/stream``（waterfall）：环绕流式模型调用。

    监听器签名 ``(agent, call, next)``；``await next()`` 得到模型结果
    ``(reasoning, content, usage, tool_calls)``，可包装或短路。
    """
    return await _kr.run_waterfall("llm/stream", agent, call, base=base)


async def turn_stopping(agent: Any, state: Dict[str, Any]) -> Any:
    """``agent/turn-stopping``（serial）：轮次停止判定。

    监听器签名 ``(agent, state)``（无 next）；返回非 None 表示仍欠工作
    （继续下一个步骤）。
    """
    return await _kr.run_serial("agent/turn-stopping", agent, state)


__all__ = [
    "turn_start",
    "turn_end",
    "pre_step",
    "step_start",
    "step_end",
    "request",
    "llm_stream",
    "turn_stopping",
]
