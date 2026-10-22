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

模型判定「无需继续」时，本模块还会做一次**借口兜底**：当用户诉求属于改动类、
而最后回答里出现未完成 / 受阻 / 推诿措辞（该句没有完成标志）时，强制改判
``continue=true`` 并生成直接续跑提词——不接受用理由、借口或推迟替代实际完成。
纯问答 / 解释 / 分析类任务不受此兜底影响，仍按「问题是否已被完整回答」判定。
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


# ── 借口兜底：改动类任务不接受「以理由替代完成」 ──────────────

#: 强改动动词（单独出现即可判定为「要求改动 / 产出」）。
_CHANGE_ACTION_STRONG_RE = re.compile(
    r"(实现|新增|增加|添加|删除|移除|重构|修复|替换|补齐|补上|补测试|优化|完善|"
    r"升级|迁移|接入|修改|改成|改为|改一下|改改|重命名|格式化|跑测试|修正|修一下)"
)
#: 弱改动动词（需与改动对象共现才算改动类）。
_CHANGE_ACTION_WEAK_RE = re.compile(
    r"(写|生成|同步|更新|验证|支持|适配|调整|落地|加|删|改|跑)"
)
#: 改动 / 产出对象。
_CHANGE_OBJECT_RE = re.compile(
    r"(代码|文件|模块|函数|类|方法|接口|配置|脚本|测试|样式|资源|命令|功能|"
    r"bug|缺陷|文档|README|日志|流程|页面|组件|服务|端口|依赖|逻辑|用例|"
    r"提词|提示词|字段|参数|变量|缓存|数据库|表|查询|路由|样式表)"
)
#: 问答 / 解释 / 分析型诉求开头。
_QUESTION_ONLY_RE = re.compile(
    r"^(请)?\s*(解释|说明|介绍|分析|讲解|为什么|为何|是什么|什么是|怎么理解|"
    r"如何理解|对比|评估|审查|review|看看|看一下|阅读|总结|聊聊|查一下|有哪些)"
)
#: 「明确要求动手」的前缀。
_CHANGE_PREFIX_RE = re.compile(
    r"(帮我|帮忙|请|直接|立即|马上)\s*(修改|改成|实现|新增|添加|删除|移除|重构|"
    r"修复|替换|调整|补齐|补上|优化|完善|升级|迁移|接入|改)"
)
#: 疑问词（问答型诉求里出现即不按改动类处理）。
_QUESTION_WORD_RE = re.compile(r"(怎么|如何|为什么|为何|能否|是否|可不可以)")

#: 未完成 / 受阻 / 推诿措辞（逐句匹配，靠完成标志排除「已修复失败」这类复盘句）。
_DEFERRAL_RE = re.compile(
    r"(无法完成|未能完成|没完成|没有完成|未完成|尚未完成|还没完成|"
    r"无法实现|未能实现|没有实现|"
    r"没有修改|没做修改|未做修改|没有改动|未改动|没改代码|没有改代码|无法修改|"
    r"被阻塞|阻塞|卡住|卡在|"
    r"没权限|无权限|权限不足|缺少权限|"
    r"时间不够|来不及|太复杂|工作量太大|上下文不足|依赖缺失|环境不支持|"
    r"无法运行|无法验证|"
    r"稍后|后续再|下次再|以后再说|暂时跳过|先跳过|留待|待办|TODO|"
    r"已记录|已上报|提个 ?issue|开个 ?issue|记录一下|"
    r"只完成了|只改了一部分|部分完成|未验证|没有验证|未测试|没有测试|没跑测试)"
)
#: 完成标志：同一句出现即视为「已完成陈述」，不再当作未完成证据。
_COMPLETION_HINT_RE = re.compile(
    r"(已完成|全部完成|均已|已修复|已解决|已通过|测试通过|验证通过|已落盘|"
    r"已改好|已实现|已补齐)"
)
#: 否定语境（「没有阻塞 / 无异常」不代表未完成）——匹配前先从句子中剔除。
_NEGATED_DEFERRAL_RE = re.compile(
    r"(没有|无|未)\s*(阻塞|失败|报错|异常|问题)"
)


def is_change_request(user_messages: Sequence[str]) -> bool:
    """判断用户诉求是否属于「改动 / 产出类」任务。

    纯问答、解释、分析类任务返回 ``False``——``/loop`` 不因它们没有文件修改
    而无限循环；改动类任务则适用「必须有真实改动与验证证据」的完成标准。

    Args:
        user_messages: 人类用户消息（按时间顺序，已过滤机器注入）。

    Returns:
        任一条消息构成改动 / 产出诉求时为 ``True``。
    """
    for raw in user_messages or ():
        text = str(raw or "").strip()
        if not text:
            continue
        if _QUESTION_ONLY_RE.match(text):
            # 问答型：只有明确要求动手、或带强改动动词且不含疑问词才算改动类。
            if _CHANGE_PREFIX_RE.search(text):
                return True
            if (
                _CHANGE_ACTION_STRONG_RE.search(text)
                and not _QUESTION_WORD_RE.search(text)
            ):
                return True
            continue
        if _CHANGE_ACTION_STRONG_RE.search(text):
            return True
        if (
            _CHANGE_ACTION_WEAK_RE.search(text)
            and _CHANGE_OBJECT_RE.search(text)
        ):
            return True
    return False


def find_deferral_evidence(last_assistant: str) -> str:
    """返回最后回答里第一处「未完成 / 受阻 / 推诿」证据（无则空串）。

    逐句判断，跳过带完成标志的句子（如「已修复失败问题」），并剔除
    「没有阻塞 / 无异常」这类否定语境，避免把真正完成的回答误判为未完成。
    """
    text = str(last_assistant or "")
    if not text.strip():
        return ""
    for sentence in re.split(r"[。！？；\n]+", text):
        cleaned = sentence.strip()
        if not cleaned:
            continue
        cleaned = _NEGATED_DEFERRAL_RE.sub("", cleaned)
        if _COMPLETION_HINT_RE.search(cleaned):
            continue
        if _DEFERRAL_RE.search(cleaned):
            if len(cleaned) > 200:
                cleaned = cleaned[:200].rstrip() + "…"
            return cleaned
    return ""


def build_forced_continuation_prompt(evidence: str) -> str:
    """构造「不接受借口、直接继续」的续跑提词（供兜底改判使用）。"""
    detail = str(evidence or "").strip()
    if len(detail) > 200:
        detail = detail[:200].rstrip() + "…"
    head = "继续直接执行用户请求，不接受任何理由、借口或推诿。"
    tail = (
        "逐项核对文件是否已真实改动、流程是否已运行、测试是否已补齐并通过；"
        "未完成的立即继续做，完成后给出可核查的改动位置与验证结果。"
    )
    if detail:
        return f"{head}上一次回答中的未完成 / 受阻内容：「{detail}」。{tail}"
    return f"{head}{tail}"


def apply_deferral_guard(
    decision: LoopPromptDecision | None,
    *,
    user_messages: Sequence[str],
    last_assistant: str,
) -> LoopPromptDecision | None:
    """模型判「无需继续」时，按「不接受借口」规则做一次兜底改判。

    生效条件（三者同时成立）：模型输出 ``continue=false``、用户诉求属于改动类
    任务、最后回答中存在未完成 / 受阻 / 推诿证据且该句没有完成标志。命中时返回
    强制 ``continue=true`` 的新决策，否则返回 ``None``（保持模型判断）。

    Args:
        decision: 模型输出解析结果（``None`` 表示解析失败，不兜底）。
        user_messages: 人类用户消息。
        last_assistant: 最后一次有内容的 assistant 回答。
    """
    if decision is None or decision.should_continue:
        return None
    if not is_change_request(user_messages):
        return None
    evidence = find_deferral_evidence(last_assistant)
    if not evidence:
        return None
    return LoopPromptDecision(
        should_continue=True,
        prompt=build_forced_continuation_prompt(evidence),
        raw=decision.raw,
    )


__all__ = [
    "LOOP_PROMPT_MARKER",
    "LoopPromptDecision",
    "LoopPromptLoadError",
    "apply_deferral_guard",
    "build_forced_continuation_prompt",
    "build_loop_prompt_messages",
    "build_system_prompt",
    "find_deferral_evidence",
    "human_user_texts",
    "is_change_request",
    "is_human_user_text",
    "last_assistant_text",
    "parse_loop_prompt_response",
]
