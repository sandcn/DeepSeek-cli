"""SessionMessagingManager — 封装 ChatSession 消息管理职责

提取自 ChatSession 和 _session_messages.py，将消息添加/清空/撤销/压缩
集中在此管理器中，通过委托模式由 ChatSession 调用。

职责范围：
1. 消息添加（add_user_message / add_system_message）
2. 消息清空（clear_messages）
3. 消息撤销（undo_last_round）
4. 上下文压缩（compress / async_compress）
5. retry_pending 同步
"""

from __future__ import annotations

import logging

_logger = logging.getLogger(__name__)

_ROLE_KEY = "role"
_SYSTEM_ROLE = "system"

class SessionMessagingManager:
    """会话消息管理器

    封装 ChatSession 的消息管理逻辑，通过回调/引用访问依赖。
    """

    def __init__(
        self,
        messages,
        model_getter,
        context_manager_getter,
        context_manager_setter,
        sandbox_getter,
        state_machine,
        emit_fn,
        observability_port,
        retry_pending_getter,
        retry_pending_setter,
    ):
        """初始化消息管理器

        Args:
            messages: 消息列表（可变引用）
            model_getter: 返回当前模型名称的可调用对象
            context_manager_getter: 返回 ContextManager 的可调用对象
            context_manager_setter: 设置 ContextManager 的可调用对象
            sandbox_getter: 返回 SandboxManager 的可调用对象
            state_machine: SessionStateMachine 实例
            emit_fn: 发射事件的可调用对象
            observability_port: ObservabilityPort 实例
            retry_pending_getter: 返回 retry_pending 的可调用对象
            retry_pending_setter: 设置 retry_pending 的可调用对象
        """
        self._messages = messages
        self._get_model = model_getter
        self._get_ctx_mgr = context_manager_getter
        self._set_ctx_mgr = context_manager_setter
        self._get_sandbox = sandbox_getter
        self._state_machine = state_machine
        self._emit = emit_fn
        self._observability = observability_port
        self._get_retry_pending = retry_pending_getter
        self._set_retry_pending = retry_pending_setter

    # ── 消息添加 ────────────────────────────────────────

    def add_user_message(self, content: str) -> None:
        """追加用户消息。"""
        self._messages.append({_ROLE_KEY: "user", "content": content})

    def add_system_message(self, content: str) -> None:
        """追加系统消息。"""
        self._messages.append({_ROLE_KEY: _SYSTEM_ROLE, "content": content})
        self._emit("messages_changed", action="add_system")

    # ── 消息清空 ────────────────────────────────────────

    def clear_messages(self, system_messages_fn, build_system_prompt_fn) -> int:
        """清空对话（保留 system prompt）。

        若现有 system 消息与当前构建状态（完整/空模式）不一致——例如 Ctrl+B
        切换空模式后 agent system 消息未同步重建——则按当前状态重建标准
        system 消息，并保留额外 system 消息。

        Returns:
            被删除的消息数量
        """
        system_msgs = system_messages_fn()
        removed = len(self._messages) - len(system_msgs)

        # ★ 空模式同步：比较现有 system 首条与当前 build_system_prompt() 首条，
        #   不一致时按当前状态（完整/空模式）重建，防止清空后残留旧状态提词。
        if system_msgs:
            parts = build_system_prompt_fn()
            if parts:
                expected_head = parts[0][:60]
                existing_head = system_msgs[0].get("content", "")[:60]
                if expected_head != existing_head:
                    base = [{"role": "system", "content": p} for p in parts]
                    extra = system_msgs[len(parts):]
                    system_msgs = base + extra

        self._messages[:] = system_msgs

        if not self._messages:
            for part in build_system_prompt_fn():
                self._messages.append({"role": "system", "content": part})

        self._sync_retry_pending()

        sm = self._get_sandbox()
        if sm:
            sm.clear()

        self._emit("messages_changed", action="clear", removed=removed)
        self._observability.gauge("session.messages", 0)
        return removed

    # ── 消息撤销 ────────────────────────────────────────

    def undo_last_round(self) -> int:
        """撤销上一轮对话（移除末尾的 assistant + tool + user 消息）。

        Returns:
            移除的消息数量
        """
        removed = 0
        while self._messages and self._messages[-1].get(_ROLE_KEY) in ("assistant", "tool"):
            self._messages.pop()
            removed += 1
        if self._messages and self._messages[-1].get(_ROLE_KEY) == "user":
            self._messages.pop()
            removed += 1
        self._emit("messages_changed", action="undo", removed=removed)
        self._observability.gauge("session.messages", len(self._messages))
        return removed

    # ── retry_pending 同步 ──────────────────────────────

    def sync_retry_pending(self) -> None:
        """根据最后一条消息的角色同步 retry_pending 标志。"""
        self._set_retry_pending(
            len(self._messages) > 0
            and self._messages[-1].get(_ROLE_KEY) == "user"
        )

    # ── 内部方法 ────────────────────────────────────────

    def _sync_retry_pending(self) -> None:
        """内部调用的 retry_pending 同步（不触发 emit）。"""
        self._set_retry_pending(
            len(self._messages) > 0
            and self._messages[-1].get(_ROLE_KEY) == "user"
        )

    # ── 上下文压缩 ──────────────────────────────────────

    def get_non_system_messages(self) -> list[dict]:
        """获取非 system 消息列表。"""
        return [m for m in self._messages if m.get(_ROLE_KEY) != _SYSTEM_ROLE]

    def get_system_messages(self) -> list[dict]:
        """获取 system 消息列表。"""
        return [m for m in self._messages if m.get(_ROLE_KEY) == _SYSTEM_ROLE]