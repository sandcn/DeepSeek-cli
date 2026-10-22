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
输入与输出均**不做字节大小限制**（2026-10-22 用户需求）：用户消息与回答全量
发送，模型生成的提词也不在本地截断。

系统指令文本存放在 ``prompts/prompts_loop_prompt.md``，每次构建时读取
（热加载，改完下一次 /loop 即生效）；文件缺失 / 不可读 / 为空时抛
``LoopPromptLoadError``，由 /loop 按失败策略结束（不发送空系统提词）。

判定**只以模型返回的 JSON 为准**：``continue`` 为 ``false`` 时直接结束，不做任何
本地启发式改写（2026-10-22 用户需求：删除借口兜底等本地判定）。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

_logger = logging.getLogger(__name__)

#: 续跑提词在会话中的标记前缀（用于区分机器注入消息与人类输入）。
#:
#: 生成提词最终会作为 user 消息进入会话（``session.run_round``），加一个短标记
#: 既让用户/日志可辨认，也让会话标题服务把它排除出「人类消息」。
LOOP_PROMPT_MARKER = "【自动继续】"

#: ``/loop`` 系统提词文件（项目根 ``prompts/``；与主 Agent 提词同目录）。
_PROMPTS_DIR = Path(__file__).resolve().parents[3] / "prompts"
_SYSTEM_PROMPT_FILENAME = "prompts_loop_prompt.md"


class LoopPromptLoadError(RuntimeError):
    """/loop 系统提词文件缺失、不可读或为空（报错并结束本次 loop）。"""


def _system_prompt_path() -> Path:
    """系统提词文件路径（模块级目录，可在测试中替换 ``_PROMPTS_DIR``）。"""
    return _PROMPTS_DIR / _SYSTEM_PROMPT_FILENAME


def load_system_prompt() -> str:
    """从 ``prompts/prompts_loop_prompt.md`` 读取系统提词（每次读取，热加载）。

    Raises:
        LoopPromptLoadError: 文件缺失 / 读取失败 / 内容为空。
    """
    path = _system_prompt_path()
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise LoopPromptLoadError(
            f"读取 /loop 系统提词失败：{path}（{exc}）"
        ) from exc
    if not text:
        raise LoopPromptLoadError(f"/loop 系统提词为空：{path}")
    return text


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


def build_system_prompt() -> str:
    """构建续跑提词模型的系统指令（每次从 ``prompts/`` 热加载）。

    Raises:
        LoopPromptLoadError: 系统提词文件缺失 / 读取失败 / 为空。
    """
    return load_system_prompt()


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


def build_loop_prompt_messages(
    user_messages: Sequence[str],
    last_assistant: str,
    *,
    run_index: int,
) -> list[dict]:
    """构建续跑提词请求（system + user 两条消息）。

    **输入不做任何字节预算**：全部人类用户消息与最后一次回答原样发送
    （由用户需求 2026-10-22 确认；超出模型上下文时由 API 报错，按
    ``/loop`` 失败策略处理）。

    Args:
        user_messages: 人类用户消息（按时间顺序）。
        last_assistant: 最后一次有内容的 assistant 回答。
        run_index: 当前已完成运行序号（1-based，供模型理解进度）。

    Returns:
        ``[{"role": "system", ...}, {"role": "user", ...}]``。
    """
    payload = {
        "run": int(run_index),
        "userMessages": [str(item or "") for item in user_messages],
        "lastAssistant": str(last_assistant or ""),
    }
    return [
        {"role": "system", "content": build_system_prompt()},
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
    "LoopPromptLoadError",
    "build_loop_prompt_messages",
    "build_system_prompt",
    "human_user_texts",
    "is_human_user_text",
    "last_assistant_text",
    "parse_loop_prompt_response",
]
