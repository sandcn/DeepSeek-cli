"""SessionPersistenceManager — 封装 ChatSession 持久化职责

提取自 ChatSession 和 _session_persistence.py，将保存/加载/断点管理
集中在此管理器中，通过委托模式由 ChatSession 调用。

职责范围：
1. 会话保存（save_session / auto_save）
2. 会话加载（load_session_data）
3. 断点管理（save/load/clear/resume checkpoint）
4. 会话列表
"""

from __future__ import annotations

import asyncio
import logging

_logger = logging.getLogger(__name__)

_ROLE_KEY = "role"
_SYSTEM_ROLE = "system"
_MIN_CHECKPOINT_MESSAGES = 2
_TASK_DESC_TRUNCATE = 200

class SessionPersistenceManager:
    """会话持久化管理器

    封装 ChatSession 的持久化逻辑，通过端口接口访问底层存储。
    不直接访问 ChatSession 的私有属性，通过公共方法/属性操作。
    合规：通过 self._persistence 访问持久化（端口模式）
    """

    def __init__(
        self,
        messages_getter,
        model_getter,
        model_setter,
        session_id_getter,
        session_id_setter,
        persistence_port,
        checkpoint_port,
        state_machine,
        emit_fn,
        observability_port,
        subagents_getter=None,
        subagents_setter=None,
    ):
        """初始化持久化管理器

        Args:
            messages_getter: 返回消息列表的可调用对象
            model_getter: 返回当前模型名称的可调用对象
            model_setter: 设置模型名称的可调用对象
            session_id_getter: 返回当前 session_id 的可调用对象
            session_id_setter: 设置 session_id 的可调用对象
            persistence_port: PersistencePort 实例
            checkpoint_port: CheckpointPort 实例
            state_machine: SessionStateMachine 实例
            emit_fn: 发射事件的可调用对象
            observability_port: ObservabilityPort 实例
            subagents_getter: 返回 SubAgent 记录列表的可调用对象（可选，保存时使用）
            subagents_setter: 设置 SubAgent 记录列表的可调用对象（可选，加载时恢复）
        """
        self._get_messages = messages_getter
        self._get_model = model_getter
        self._set_model = model_setter
        self._get_session_id = session_id_getter
        self._set_session_id = session_id_setter
        self._persistence = persistence_port
        self._checkpoint = checkpoint_port
        self._state_machine = state_machine
        self._emit = emit_fn
        self._observability = observability_port
        self._get_subagents = subagents_getter
        self._set_subagents = subagents_setter

    # ── 会话保存 ────────────────────────────────────────

    def save(self) -> str | None:
        """保存当前会话到 .chat/msg_list/。

        Returns:
            session_id，无可保存内容时返回 None
        """
        messages = self._get_messages()
        non_system = [m for m in messages if m.get(_ROLE_KEY) != _SYSTEM_ROLE]
        if not non_system:
            self._safe_save_state()
            return self._get_session_id()

        subagents = self._get_subagents() if self._get_subagents is not None else None
        sid = self._persistence.save_session(
            messages=non_system,
            model=self._get_model(),
            session_id=self._get_session_id(),
            subagents=subagents,
        )
        self._set_session_id(sid)
        self._safe_save_state()
        self._emit("saved", session_id=sid)
        return sid

    def load(self, session_id: str) -> dict | None:
        """加载历史会话。

        Args:
            session_id: 会话 ID（可带或不带 .json 后缀）

        Returns:
            会话数据字典，不存在时返回 None
        """
        data = self._persistence.load_session(session_id)
        if data is None:
            return None

        loaded_msgs = data.get("messages", [])
        if not loaded_msgs:
            return None

        messages = self._get_messages()
        system_msgs = [m for m in messages if m.get(_ROLE_KEY) == _SYSTEM_ROLE]
        messages[:] = system_msgs
        for msg in loaded_msgs:
            messages.append(msg)

        # 恢复 SubAgent 记录（含完整聊天信息，供 /export 导出）
        if self._set_subagents is not None:
            self._set_subagents(data.get("subagents") or [])

        # ★ 2026-08-17（用户需求：load 命令支持已完成 subagent 轨迹）：恢复
        #   轨迹存档（--load 启动 / webui 加载路径）——主轨迹显示历史 subagent
        #   记录、Enter 可进入查看完整轨迹（数据源与构建逻辑复用运行时同一
        #   套，无第二份实现；非 TUI 环境零成本跳过）。
        self._restore_trace_archive(data.get("subagents") or [])

        self._set_model(data.get("model", self._get_model()))
        self._set_session_id(session_id)

        self._observability.gauge("session.messages", len(messages))
        self._emit("loaded", data=data)
        return data

    def list_sessions(self) -> list[dict]:
        """列出所有保存的会话。"""
        return self._persistence.list_sessions()

    def get_session_ids(self) -> list[str]:
        """列出所有保存的会话 ID。"""
        return [s["id"] for s in self._persistence.list_sessions()]

    def _restore_trace_archive(self, records: list) -> None:
        """恢复 subagent 轨迹存档（load 路径统一接入点）。

        委托 TUI 控制器（``SubAgentPanelController.restore_trace_archive``）
        ——非 TUI 环境（无头/单次模式）控制器单例仍可用但无人消费，异常时
        记 debug 日志零成本跳过（轨迹恢复属增强能力，不阻断会话加载）。
        """
        try:
            from ....tui.subagent import SubAgentPanelController
            SubAgentPanelController.get_default().restore_trace_archive(records)
        except Exception:
            _logger.debug("恢复 subagent 轨迹存档异常", exc_info=True)

    # ── 自动保存（异步） ────────────────────────────────

    async def auto_save(self, messages_getter) -> str | None:
        """自动保存会话（异步，在子线程中做文件 IO）。

        Args:
            messages_getter: 返回消息快照的可调用对象

        Returns:
            session_id，无可保存内容时返回 None
        """
        try:
            snapshot = list(messages_getter())
            snapshot_model = self._get_model()
            snapshot_sid = self._get_session_id()
            subagents = self._get_subagents() if self._get_subagents is not None else None

            non_system = [m for m in snapshot if m.get(_ROLE_KEY) != _SYSTEM_ROLE]
            if not non_system:
                self._safe_save_state()
                return snapshot_sid

            session_id = await asyncio.to_thread(
                self._persistence.save_session,
                non_system,
                snapshot_model,
                snapshot_sid,
                subagents,
            )
            self._set_session_id(session_id)
            self._safe_save_state()
            self._emit("saved", session_id=session_id)
            return session_id
        except Exception as exc:
            _logger.exception("自动保存会话失败: %s", exc)
            self._safe_save_state()
            return None

    # ── 断点管理 ────────────────────────────────────────

    def save_checkpoint(self) -> None:
        """保存断点（任务中断时调用）。"""
        self._checkpoint.save(self._get_messages(), self._get_model())
        self._emit("checkpoint_saved")

    def clear_checkpoint(self) -> None:
        """清除断点（任务成功完成时调用）。"""
        self._checkpoint.clear()
        self._emit("checkpoint_cleared")

    def load_checkpoint(self) -> dict | None:
        """加载断点数据。"""
        return self._checkpoint.load()

    def has_checkpoint(self) -> bool:
        """检查是否存在有效断点。"""
        return self._checkpoint.exists()

    def resume_from_checkpoint(self, messages, model, set_model_fn) -> bool:
        """从断点恢复任务。

        Args:
            messages: 消息列表（可变引用，会被替换）
            model: 当前模型名称
            set_model_fn: 设置模型名称的可调用对象

        Returns:
            是否成功恢复
        """
        info = self._checkpoint.get_info()
        if not info:
            return False

        data = self._checkpoint.load()
        if not data:
            return False

        checkpoint_msgs = data.get("messages", [])
        if len(checkpoint_msgs) < _MIN_CHECKPOINT_MESSAGES:
            self._checkpoint.clear()
            self._emit("checkpoint_cleared")
            return False

        system_msgs = [m for m in messages if m.get(_ROLE_KEY) == _SYSTEM_ROLE]
        messages[:] = system_msgs
        for msg in checkpoint_msgs:
            if msg.get(_ROLE_KEY) == _SYSTEM_ROLE:
                continue
            messages.append(dict(msg))

        resume_prompt = (
            f"[检查点恢复] 你的任务在之前被中断了。\n"
            f"中断时的任务描述: {info['task_description'][:_TASK_DESC_TRUNCATE]}\n"
            f"请先回顾以上对话历史，了解当前进度和已完成的工作，\n"
            f"然后继续完成剩余任务。不要重复已完成的步骤。"
        )
        messages.append({_ROLE_KEY: _SYSTEM_ROLE, "content": resume_prompt})

        checkpoint_model = data.get("model", model)
        if checkpoint_model:
            set_model_fn(checkpoint_model)

        self._checkpoint.clear()
        self._emit("checkpoint_cleared")

        self._observability.gauge("session.messages", len(messages))
        self._emit("checkpoint_restored", info=info)
        return True

    # ── 状态机辅助 ──────────────────────────────────────

    def _safe_save_state(self) -> None:
        """安全执行状态机 save 转换（忽略无效转换）。"""
        from ...state_machine import InvalidTransitionError

        if hasattr(self._state_machine, 'can') and self._state_machine.can("save"):
            try:
                self._state_machine.save()
            except InvalidTransitionError:
                _logger.debug(
                    "状态机 save 转换无效（当前状态: %s）",
                    self._state_machine.name,
                )