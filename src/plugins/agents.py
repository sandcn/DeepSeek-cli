"""Agent 注册表插件 — 提供 ``ctx.agents``（对应 dsh 的 core/agent）。

Agent 接口与**活跃 Agent 注册表**：任何被创建的主 Agent 或 SubAgent 都在
此登记，携带自身 id / 类型 / 模型 / 会话 / 父 Agent / 状态，并拥有一个
独立的作用域（``kernel.scope.Scope``）——每个 agent 的能力注册落在自己的
作用域内，互不污染。

生命周期：创建即登记（``agent/created``，串行等待，失败回滚），销毁即注销
（``agent/destroyed``）。所有登记项随插件 Fiber 卸载撤销（可逆副作用）。
"""

from __future__ import annotations

import itertools
import logging
from typing import Any, Dict, List, Optional

from ..core.events.agent_types import AgentEventType, AgentRecord
from ..kernel import Service, ScopeRegistry, plugin

_logger = logging.getLogger(__name__)


class AgentsService(Service):
    """Agent 注册表 — 占据 ``ctx.agents``。"""

    provide = "agents"
    name = "agents"
    inject = ("events",)

    def __init__(self, ctx, config=None):
        super().__init__(ctx, config)
        self._records: Dict[str, AgentRecord] = {}
        self._by_agent: Dict[int, str] = {}
        self._by_session: Dict[str, List[str]] = {}
        self._scopes = ScopeRegistry(ctx)
        self._seq = itertools.count(1)
        ctx.effect(lambda: self._on_unload)

    # ── 生命周期 ─────────────────────────────────────────

    def _on_unload(self) -> None:
        for record in list(self._records.values()):
            self._dispose_record(record)
        self._records.clear()
        self._by_agent.clear()
        self._by_session.clear()
        self._scopes.close_all()

    # ── 注册 ─────────────────────────────────────────────

    def register(
        self,
        agent: Any,
        *,
        kind: str = "main",
        parent: Optional[AgentRecord] = None,
        meta: Optional[dict] = None,
        session_id: Optional[str] = None,
        model: Optional[str] = None,
        isolated: Optional[list] = None,
        scope_key: Optional[str] = None,
        owned: bool = True,
    ) -> AgentRecord:
        """登记一个活跃 Agent 并为其打开独立作用域。"""
        if agent is None:
            raise ValueError("agent 不能为 None")
        if id(agent) in self._by_agent:
            return self._records[self._by_agent[id(agent)]]

        agent_id = scope_key or f"{kind}-{next(self._seq)}"
        scope = self._scopes.open(agent_id, isolated=list(isolated or ()))
        record = AgentRecord(
            id=agent_id,
            kind=kind,
            agent=agent,
            model=model if model is not None else getattr(agent, "model", "") or "",
            session_id=session_id,
            parent_id=self._resolve_parent_id(parent),
            status=self._read_status(agent),
            meta=dict(meta or {}),
            scope=scope,
        )
        self._records[agent_id] = record
        self._by_agent[id(agent)] = agent_id
        if session_id:
            self._by_session.setdefault(session_id, []).append(agent_id)

        # 反向绑定：Agent 可从自身读取登记信息
        for attr, value in (
            ("agent_id", record.id),
            ("agent_kind", record.kind),
            ("agent_record", record),
            ("agent_scope", scope),
        ):
            try:
                setattr(agent, attr, value)
            except Exception:
                _logger.debug("绑定 agent 属性失败: %s", attr, exc_info=True)

        if owned:
            self.ctx.effect(lambda: (lambda: self.unregister(record)))

        self._notify(AgentEventType.CREATED, record)
        return record

    def unregister(self, record: Any) -> bool:
        """注销 Agent（关闭其作用域）。接受 record / agent / agent id。"""
        target = self._coerce_record(record)
        if target is None:
            return False
        self._dispose_record(target)
        return True

    def _dispose_record(self, record: AgentRecord) -> None:
        self._records.pop(record.id, None)
        self._by_agent.pop(id(record.agent), None)
        ids = self._by_session.get(record.session_id or "")
        if ids and record.id in ids:
            ids.remove(record.id)
            if not ids:
                self._by_session.pop(record.session_id, None)
        try:
            record.scope.close()
        except Exception:
            _logger.debug("关闭 agent 作用域失败: %s", record.id, exc_info=True)
        self._notify(AgentEventType.DESTROYED, record)

    # ── 查询 ─────────────────────────────────────────────

    def active(self) -> List[AgentRecord]:
        return list(self._records.values())

    def all(self) -> List[AgentRecord]:
        return self.active()

    def get(self, agent_id: str) -> Optional[AgentRecord]:
        return self._records.get(agent_id)

    def of(self, agent: Any) -> Optional[AgentRecord]:
        if agent is None:
            return None
        return self._records.get(self._by_agent.get(id(agent), ""))

    def of_session(self, session_id: str) -> List[AgentRecord]:
        return [self._records[i] for i in self._by_session.get(session_id, []) if i in self._records]

    def by_kind(self, kind: str) -> List[AgentRecord]:
        return [record for record in self._records.values() if record.kind == kind]

    def count(self) -> int:
        return len(self._records)

    def scope_of(self, agent: Any) -> Any:
        record = self.of(agent)
        return record.scope if record is not None else None

    # ── 状态 ─────────────────────────────────────────────

    def set_status(self, record: Any, status: str) -> None:
        target = self._coerce_record(record)
        if target is None:
            return
        target.touch(status)
        self._notify(AgentEventType.STATUS, target, status)

    def notify_stream(self, record: Any, phase: str, **payload: Any) -> None:
        target = self._coerce_record(record)
        if target is None:
            return
        self.ctx.notify(AgentEventType.ASSISTANT_STREAM, target, phase, **payload)

    def notify_continuation(self, record: Any, **payload: Any) -> None:
        target = self._coerce_record(record)
        if target is None:
            return
        self.ctx.notify(AgentEventType.CONTINUATION, target, **payload)

    def notify_inbox(self, record: Any, message: Any) -> None:
        target = self._coerce_record(record)
        if target is None:
            return
        self.ctx.notify(AgentEventType.INBOX, target, message)

    def notify_validation(self, record: Any, failure: str) -> None:
        target = self._coerce_record(record)
        if target is None:
            return
        self.ctx.notify(AgentEventType.VALIDATION, target, failure)

    # ── 内部 ─────────────────────────────────────────────

    def _resolve_parent_id(self, parent: Any) -> Optional[str]:
        """把 parent（AgentRecord / agent / id）解析为记录 id。"""
        if parent is None:
            return None
        record = self._coerce_record(parent)
        return record.id if record is not None else None

    def _coerce_record(self, value: Any) -> Optional[AgentRecord]:
        if isinstance(value, AgentRecord):
            return value if value.id in self._records else None
        if isinstance(value, str):
            return self._records.get(value)
        if value is None:
            return None
        agent_id = self._by_agent.get(id(value))
        return self._records.get(agent_id or "")

    @staticmethod
    def _read_status(agent: Any) -> str:
        state_machine = getattr(agent, "state_machine", None)
        name = getattr(state_machine, "name", None)
        return name.lower() if isinstance(name, str) and name else "idle"

    def _notify(self, event: str, *args: Any) -> None:
        try:
            self.ctx.notify(event, *args)
        except Exception:
            _logger.debug("agent 事件发布失败: %s", event, exc_info=True)


@plugin("agents", inject=["events"], provide=["agents"])
def apply(ctx):
    return AgentsService(ctx)
