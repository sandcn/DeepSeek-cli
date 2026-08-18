"""DeitmsgPlugin — 直接编辑上一条消息 (/deitmsg)

/deitmsg 是 /editmsg 的快捷版本：不经过交互式消息选择弹窗，
直接定位到最后一条用户消息，恢复沙盒文件到该消息之前的状态，
截断消息并预填旧内容供用户重新编辑。

同时在上屏显示本次沙盒还原了多少个文件。
"""

from __future__ import annotations

import logging
from typing import Any

from .base import InteractiveCommandPlugin
from ..base import CommandMeta, get_plugin_registry

_logger = logging.getLogger(__name__)

def _content_str(content: Any) -> str:
    """将 content（可能是 str 或 list[dict]）转换为纯文本字符串。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for c in content:
            if isinstance(c, dict):
                parts.append(str(c.get("text", c)))
            else:
                parts.append(str(c))
        return " ".join(parts)
    return str(content)

class DeitmsgPlugin(InteractiveCommandPlugin):
    """直接编辑上一条用户消息 (/deitmsg)

    与 /editmsg 的区别：
    - 不经过交互式消息选择弹窗
    - 直接定位到最后一条用户消息
    - 恢复沙盒文件后，在上屏显示还原了多少个文件
    """

    def __init__(self):
        super().__init__()
        self.meta = CommandMeta(
            name="deitmsg",
            description="直接编辑上一条消息（快捷版 /editmsg）",
        )

    async def async_execute(self, ctx: Any) -> bool:
        """异步执行 /deitmsg 命令

        直接定位到最后一条 user 消息，恢复沙盒、截断消息、预填旧内容。
        """
        from ....core.constants import YELLOW, RESET, GREEN, DIM
        from ....app_loop import _non_system_messages
        from ....api.interrupt_async import flush_stdin, reset_interrupt_async

        loop = self._loop
        if loop is None:
            _logger.error("DeitmsgPlugin 未绑定 InteractiveLoop")
            return False

        chat_ui = loop._chat_ui
        monitor = loop._monitor
        session = ctx.session
        state = ctx.state  # dict: {"model": ..., "retry": ..., "prefill": ...}

        # ── 预检查：会话中是否有 user 消息可编辑 ──
        messages = getattr(session, 'messages', None) or []
        if not any(m.get("role") == "user" for m in messages):
            if chat_ui is not None:
                chat_ui.write_line(
                    f"  {YELLOW}\u26a0{RESET} \u5f53\u524d\u4f1a\u8bdd\u65e0\u7528\u6237\u6d88\u606f\uff0c\u8bf7\u5148\u53d1\u9001\u6d88\u606f\u540e\u518d\u4f7f\u7528 /deitmsg"
                )
            return True

        needs_rerender = False
        restore_text = ""
        try:
            # Layer 2 防御：排空 stdin 残余字节
            flush_stdin(input_instance=chat_ui._input if chat_ui else None)

            # ── 定位最后一条 user 消息 ──
            last_user_idx = -1
            for i in range(len(messages) - 1, -1, -1):
                if messages[i].get("role") == "user":
                    last_user_idx = i
                    break

            if last_user_idx < 0:
                if chat_ui is not None:
                    chat_ui.write_line(
                        f"  {YELLOW}\u26a0{RESET} \u672a\u627e\u5230\u7528\u6237\u6d88\u606f"
                    )
                return True

            old_content = _content_str(messages[last_user_idx].get("content", ""))

            # ── 恢复沙盒 + 截断 + remap（统一公共助手） ──
            # ★ P1-1 修复（先 remap 后删）：修复前本插件内联「restore → del
            #   messages → remap_indices」顺序——remap 抛异常时消息已删且
            #   prefill 未设置（old_content 只存于局部变量，用户内容永久
            #   丢失），沙盒记录与消息索引不一致且无补偿；界面也不重渲染
            #   （needs_rerender=False）残留已删消息。editmsg 侧同逻辑已在
            #   _truncate_messages（P2-7）修复为「先 remap 后删」，本插件
            #   未同步。现复用同一助手：remap 失败时异常在消息删除**前**
            #   抛出（无中间态），被 except 捕获显示「编辑失败」。
            from ....tui.pipeline.message_editor import _truncate_messages
            restore_text = _truncate_messages(session.agent, last_user_idx)

            # ── 设置 prefill ──
            state["prefill"] = old_content
            state["retry"] = False
            session.sync_retry_pending()

            # ── Edit 语义：预填旧内容供用户编辑重发，不是自动续接 ──
            session.reset_retry_pending_for_edit(has_prefill=bool(state["prefill"]))

            # ── 标记需重新渲染 ──
            needs_rerender = True

        except Exception as exc:
            _logger.warning("DeitmsgPlugin 编辑异常: %s", exc, exc_info=True)
            if chat_ui is not None:
                chat_ui.write_line(
                    f"  {YELLOW}\u26a0{RESET} \u7f16\u8f91\u5931\u8d25: {exc}"
                )
            needs_rerender = False
        finally:
            if monitor is not None:
                try:
                    session.captured_prefill = ''
                    reset_interrupt_async(
                        input_instance=chat_ui._input if chat_ui else None
                    )
                    monitor.clear_interrupted()
                except Exception:
                    _logger.warning(
                        "DeitmsgPlugin finally 块清理异常", exc_info=True
                    )
            if chat_ui is not None:
                try:
                    chat_ui.flush()
                except Exception:
                    _logger.warning(
                        "DeitmsgPlugin chat_ui.flush() finally 异常", exc_info=True
                    )

        # ── 显示沙盒还原信息并重新渲染 ──
        #    与 /editmsg 同语义：先清空消息区旧显示，再重新渲染剩余消息一次。
        if needs_rerender and chat_ui is not None:
            # 1. 先清空消息区旧显示（删除被编辑消息及其后内容的旧渲染）
            try:
                chat_ui.clear_messages()
            except Exception as exc:
                _logger.warning(
                    "DeitmsgPlugin clear_messages 异常: %s", exc
                )
            # 2. 重新渲染截断后的剩余消息（一次，不追加残留副本）
            try:
                non_system = _non_system_messages(session)
                chat_ui.display_messages(non_system, speed=0)
            except Exception as exc:
                _logger.warning(
                    "DeitmsgPlugin display_messages 异常: %s", exc
                )
            # 3. 视觉分隔线 + 沙盒还原信息（在 display_messages 之后，避免被消息渲染滚动覆盖）
            # ★ P2-3 修复：恢复失败以 ⚠ 渲染（与 editmsg 统一经
            #   _restore_feedback 判定），不再无条件绿色 ✓。
            chat_ui.write_line(f"  {DIM}{'─' * 40}{RESET}")
            from ....tui.pipeline.message_editor import _restore_feedback
            feedback_text, restore_failed = _restore_feedback(restore_text)
            if restore_failed:
                chat_ui.write_line(f"  {YELLOW}\u26a0{RESET} {feedback_text}")
            else:
                chat_ui.write_line(f"  {GREEN}\u2713{RESET} {feedback_text}")

            # 确保渲染命令在插件返回前排空
            try:
                chat_ui.flush()
            except Exception:
                _logger.warning(
                    "DeitmsgPlugin chat_ui.flush() post-finally 异常", exc_info=True
                )

        return True

    def execute(self, ctx: Any) -> bool:
        """同步版本 — 旧命令系统路径友好降级（不抛异常）。

        ★ P2-2 附带修复：registry 自动注册同步 handler——同步路径
        （handle_command）触发本方法。修复前直接 raise RuntimeError 使
        调用方崩溃；现输出提示并返回 True。
        """
        try:
            from ....core.adapters.output import get_default_output_port
            get_default_output_port().write(
                f"  {YELLOW}\u26a0{RESET} /deitmsg \u9700\u8981\u4ea4\u4e92\u5f0f TUI \u73af\u5883\uff0c\u8bf7\u5728 TUI \u4e2d\u4f7f\u7528"
            )
        except Exception:
            _logger.debug("deitmsg_plugin: 同步降级提示输出异常", exc_info=True)
        return True

# 模块级自注册
get_plugin_registry().register(DeitmsgPlugin())