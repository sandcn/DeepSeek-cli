"""``/loop`` 续跑提词提供方 —— 提示词构建与模型输出解析。

模型只负责两件事：

1. 判断用户请求是否还有**未完成的功能 / 步骤 / 验证项**；
2. 若仍有，则生成一段可直接发给助手的续跑提词。

输出契约为一个 JSON 对象（字段名同时容忍常见别名）：

.. code-block:: json

    {"continue": true, "prompt": "继续完成 ..."}

输入只包含「人类用户消息」与「最后一次有内容的 assistant 回答」——tool 结果、
后台 bash/subagent 结果 JSON、内部注入消息（``【继续】`` / ``【自动继续】`` /
``<skill_content>`` 等）都会被过滤，避免把机器载荷误当成用户诉求。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Sequence

_logger = logging.getLogger(__name__)

#: 续跑提词在会话中的标记前缀（用于区分机器注入消息与人类输入）。
#:
#: 生成提词最终会作为 user 消息进入会话（``session.run_round``），加一个短标记
#: 既让用户/日志可辨认，也让会话标题服务把它排除出「人类消息」。
LOOP_PROMPT_MARKER = "【自动继续】"

#: 系统指令（``{max_prompt_bytes}`` 仅用于提示模型长度上限）。
SYSTEM_TEMPLATE = (
    "你是 /loop 循环的续跑提词生成器。阅读用户请求与助手最后一次回答，"
    "判断任务是否还有未完成的功能、步骤或验证项。\n"
    "只输出一个 JSON 对象，格式：{\"continue\": true/false, \"prompt\": \"...\"}\n"
    "规则：\n"
    "- continue=true：仍存在明确的未完成条目；prompt 写一段可直接发给助手的中文指令，"
    "只针对上一次回答中明确承诺、暴露或显然尚未完成的条目，逐条列出要继续做的事"
    "（涉及文件、行为、测试 / 验证要求），不要重复已完成内容，不要发明新需求。\n"
    "- continue=false：用户要求已全部完成、没有可继续事项，或无法从回答中确认还有"
    "未完成项；此时 prompt 必须为空字符串。\n"
    "- 只输出 JSON：不要代码围栏、解释、前后缀或多余文本。\n"
    "- prompt 使用与用户消息相同的语言，长度不超过 {max_prompt_bytes} 字节。"
)

#: 不应参与「人类用户消息」判断的前缀（本项目内部注入 / 机器生成）。
_NON_HUMAN_PREFIXES: tuple[str, ...] = (
    "<skill_content>",
    "<compacted-summary>",
    "[对话摘要]",
    "[检查点恢复]",
    "【继续】",
    LOOP_PROMPT_MARKER,
)

#: 代码围栏（模型偶尔把 JSON 包在围栏里）。
_FENCE_RE = re.compile(r"^\s*```[A-Za-z0-9_+-]*\s*|\s*```\s*$", re.MULTILINE)
#: 包裹引号（模型偶尔给 prompt 加引号）。
_WRAPPING_RE = re.compile(r"^[\"'“”‘’`「」『』]+|[\"'“”‘’`「」『』]+$")


@dataclass(frozen=True)
class LoopPromptDecision:
    """一次续跑决策（模型输出被接受后的规范化结果）。

    Attributes:
        should_continue: 是否继续循环。
        prompt: 续跑提词（``should_continue=False`` 时恒为空串）。
        raw: 模型原始输出（诊断 / 日志用）。
    """

    should_continue: bool
    prompt: str = ""
    raw: str = ""


def build_system_prompt(*, max_prompt_bytes: int = 4096) -> str:
    """构建续跑提词模型的系统指令。"""
    # 模板里含 JSON 字面量 ``{"continue": ...}``，不能用 str.format——
    # 花括号会被当成占位符；只替换唯一的上限占位符。
    return SYSTEM_TEMPLATE.replace(
        "{max_prompt_bytes}", str(max(1, int(max_prompt_bytes))),
    )


def _message_text(content: Any) -> str:
    """把消息 content 转成纯文本（多模态 content blocks 只取文本部分）。"""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    try:
        from ..multimodal import content_to_text

        return content_to_text(content)
    except Exception:
        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if isinstance(block, dict):
                    text = block.get("text")
                    if isinstance(text, str):
                        parts.append(text)
            return " ".join(parts)
        return str(content)


def _is_background_result(text: str) -> bool:
    """是否为后台 bash/subagent 结果 JSON（``role=user`` 的机器载荷）。"""
    stripped = str(text or "").strip()
    if not stripped.startswith("{"):
        return False
    try:
        data = json.loads(stripped)
    except Exception:
        return False
    if not isinstance(data, dict):
        return False
    if "task_id" in data:
        return True
    return "stdout" in data and "returncode" in data


def is_human_user_text(text: str) -> bool:
    """判断一段 user 文本是否代表人类输入（排除内部注入与后台结果）。"""
    cleaned = str(text or "").strip()
    if not cleaned:
        return False
    if cleaned.startswith(_NON_HUMAN_PREFIXES):
        return False
    if _is_background_result(cleaned):
        return False
    # 纯图片消息的文本占位不构成可分析的诉求。
    return bool(cleaned.replace("[图片]", "").strip())


def human_user_texts(messages: Sequence[dict]) -> list[str]:
    """提取所有人类用户消息文本（保持时间顺序）。"""
    out: list[str] = []
    for message in messages or ():
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        text = _message_text(message.get("content"))
        if is_human_user_text(text):
            out.append(text.strip())
    return out


def last_assistant_text(messages: Sequence[dict]) -> str:
    """返回最后一次有内容的 assistant 回答（找不到时为空串）。

    工具调用轮次可能产生 ``content`` 为空、只有 ``tool_calls`` 的 assistant
    消息——跳过它们，继续向前找真正有文本的回答。
    """
    for message in reversed(list(messages or ())):
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        text = _message_text(message.get("content")).strip()
        if text:
            return text
    return ""


def _truncate_utf8(text: str, max_bytes: int) -> str:
    """按 UTF-8 字节安全截断（超出时追加省略号，且总长不超过上限）。"""
    raw = str(text or "").encode("utf-8", "replace")
    limit = max(0, int(max_bytes))
    if len(raw) <= limit:
        return str(text or "")
    clipped = raw[:limit].decode("utf-8", "ignore").rstrip()
    while clipped and len((clipped + "…").encode("utf-8")) > limit:
        clipped = clipped[:-1]
    return clipped + "…" if len((clipped + "…").encode("utf-8")) <= limit else clipped


def select_user_messages_within_budget(
    messages: Sequence[str], max_bytes: int,
) -> tuple[list[str], int]:
    """按「最新优先」预算裁剪用户消息，返回 ``(保留列表, 省略条数)``。

    - 从最新一条向前累计；放不下就停止，绝不因旧消息挤掉最新消息；
    - 最新一条即使单独超预算也保留（截断到预算内）；
    - ``max_bytes <= 0`` 表示不限制。
    """
    items = [str(item or "") for item in messages]
    if not items:
        return [], 0
    budget = int(max_bytes)
    if budget <= 0:
        return items, 0
    used = 0
    start = len(items)
    for index in range(len(items) - 1, -1, -1):
        text = items[index]
        cost = len(text.encode("utf-8", "replace")) + 16
        if used + cost <= budget:
            used += cost
            start = index
            continue
        if start == len(items):
            # 最新一条单独超预算：截断后仍保留。
            items[index] = _truncate_utf8(text, max(16, budget - 16))
            start = index
        break
    return items[start:], start


def _payload_bytes(
    user_messages: Sequence[str], last_assistant: str, run_index: int,
) -> int:
    payload = {
        "run": int(run_index),
        "userMessages": list(user_messages),
        "lastAssistant": str(last_assistant or ""),
    }
    return len(json.dumps(payload, ensure_ascii=False).encode("utf-8", "replace"))


def build_loop_prompt_messages(
    user_messages: Sequence[str],
    last_assistant: str,
    *,
    run_index: int,
    max_input_bytes: int = 0,
    max_prompt_bytes: int = 4096,
) -> list[dict]:
    """构建续跑提词请求（system + user 两条消息）。

    Args:
        user_messages: 人类用户消息（按时间顺序）。
        last_assistant: 最后一次有内容的 assistant 回答。
        run_index: 当前已完成运行序号（1-based，供模型理解进度）。
        max_input_bytes: 整个请求的 UTF-8 字节预算（``0`` = 不限制）。
        max_prompt_bytes: 提示模型输出提词的长度上限（仅写入 system 指令）。

    Returns:
        ``[{"role": "system", ...}, {"role": "user", ...}]``。
    """
    system = build_system_prompt(max_prompt_bytes=max_prompt_bytes)
    selected: list[str] = list(user_messages)
    omitted = 0
    limit = int(max_input_bytes or 0)
    if limit > 0:
        fixed = len(system.encode("utf-8", "replace")) + 256
        budget = max(64, limit - fixed)
        # 最后回答占一半左右预算，用户消息使用剩余部分（最新优先）。
        answer_budget = max(256, budget // 2)
        answer = _truncate_utf8(last_assistant, answer_budget)
        selected, omitted = select_user_messages_within_budget(
            user_messages, max(64, budget - len(answer.encode("utf-8", "replace")) - 32),
        )
        while selected and _payload_bytes(selected, answer, run_index) + fixed > limit:
            selected = selected[1:]
            omitted += 1
        if not selected and _payload_bytes([], answer, run_index) + fixed > limit:
            # 连空列表都超预算：进一步截断回答。
            answer = _truncate_utf8(
                answer,
                max(64, limit - fixed - _payload_bytes([], "", run_index)),
            )
    else:
        answer = str(last_assistant or "")

    payload = {
        "run": int(run_index),
        "userMessages": selected,
        "lastAssistant": answer,
    }
    if omitted > 0:
        payload["omittedMessages"] = int(omitted)
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def _extract_json_object(text: str) -> str:
    """从模型输出中提取最外层 JSON 对象文本（容忍围栏 / 前后说明）。"""
    stripped = _FENCE_RE.sub("", str(text or "")).strip()
    if not stripped:
        return ""
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start < 0 or end <= start:
        return ""
    return stripped[start:end + 1]


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("true", "1", "yes", "y", "on", "是", "继续"):
            return True
        if text in ("false", "0", "no", "n", "off", "否", "停止", "完成"):
            return False
    return None


def _first_present(data: dict, keys: Sequence[str]) -> Any:
    for key in keys:
        if key in data:
            return data[key]
    return None


def parse_loop_prompt_response(raw: object) -> LoopPromptDecision | None:
    """把模型输出解析成 :class:`LoopPromptDecision`（失败返回 ``None``）。

    容忍：代码围栏、前后说明、字段别名（``continue`` / ``should_continue`` /
    ``是否继续``，``prompt`` / ``next_prompt`` / ``继续提词``）、布尔字符串。
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    candidate = _extract_json_object(text)
    if not candidate:
        return None
    try:
        data = json.loads(candidate)
    except Exception:
        _logger.debug("续跑提词 JSON 解析失败: %r", candidate[:200])
        return None
    if not isinstance(data, dict):
        return None

    continue_raw = _first_present(
        data,
        ("continue", "continue_", "should_continue", "shouldContinue", "是否继续"),
    )
    should_continue = _as_bool(continue_raw)
    if should_continue is None:
        return None

    prompt_raw = _first_present(
        data,
        ("prompt", "next_prompt", "nextPrompt", "继续提词", "提词"),
    )
    prompt = str(prompt_raw or "").strip()
    prompt = prompt.removeprefix(LOOP_PROMPT_MARKER).strip()
    prompt = _WRAPPING_RE.sub("", prompt).strip()
    if should_continue:
        if not prompt:
            return None
    else:
        prompt = ""
    return LoopPromptDecision(
        should_continue=bool(should_continue),
        prompt=prompt,
        raw=text,
    )


__all__ = [
    "LOOP_PROMPT_MARKER",
    "LoopPromptDecision",
    "SYSTEM_TEMPLATE",
    "build_loop_prompt_messages",
    "build_system_prompt",
    "human_user_texts",
    "is_human_user_text",
    "last_assistant_text",
    "parse_loop_prompt_response",
    "select_user_messages_within_budget",
]
