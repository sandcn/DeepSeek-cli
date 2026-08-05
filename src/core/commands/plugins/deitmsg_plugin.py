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
        from ....core.sandbox_manager import get_sandbox_manager as _get_sandbox_manager
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
        restored_count = 0
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

            # ── 恢复沙盒 ──
            target_index = last_user_idx - 1 if last_user_idx > 0 else 0
            sandbox_manager = _get_sandbox_manager()
            if sandbox_manager:
                results = sandbox_manager.restore_to_message(target_index)
                if results:
                    restored_count = sum(
                        1 for success in results.values() if success
                    )

            # ── 截断消息 ──
            original_len = len(messages)
            del messages[last_user_idx:]

            # ── 同步沙盒索引 ──
            if sandbox_manager:
                sandbox_manager.remap_indices(
                    list(range(last_user_idx, original_len))
                )

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
            chat_ui.write_line(f"  {DIM}{'─' * 40}{RESET}")
            if restored_count > 0:
                chat_ui.write_line(
                    f"  {GREEN}\u2713{RESET} \u5df2\u8fd8\u539f {restored_count} \u4e2a\u6587\u4ef6\u6c99\u76d2"
                )
            else:
                chat_ui.write_line(
                    f"  {DIM}\u6c99\u76d2\u65e0\u6587\u4ef6\u9700\u8fd8\u539f{RESET}"
                )

            # 确保渲染命令在插件返回前排空
            try:
                chat_ui.flush()
            except Exception:
                _logger.warning(
                    "DeitmsgPlugin chat_ui.flush() post-finally 异常", exc_info=True
                )

        return True

    def execute(self, ctx: Any) -> bool:
        """同步版本 — 抛出异常，防止误调用"""
        raise RuntimeError(
            "DeitmsgPlugin 需要异步执行，请调用 async_execute()"
        )

# 模块级自注册
get_plugin_registry().register(DeitmsgPlugin())