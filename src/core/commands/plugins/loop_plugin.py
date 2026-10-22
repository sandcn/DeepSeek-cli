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
- **流式生成中省略提词 = 接管当前轮**（2026-10-22 用户需求）：AI 正在
  流式输出时输入 ``/loop <批次数>``（不带提词），命令立即直连并接管当前
  正在生成的这一轮——当前轮直接计为第 1 批第 1 次运行（不重跑、不清空
  当前对话），本轮结束后生成续跑提词继续；``/loop`` 的初始提词取**当前
  会话中最早的一条人类用户消息**。空闲时省略提词仍报用法错误。
- TUI：模式行「✎ 提词」、循环浮窗显示批次数 / 当前批次 / 批内运行次数 /
  本批与本次运行时长；每批开始、批内每次运行、本批完成、全部完成均有聊天区
  提示。
- **聊天区不显示 LLM 原文**（2026-10-22 用户需求）：续跑提词请求的完整输入
  （system + user）与模型原始输出（原文 JSON）都不再上屏，只显示解析结果
  （``continue`` 判定、失败原因）与最终提词——原文整段刷屏价值低，提词在
  循环浮窗与「下次提词」提示中同样可见。
- **上下文不足自动全量压缩**（2026-10-23 用户需求）：续跑提词 JSON 新增
  ``compact`` 字段；模型分析输入数据后，若最后一次回答出现「上下文不够 /
  上下文不足 / token 不够」等借口，则返回 ``compact=true``。``/loop`` 一旦
  判定为 ``true``（无论 ``continue`` 真假）即调用
  ``ContextManager.check_and_compress(force=True)`` 执行一次**全量上下文
  压缩**（不保留近期尾部、系统提词不参与压缩），随后按原流程继续；压缩
  不可用 / 失败 / 无可压缩历史只提示并继续，**绝不终止循环**。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from .base import InteractiveCommandPlugin
from ..base import CommandMeta, declare_command_plugin
from ...loop_prompt import LOOP_PROMPT_MARKER, first_human_user_text

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


def parse_loop_takeover_command(text: str) -> int | None:
    """识别「流式生成中省略提词的 ``/loop <批次数>``」并返回批次数。

    只有**命令名 + 唯一一个正整数参数**才算接管请求；带提词（第二个 token
    起非空）、批次数缺失 / 非数字 / ``<=0`` 都返回 ``None``（走原有命令
    分发与用法提示）。该判定只在 AI 生成轮进行中由主循环使用。
    """
    parts = str(text or "").strip().split(maxsplit=1)
    if len(parts) != 2:
        return None
    if parts[0].lstrip("/").lower() != "loop":
        return None
    arg = parts[1].strip()
    if not arg or not arg.isdigit():
        return None
    try:
        count = int(arg)
    except (TypeError, ValueError):
        return None
    return count if count > 0 else None


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


def _write_loop_llm_output(
    chat_ui,
    *,
    round_index: int,
    decision,
    error: str,
) -> None:
    """显示本轮续跑提词 LLM 的**解析结果**（2026-10-22 用户需求）。

    ★ 聊天区不显示 LLM 原文：发给模型的完整输入（system + user）与模型原始
    输出（原文 JSON）都不再上屏，只显示 ``continue`` 判定、失败原因与最终
    提词——原文整段刷屏价值低，提词在循环浮窗（``↳`` 行）与「下次提词」提示
    中同样可见。
    """
    if chat_ui is None:
        return
    from ...constants import DIM, RESET, YELLOW

    _write_line(chat_ui, f"  {DIM}  ─ 第 {round_index} 次 LLM 输出 ─{RESET}")
    if decision is None:
        _write_line(
            chat_ui,
            f"  {YELLOW}解析: 失败（{error or '未生成输出'}）{RESET}",
        )
        return
    continue_text = "true" if decision.should_continue else "false"
    compact_text = "true" if bool(getattr(decision, "compact", False)) else "false"
    _write_line(
        chat_ui,
        f"  {DIM}解析:{RESET} continue={continue_text} compact={compact_text}",
    )
    if decision.prompt:
        _write_line(chat_ui, f"  {DIM}提词:{RESET}")
        _write_line(chat_ui, decision.prompt)
    else:
        _write_line(chat_ui, f"  {DIM}提词: （空）{RESET}")


async def _full_compact_context(chat_ui, session, decision) -> bool:
    """``compact=true`` 时执行一次全量上下文压缩（用户需求 2026-10-23）。

    - 入口：``ContextManager.check_and_compress(force=True)``——强制全量压缩
      （不保留近期尾部），系统提词不参与压缩（引擎只回放被遮蔽区域，系统
      提词原样保留在消息列表头部）；
    - 阻塞的摘要模型调用经 ``asyncio.to_thread`` 执行，不阻塞 TUI 事件循环；
    - 判定只看决策字段：``compact`` 缺失 / 非真时什么都不做（兼容旧模型输出）；
    - 任何失败（上下文管理器缺失 / 压缩被禁用 / 无可压缩历史 / 摘要异常）
      只提示并返回 ``False``，**绝不终止 /loop**。

    Returns:
        True 表示确实完成了一次全量压缩，False 表示未压缩（含被跳过 / 失败）。
    """
    if decision is None or not bool(getattr(decision, "compact", False)):
        return False

    from ...constants import DIM, GREEN, RESET, YELLOW

    _write_line(
        chat_ui,
        f"  {DIM}  ↺ 提词判定 compact=true：执行全量上下文压缩…{RESET}",
    )
    context_manager = (
        getattr(session, "context_manager", None) if session is not None else None
    )
    if context_manager is None:
        _write_line(
            chat_ui,
            f"  {YELLOW}  ↺ 全量上下文压缩不可用：上下文管理器未初始化"
            f"（继续循环）{RESET}",
        )
        return False

    try:
        result = await asyncio.to_thread(
            context_manager.check_and_compress, force=True,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - 压缩失败不得终止循环
        _logger.warning("/loop 全量上下文压缩失败", exc_info=True)
        _write_line(
            chat_ui,
            f"  {YELLOW}  ↺ 全量上下文压缩失败：{exc}（继续循环）{RESET}",
        )
        return False

    if result is None:
        _write_line(
            chat_ui,
            f"  {YELLOW}  ↺ 全量上下文压缩跳过：暂无可压缩的历史"
            f"或压缩未启用（继续循环）{RESET}",
        )
        return False
    if not bool(getattr(result, "success", False)):
        stats = getattr(result, "stats", None) or {}
        reason = stats.get("error") if isinstance(stats, dict) else ""
        _write_line(
            chat_ui,
            f"  {YELLOW}  ↺ 全量上下文压缩未完成："
            f"{reason or '摘要失败'}（继续循环）{RESET}",
        )
        return False

    _write_line(
        chat_ui,
        f"  {GREEN}  ↺ 全量上下文压缩完成："
        f"{int(getattr(result, 'shadowed_count', 0) or 0)} 条历史 → 检查点，"
        f"节省 ~{int(getattr(result, 'saved_tokens', 0) or 0)}t{RESET}",
    )
    return True


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
                "（不设次数上限），false 后批次数减一，0 结束；"
                "流式生成中省略提词可接管当前轮"
            ),
            group="session",
            usage="<批次数> <提词>（流式生成中可省略提词，接管当前轮）",
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
        from ...interrupt_state import is_interrupted_async, reset_interrupt_async
        from ...stats import reset_token_speed

        chat_ui = loop._chat_ui
        loop_state = loop._loop_state
        force_exit = loop._force_exit
        session = ctx.session
        service = getattr(session, "loop_prompt_service", None)

        # ★ 流式接管（2026-10-22 用户需求）：主循环在 AI 生成期间直连本命令时，
        #   会把接管请求放进 loop_state；此处消费它——当前流式轮已经结束，
        #   直接计为第 1 批第 1 次运行（不重跑、不清空当前对话）。
        takeover = loop_state.pop("_loop_takeover", None)
        adopted = takeover is not None
        if adopted:
            try:
                count = int(takeover.get("count") or 0)
            except (TypeError, ValueError):
                count = 0
            prompt = str(takeover.get("prompt") or "").strip()
            if not prompt:
                prompt = first_human_user_text(session.messages)
            if count <= 0 or not prompt:
                reason = (
                    "批次数无效" if count <= 0
                    else "当前会话没有可用于接管的人类用户消息"
                )
                _write_line(chat_ui, f"  {YELLOW}+ 取消接管：{reason}{RESET}")
                # 接管浮窗 / 模式由主循环在接收时打开，这里负责回收。
                loop_state["_loop_mode"] = False
                close_loop_window(chat_ui)
                if chat_ui is not None:
                    try:
                        chat_ui.bottom_bar.disable_status()
                        chat_ui.bottom_bar.reset_tool_count()
                    except Exception:
                        _logger.debug("取消接管时重置底部栏失败", exc_info=True)
                try:
                    reset_token_speed()
                except Exception:
                    _logger.debug("取消接管时重置 token 速度失败", exc_info=True)
                if service is not None:
                    try:
                        service.end_loop()
                    except Exception:
                        _logger.debug("取消接管时清理提词服务失败", exc_info=True)
                return True
        else:
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
                    f"  {DIM}  各批开始时清空对话并重新使用最初的提词；"
                    f"流式生成中可省略提词以接管当前轮{RESET}",
                )
                return True

        # ── 清理前一轮可能的强制退出标记 ──────────────────────
        force_exit.clear()
        # ── 自动保存循环前的对话 ────────────────────────────
        await _save_loop_snapshot(session, chat_ui)

        total = count
        open_loop_window(chat_ui, total, prompt, runs_per_round=1)

        if service is not None:
            try:
                service.begin_loop()
            except Exception:
                _logger.debug("初始化 /loop 提词服务失败", exc_info=True)

        try:
            loop_state["_loop_mode"] = True
            if chat_ui is not None:
                chat_ui.bottom_bar.enable_status()
            if adopted:
                _write_line(
                    chat_ui,
                    f"  {GREEN}+ 接管当前流式轮（{count} 批）: "
                    f"\"{_preview(prompt, 60)}\"{RESET}",
                )
                _write_line(
                    chat_ui,
                    f"  {DIM}  当前轮计为第 1 批第 1 次运行（不重跑）；"
                    f"本轮结束后生成续跑提词继续{RESET}",
                )
            else:
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
                current_prompt = prompt
                batch_run = 0
                last_round = None
                last_displayed_round = 0
                batch_completed = False
                stop_all = False
                adopted_batch = adopted and batch == 1

                if adopted_batch:
                    # ★ 接管批（2026-10-22 用户需求）：当前流式轮已经结束，
                    #   直接计为第 1 批第 1 次运行——不清空对话、不重复跑。
                    batch_run = 1
                    total_runs = 1
                    _write_line(
                        chat_ui,
                        f"  {DIM}  ─ 第 {batch}/{count} 批 · "
                        f"第 1 次运行（接管当前轮，不重跑）─{RESET}",
                    )
                    set_loop_window_status(
                        chat_ui,
                        total=count,
                        round=batch,
                        run=1,
                        runs_per_round=1,
                        prompt=prompt,
                        next_prompt="",
                        generating=False,
                        new_batch=True,
                    )
                    if service is None:
                        _write_line(
                            chat_ui,
                            f"  {YELLOW}+ 续跑提词服务不可用，结束整个循环"
                            f"（已完成 0 批 / 1 次运行）{RESET}",
                        )
                        stop_all = True
                    elif _has_pending_input(loop):
                        _write_line(
                            chat_ui,
                            f"  {YELLOW}+ 检测到用户输入，提前结束整个循环"
                            f"（已完成 0 批 / 1 次运行）{RESET}",
                        )
                        stop_all = True
                    else:
                        decision, error = await self._takeover_decision(service)
                        if decision is None:
                            if await is_interrupted_async():
                                _write_line(
                                    chat_ui,
                                    f"  {YELLOW}+ ESC 中断，提前结束整个循环"
                                    f"（已完成 0 批 / 1 次运行）{RESET}",
                                )
                            else:
                                _write_line(
                                    chat_ui,
                                    f"  {YELLOW}+ 提词生成失败：{error or '未知原因'}，"
                                    f"结束整个循环（已完成 0 批 / 1 次运行）{RESET}",
                                )
                            last_round = (1, None, error)
                            stop_all = True
                        else:
                            # ★ 判定 compact=true 即执行全量上下文压缩（用户需求
                            #   2026-10-23：无论 continue 真假，先压缩再按原流程走）。
                            await _full_compact_context(chat_ui, session, decision)
                            if not decision.should_continue:
                                last_round = (1, decision, "")
                                batch_completed = True
                            else:
                                _write_loop_llm_output(
                                    chat_ui,
                                    round_index=1,
                                    decision=decision,
                                    error="",
                                )
                                last_displayed_round = 1
                                service.take_result()
                                next_prompt = f"{LOOP_PROMPT_MARKER}{decision.prompt}"
                                set_loop_window_status(
                                    chat_ui,
                                    prompt=prompt,
                                    next_prompt=decision.prompt,
                                    generating=False,
                                )
                                _write_line(
                                    chat_ui,
                                    f"  {DIM}  ↳ 下次提词（第 2 次运行）: "
                                    f"{_preview(decision.prompt)}{RESET}",
                                )
                                current_prompt = next_prompt
                else:
                    # ★ 每批开始：清空对话并重新使用用户最初的提词。
                    session.clear_messages()
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
                        # ★ 新批锚点（2026-10-22 用户需求：浮窗显示当前批运行时间）
                        #   ——批内每次运行不再传 round，本批计时不会被重置。
                        new_batch=True,
                    )

                # ── 批内运行（接管批从第 2 次运行开始；普通批从第 1 次开始）──
                while not batch_completed and not stop_all:
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
                        # ★ 不再传 round：本批开始时已设好批次与本批计时锚点，
                        #   此处只更新批内运行序号（本次运行计时锚点）。
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
                    round_error = ""
                    if service is not None:
                        try:
                            round_decision = service.peek_result()
                            round_error = service.last_error
                        except Exception:
                            _logger.debug("读取 /loop 提词输出失败", exc_info=True)

                    if result.get("interrupted", False):
                        _write_line(
                            chat_ui,
                            f"  {YELLOW}+ ESC 中断，提前结束整个循环"
                            f"（已完成 {batch} 批 / {total_runs} 次运行）{RESET}",
                        )
                        last_round = (batch_run, round_decision, round_error)
                        stop_all = True
                        break

                    if service is None:
                        _write_line(
                            chat_ui,
                            f"  {YELLOW}+ 续跑提词服务不可用，结束整个循环"
                            f"（已完成 {batch} 批 / {total_runs} 次运行）{RESET}",
                        )
                        last_round = (batch_run, None, "续跑提词服务不可用")
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
                        last_round = (batch_run, None, error)
                        stop_all = True
                        break

                    # ★ 判定 compact=true 即执行全量上下文压缩（用户需求
                    #   2026-10-23：无论 continue 真假，先压缩再按原流程走）。
                    await _full_compact_context(chat_ui, session, decision)

                    if not decision.should_continue:
                        # 本批完成：输出在「本批完成」提示之后展示。
                        last_round = (batch_run, decision, error)
                        batch_completed = True
                        break

                    # 本批内继续：立即展示该轮 LLM 解析结果，再进入下一轮。
                    _write_loop_llm_output(
                        chat_ui,
                        round_index=batch_run,
                        decision=decision,
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

                # 本批结束：在提示之后展示最后一轮 LLM 解析结果（未即时显示过的）。
                if last_round is not None and last_round[0] > last_displayed_round:
                    round_index, decision, error = last_round
                    _write_loop_llm_output(
                        chat_ui,
                        round_index=round_index,
                        decision=decision,
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

    @staticmethod
    async def _takeover_decision(service) -> tuple[Any, str]:
        """为「接管当前流式轮」生成一次续跑提词。

        当前轮在 AI 生成期间结束时，``/loop`` 才拿到执行权（不重跑当前轮）。
        这里显式让 ``LoopPromptService`` 以 ``run_index=1`` 生成一次续跑
        决策——等价于普通批第一次运行结束时由 Agent 钩子生成，但使用当前轮
        **最终**消息状态，避免接管轮未 arm / 钩子已过导致空结果或陈旧结果。

        Returns:
            ``(decision, error)``；失败时 ``decision`` 为 ``None``。
        """
        try:
            service.set_run_index(1)
            service.arm()
        except Exception as exc:  # noqa: BLE001 - 辅助服务失败不阻断错误上报
            return None, f"初始化提词服务失败：{exc}"
        try:
            await service.on_agent_round_end()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            return None, f"生成续跑提词失败：{exc}"
        finally:
            try:
                service.disarm()
            except Exception:
                _logger.debug("disarm /loop 提词服务失败", exc_info=True)
        try:
            return service.peek_result(), service.last_error
        except Exception as exc:  # noqa: BLE001
            return None, f"读取提词结果失败：{exc}"

    def execute(self, ctx: Any) -> bool:
        """同步版本 — 抛出异常，防止误调用。"""
        raise RuntimeError("LoopPlugin 需要异步执行，请调用 async_execute()")


# 模块级自注册
declare_command_plugin(LoopPlugin())
