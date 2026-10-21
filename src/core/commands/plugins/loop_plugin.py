"""LoopPlugin — LLM 续跑提词驱动的循环执行 (/loop)

行为（2026-10-21 用户需求）：

- ``/loop [N] <提词>``：``N`` 为**总运行次数**（含首次用户提词）；省略 ``N``
  或 ``N=0`` 表示无限，直到 LLM 判断无需继续、用户输入或 ESC。
- 整次 loop 只在开始时 ``clear_messages()`` 一次；首次运行用户提词，之后每次
  运行使用上一轮末尾由辅助 LLM 生成的提词（生成与使用不同步）。
- 生成时机在 ``Agent.run()`` 内、等待后台 bash/subagent **之前**（见
  ``LoopPromptService``）；生成失败重试一次，仍失败则结束 loop。
- ``continue=false`` 时结束本次 loop；等待期间检测到用户输入时提前结束，
  避免无限循环饿死用户消息。
- TUI：模式行「✎ 提词」、循环浮窗显示生成状态与下一轮提词；开始/每次运行/
  继续/停止均有聊天区提示。
"""

from __future__ import annotations

import logging
from typing import Any

from .base import InteractiveCommandPlugin
from ..base import CommandMeta, declare_command_plugin
from ...loop_prompt import LOOP_PROMPT_MARKER

_logger = logging.getLogger(__name__)


def parse_loop_args(arg: str) -> tuple[int | None, str]:
    """解析 ``/loop`` 参数，返回 ``(count, prompt)``。

    - ``count`` 为 ``None`` 表示无限轮；
    - 仅当第一个 token 是数字**且**存在第二个 token 时才把首 token 当次数，
      因此纯数字提词（如 ``/loop 3``）按无限轮 + 提词 ``"3"`` 处理；
    - ``N<=0`` 视为无限。
    """
    text = str(arg or "").strip()
    if not text:
        return None, ""
    parts = text.split(maxsplit=1)
    if len(parts) == 2 and parts[0].isdigit():
        count = int(parts[0])
        return (count if count > 0 else None), parts[1].strip()
    return None, text


def _preview(text: str, limit: int = 80) -> str:
    """单行预览（折叠换行 + 截断）。"""
    one_line = " ".join(str(text or "").split())
    return one_line if len(one_line) <= limit else one_line[: limit - 1] + "…"


def _write_line(chat_ui, text: str) -> None:
    if chat_ui is None:
        return
    try:
        chat_ui.write_line(text)
    except Exception:
        _logger.debug("/loop 输出失败", exc_info=True)


def _has_pending_input(loop) -> bool:
    """循环期间是否有待处理用户输入（消息队列 / 输入框 / 已排出的 queued）。

    无限轮必须以用户输入为逃生口：一旦检测到输入就结束 loop，让外层主循环
    取走并处理该条消息。
    """
    state = getattr(loop, "_loop_state", None)
    if isinstance(state, dict):
        if state.get("queued_input"):
            return True
        probe = state.get("_input_probe")
        if callable(probe):
            try:
                if probe():
                    return True
            except Exception:
                _logger.debug("/loop 用户输入探测失败", exc_info=True)
    chat_ui = getattr(loop, "_chat_ui", None)
    input_ = getattr(chat_ui, "input", None) if chat_ui is not None else None
    has_queued = getattr(input_, "has_queued_input", None)
    if callable(has_queued):
        try:
            return bool(has_queued())
        except Exception:
            _logger.debug("/loop 输入框排队探测失败", exc_info=True)
    return False


def _loop_total(count: int | None) -> int:
    """浮窗/展示用的总次数（0 = 无限）。"""
    return 0 if count is None else max(0, int(count))


class LoopPlugin(InteractiveCommandPlugin):
    """``/loop``：一次用户提词 + LLM 生成的续跑提词，直到完成或达到次数上限。"""

    def __init__(self):
        super().__init__()
        self.meta = CommandMeta(
            name="loop",
            description=(
                "循环执行提词（首次用用户提词，之后由 LLM 根据未完成项生成续跑提词；"
                "省略次数=无限）"
            ),
            group="session",
            usage="[次数] <提词>",
        )

    async def async_execute(self, ctx: Any) -> bool:
        loop = self._loop
        if loop is None:
            _logger.error("LoopPlugin 未绑定 InteractiveLoop")
            return False

        # 延迟导入避免模块加载时级联依赖（app_loop → plugins → app_loop 循环导入）
        from ...constants import DIM, RESET, GREEN, YELLOW
        from ....app_loop import _save_loop_snapshot
        from ....app_loop._float_window import (
            close_loop_window,
            open_loop_window,
            set_loop_window_status,
        )
        from ...interrupt_state import reset_interrupt_async
        from ...stats import reset_token_speed

        chat_ui = loop._chat_ui
        loop_state = loop._loop_state
        force_exit = loop._force_exit
        session = ctx.session

        count, prompt = parse_loop_args(ctx.arg)
        if not prompt:
            _write_line(chat_ui, f"  {YELLOW}用法: /loop [次数] <提词>{RESET}")
            _write_line(
                chat_ui,
                f"  {DIM}  次数省略或为 0 表示无限（LLM 判断完成 / 用户输入 / ESC 结束）{RESET}",
            )
            return True

        # ── 清理前一轮可能的强制退出标记 ──────────────────────
        force_exit.clear()
        # ── 自动保存循环前的对话 ────────────────────────────
        await _save_loop_snapshot(session, chat_ui)

        total = _loop_total(count)
        open_loop_window(chat_ui, total, prompt, runs_per_round=1)

        service = getattr(session, "loop_prompt_service", None)
        if service is not None:
            try:
                service.begin_loop()
            except Exception:
                _logger.debug("初始化 /loop 提词服务失败", exc_info=True)

        executed = 0
        try:
            loop_state["_loop_mode"] = True
            if chat_ui is not None:
                chat_ui.bottom_bar.enable_status()
            count_text = "无限" if count is None else str(count)
            _write_line(
                chat_ui,
                f"  {GREEN}+ 开始循环（{count_text}）: "
                f"\"{_preview(prompt, 60)}\"{RESET}",
            )
            # ★ 整次 loop 只清空一次：后续运行在同一上下文连续推进。
            session.clear_messages()

            current_prompt = prompt
            while count is None or executed < count:
                # 用户输入优先：无限轮必须能被用户打断/插话，不能被饿死。
                if executed > 0 and _has_pending_input(loop):
                    _write_line(
                        chat_ui,
                        f"  {YELLOW}+ 检测到用户输入，提前结束循环"
                        f"（已执行 {executed} 次）{RESET}",
                    )
                    break

                executed += 1
                label = (
                    f"第 {executed} 次"
                    if count is None
                    else f"第 {executed}/{count} 次"
                )
                _write_line(chat_ui, f"  {DIM}  ─ {label} ─{RESET}")
                set_loop_window_status(
                    chat_ui,
                    total=total,
                    round=executed,
                    run=0,
                    runs_per_round=1,
                    prompt=current_prompt,
                    next_prompt="",
                    generating=False,
                )
                reset_interrupt_async()
                if service is not None:
                    try:
                        service.set_run_index(executed)
                        service.arm()
                    except Exception:
                        _logger.debug("arm /loop 提词服务失败", exc_info=True)
                result = await session.run_round(current_prompt)
                if service is not None:
                    try:
                        service.disarm()
                    except Exception:
                        _logger.debug("disarm /loop 提词服务失败", exc_info=True)

                if result.get("interrupted", False):
                    _write_line(
                        chat_ui,
                        f"  {YELLOW}+ ESC 中断，提前结束循环"
                        f"（已执行 {executed} 次）{RESET}",
                    )
                    break

                # 已达到用户指定次数：本次 run 的末尾提词生成结果不会被使用，
                # 直接回到 while 条件正常收尾（不误报「生成失败 / LLM 判定停止」）。
                if count is not None and executed >= count:
                    continue

                if service is None:
                    _write_line(
                        chat_ui,
                        f"  {YELLOW}+ 续跑提词服务不可用，结束循环"
                        f"（已执行 {executed} 次）{RESET}",
                    )
                    break

                decision = service.take_result()
                error = service.last_error
                if decision is None:
                    _write_line(
                        chat_ui,
                        f"  {YELLOW}+ 提词生成失败：{error or '未知原因'}，"
                        f"结束循环（已执行 {executed} 次）{RESET}",
                    )
                    break
                if not decision.should_continue:
                    _write_line(
                        chat_ui,
                        f"  {GREEN}+ LLM 判断无需继续，结束循环"
                        f"（已执行 {executed} 次）{RESET}",
                    )
                    break

                next_prompt = f"{LOOP_PROMPT_MARKER}{decision.prompt}"
                set_loop_window_status(
                    chat_ui,
                    prompt=current_prompt,
                    next_prompt=decision.prompt,
                    generating=False,
                )
                _write_line(
                    chat_ui,
                    f"  {DIM}  ↳ 下次提词（第 {executed + 1} 次）: "
                    f"{_preview(decision.prompt)}{RESET}",
                )
                current_prompt = next_prompt
            else:
                _write_line(
                    chat_ui,
                    f"  {GREEN}+ 循环 {count} 次执行完毕{RESET}",
                )
        finally:
            set_loop_window_status(chat_ui, generating=False, next_prompt="")
            close_loop_window(chat_ui)
            loop_state["_loop_mode"] = False
            if chat_ui is not None:
                chat_ui.bottom_bar.disable_status()
                chat_ui.bottom_bar.reset_tool_count()
            reset_token_speed()
            if service is not None:
                try:
                    service.end_loop()
                except Exception:
                    _logger.debug("清理 /loop 提词服务失败", exc_info=True)
        # ── 自动保存循环后的对话 ────────────────────────────
        await _save_loop_snapshot(session, chat_ui)
        return True

    def execute(self, ctx: Any) -> bool:
        """同步版本 — 抛出异常，防止误调用。"""
        raise RuntimeError("LoopPlugin 需要异步执行，请调用 async_execute()")


# 模块级自注册
declare_command_plugin(LoopPlugin())
