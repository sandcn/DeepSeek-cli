"""EditmsgPlugin — 编辑当前会话消息 (/editmsg, Ctrl+O)

暂停 ChatUIConsumer + 停止 EscapeMonitor，
让底部栏补全弹窗 + raw I/O 处理 ↑↓/Enter/Esc 交互，
选择完成后恢复两者。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from .base import InteractiveCommandPlugin
from ..base import CommandMeta, get_plugin_registry
from ....ui.colors import YELLOW, RESET

_logger = logging.getLogger(__name__)


class EditmsgPlugin(InteractiveCommandPlugin):
    """编辑当前会话消息 (Ctrl+O)

    暂停 ChatUIConsumer + 停止 EscapeMonitor，
    让底部栏补全弹窗 + raw I/O 处理 ↑↓/Enter/Esc 交互，
    选择完成后恢复两者。
    """

    def __init__(self):
        super().__init__()
        self.meta = CommandMeta(
            name="editmsg",
            description="编辑当前会话消息 (Ctrl+O)",
        )

    async def async_execute(self, ctx: Any) -> bool:
        """异步执行 /editmsg 命令

        复制 _handle_editmsg_cmd 的完整编排逻辑：
        suspend/stop → edit → resume/start 时序。
        """
        # 延迟导入避免模块加载时级联依赖
        from ....ui.msg_list import edit_current_messages as _edit_msgs
        from ....app_loop import _non_system_messages

        loop = self._loop
        if loop is None:
            _logger.error("EditmsgPlugin 未绑定 InteractiveLoop")
            return False

        chat_ui = loop._chat_ui
        monitor = loop._monitor
        session = ctx.session
        state = ctx.state  # dict: {"model": ..., "retry": ..., "prefill": ...}

        # ── 预检查：会话中是否有 user 消息可编辑 ──
        # 必须在 suspend/stop 之前检查，避免无编辑可做时仍进行不必要的终端模式切换。
        # 没有 user 消息时直接提示返回，不进入编辑交互。
        has_user_msg = any(
            m.get("role") == "user"
            for m in getattr(session, 'messages', []) or []
        )
        if not has_user_msg:
            if chat_ui is not None:
                chat_ui.write_line(
                    f"  {YELLOW}\u26a0{RESET} \u5f53\u524d\u4f1a\u8bdd\u65e0\u7528\u6237\u6d88\u606f\uff0c\u8bf7\u5148\u53d1\u9001\u6d88\u606f\u540e\u518d\u4f7f\u7528 /editmsg"
                )
            # ★ 返回 True：命令已被识别并处理（输出了提示信息），阻止调用方输出"未知命令"。
            #   其他插件（LoopPlugin/ModelPlugin）在参数校验失败路径也返回 True，
            #   editmsg 只有返回 True 才能避免 _handle_command_msg 的 else 分支误报。
            return True

        if chat_ui is not None:
            chat_ui.suspend()
        if monitor is not None:
            # ★ stop 在 try 外（非 finally）：必须在进入编辑交互前确认终端
            #   已恢复 cooked 模式（EditMsg Picker 需要 raw I/O 处理 ↑↓/Enter），
            #   若放入 finally，edit 过程中终端仍处于 cbreak 模式，Picker 将无法正常工作。
            #   start 在 finally 中确保编辑完成后始终恢复监听，无竞态风险。
            monitor.stop()
        needs_rerender = False
        try:
            edit_state = {"model": state.get("model", ""), "retry": False, "prefill": ""}
            await asyncio.to_thread(
                _edit_msgs, session.agent, edit_state,
            )
            state["prefill"] = edit_state.get("prefill", "")
            state["retry"] = edit_state.get("retry", False)
            state["model"] = edit_state.get("model", state.get("model", ""))
            session.sync_retry_pending()

            # ★ Bug 修复: Edit 语义是预填旧内容供用户编辑重发，不是自动续接。
            #   当有 prefill 且非主动 retry 时，重置 retry_pending = False，
            #   确保下一轮 _handle_round 走 prefill 路径（显示旧内容到编辑行），
            #   而不是 retry 路径（自动重新生成回复，绕过 prefill）。
            #   不做此重置时，若截断后最后一条消息角色是 user（如连续两条 user 消息），
            #   sync_retry_pending 会设 retry_pending=True，导致 prefill 被静默吞掉。
            if state["prefill"] and not state["retry"]:
                session.reset_retry_pending()

            # ★ 编辑生效（retry=True）后，标记需重新渲染剩余消息到上屏
            needs_rerender = bool(state["retry"] or state["prefill"])
        finally:
            if monitor is not None:
                monitor.start()
            if chat_ui is not None:
                chat_ui.resume()

        # ★ 编辑后反馈：编辑失败（未产生 prefill/retry）时给用户明确提示
        if not needs_rerender and chat_ui is not None:
            chat_ui.write_line(
                f"  {YELLOW}\u26a0{RESET} \u672a\u7f16\u8f91\u4efb\u4f55\u6d88\u606f\uff0c\u5df2\u53d6\u6d88"
            )

        # ★ 编辑生效后重新渲染剩余消息到上屏（scroll 区域内）
        # 通过 ChatUI 的 command queue 统一渲染，避免直接 stdout 写入
        # 与 render 线程（_drain_queue → force_redraw）的并发竞态。
        if needs_rerender and chat_ui is not None:
            non_system = _non_system_messages(session)
            chat_ui.display_messages(non_system, speed=0)

        return True

    def execute(self, ctx: Any) -> bool:
        """同步版本 — 抛出异常，防止误调用"""
        raise RuntimeError(
            "EditmsgPlugin 需要异步执行，请调用 async_execute()"
        )


# 模块级自注册
get_plugin_registry().register(EditmsgPlugin())
