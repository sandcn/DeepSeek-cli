"""一次性摘要调用 — 对齐 dsh ``compaction-basic/summarizer`` 的回放与框定。

摘要请求把「系统提词 + 被遮蔽区域消息」按原顺序回放，末尾追加压缩指令
作为最后一条 user 消息；这样辅助调用是会话最后一个已路由请求的真实前缀，
提供方的热前缀 KV 缓存得以复用，只有尾随指令与摘要输出未缓存。

只接受文本摘要；空内容或图片输出一律失败。

摘要调用默认经 ``core.adapters.model.SyncModelBridge`` 走**流式**管线
（``api.model_async`` 的 SSE 摘要调用，silent + 内部 label）——避免非流式
长输出在服务端/网关侧空闲超时被截断，并复用真实 usage 校准与实时 token
统计；摘要内容不渲染、不计入主 Agent 上下文使用率。
"""

from __future__ import annotations

import inspect

from .checkpoint import COMPACTION_INSTRUCTION
from .types import SummaryResult

#: 摘要文本的最小有效长度（过短视为无效输出）。
_MIN_SUMMARY_CHARS = 10

#: summarize_fn 是否支持 ``max_tokens`` 参数（按函数身份缓存，避免每次探测）。
_max_tokens_support: dict = {}


def _supports_max_tokens(summarize_fn) -> bool:
    key = id(summarize_fn)
    cached = _max_tokens_support.get(key)
    if cached is not None:
        return cached
    try:
        params = inspect.signature(summarize_fn).parameters
        supported = "max_tokens" in params or any(
            p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()
        )
    except (TypeError, ValueError):
        supported = False
    _max_tokens_support[key] = supported
    return supported


def _invoke_summarize(summarize_fn, messages, model, max_tokens):
    """调用摘要函数；支持 ``max_tokens`` 时一并传入（否则按基础签名）。"""
    if max_tokens and _supports_max_tokens(summarize_fn):
        return summarize_fn(messages, model=model, max_tokens=max_tokens)
    return summarize_fn(messages, model=model)


class SummaryError(Exception):
    """摘要调用未能产出有效文本。"""


#: 未结束后台任务清单的提词段落标题（与压缩指令同语言）。
_PENDING_JOBS_HEADER = "## Live Background Jobs (MUST keep these exact ids)"


def _format_pending_jobs(pending_jobs) -> str:
    """把未结束后台任务清单格式化为压缩指令尾部的硬性保留段。

    每项 ``{"task_id", "kind", "detail", "status"}``：kind 为 ``bash``
    （bg-xxx，bash_opt 管理）或 ``subagent``（sa-xxx，subagent_opt 管理），
    据此给出对应管理工具提示。空清单 / 无有效项返回空串（不追加段落，
    保持指令原样）。
    """
    lines: list = []
    for job in list(pending_jobs or ()):
        if not isinstance(job, dict):
            continue
        task_id = str(job.get("task_id") or "").strip()
        if not task_id:
            continue
        kind = str(job.get("kind") or "").strip() or "job"
        manager = "subagent_opt" if kind == "subagent" else "bash_opt"
        status = str(job.get("status") or "running").strip() or "running"
        detail = str(job.get("detail") or "").strip()
        suffix = f" — {detail}" if detail else ""
        lines.append(
            f"- `{task_id}` ({kind}, status={status}, keep managing with {manager}){suffix}"
        )
    if not lines:
        return ""
    return "\n".join([
        _PENDING_JOBS_HEADER,
        "These background jobs have NOT finished. Copy their exact task ids into the "
        "checkpoint verbatim (never shorten, rename, or drop them) and note each one's "
        "purpose and status, so the next model can keep managing them:",
        *lines,
    ])


def build_summarization_messages(head_messages: list, region_messages: list,
                                 prior_hint: str = "",
                                 pending_jobs=None) -> list:
    """构建摘要调用的消息序列（回放前缀 + 压缩指令）。

    pending_jobs: 仍未结束的后台任务清单（可选）——追加到压缩指令尾部，
        确保这些 task_id 逐字保留在检查点里。
    """
    messages: list = []
    for message in list(head_messages or ()) + list(region_messages or ()):
        if isinstance(message, dict):
            messages.append(message)
    instruction = COMPACTION_INSTRUCTION
    pending_section = _format_pending_jobs(pending_jobs)
    if pending_section:
        instruction = f"{instruction}\n\n{pending_section}"
    if prior_hint:
        instruction = f"{prior_hint}\n{instruction}"
    messages.append({"role": "user", "content": instruction})
    return messages


def summarize_region(
    head_messages: list,
    region_messages: list,
    summarize_fn,
    model: str,
    max_tokens: int = 0,
    has_prior: bool = False,
    pending_jobs=None,
) -> SummaryResult:
    """调用模型生成结构化检查点文本。

    Args:
        head_messages: 系统提词消息（回放前缀）。
        region_messages: 被遮蔽区域的历史消息。
        summarize_fn: 摘要调用函数，签名 ``(messages, model=) -> (reasoning, content, usage, tool_calls)``。
        model: 模型名称。
        max_tokens: 摘要输出上限（0 表示不限制；当前 summarize_fn 不接受该参数时忽略）。
        has_prior: 被遮蔽区域是否已包含既有检查点。
        pending_jobs: 仍未结束的后台任务清单（``[{"task_id", "kind", "detail",
            "status"}, ...]``）——注入指令尾部，确保 bg-xxx / sa-xxx 的
            task_id 逐字保留在检查点里。

    Returns:
        SummaryResult。

    Raises:
        SummaryError: 无可压缩内容 / 输出为空或过短 / 输出含非文本内容。
    """
    if not region_messages:
        raise SummaryError("没有可压缩的消息")
    prior_hint = (
        "注意：对话中包含既有检查点（<compacted-summary> 或 [对话摘要]），"
        "请保留仍然成立的事实、丢弃过时内容，并把新信息合并为一份统一的摘要。"
        if has_prior else ""
    )
    messages = build_summarization_messages(
        head_messages, region_messages, prior_hint, pending_jobs=pending_jobs,
    )

    _reasoning, content, usage, _tool_calls = _invoke_summarize(
        summarize_fn, messages, model, max_tokens,
    )

    if not isinstance(content, str):
        raise SummaryError("摘要输出不是文本内容")

    summary = content.strip()
    if len(summary) < _MIN_SUMMARY_CHARS:
        raise SummaryError(f"摘要内容无效（长度={len(summary)}）")

    return SummaryResult(
        summary=summary,
        usage=usage if isinstance(usage, dict) else {},
        model=model or "",
        max_tokens=max_tokens or 0,
    )


__all__ = [
    "SummaryError",
    "build_summarization_messages",
    "summarize_region",
]
