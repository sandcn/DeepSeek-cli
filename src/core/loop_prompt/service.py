"""``/loop`` 续跑提词服务 —— 每次运行末尾同步生成、下一次运行处消费。

设计约束（用户需求 2026-10-21）：

- **生成时机**：``Agent.run()`` 里，一轮 pipeline 完成后、等待后台 bash/subagent
  之前调用 :meth:`LoopPromptService.on_agent_round_end`；必须 ``await``（不能
  fire-and-forget），否则下一轮可能拿不到结果。
- **生成与使用不同步**：结果暂存于本服务，由 ``/loop`` 编排在下一轮运行处经
  :meth:`take_result` 取用——「原来第二轮」的位置。
- **只服务 /loop**：``arm()`` / ``disarm()`` 由编排控制；普通对话不产生辅助请求。
- **不污染统计**：辅助请求以内部 label + ``silent=True`` 发起，不渲染、不计入
  ``main · N%`` 上下文占用。
- **失败不阻断主链路**：异常 / 超时 / JSON 非法只记录 ``last_error``，由编排按
  用户选择的策略处理（重试一次后结束 loop）。
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any, Callable, Sequence

from .provider import (
    LoopPromptDecision,
    build_loop_prompt_messages,
    human_user_texts,
    last_assistant_text,
    parse_loop_prompt_response,
)

_logger = logging.getLogger(__name__)

#: 默认请求超时（毫秒）。
_DEFAULT_TIMEOUT_MS = 120_000
#: 默认输入字节预算。
_DEFAULT_MAX_INPUT_BYTES = 32_768
#: 默认生成提词字节上限。
_DEFAULT_MAX_PROMPT_BYTES = 4_096


def _truncate_utf8(text: str, max_bytes: int) -> str:
    raw = str(text or "").encode("utf-8", "replace")
    if len(raw) <= max(0, int(max_bytes)):
        return str(text or "")
    return raw[: max(0, int(max_bytes))].decode("utf-8", "ignore").rstrip()


class LoopPromptService:
    """每个 ``ChatSession`` 一个实例的续跑提词服务。

    Args:
        messages_getter: 返回当前 Agent 消息列表。
        model_getter: 返回当前会话模型名。
        emit: 会话事件发射函数（``ChatSession._emit``）。
        config_getter: 可选配置读取函数（返回 ``loop_prompt`` 节 dict）。
        call_model: 可选的模型调用替身（测试用）；默认走
            ``api.model_async.call_model_async``（silent + 内部 label）。
    """

    def __init__(
        self,
        *,
        messages_getter: Callable[[], Sequence[dict]],
        model_getter: Callable[[], str],
        emit: Callable[..., None] | None = None,
        config_getter: Callable[[], dict] | None = None,
        call_model: Callable[..., Any] | None = None,
    ) -> None:
        self._get_messages = messages_getter
        self._get_model = model_getter
        self._emit = emit
        self._config_getter = config_getter
        self._call_model = call_model

        self._lock = threading.RLock()
        self._armed = False
        self._generating = False
        self._revision = 0
        self._result: LoopPromptDecision | None = None
        self._last_error = ""
        #: 当前已完成运行序号（仅写入提示词，便于模型理解进度）。
        self._run_index = 0

    # ── 编排生命周期 ─────────────────────────────────────

    def begin_loop(self) -> None:
        """一次 ``/loop`` 开始时重置状态（中止上一轮遗留的生成结果）。"""
        with self._lock:
            self._revision += 1
            self._armed = False
            self._result = None
            self._last_error = ""
            self._generating = False

    def end_loop(self) -> None:
        """一次 ``/loop`` 结束时清理（幂等）。"""
        with self._lock:
            self._armed = False
            self._result = None
            self._generating = False

    def arm(self) -> None:
        """允许下一次 ``Agent.run()`` 末尾生成一次（清空旧结果）。"""
        with self._lock:
            self._revision += 1
            self._armed = True
            self._result = None
            self._last_error = ""

    def disarm(self) -> None:
        """禁止本次运行再生成（``run_round`` 返回后调用）。"""
        with self._lock:
            self._armed = False

    def take_result(self) -> LoopPromptDecision | None:
        """取走本次生成结果（取走后清空；无结果返回 ``None``）。"""
        with self._lock:
            result = self._result
            self._result = None
            return result

    @property
    def last_error(self) -> str:
        """最近一次生成失败原因（成功时为空串）。"""
        with self._lock:
            return self._last_error

    @property
    def generating(self) -> bool:
        with self._lock:
            return self._generating

    def status(self) -> dict:
        """诊断状态（测试 / 调试用）。"""
        with self._lock:
            return {
                "armed": self._armed,
                "generating": self._generating,
                "revision": self._revision,
                "has_result": self._result is not None,
                "last_error": self._last_error,
            }

    # ── 配置 ─────────────────────────────────────────────

    def _config(self) -> dict:
        from ...config.defaults import DEFAULTS

        raw_default = DEFAULTS.get("loop_prompt")
        base = dict(raw_default) if isinstance(raw_default, dict) else {}
        if self._config_getter is not None:
            try:
                data = self._config_getter()
                if isinstance(data, dict):
                    for key in list(base):
                        if key in data:
                            base[key] = data[key]
                    return base
            except Exception:
                _logger.debug("读取 loop_prompt 配置失败，回退默认值", exc_info=True)
        try:
            from ...config import get_rc

            rc = get_rc()
            raw = rc.get("loop_prompt") if isinstance(rc, dict) else None
            if isinstance(raw, dict):
                for key in list(base):
                    if key in raw:
                        base[key] = raw[key]
        except Exception:
            _logger.debug("读取 RC loop_prompt 失败，回退默认值", exc_info=True)
        return base

    @staticmethod
    def _as_int(cfg: dict, key: str, fallback: int) -> int:
        try:
            return int(cfg.get(key, fallback))
        except (TypeError, ValueError):
            return fallback

    # ── 生成入口（Agent.run 在后台任务等待前调用） ────────

    async def on_agent_round_end(self) -> None:
        """一次 ``Agent.run()`` 的 pipeline 结束后、后台任务等待前调用。

        未被 ``arm()`` 时立即返回（普通对话零额外请求）。生成过程整体
        ``await``，结果写入 ``self._result``；任何异常都只记录，不抛给 Agent。
        """
        with self._lock:
            if not self._armed:
                return
            self._armed = False
            revision = self._revision
            self._result = None
            self._last_error = ""

        cfg = self._config()
        if not cfg.get("enabled", True):
            self._set_error("提词生成已禁用（loop_prompt.enabled=false）")
            return
        if await self._interrupted():
            self._set_error("对话已中断，跳过提词生成")
            return

        self._notify_generating(True)
        try:
            await self._generate(revision, cfg)
        finally:
            self._notify_generating(False)

    async def _generate(self, revision: int, cfg: dict) -> None:
        """执行一次生成（含一次失败重试），结果按 revision 校验后暂存。"""
        try:
            messages = list(self._get_messages() or ())
        except Exception as exc:
            self._set_error(f"读取会话消息失败：{exc}")
            return

        user_messages = human_user_texts(messages)
        last_answer = last_assistant_text(messages)
        if not user_messages and not last_answer:
            self._set_error("没有可分析的用户消息或回答")
            return

        run_index = max(0, int(getattr(self, "_run_index", 0) or 0))
        max_input_bytes = self._as_int(cfg, "max_input_bytes", _DEFAULT_MAX_INPUT_BYTES)
        max_prompt_bytes = self._as_int(cfg, "max_prompt_bytes", _DEFAULT_MAX_PROMPT_BYTES)
        try:
            request = build_loop_prompt_messages(
                user_messages,
                last_answer,
                run_index=run_index,
                max_input_bytes=max_input_bytes,
                max_prompt_bytes=max_prompt_bytes,
            )
        except Exception as exc:
            self._set_error(f"构建提词请求失败：{exc}")
            return

        timeout_ms = self._as_int(cfg, "timeout_ms", _DEFAULT_TIMEOUT_MS)
        timeout = max(1.0, timeout_ms / 1000.0) if timeout_ms > 0 else None
        retry_once = bool(cfg.get("retry_once", True))
        attempts = 2 if retry_once else 1

        decision: LoopPromptDecision | None = None
        last_error = ""
        for attempt in range(1, attempts + 1):
            try:
                if timeout is None:
                    content, reasoning = await self._call(request)
                else:
                    content, reasoning = await asyncio.wait_for(
                        self._call(request), timeout=timeout,
                    )
            except asyncio.CancelledError:
                raise
            except asyncio.TimeoutError:
                last_error = f"提词生成超时（{int(timeout or 0)} 秒）"
                _logger.warning("/loop 提词生成超时（第 %d/%d 次）", attempt, attempts)
                continue
            except Exception as exc:  # noqa: BLE001 - 辅助请求失败不阻断主链路
                last_error = f"提词生成失败：{exc}"
                _logger.warning(
                    "/loop 提词生成失败（第 %d/%d 次）: %s", attempt, attempts, exc,
                    exc_info=True,
                )
                continue

            decision = (
                parse_loop_prompt_response(content)
                or parse_loop_prompt_response(reasoning)
            )
            if decision is not None:
                break
            last_error = "提词模型未返回有效 JSON（需要 continue / prompt）"
            _logger.warning(
                "/loop 提词输出无法解析（第 %d/%d 次）: %r",
                attempt, attempts, str(content or reasoning)[:300],
            )

        with self._lock:
            if revision != self._revision:
                _logger.debug("丢弃陈旧的 /loop 提词结果（revision %s != %s）",
                              revision, self._revision)
                return

        if decision is None:
            self._set_error(last_error or "提词生成失败")
            return

        if decision.should_continue:
            prompt = _truncate_utf8(decision.prompt, max_prompt_bytes).strip()
            if not prompt:
                self._set_error("提词模型返回 continue=true 但 prompt 为空")
                return
            decision = LoopPromptDecision(True, prompt, decision.raw)
        else:
            decision = LoopPromptDecision(False, "", decision.raw)

        if await self._interrupted():
            self._set_error("提词生成期间对话被中断")
            return

        with self._lock:
            if revision != self._revision:
                return
            self._result = decision
            self._last_error = ""
        self._notify_generated(
            should_continue=decision.should_continue,
            prompt=decision.prompt,
            error="",
        )

    def set_run_index(self, run_index: int) -> None:
        """设置当前已完成运行序号（只用于提示词，便于模型理解进度）。"""
        try:
            self._run_index = max(0, int(run_index))
        except (TypeError, ValueError):
            self._run_index = 0

    @staticmethod
    async def _interrupted() -> bool:
        """当前是否处于对话中断状态（中断期间不再发起辅助请求）。"""
        try:
            from ..interrupt_state import is_interrupted_async

            return bool(await is_interrupted_async())
        except Exception:
            return False

    async def _call(self, messages: list[dict]) -> tuple[str, str]:
        """调用模型，返回 ``(content, reasoning)``。"""
        if self._call_model is not None:
            result = self._call_model(messages)
            if asyncio.iscoroutine(result):
                result = await result
            if isinstance(result, tuple):
                content = result[0] if result else ""
                reasoning = result[1] if len(result) > 1 else ""
                return str(content or ""), str(reasoning or "")
            return str(result or ""), ""
        from ...api.model_async import call_model_async
        from ..constants import STREAM_LABEL_LOOP_PROMPT

        reasoning, content, _usage, _tool_calls = await call_model_async(
            messages,
            model=self._get_model(),
            tools=None,
            display=None,
            label=STREAM_LABEL_LOOP_PROMPT,
            silent=True,
            override_max_retries=1,
            empty_response_retries=1,
        )
        return str(content or ""), str(reasoning or "")

    # ── 事件 ─────────────────────────────────────────────

    def _emit_event(self, event: str, **data: Any) -> None:
        if self._emit is None:
            return
        try:
            self._emit(event, **data)
        except Exception:
            _logger.debug("广播 %s 事件失败", event, exc_info=True)

    def _notify_generating(self, generating: bool) -> None:
        with self._lock:
            self._generating = bool(generating)
        self._emit_event("loop_prompt_generating", generating=bool(generating))

    def _notify_generated(
        self, *, should_continue: bool | None, prompt: str, error: str,
    ) -> None:
        self._emit_event(
            "loop_prompt_generated",
            should_continue=should_continue,
            prompt=str(prompt or ""),
            error=str(error or ""),
        )

    def _set_error(self, message: str) -> None:
        with self._lock:
            self._last_error = str(message or "")
        _logger.warning("/loop 续跑提词未生成：%s", message)
        self._notify_generated(should_continue=None, prompt="", error=str(message or ""))


__all__ = ["LoopPromptService"]
