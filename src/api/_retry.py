"""重试逻辑 — API 调用重试与解析失败重试"""
from __future__ import annotations

import asyncio
import json
import logging

import httpx

from .client_async import (
    RateLimitError, APIError, _CONNECTION_ERRORS,
)
from .interrupt_async import is_interrupted_async, wait_for_interrupt_async
from ..config import MAX_RETRIES, RETRY_BASE_SEC
from ..tui.events.consumers import publish_output

_logger = logging.getLogger(__name__)

_MAX_PARSE_RETRIES = 1

# 可重试的 HTTP 状态码（瞬时错误）：请求超时/速率限制/服务端过载/网关超时。
# 其余（400/401/403/404/422 等）为永久错误，重试无意义，直接抛出。
_RETRYABLE_HTTP_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})


async def retry_api_call_async(
    api_func,
    *,
    silent: bool = False,
    display=None,
    label: str | None = None,
    api_args: tuple = (),
    fixed_delay_sec: float | None = None,
    override_max_retries: int | None = None,
):
    """异步版重试逻辑。

    Args:
        api_func: 要调用的异步模型函数
        api_args: 传给 api_func 的位置参数元组
        silent/display/label: 重试报告控制参数
        fixed_delay_sec: 覆盖固定重试间隔（秒），设为非 None 值时代替默认间隔；
                        None 时使用全局默认固定间隔 RETRY_BASE_SEC（默认 30 秒）
        override_max_retries: 覆盖最大重试次数，设为非 None 值时替代 MAX_RETRIES
                            （全局默认 10）；None 时使用 MAX_RETRIES
    """
    async def _report(msg: str):
        if display is not None and label:
            display.update_model_phase(label, "error", msg)
        elif not silent:
            from ..core.constants import YELLOW, RESET
            publish_output(f"\n{YELLOW}{msg}{RESET}", level="raw")
        # silent=True 且无 display 时：不输出

    empty = ("", "(已中断，无内容)", {"input": 0, "output": 0}, [])

    effective_max_retries = override_max_retries if override_max_retries is not None else MAX_RETRIES
    effective_max_retries = max(effective_max_retries, 1)  # 至少尝试 1 次

    # 输入校验：负值 fixed_delay_sec clamp 到 0，避免无间隔重试风暴
    if fixed_delay_sec is not None and fixed_delay_sec < 0:
        _logger.warning("fixed_delay_sec=%s 为负值，已 clamp 到 0", fixed_delay_sec)
        fixed_delay_sec = 0.0

    for attempt in range(1, effective_max_retries + 1):
        if await is_interrupted_async():
            await _report("已中断生成（保留部分内容）")
            return empty
        try:
            return await api_func(*api_args)
        except KeyboardInterrupt:
            await _report("已中断生成（保留部分内容）")
            return empty
        except _CONNECTION_ERRORS as e:
            _logger.warning(
                "连接错误 (尝试 %d/%d): %s", attempt, effective_max_retries, e, exc_info=True,
            )
            if attempt < effective_max_retries:
                wait = fixed_delay_sec if fixed_delay_sec is not None else RETRY_BASE_SEC
                await _report(f"连接错误 (第{attempt}次): {e}，{wait:.1f}秒后重试...")
                if await wait_for_interrupt_async(wait):
                    return ("", "(已中断)", {"input": 0, "output": 0}, [])
            else:
                await _report(f"连接错误 (已重试{effective_max_retries}次): {e}")
                return ("", f"连接错误: {str(e)}", {"input": 0, "output": 0}, [])
        except (
            httpx.HTTPStatusError, httpx.RequestError,
            json.JSONDecodeError, TimeoutError,
            RateLimitError, APIError,
        ) as e:
            # APIError 仅在瞬时状态码（5xx/408/425/429）时重试；
            # 永久性错误（400/401/403/404/422 等）重试无意义，直接抛出不重试。
            if isinstance(e, APIError) and e.status_code not in _RETRYABLE_HTTP_STATUS:
                _logger.warning("API 调用返回不可重试状态码 %d: %s", e.status_code, e)
                raise
            _logger.warning(
                "API 调用失败 (尝试 %d/%d): %s", attempt, effective_max_retries, e, exc_info=True,
            )
            if attempt < effective_max_retries:
                wait = fixed_delay_sec if fixed_delay_sec is not None else RETRY_BASE_SEC
                await _report(f"API 调用失败 (第{attempt}次): {e}，{wait:.1f}秒后重试...")
                if await wait_for_interrupt_async(wait):
                    return ("", "(已中断)", {"input": 0, "output": 0}, [])
            else:
                await _report(f"API 调用失败 (已重试{effective_max_retries}次): {e}")
                return ("", f"抱歉，API 调用出错: {str(e)}", {"input": 0, "output": 0}, [])
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
