"""LoopPlugin — LLM 续跑提词驱动的循环执行 (/loop)

行为（2026-10-22 用户需求：``N`` 语义改为**批次数**）：

- ``/loop <N> <提词>``：``N`` 为**批次数**，必须显式给出正整数；省略 ``N``
  或 ``N<=0`` 报错（不再有「省略 = 无限」语义）。
- **每批内**只要 LLM 返回 ``continue=true`` 就继续执行（**不设运行次数上限**），
  每次都使用上一轮生成的续跑提词；``continue=false`` 表示本批完成。
- 本批完成后批次数 ``N-1``；``N==0`` 结束整个 ``/loop``；否则开始下一批。
- **每批开始时** ``clear_messages()`` 并重新使用用户最初的提词（批内后续
  运行使用 LLM 生成的续跑提词）。
- 生成时机在 ``Agent.run()`` 内、等待后台 bash/subagent **之前**（见
  ``LoopPromptService``）；生成失败重试一次，仍失败则结束整个 loop。
- 用户输入 / ESC 中断立即终止整个 ``/loop``（不继续剩余批次）。
- TUI：模式行「✎ 提词」、循环浮窗显示批次数 / 当前批次 / 批内运行次数；
  每批开始、批内每次运行、本批完成、全部完成均有聊天区提示。
"""

from __future__ import annotations

import logging
from typing import Any

from .base import InteractiveCommandPlugin
from ..base import CommandMeta, declare_command_plugin
from ...loop_prompt import LOOP_PROMPT_MARKER

_logger = logging.getLogger(__name__)


def parse_loop_args(arg: str) -> tuple[int, str]:
    """解析 ``/loop`` 参数，返回 ``(count, prompt)``。

    - ``count`` 为**批次数**，必须显式给出**正整数**；无效时返回 ``0``
      （由调用方输出用法提示，不再有「省略 = 无限」语义）；
    - 仅当第一个 token 是数字**且**存在第二个 token 时才算批次数——纯数字
      提词（如 ``/loop 3``）没有提词，按无效处理；
    - ``N<=0`` 或缺少提词均返回 ``0``，不再视为无限轮。
    """
    text = str(arg or "").strip()
    if not text:
        return 0, ""
    parts = text.split(maxsplit=1)
    if len(parts) < 2 or not parts[0].isdigit():
        return 0, text
    count = int(parts[0])
    prompt = parts[1].strip()
    if count <= 0 or not prompt:
        return 0, prompt
    return count, prompt


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


def _write_loop_llm_input(chat_ui, *, round_index: int, request) -> None:
    """显示本轮续跑提词 LLM 的**完整输入**（system + user，全量不截断）。

    输入即实际发送给模型的 ``build_loop_prompt_messages`` 结果：第 1 条为系统
    提词，第 2 条为用户消息 + 最后一次回答的 JSON。按 ``\\n`` 原样逐行交给
    ``write_line``（TUI 会拆行渲染），不做任何长度限制或省略。
    """
    if chat_ui is None:
        return
    from ...constants import DIM, RESET

    _write_line(chat_ui, f"  {DIM}  ─ 第 {round_index} 次 LLM 输入 ─{RESET}")
    messages = list(request or ())
    if not messages:
        _write_line(chat_ui, f"  {DIM}（本轮未生成输入）{RESET}")
        return
    total = len(messages)
    for index, message in enumerate(messages, start=1):
        role = str(message.get("role", "") or "?")
        content = str(message.get("content", "") or "")
        _write_line(
            chat_ui,
            f"  {DIM}[{index}/{total}] {role} · {len(content)} 字符{RESET}",
        )
        for line in content.split("\n"):
            _write_line(chat_ui, line)


def _write_loop_llm_output(
    chat_ui,
    *,
    round_index: int,
    decision,
    raw: str,
    error: str,
) -> None:
    """显示本轮续跑提词 LLM 的原始输出与解析结果（2026-10-22 用户需求）。

    先原文、后解析结果；解析失败时显示原始输出与失败原因。原始输出按
    ``\\n`` 原样交给 ``write_line``（TUI 会拆行渲染），不做截断。
    """
    if chat_ui is None:
        return
    from ...constants import DIM, RESET, YELLOW

    _write_line(chat_ui, f"  {DIM}  ─ 第 {round_index} 次 LLM 输出 ─{RESET}")
    text = str(raw or "")
    if text:
        _write_line(chat_ui, f"  {DIM}原文:{RESET}")
        _write_line(chat_ui, text)
    if decision is None:
        _write_line(
            chat_ui,
            f"  {YELLOW}解析: 失败（{error or '未生成输出'}）{RESET}",
        )
        return
    continue_text = "true" if decision.should_continue else "false"
    _write_line(chat_ui, f"  {DIM}解析:{RESET} continue={continue_text}")
    if decision.prompt:
        _write_line(chat_ui, f"  {DIM}提词:{RESET}")
        _write_line(chat_ui, decision.prompt)
    else:
        _write_line(chat_ui, f"  {DIM}提词: （空）{RESET}")


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


class LoopPlugin(InteractiveCommandPlugin):
    """``/loop``：按批次执行用户提词，批内由 LLM 续跑提词驱动无限运行。"""

    def __init__(self):
        super().__init__()
        self.meta = CommandMeta(
            name="loop",
            description=(
                "按批次循环执行提词：每批内 LLM 返回 continue=true 就继续"
                "（不设次数上限），false 后批次数减一，0 结束"
            ),
            group="session",
            usage="<批次数> <提词>",
            queue_only=True,
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
        if count <= 0 or not prompt:
            _write_line(chat_ui, f"  {YELLOW}用法: /loop <批次数> <提词>{RESET}")
            _write_line(
                chat_ui,
                f"  {DIM}  批次数必须是正整数；每批内 continue=true 无限续跑，"
                f"false 后批次数-1，0 结束（用户输入 / ESC 可随时终止）{RESET}",
            )
            _write_line(
                chat_ui,
                f"  {DIM}  各批开始时清空对话并重新使用最初的提词{RESET}",
            )
            return True

        # ── 清理前一轮可能的强制退出标记 ──────────────────────
        force_exit.clear()
        # ── 自动保存循环前的对话 ────────────────────────────
        await _save_loop_snapshot(session, chat_ui)

        total = count
        open_loop_window(chat_ui, total, prompt, runs_per_round=1)

        service = getattr(session, "loop_prompt_service", None)
        if service is not None:
            try:
                service.begin_loop()
            except Exception:
                _logger.debug("初始化 /loop 提词服务失败", exc_info=True)

        try:
            loop_state["_loop_mode"] = True
            if chat_ui is not None:
                chat_ui.bottom_bar.enable_status()
            _write_line(
                chat_ui,
                f"  {GREEN}+ 开始循环（{count} 批）: "
                f"\"{_preview(prompt, 60)}\"{RESET}",
            )

            remaining = count
            batch = 0
            total_runs = 0
            while remaining > 0:
                # 批间用户输入：立即终止整个 /loop（不进入下一批）。
                if total_runs > 0 and _has_pending_input(loop):
                    _write_line(
                        chat_ui,
                        f"  {YELLOW}+ 检测到用户输入，提前结束整个循环"
                        f"（已完成 {batch} 批 / {total_runs} 次运行）{RESET}",
                    )
                    break

                batch += 1
                remaining_after = remaining - 1
                # ★ 每批开始：清空对话并重新使用用户最初的提词。
                session.clear_messages()
                current_prompt = prompt
                batch_run = 0
                _write_line(
                    chat_ui,
                    f"  {GREEN}+ 第 {batch}/{count} 批开始"
                    f"（剩余 {remaining} 批）: \"{_preview(prompt, 60)}\"{RESET}",
                )
                set_loop_window_status(
                    chat_ui,
                    total=count,
                    round=batch,
                    run=0,
                    runs_per_round=1,
                    prompt=prompt,
                    next_prompt="",
                    generating=False,
                )

                last_round = None
                last_displayed_round = 0
                batch_completed = False
                stop_all = False
                while True:
                    # 批内用户输入：同样立即终止整个 /loop。
                    if total_runs > 0 and _has_pending_input(loop):
                        _write_line(
                            chat_ui,
                            f"  {YELLOW}+ 检测到用户输入，提前结束整个循环"
                            f"（已完成 {batch - 1} 批 / {total_runs} 次运行）{RESET}",
                        )
                        stop_all = True
                        break

                    batch_run += 1
                    total_runs += 1
                    _write_line(
                        chat_ui,
                        f"  {DIM}  ─ 第 {batch}/{count} 批 · "
                        f"第 {batch_run} 次运行 ─{RESET}",
                    )
                    set_loop_window_status(
                        chat_ui,
                        total=count,
                        round=batch,
                        run=batch_run,
                        runs_per_round=1,
                        prompt=current_prompt,
                        next_prompt="",
                        generating=False,
                    )
                    reset_interrupt_async()
                    if service is not None:
                        try:
                            service.set_run_index(total_runs)
                            service.arm()
                        except Exception:
                            _logger.debug("arm /loop 提词服务失败", exc_info=True)
                    result = await session.run_round(current_prompt)
                    if service is not None:
                        try:
                            service.disarm()
                        except Exception:
                            _logger.debug("disarm /loop 提词服务失败", exc_info=True)

                    round_decision = None
                    round_raw = ""
                    round_error = ""
                    round_request: list = []
                    if service is not None:
                        try:
                            round_decision = service.peek_result()
                            round_raw = service.last_raw
                            round_error = service.last_error
                            round_request = service.last_request
                        except Exception:
                            _logger.debug("读取 /loop 提词输出失败", exc_info=True)
                    if round_request:
                        _write_loop_llm_input(
                            chat_ui,
                            round_index=batch_run,
                            request=round_request,
                        )

                    if result.get("interrupted", False):
                        _write_line(
                            chat_ui,
                            f"  {YELLOW}+ ESC 中断，提前结束整个循环"
                            f"（已完成 {batch} 批 / {total_runs} 次运行）{RESET}",
                        )
                        last_round = (
                            batch_run, round_decision, round_raw, round_error,
                        )
                        stop_all = True
                        break

                    if service is None:
                        _write_line(
                            chat_ui,
                            f"  {YELLOW}+ 续跑提词服务不可用，结束整个循环"
                            f"（已完成 {batch} 批 / {total_runs} 次运行）{RESET}",
                        )
                        last_round = (batch_run, None, "", "续跑提词服务不可用")
                        stop_all = True
                        break

                    decision = round_decision
                    error = round_error
                    if decision is None:
                        _write_line(
                            chat_ui,
                            f"  {YELLOW}+ 提词生成失败：{error or '未知原因'}，"
                            f"结束整个循环（已完成 {batch} 批 / "
                            f"{total_runs} 次运行）{RESET}",
                        )
                        last_round = (batch_run, None, round_raw, error)
                        stop_all = True
                        break
                    if not decision.should_continue:
                        # 本批完成：输出在「本批完成」提示之后展示。
                        last_round = (batch_run, decision, round_raw, error)
                        batch_completed = True
                        break

                    # 本批内继续：立即展示该轮 LLM 输出，再进入下一轮。
                    _write_loop_llm_output(
                        chat_ui,
                        round_index=batch_run,
                        decision=decision,
                        raw=round_raw or decision.raw,
                        error="",
                    )
                    last_displayed_round = batch_run
                    # 保持 take_result 的原有消费语义（peek 不改变结果状态）。
                    service.take_result()

                    next_prompt = f"{LOOP_PROMPT_MARKER}{decision.prompt}"
                    set_loop_window_status(
                        chat_ui,
                        prompt=current_prompt,
                        next_prompt=decision.prompt,
                        generating=False,
                    )
                    _write_line(
                        chat_ui,
                        f"  {DIM}  ↳ 下次提词（第 {batch_run + 1} 次运行）: "
                        f"{_preview(decision.prompt)}{RESET}",
                    )
                    current_prompt = next_prompt

                if batch_completed:
                    _write_line(
                        chat_ui,
                        f"  {GREEN}+ 第 {batch}/{count} 批完成"
                        f"（本批运行 {batch_run} 次），剩余 {remaining_after} 批{RESET}",
                    )
                    if remaining_after == 0:
                        _write_line(
                            chat_ui,
                            f"  {GREEN}+ 全部 {count} 批执行完毕{RESET}",
                        )

                # 本批结束：在提示之后展示最后一轮 LLM 输出（未即时显示过的）。
                if last_round is not None and last_round[0] > last_displayed_round:
                    round_index, decision, raw, error = last_round
                    _write_loop_llm_output(
                        chat_ui,
                        round_index=round_index,
                        decision=decision,
                        raw=raw,
                        error=error,
                    )

                if stop_all or not batch_completed:
                    break

                remaining -= 1
                if remaining == 0:
                    break
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
