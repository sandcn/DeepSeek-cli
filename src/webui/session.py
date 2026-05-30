"""WEBChatSession — Web UI 专用会话子类

大量重用 ChatSession 的全部功能，通过继承复用以下核心能力：

  重用来源                    | 说明
  ----------------------------|-----------------------------------------------
  ChatSession.__init__        | 端口注入、Agent/状态机/Hook 系统/可观测性创建
  ChatSession.initialize      | SandboxManager + ContextManager 初始化
  ChatSession.run_round       | 异步对话执行（含并发锁、消息排队、状态机转换）
  ChatSession.retry           | 重试上一轮对话
  ChatSession._execute_round  | 对话执行公共逻辑（统计、自动保存、事件发射）
  ChatSession.save/load       | 会话持久化（JsonFilePersistence）
  ChatSession.checkpoint      | 断点保存/恢复（save/load/clear/resume）
  ChatSession.messages/model  | 核心属性访问器和修改器
  ChatSession.clear_messages  | 清空对话（保留 system prompt）
  ChatSession.undo_last_round | 撤销上一轮
  ChatSession.compress        | 上下文压缩
  ChatSession.on/off/_emit    | Hook 事件系统（解耦 UI 层）
  ChatSession.state_machine   | 状态机驱动生命周期
  ChatSession._metrics        | 可观测性指标收集

  WEBChatSession 在此基础上仅添加 Web UI 场景特有的便利方法，
  不覆盖父类任何方法，保持 100% 接口兼容。
"""

from __future__ import annotations

import logging
from typing import Any

from ..core.session import ChatSession

_logger = logging.getLogger(__name__)


class WEBChatSession(ChatSession):
    """Web UI 专用会话对象 — 继承 ChatSession 全部功能。

    大量重用 ChatSession 的核心能力：
    - 对话生命周期管理（run_round / retry / _execute_round）
    - 状态机形式化控制（SessionStateMachine）
    - 消息管理（add_user_message / clear_messages / undo_last_round / compress）
    - 会话持久化（save / load / list_sessions / checkpoint）
    - Hook 事件系统（on / off / _emit）
    - Agent + ContextManager 编排
    - 可观测性埋点（Metrics / Tracer）

    新增 Web UI 场景特有能力：
    - get_web_state()      — 将内部状态组装为前端所需字典
    - web_initialize()     — 调用父类 initialize() 后附加 Web UI 特有初始化
    - get_web_sessions()   — 复用父类 list_sessions() 添加当前会话标识
    - get_web_title()      — 读取持久化的会话标题

    使用方式:
        session = WEBChatSession()
        session.web_initialize()
        result = await session.run_round("你好")
    """

    def __init__(self, **kwargs):
        """重用 ChatSession.__init__ 的全部初始化逻辑：

        1. 端口注入（Persistence/Checkpoint/Config Port）
        2. Agent 创建（注入 NullPort 避免 UI 依赖）
        3. 状态机创建 + 回调注册
        4. Hook 系统初始化
        5. 并发锁、消息队列创建
        6. 可观测性（Metrics/Tracer）初始化
        """
        super().__init__(**kwargs)

    # ═══════════════════════════════════════════════════════
    # Web UI 初始化 — 复用 ChatSession.initialize()
    # ═══════════════════════════════════════════════════════

    def web_initialize(self, model: str | None = None,
                       loaded_messages: list[dict] | None = None) -> None:
        """Web UI 初始化：调用父类 initialize() + 注册 Web UI 特有 Hook。

        重用 ChatSession.initialize() 的全部逻辑：
        - SandboxManager 创建
        - ContextManager 创建及消息变更回调注册
        - 历史消息加载
        - 状态转换: INIT → IDLE
        - StateMachineMiddleware 注册
        - 可观测性仪表盘初始化

        Args:
            model: 模型名称，None 使用当前值
            loaded_messages: 历史消息列表（不含 system 消息）
        """
        super().initialize(model=model, loaded_messages=loaded_messages)

    # ═══════════════════════════════════════════════════════
    # Web UI 特有状态查询 — 组合父类属性/方法
    # ═══════════════════════════════════════════════════════

    def get_web_state(self) -> dict[str, Any]:
        """获取会话完整状态（供前端初始化/重连同步使用）。

        通过组合父类属性实现：
        - self.messages         → 消息列表
        - self.model            → 当前模型
        - self.session_id       → 会话 ID
        - self.state_name       → 当前状态名称
        - self.retry_pending    → 是否需要续接

        Returns:
            包含 messages/model/session_id/state/retry_pending 的字典
        """
        return {
            "messages": list(self.messages),
            "model": self.model,
            "session_id": self.session_id or "",
            "state": self.state_name,
            "retry_pending": self.retry_pending,
        }

    def get_web_sessions(self) -> list[dict]:
        """获取已保存的会话列表（标记当前会话）。

        重用 ChatSession.list_sessions() 获取全部会话元数据，
        在此基础上额外标记当前激活的会话。

        Returns:
            会话列表，每条包含 id/model/title/created 等字段，
            当前会话额外包含 "current": true
        """
        sessions = super().list_sessions()
        current_id = self.session_id or ""
        for s in sessions:
            s["current"] = (s.get("id", "") == current_id)
        return sessions

    def get_web_title(self) -> str:
        """获取当前会话的持久化标题。

        通过 ChatSession.session_id 读取已保存的会话文件中的标题。
        若无已保存 session_id 或标题不存在，返回空字符串。

        Returns:
            会话标题（已去前后空白），无可返回空字符串
        """
        if not self.session_id:
            return ""
        try:
            from ..chat_msgs import load_session as _load
            data = _load(self.session_id)
            if data:
                return (data.get("title", "") or "").strip()
        except Exception:
            _logger.debug("读取会话标题失败: session_id=%s", self.session_id)
        return ""

    # ═══════════════════════════════════════════════════════
    # 对话执行 — 100% 重用 ChatSession
    # ═══════════════════════════════════════════════════════

    # run_round()        — 完整继承，含并发锁/消息排队/状态机/异常恢复
    # retry()            — 完整继承，含状态机 retry 转换
    # _execute_round()   — 完整继承，含统计/自动保存/Hook 发射
    #
    # 以上异步方法完全来自 ChatSession，WEBChatSession 不覆盖。
    # 若未来需要添加 Web UI 特有行为（如自动推送状态到前端），
    # 可通过 ChatSession 的 Hook 系统（self.on/self._emit）扩展，
    # 无需覆盖父类方法。

    # ═══════════════════════════════════════════════════════
    # 消息管理 — 100% 重用 ChatSession
    # ═══════════════════════════════════════════════════════

    # add_user_message()   — 委托给 Agent
    # clear_messages()     — 保留 system prompt 清空其余
    # undo_last_round()    — 移除末尾 assistant+tool+user
    # add_system_message() — 追加 system 消息
    # compress()           — 上下文压缩
    #
    # 以上方法全部来自 ChatSession，直接可用。
    # 消息变更时自动触发 "messages_changed" Hook。

    # ═══════════════════════════════════════════════════════
    # 会话持久化 — 100% 重用 ChatSession
    # ═══════════════════════════════════════════════════════

    # save()        — JsonFilePersistence.save_session
    # load()        — JsonFilePersistence.load_session + 消息替换
    # list_sessions — JsonFilePersistence.list_sessions
    # save_checkpoint / load_checkpoint / clear_checkpoint
    #              — JsonFileCheckpoint 断点管理
    # resume_from_checkpoint / has_checkpoint
    #              — 断点恢复逻辑

    # ═══════════════════════════════════════════════════════
    # 属性访问 — 100% 重用 ChatSession
    # ═══════════════════════════════════════════════════════

    # messages          — list[dict] 当前消息列表
    # model             — str 当前模型名（读写）
    # session_id        — str|None 当前会话 ID（读写）
    # agent             — Agent 实例
    # context_manager   — ContextManager|None
    # retry_pending     — bool 是否需要续接
    # pending_messages  — list[str] 排队的消息
    # state_machine     — SessionStateMachine
    # state_name        — str 当前状态名称

    # ═══════════════════════════════════════════════════════
    # Hook 系统 — 100% 重用 ChatSession
    # ═══════════════════════════════════════════════════════

    # on(event, callback)   — 注册事件回调
    # off(event, callback)  — 移除事件回调
    # _emit(event, **data)  — 触发事件
    #
    # 支持事件:
    #   round_start / round_end / cost_update
    #   saved / loaded / messages_changed
    #   checkpoint_saved / checkpoint_cleared


__all__ = ["WEBChatSession"]
