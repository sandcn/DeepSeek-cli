"""事件域 — 会话事件 / Agent 事件 / 能力事件（对应 dsh 的三类事件域）。

DeepSeek Harness 把事件分为三个域，选对事件域是大多数改动的第一个决定：

1. **会话事件**（``SessionEventType``）——追加到会话日志并通过
   ``session/event`` 广播的**持久事实**。当某个事实必须在重新加载后仍然
   存在（上下文、回放、fork、遥测）时使用它。``turn/*``、``step/*``、
   ``system/message``、``user/message``、``assistant/message``、
   ``assistant/attempt``、``tool/*`` 属于这一域。
2. **Agent 事件**（``AgentEventType``）——携带活跃 Agent 的**实时扩展点**：
   inbox、步骤、状态、请求、验证、续跑。要观察或拦截进行中的工作时使用。
3. **能力事件**（``CapabilityEventType``）——向某个接缝（``fs/*``、
   ``tools/*``、``telemetry/*``）附加策略与适配器，无需导入循环。

「一切皆插件」：三类事件域的事件类型不再是本模块的硬编码字面量，而是登记到
``type_registry``（域 ``session`` / ``agent`` / ``capability``）——由清单中的
独立插件条目（``event_type``，经 ``src.plugins.event_type_entries``）注册，
可按 Profile/Patch/Overlay 覆盖或禁用；类属性经元类 ``__getattr__`` 实时解析
（``SessionEventType.TURN_START``），``ALL`` 为当前生效项元组的实时属性。

本模块零业务依赖（仅常量与轻量数据类），核心层、插件层与适配器层均可引用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .type_registry import active_events, declare_events, event_value

_MISSING = object()


# ═══════════════════════════════════════════════════════════════
# 1. 会话事件（持久事实）
# ═══════════════════════════════════════════════════════════════

_SESSION_EVENT_TYPES: dict = {
    "TURN_START": "turn/start",
    "TURN_END": "turn/end",
    "STEP_START": "step/start",
    "STEP_END": "step/end",
    "SYSTEM_MESSAGE": "system/message",
    "USER_MESSAGE": "user/message",
    "ASSISTANT_MESSAGE": "assistant/message",
    "ASSISTANT_ATTEMPT": "assistant/attempt",
    "TOOL_RESULT": "tool/result",
    "REQUEST_HEADER": "request/header",
    "REQUEST_CONTEXT": "request/context",
    # ── 结构变更（也是仅追加事实，回放时在对应位置生效） ──
    "INSERT": "session/insert",
    "REPLACE": "session/replace",
    "DELETE": "session/delete",
    "TRUNCATE": "session/truncate",
    "RESET": "session/reset",
    #: 所有会话事件的广播通道（订阅者监听一次即可看到全部事件；不计入 ALL）
    "EMIT": "session/event",
}


# ═══════════════════════════════════════════════════════════════
# 2. Agent 事件（实时扩展点）
# ═══════════════════════════════════════════════════════════════

_AGENT_EVENT_TYPES: dict = {
    "CREATED": "agent/created",
    "DESTROYED": "agent/destroyed",
    "INBOX": "agent/inbox",
    "PRE_STEP": "agent/pre-step",
    "STEP_START": "agent/step-start",
    "STEP_END": "agent/step-end",
    "REQUEST": "agent/request",
    "ASSISTANT_STREAM": "agent/assistant-stream",
    "TURN_STOPPING": "agent/turn-stopping",
    "STATUS": "agent/status",
    "VALIDATION": "agent/validation",
    "CONTINUATION": "agent/continuation",
}


# ═══════════════════════════════════════════════════════════════
# 3. 能力事件（接缝）
# ═══════════════════════════════════════════════════════════════

_CAPABILITY_EVENT_TYPES: dict = {
    # 工具执行流水线
    "TOOLS_PRE_EXECUTE": "tools/pre-execute",
    "TOOLS_EXECUTE": "tools/execute",
    "TOOLS_POST_EXECUTE": "tools/post-execute",
    # 文件系统接缝
    "FS_READ": "fs/read",
    "FS_WRITE": "fs/write",
    "FS_REMOVE": "fs/remove",
    "FS_MOVE": "fs/move",
    "FS_LIST": "fs/list",
    # 子进程 / Shell 接缝
    "SHELL_SPAWN": "shell/spawn",
    "SUBPROCESS_SPAWN": "subprocess/spawn",
    # 终端 / 后台任务接缝
    "TERMINALS_OPEN": "terminals/open",
    "TERMINALS_CLOSE": "terminals/close",
    "JOBS_START": "jobs/start",
    "JOBS_STOP": "jobs/stop",
    # 沙盒 / 审批接缝
    "SANDBOX_CHECK": "sandbox/check",
    "APPROVAL_REQUEST": "approval/request",
    # 遥测接缝
    "TELEMETRY_EVENT": "telemetry/event",
}

declare_events("session", _SESSION_EVENT_TYPES)
declare_events("agent", _AGENT_EVENT_TYPES)
declare_events("capability", _CAPABILITY_EVENT_TYPES)


class _EventDomainMeta(type):
    """事件域元类 — 类属性实时解析注册表当前生效值。"""

    _domain = ""
    _exclude_from_all: tuple = ()

    def __getattr__(cls, name: str):
        if name.startswith("_"):
            raise AttributeError(name)
        value = event_value(cls._domain, name, _MISSING)
        if value is _MISSING:
            raise AttributeError(f"{cls.__name__} 无事件类型 {name!r}")
        return value

    @property
    def ALL(cls) -> tuple:
        excluded = set(cls._exclude_from_all)
        return tuple(
            value
            for name, value in active_events(cls._domain).items()
            if name not in excluded
        )


class SessionEventType(metaclass=_EventDomainMeta):
    """会话事件类型 — 追加到会话日志、可回放/投影的持久事实。"""

    _domain = "session"
    _exclude_from_all = ("EMIT",)


class AgentEventType(metaclass=_EventDomainMeta):
    """Agent 事件类型 — 携带活跃 Agent 的实时扩展点。"""

    _domain = "agent"


class CapabilityEventType(metaclass=_EventDomainMeta):
    """能力事件类型 — 向接缝附加策略与适配器。"""

    _domain = "capability"


#: 会进入模型历史投影的会话事件（「模型可见即已记录」约束的作用域）
MESSAGE_EVENTS = frozenset({
    SessionEventType.SYSTEM_MESSAGE,
    SessionEventType.USER_MESSAGE,
    SessionEventType.ASSISTANT_MESSAGE,
    SessionEventType.TOOL_RESULT,
})


# ═══════════════════════════════════════════════════════════════
# Agent 描述与记录
# ═══════════════════════════════════════════════════════════════

@dataclass
class AgentDescriptor:
    """Agent 描述 — 创建 Agent 时登记的静态信息。"""

    kind: str = "main"
    model: str = ""
    session_id: Optional[str] = None
    parent_id: Optional[str] = None
    meta: dict = field(default_factory=dict)


@dataclass
class AgentRecord:
    """活跃 Agent 记录 — 注册表持有的运行时句柄。"""

    id: str
    kind: str
    agent: Any
    model: str = ""
    session_id: Optional[str] = None
    parent_id: Optional[str] = None
    status: str = "idle"
    meta: dict = field(default_factory=dict)
    scope: Any = None

    def touch(self, status: str) -> None:
        self.status = status

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "model": self.model,
            "session_id": self.session_id,
            "parent_id": self.parent_id,
            "status": self.status,
            "meta": dict(self.meta),
        }


__all__ = [
    "SessionEventType",
    "MESSAGE_EVENTS",
    "AgentEventType",
    "CapabilityEventType",
    "AgentDescriptor",
    "AgentRecord",
]
