"""会话插件 — 提供 ``ctx.sessions``。

封装 ChatSession 的创建、恢复、保存与列举，隐藏会话管理复杂度。
"""

from __future__ import annotations

from ..kernel import Service, plugin


class SessionService(Service):
    """会话服务 — 占据 ``ctx.sessions``。"""

    provide = "sessions"
    name = "sessions"
    inject = ("config", "events")

    def create(self, agent=None, model: str | None = None, sandbox=None, **kwargs):
        from ..core.session import ChatSession

        session = ChatSession(model=model, agent=agent, sandbox=sandbox, **kwargs)
        session.initialize()
        return session

    def load(self, session_id: str):
        from ..chat_msgs import load_session

        return load_session(session_id)

    def list(self):
        from ..chat_msgs import list_sessions

        return list_sessions()

    def save(self, session):
        return session.save()

    def recover_cmd(self, session_id: str):
        from ..chat_msgs import get_recover_cmd

        return get_recover_cmd(session_id)


@plugin("sessions", inject=["config", "events"], provide=["sessions"])
def apply(ctx):
    return SessionService(ctx)
