"""LoopPlugin — 循环执行 N 次指定提词 (/loop)

复制 _handle_loop_cmd 的完整编排逻辑，包括：
- _save_loop_snapshot 前后快照
- _loop_mode 设置/清理
- for 循环中的 session.run_round 两次调用
- 中断处理和 finally 清理
"""

from __future__ import annotations

import logging
from typing import Any

from .base import InteractiveCommandPlugin
from ..base import CommandMeta, get_plugin_registry

_logger = logging.getLogger(__name__)

class LoopPlugin(InteractiveCommandPlugin):
    """循环执行 N 次指定提词 (/loop)

    每轮第一次使用用户提词，第二次使用固定提词「继续完成所有」。
    包含完整的 _loop_mode 管理、中断处理、快照保存逻辑。
    """

    def __init__(self):
        super().__init__()
        self.meta = CommandMeta(
            name="loop",
            description="循环执行 N 次指定提词（每轮第1次用用户提词，第2次用固定提词）",
        )

    async def async_execute(self, ctx: Any) -> bool:
        """异步执行 /loop 命令

        复制 _handle_loop_cmd 的完整编排逻辑。
        """
        loop = self._loop
        if loop is None:
            _logger.error("LoopPlugin 未绑定 InteractiveLoop")
            return False

        # 延迟导入避免模块加载时级联依赖（app_loop → plugins → app_loop 循环导入）
        from ...constants import DIM, RESET, GREEN, YELLOW
        from ....app_loop import _save_loop_snapshot
        from ....api.interrupt_async import reset_interrupt_async
        from ....api.stats import reset_token_speed

        chat_ui = loop._chat_ui
        loop_state = loop._loop_state
        force_exit = loop._force_exit
        session = ctx.session

        # 解析参数
        parts = ctx.arg.split(maxsplit=1) if ctx.arg else []
        if len(parts) < 2 or not parts[0].isdigit() or int(parts[0]) < 1:
            chat_ui.write_line(f"  {YELLOW}用法: /loop <次数> <提词>{RESET}")
            return True
        count = int(parts[0])
        prompt = parts[1].strip()
        if not prompt:
            chat_ui.write_line(f"  {YELLOW}用法: /loop <次数> <提词>{RESET}")
            return True

        # ── 清理前一轮可能的强制退出标记 ──────────────────────
        force_exit.clear()
        # ── 自动保存循环前的对话 ────────────────────────────
        await _save_loop_snapshot(session, chat_ui)
        # ── /loop 模式：启用状态行持续活跃 + 跨轮累加耗时 ────
        try:
            # _loop_mode + enable_status + write_line 全部在 try 内，
            # 确保 finally 始终清理 _loop_mode，防止状态泄漏
            loop_state["_loop_mode"] = True
            if chat_ui is not None:
                chat_ui.bottom_bar.enable_status()
            chat_ui.write_line(
                f"  {GREEN}+ 开始循环 {count} 次: \"{prompt[:60]}"
                f"{'...' if len(prompt) > 60 else ''}\"{RESET}"
            )
            for i in range(count):
                chat_ui.write_line(f"  {DIM}  ─ 第 {i+1}/{count} 轮 · 第1次 ─{RESET}")
                # 清空对话（每轮开始前清空）
                reset_interrupt_async()
                session.clear_messages()
                # 第1次运行
                result = await session.run_round(prompt)
                if result.get("interrupted", False):
                    chat_ui.write_line(
                        f"  {YELLOW}+ ESC 中断，提前结束循环"
                        f"（已执行 {i+1}/{count} 轮）{RESET}"
                    )
                    break

                # 第2次运行（固定提词"继续完成所有"）
                chat_ui.write_line(f"  {DIM}  ─ 第 {i+1}/{count} 轮 · 第2次 ─{RESET}")
                reset_interrupt_async()
                result2 = await session.run_round("继续完成所有")
                if result2.get("interrupted", False):
                    chat_ui.write_line(
                        f"  {YELLOW}+ ESC 中断，提前结束循环"
                        f"（已执行 {i+1}/{count} 轮）{RESET}"
                    )
                    break
            else:
                chat_ui.write_line(f"  {GREEN}+ 循环 {count} 次执行完毕{RESET}")
        finally:
            # ── /loop 结束：清理 _loop_mode 标志 + 重置状态 ────
            loop_state["_loop_mode"] = False
            if chat_ui is not None:
                chat_ui.bottom_bar.disable_status()
                chat_ui.bottom_bar.reset_tool_count()
            reset_token_speed()
        # ── 自动保存循环后的对话 ────────────────────────────
        await _save_loop_snapshot(session, chat_ui)

        return True

    def execute(self, ctx: Any) -> bool:
        """同步版本 — 抛出异常，防止误调用"""
        raise RuntimeError(
            "LoopPlugin 需要异步执行，请调用 async_execute()"
        )

# 模块级自注册
get_plugin_registry().register(LoopPlugin())