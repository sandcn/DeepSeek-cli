"""重试逻辑 — API 调用重试与解析失败重试"""
from __future__ import annotations

import asyncio
import json
import logging

import httpx

from .errors import (
    APIError, RateLimitError, CONNECTION_ERRORS as _CONNECTION_ERRORS,
    EmptyResponseError, is_retryable, compute_retry_delay,
    compute_empty_response_delay, format_user_error,
)
from .interrupt_async import is_interrupted_async, wait_for_interrupt_async
from ..config import MAX_RETRIES, RETRY_BASE_SEC, EMPTY_RESPONSE_MAX_RETRIES
from ..core.events.publish import publish_output

_logger = logging.getLogger(__name__)

_MAX_PARSE_RETRIES = 1

# ── 空响应专用重试策略 ──────────────────────────────────────
# 空响应（无正文/无推理/无工具调用）不是"调用失败"，而是"没拿到回答"：
# 请求本身健康、尚未渲染任何内容、原样重发幂等安全，因此走**独立**于
# MAX_RETRIES 的重试预算，且**不输出 ✖ 错误**（自动恢复不是错误）：
#
# - 交互主对话（调用方未指定 override_max_retries）默认无限重试
#   （EMPTY_RESPONSE_MAX_RETRIES 配置，0 = 无限），直到拿到有效回答或用户
#   ESC 中断——用户需求：不要以「模型返回空响应」错误结束本轮，也不要让
#   用户手动「继续」；
# - 有界调用方（SubAgent 传 override_max_retries=1、压缩摘要显式传
#   empty_response_retries）沿用各自预算快速失败，不拖住父流程。
#
# 连续空响应往往是同一请求的**确定性**结果，原样重发无济于事：达到
# _EMPTY_NUDGE_AFTER 次后向**发送副本**追加一条续接提示词改变请求尾部
# （不写入会话历史），提高恢复概率。
_EMPTY_NUDGE_AFTER = 3
_EMPTY_NUDGE_MESSAGE = (
    "（系统提示：上一次响应为空。请直接给出实质回答；如需调用工具，"
    "请直接发起工具调用。）"
)
# 持续未恢复时每隔多少次输出一条弱提示（DIM，非错误），避免长等待像卡死
_EMPTY_HINT_INTERVAL = 10


def _empty_response_budget(
    override_max_retries: int | None,
    explicit: int | None,
) -> int | None:
    """空响应重试预算（返回 ``None`` 表示无限）。

    优先级：
    1. ``explicit``（``empty_response_retries`` 调用方显式指定；``<=0`` = 无限）
    2. 有界调用方（``override_max_retries`` 非 None，如 SubAgent=1）沿用其预算
    3. 全局配置 ``EMPTY_RESPONSE_MAX_RETRIES``（``<=0`` = 无限，默认）
    """
    if explicit is not None:
        try:
            value = int(explicit)
        except (TypeError, ValueError):
            value = 0
        return None if value <= 0 else max(value, 1)
    if override_max_retries is not None:
        try:
            return max(int(override_max_retries), 1)
        except (TypeError, ValueError):
            return None
    try:
        configured = int(EMPTY_RESPONSE_MAX_RETRIES or 0)
    except (TypeError, ValueError):
        configured = 0
    return None if configured <= 0 else max(configured, 1)


def _apply_empty_nudge(api_args: tuple) -> bool:
    """连续空响应后向**发送副本**追加一条用户续接提示（返回是否追加）。

    仅当末条消息为 ``user`` / ``tool``（模型本应作答）时追加——末条为
    ``assistant``（续写 / 预填场景）时追加会造成非法消息序列，故不追加。
    幂等：已注入过（例如解析重试二次进入）不再重复追加；只改上传副本，
    会话历史不受影响。
    """
    if not api_args or not isinstance(api_args[0], list):
        return False
    messages = api_args[0]
    if not messages or not isinstance(messages[-1], dict):
        return False
    if messages[-1].get("role") not in ("user", "tool"):
        return False
    if any(isinstance(m, dict) and m.get("content") == _EMPTY_NUDGE_MESSAGE
           for m in messages):
        return False
    messages.append({"role": "user", "content": _EMPTY_NUDGE_MESSAGE})
    return True


def _publish_empty_hint(attempts: int) -> None:
    """持续空响应时输出一条 DIM 弱提示（非错误），避免用户误以为卡死。"""
    try:
        from ..core.constants import DIM, RESET
        publish_output(
            f"\n{DIM}  ↻ 模型连续返回空响应（第 {attempts} 次），"
            f"仍在自动重试…（ESC 可中断）{RESET}",
            level="raw",
        )
    except Exception:
        _logger.debug("空响应提示输出失败", exc_info=True)

# 候选可重试异常：捕获后交由 is_retryable() 统一判定（连接错误 /
# httpx 传输错误 / JSON 解析错误 / 超时 / 语义化 API 错误）
_RETRY_EXCEPTIONS = _CONNECTION_ERRORS + (
    httpx.HTTPStatusError, httpx.RequestError,
    json.JSONDecodeError, TimeoutError, asyncio.TimeoutError,
    RateLimitError, APIError,
)


async def retry_api_call_async(
    api_func,
    *,
    silent: bool = False,
    display=None,
    label: str | None = None,
    api_args: tuple = (),
    fixed_delay_sec: float | None = None,
    override_max_retries: int | None = None,
    empty_response_retries: int | None = None,
):
    """异步版重试逻辑。

    Args:
        api_func: 要调用的异步模型函数
        api_args: 传给 api_func 的位置参数元组
        silent/display/label: 重试报告控制参数
        fixed_delay_sec: 覆盖固定重试间隔（秒），设为非 None 值时代替默认间隔；
                        None 时使用全局默认固定间隔 RETRY_BASE_SEC（默认 30 秒）
        override_max_retries: 覆盖**瞬时错误**最大尝试次数，设为非 None 值时替代
                            MAX_RETRIES（全局默认 10）；None 时使用 MAX_RETRIES。
                            非 None 时同时作为空响应的默认预算（有界调用方）。
        empty_response_retries: 空响应（EmptyResponseError）自动重发上限；
                            None（默认）= 按 override_max_retries / 全局配置
                            EMPTY_RESPONSE_MAX_RETRIES（0 = 无限）推导；
                            ``<=0`` = 无限重试（交互主对话默认）；
                            ``>0`` = 有界重试（内部调用方，用尽后返回可读错误文本）。

    空响应与瞬时错误的重试预算相互**独立**：空响应不消耗 MAX_RETRIES（反之
    亦然），偶发空响应不会挤占网络错误的重试额度。空响应重试全程**不输出
    ✖ 错误**（自动恢复不是错误），仅在连续多次未恢复时输出一条 DIM 弱提示。
    """
    async def _report(msg: str):
        if display is not None and label:
            display.update_model_phase(label, "error", msg)
        elif not silent:
            from ..core.constants import YELLOW, RESET
            publish_output(f"\n{YELLOW}{msg}{RESET}", level="raw")
        # silent=True 且无 display 时：不输出

    empty: tuple = ("", "(已中断，无内容)", {"input": 0, "output": 0}, [])

    effective_max_retries = override_max_retries if override_max_retries is not None else MAX_RETRIES
    effective_max_retries = max(effective_max_retries, 1)  # 至少尝试 1 次
    empty_budget = _empty_response_budget(override_max_retries, empty_response_retries)

    # 输入校验：负值 fixed_delay_sec clamp 到 0，避免无间隔重试风暴
    if fixed_delay_sec is not None and fixed_delay_sec < 0:
        _logger.warning("fixed_delay_sec=%s 为负值，已 clamp 到 0", fixed_delay_sec)
        fixed_delay_sec = 0.0

    attempt = 0          # 瞬时错误尝试次数（空响应不计入）
    empty_attempts = 0   # 连续空响应次数（独立预算）
    while True:
        if await is_interrupted_async():
            await _report("已中断生成（保留部分内容）")
            return empty
        attempt += 1
        try:
            return await api_func(*api_args)
        except KeyboardInterrupt:
            await _report("已中断生成（保留部分内容）")
            return empty
        except _RETRY_EXCEPTIONS as e:
            # 统一重试判定：瞬时错误（连接错误/超时/429/5xx 等）重试；
            # 永久错误（400/401/403/404/422 等）重试无意义，直接抛出。
            if not is_retryable(e):
                _logger.warning("API 调用返回不可重试错误: %s", e)
                raise
            # ── 空响应：独立预算 + 静默自动恢复（不报错、不占 MAX_RETRIES） ──
            if isinstance(e, EmptyResponseError):
                empty_attempts += 1
                if empty_budget is not None and empty_attempts >= empty_budget:
                    _logger.warning(
                        "模型连续返回空响应 %d 次仍无产出，停止重发: %s",
                        empty_attempts, e,
                    )
                    await _report(f"API 调用失败 (已重试{empty_attempts}次): {e}")
                    return ("", format_user_error(e), {"input": 0, "output": 0}, [])
                # 连续空响应多为同一请求的确定性结果：第 N 次后追加续接
                # 提示词改变请求尾部（仅发送副本），提高恢复概率
                if empty_attempts == _EMPTY_NUDGE_AFTER and _apply_empty_nudge(api_args):
                    _logger.info(
                        "连续 %d 次空响应，已向发送副本追加续接提示词后重发",
                        empty_attempts,
                    )
                wait = compute_empty_response_delay(empty_attempts)
                _logger.warning(
                    "模型返回空响应（第 %d 次），%.1f 秒后自动重发: %s",
                    empty_attempts, wait, e,
                )
                if not silent and empty_attempts % _EMPTY_HINT_INTERVAL == 0:
                    _publish_empty_hint(empty_attempts)
                if await wait_for_interrupt_async(wait):
                    return ("", "(已中断)", {"input": 0, "output": 0}, [])
                attempt -= 1  # 空响应不占用瞬时错误预算
                continue
            category = "连接错误" if isinstance(e, _CONNECTION_ERRORS) else "API 调用失败"
            _logger.warning(
                "%s (尝试 %d/%d): %s", category, attempt, effective_max_retries, e, exc_info=True,
            )
            if attempt < effective_max_retries:
                # 显式 fixed_delay_sec（含 SubAgent 的 0）优先；
                # 否则指数退避，429 时优先尊重服务端 Retry-After
                if fixed_delay_sec is not None:
                    wait = max(fixed_delay_sec, 0.0)
                else:
                    wait = compute_retry_delay(attempt, RETRY_BASE_SEC, e)
                await _report(f"{category} (第{attempt}次): {e}，{wait:.1f}秒后重试...")
                if await wait_for_interrupt_async(wait):
                    return ("", "(已中断)", {"input": 0, "output": 0}, [])
            else:
                await _report(f"{category} (已重试{effective_max_retries}次): {e}")
                return ("", format_user_error(e), {"input": 0, "output": 0}, [])
        except asyncio.CancelledError:
            await _report("已中断生成（保留部分内容）")
            raise
        except Exception as e:
            _logger.warning("API 调用出现非重试异常: %s", e, exc_info=True)
            raise

    return ("", "", {"input": 0, "output": 0}, [])


async def retry_on_parse_failure_async(
    api_func,
    *,
    silent: bool = False,
    display=None,
    label: str | None = None,
    api_args: tuple = (),
    retry_func=None,
    override_max_retries: int | None = None,
    fixed_delay_sec: float | None = None,
    empty_response_retries: int | None = None,
):
    """解析失败重试包装函数。

    内部先调用 retry_api_call_async 获取结果，检查 usage 中的
    _parse_failed_ids；若非空，输出"解析参数错误正重新生成"提示并
    再次调用 retry_api_call_async（最多重试 _MAX_PARSE_RETRIES 次）。

    重试用尽后从 usage 中弹出 _parse_failed_ids（避免泄漏到下游统计），
    返回部分成功的结果（成功解析的 tool_calls 正常流转）。

    此函数是 retry_api_call_async 的外层包装，严格遵守安全约束：
    不修改 retry_api_call_async 的内部逻辑。

    Args:
        api_func: 底层 API 调用函数
        retry_func: 可注入的 API 重试函数，默认使用 retry_api_call_async。
                    用于单元测试中 mock 底层 API 调用，避免真实网络请求。
        override_max_retries: 覆盖最大重试次数（透传给 retry_api_call_async）。
            SubAgent 等快速失败场景传 1（不重试），避免叠加全局长重试
            （MAX_RETRIES=10 × RETRY_BASE_SEC=30 ≈ 5 分钟）导致子代理"卡住"。
        fixed_delay_sec: 覆盖固定重试间隔（透传给 retry_api_call_async），
            SubAgent 场景传 0 避免 30s 长等待。
        empty_response_retries: 空响应自动重发上限（透传给 retry_api_call_async）。
            None = 沿用 override_max_retries / 全局配置；``<=0`` = 无限重试
            （交互主对话默认）；``>0`` = 有界（压缩摘要等内部调用方，避免后台
            调用在服务端持续返回空响应时永久挂起）。
    """
    _retry = retry_func if retry_func is not None else retry_api_call_async

    def _report(msg: str):
        if display is not None and label:
            display.update_model_phase(label, "error", msg)
        elif not silent:
            from ..core.constants import YELLOW, RESET
            publish_output(f"\n{YELLOW}{msg}{RESET}", level="raw")

    from .json_repair import _PARSE_RETRY_STATS, _JSON_REPAIR_LOCK

    def _retry_kwargs():
        """构造透传给 _retry 的额外 kwargs（兼容自定义 retry_func 不支持新参数）。"""
        kwargs: dict = {}
        if override_max_retries is not None:
            kwargs["override_max_retries"] = override_max_retries
        if fixed_delay_sec is not None:
            kwargs["fixed_delay_sec"] = fixed_delay_sec
        if empty_response_retries is not None:
            kwargs["empty_response_retries"] = empty_response_retries
        return kwargs

    result = await _retry(
        api_func,
        silent=silent, display=display, label=label,
        api_args=api_args,
        **_retry_kwargs(),
    )
    reasoning_content, content, usage, tool_calls = result

    for attempt in range(_MAX_PARSE_RETRIES):
        failed_ids = usage.get("_parse_failed_ids", [])
        if not failed_ids:
            break
        # 更新解析重试统计：触发重试
        with _JSON_REPAIR_LOCK:
            _PARSE_RETRY_STATS["retry_triggered"] += 1
        _logger.warning(
            "解析失败，触发重试（第 %d 次）。失败 ID: %s",
            attempt + 1, failed_ids,
        )
        _report("解析参数错误正重新生成")
        result = await _retry(
            api_func,
            silent=silent, display=display, label=label,
            api_args=api_args,
            **_retry_kwargs(),
        )
        reasoning_content, content, usage, tool_calls = result
        # 重试后检查是否仍失败 → 更新成功/耗尽统计
        if not usage.get("_parse_failed_ids"):
            with _JSON_REPAIR_LOCK:
                _PARSE_RETRY_STATS["retry_success"] += 1

    # 重试用尽后仍存在解析失败：记录 warning 日志（含失败 ID 列表；
    # 原始参数片段已在 _tool_parse_utils 解析层通过 _logger.warning 记录）
    failed_ids = usage.get("_parse_failed_ids", [])
    if failed_ids:
        with _JSON_REPAIR_LOCK:
            _PARSE_RETRY_STATS["retry_exhausted"] += 1
        success_ids = [tc.get("id", "?") for tc in tool_calls] if tool_calls else []
        _logger.warning(
            "解析重试用尽（共 %d 次），仍有 %d 个 tool_call 解析失败。"
            "失败 ID: %s，成功 ID: %s",
            _MAX_PARSE_RETRIES + 1, len(failed_ids), failed_ids, success_ids,
        )

    # 清理 _parse_failed_ids，避免泄漏到下游统计
    usage.pop("_parse_failed_ids", None)
    return reasoning_content, content, usage, tool_calls
