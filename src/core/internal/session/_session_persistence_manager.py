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
import contextlib
import hashlib
import json
import logging
import threading

_logger = logging.getLogger(__name__)

_ROLE_KEY = "role"
_SYSTEM_ROLE = "system"
_MIN_CHECKPOINT_MESSAGES = 2
_TASK_DESC_TRUNCATE = 200

#: 定时自动保存间隔（秒）。对话内容变化后最多等待该时长即异步落盘一次。
_AUTOSAVE_INTERVAL_SECONDS = 30.0
#: 无会话日志源时用于变更检测的消息摘要长度（字节）。
_MESSAGE_DIGEST_SIZE = 16


def _messages_digest(messages: list[dict]) -> str:
    """计算消息内容的稳定摘要（仅无会话日志源时的回退路径）。"""
    try:
        payload = json.dumps(
            messages,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
            separators=(",", ":"),
        )
    except (TypeError, ValueError):
        payload = repr(messages)
    return hashlib.blake2b(
        payload.encode("utf-8", "replace"), digest_size=_MESSAGE_DIGEST_SIZE,
    ).hexdigest()


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
        log_getter=None,
        log_setter=None,
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
        self._get_log = log_getter
        self._set_log = log_setter

        # ── 定时自动保存（30s，仅内容变化时异步落盘） ──────
        self._autosave_task: asyncio.Task | None = None
        self._autosave_flush_lock: asyncio.Lock | None = None
        #: 上一次成功保存时的内容签名（None 表示尚未建立基线）
        self._last_saved_signature: tuple | None = None
        #: 同步保护 save() 与后台线程写入，避免两个调用同时写同一文件
        self._save_lock = threading.RLock()

    # ── 会话保存 ────────────────────────────────────────

    def save(self) -> str | None:
        """保存当前会话到 .chat/msg_list/（含会话日志快照）。

        Returns:
            session_id，无可保存内容时返回 None
        """
        signature = self._content_signature()
        non_system = self._non_system_snapshot()
        if not non_system:
            self._last_saved_signature = signature
            self._safe_save_state()
            return self._get_session_id()

        subagents = self._subagents_snapshot()
        sid = self._save_payload(
            non_system, self._get_model(), self._get_session_id(), subagents,
            self.session_log_snapshot(),
        )
        self._set_session_id(sid)
        self._last_saved_signature = signature
        self._safe_save_state()
        self._emit("saved", session_id=sid)
        return sid

    # ── 保存快照辅助 ────────────────────────────────────

    def _non_system_snapshot(self) -> list[dict]:
        """返回非 system 消息快照（按当前消息列表顺序）。"""
        return [
            m for m in list(self._get_messages())
            if m.get(_ROLE_KEY) != _SYSTEM_ROLE
        ]

    def _subagents_snapshot(self) -> list:
        """返回 SubAgent 记录快照（无来源时为空列表）。"""
        if self._get_subagents is None:
            return []
        try:
            return list(self._get_subagents() or [])
        except Exception:
            _logger.debug("获取 SubAgent 记录失败", exc_info=True)
            return []

    def session_log_snapshot(self) -> list[dict] | None:
        """返回当前会话日志的事件快照（无日志源时 None）。

        会话日志是仅追加的事实源；快照随定时自动保存一起落盘，加载时据此
        重建消息视图。无日志视图（自定义 Agent/旧会话）时返回 None，保存端
        不写入 ``session_log`` 字段（保持向后兼容）。
        """
        if self._get_log is None:
            return None
        try:
            log = self._get_log()
            if log is None:
                return None
            return log.snapshot()
        except Exception:
            _logger.debug("获取会话日志快照失败，本次不写入日志", exc_info=True)
            return None

    def _save_payload(self, messages: list[dict], model: str,
                      session_id: str | None, subagents: list,
                      session_log: list[dict] | None) -> str:
        """写入会话文件（线程安全；含会话日志快照）。"""
        with self._save_lock:
            return self._persistence.save_session(
                messages=messages,
                model=model,
                session_id=session_id,
                subagents=subagents,
                # 会话日志 = 仅追加事实源：定时自动保存 / 退出保存一并落盘，
                # 加载时优先由日志重建消息视图。
                session_log=session_log,
            )

    def _content_signature(self) -> tuple | None:
        """计算对话内容变更签名（用于判定是否需要落盘）。

        优先使用会话日志的事件数（O(1)，覆盖追加/插入/替换/删除等全部
        结构变更）；无日志源时回退到消息数量 + 内容摘要。
        """
        try:
            messages = list(self._get_messages())
        except Exception:
            return None

        log = None
        if self._get_log is not None:
            try:
                log = self._get_log()
            except Exception:
                log = None
        if log is not None:
            try:
                return ("log", id(log), len(log), len(messages))
            except Exception:
                _logger.debug("读取会话日志签名失败，回退消息摘要", exc_info=True)
        return ("messages", len(messages), _messages_digest(messages))

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
        log_events = data.get("session_log") or []
        if not loaded_msgs and not log_events:
            return None

        # ── 会话日志即唯一事实源：优先用日志快照重建消息视图 ──
        restored = False
        if log_events and self._set_log is not None:
            try:
                from ...session_log import SessionLog

                self._set_log(SessionLog.restore(log_events, session_id=session_id))
                restored = True
            except Exception:
                _logger.debug("会话日志恢复失败，回退消息列表", exc_info=True)

        # ★ 消息视图（log 恢复路径经 _set_log → _rebind_log 原地重绑同一列表对象，
        #   故此处统一取一次引用即对两种恢复路径均有效）——修复前该赋值仅存在于
        #   「非 log 恢复」分支，log 恢复（restored=True，当前会话文件的常规路径）
        #   时下方 gauge 引用未绑定变量 → UnboundLocalError，导致 --load 启动
        #   直接崩溃、退出时无法打印恢复命令行。
        messages = self._get_messages()
        if not restored:
            system_msgs = [m for m in messages if m.get(_ROLE_KEY) == _SYSTEM_ROLE]
            messages[:] = system_msgs
            for msg in loaded_msgs:
                messages.append(msg)

        # 恢复 SubAgent 记录（含完整聊天信息，供 /export 导出）
        if self._set_subagents is not None:
            self._set_subagents(data.get("subagents") or [])

        # ★ 2026-08-17（用户需求：load 命令支持已完成 subagent 轨迹）：恢复
        #   轨迹存档（--load 启动路径）——主轨迹显示历史 subagent
        #   记录、Enter 可进入查看完整轨迹（数据源与构建逻辑复用运行时同一
        #   套，无第二份实现；非 TUI 环境零成本跳过）。
        self._restore_trace_archive(data.get("subagents") or [])

        self._set_model(data.get("model", self._get_model()))
        self._set_session_id(session_id)
        # 加载即视为已保存基线：内容未再变化时不会触发定时自动保存。
        self._last_saved_signature = self._content_signature()

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
            from ...adapters.ui_runtime import get_subagent_panel_controller
            get_subagent_panel_controller().get_default().restore_trace_archive(records)
        except Exception:
            _logger.debug("恢复 subagent 轨迹存档异常", exc_info=True)

    # ── 定时自动保存（30s，异步） ───────────────────────

    def start_autosave(self) -> None:
        """启动周期自动保存任务（幂等；需在事件循环中调用）。

        任务每 ``_AUTOSAVE_INTERVAL_SECONDS`` 秒检查一次对话内容签名，
        仅当内容相对上次成功保存发生变化时才在后台线程落盘。
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = self._autosave_task
        if task is not None and not task.done():
            return
        self._autosave_task = loop.create_task(
            self._autosave_loop(), name="session-autosave",
        )

    def stop_autosave(self) -> None:
        """请求停止周期自动保存任务（同步版本，用于退出路径）。"""
        task = self._autosave_task
        self._autosave_task = None
        if task is not None and not task.done():
            task.cancel()

    async def stop_autosave_async(self) -> None:
        """停止并等待周期自动保存任务退出（测试 / 异步清理路径）。"""
        task = self._autosave_task
        self._autosave_task = None
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def _autosave_loop(self) -> None:
        """周期自动保存循环（每 30s 检查一次内容是否变化）。"""
        current = asyncio.current_task()
        try:
            while True:
                await asyncio.sleep(_AUTOSAVE_INTERVAL_SECONDS)
                try:
                    await self.auto_save()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    _logger.exception("定时自动保存会话失败: %s", exc)
        except asyncio.CancelledError:
            raise
        finally:
            if self._autosave_task is current:
                self._autosave_task = None

    def _flush_lock(self) -> asyncio.Lock:
        """惰性创建异步保存锁（避免在无事件循环的构造期绑定）。"""
        if self._autosave_flush_lock is None:
            self._autosave_flush_lock = asyncio.Lock()
        return self._autosave_flush_lock

    async def auto_save(self) -> str | None:
        """按需异步保存会话（仅当对话内容相对上次保存发生变化）。

        文件 IO 在 ``asyncio.to_thread`` 后台线程执行，不阻塞事件循环；
        保存内容包含对话消息与会话日志快照。

        Returns:
            session_id；内容未变化时返回 None。
        """
        try:
            signature = self._content_signature()
            if signature == self._last_saved_signature:
                return None
            async with self._flush_lock():
                signature = self._content_signature()
                if signature == self._last_saved_signature:
                    return None
                return await self._save_if_changed(signature)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _logger.exception("自动保存会话失败: %s", exc)
            self._safe_save_state()
            return None

    async def _save_if_changed(self, signature: tuple | None) -> str | None:
        """执行一次后台异步保存并更新内容基线。"""
        snapshot = list(self._get_messages())
        snapshot_model = self._get_model()
        snapshot_sid = self._get_session_id()
        non_system = [
            m for m in snapshot if m.get(_ROLE_KEY) != _SYSTEM_ROLE
        ]
        if not non_system:
            self._last_saved_signature = signature
            self._safe_save_state()
            return snapshot_sid

        subagents = self._subagents_snapshot()
        # 会话日志快照在事件循环线程内取（仅复制事件列表）；后台线程只做
        # JSON 序列化与文件写入，避免跨线程读取仍可能被追加的日志。
        session_log = self.session_log_snapshot()
        session_id = await asyncio.to_thread(
            self._save_payload,
            non_system,
            snapshot_model,
            snapshot_sid,
            subagents,
            session_log,
        )
        self._set_session_id(session_id)
        # 仅在成功落盘后推进基线；保存期间若内容继续变化，下个周期会再次保存。
        self._last_saved_signature = signature
        self._safe_save_state()
        self._emit("saved", session_id=session_id)
        return session_id

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