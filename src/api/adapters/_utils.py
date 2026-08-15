"""适配器层公共工具函数"""
from __future__ import annotations

import logging
from typing import Optional

# ── 推理模型名称匹配模式 ──────────────────────────────────
_REASONER_PATTERNS: frozenset[str] = frozenset({"reasoner"})

# ── V4 模型检测 ─────────────────────────────────────────
_V4_PREFIX = "deepseek-v4"


def is_deepseek_v4_model(model: str) -> bool:
    """判断模型是否为 DeepSeek V4 系列（deepseek-v4-*）。

    V4 模型使用 thinking mode，需要在 API 请求中注入 thinking 参数。
    """
    return model.startswith(_V4_PREFIX)


def ensure_reasoning_content(messages: list, model: Optional[str] = None) -> list:
    """确保消息列表中所有 assistant 消息的 reasoning_content 字段正确。

    这是网络边界处的最终防御层——无论上游代码是否正确设置了
    reasoning_content，消息在发往 API 前都会被修复。

    DeepSeek V4 thinking mode 的推理内容校验规则（2026-04-30 最终修正）：
    - **所有** assistant 消息的 `reasoning_content` key **必须存在**
    - `reasoning_content` 值必须是字符串类型
    - 不含 tool_calls 的消息：`reasoning_content` 可以是空字符串
    - 含 tool_calls 的消息：`reasoning_content` 也可以是空字符串
    """
    _log = logging.getLogger(__name__)
    for i, msg in enumerate(messages):
        if msg.get("role") != "assistant":
            continue
        rc = msg.get("reasoning_content")
        if "reasoning_content" not in msg:
            # ① key 缺失 → 补空字符串
            msg["reasoning_content"] = ""
            _log.debug("assistant[%d] 缺失 reasoning_content，已补空字符串", i)
        elif rc is None:
            # ② key 存在但值为 None → 补空字符串
            msg["reasoning_content"] = ""
            _log.debug("assistant[%d] reasoning_content 修复: None → ''", i)
        elif not isinstance(rc, str):
            # ③ key 存在且值非 str 类型 → 类型转换为 str
            msg["reasoning_content"] = str(rc)
            _log.debug("assistant[%d] reasoning_content 类型转换: %s → str",
                       i, type(rc).__name__)
        # ④ key 存在且值是 str → 保留原值，不做任何操作
    return messages


def ensure_tool_response_complete(messages: list, model: Optional[str] = None) -> list:
    """确保消息历史中 assistant 的 tool_calls 与 role=tool 响应一一配对。

    这是网络边界处的最终防御层，修复下述 API 400 错误：
      "An assistant message with 'tool_calls' must be followed by tool messages
       responding to each 'tool_call_id'"

    触发场景：消息历史中存在带 tool_calls 的 assistant 消息，但后续缺少
    对应 tool_call_id 的 role=tool 响应消息（例如 ToolScheduler 调度结果
    缺失、上下文压缩删除、消息编辑等）。OpenAI / DeepSeek 等 provider 会
    严格校验并拒绝这类历史。

    修复策略：对每个「已声明但未收到响应」的 tool_call_id，紧跟其所属
    assistant 消息之后补发一条占位 tool 消息（内容标记为已丢弃），保证
    消息序列可被 API 接受。不删除任何已有消息，语义损失最小。

    注意：
    - 本函数返回修复后的**新列表**，不修改传入列表；调用方可安全传入
      deepcopy 副本（model_async 中已 copy.deepcopy）或原始列表。
    - 空字符串 tool_call_id 不补发：无法确定配对关系，且补发空 id 的
      tool 消息可能引入新的格式问题。
    """
    _log = logging.getLogger(__name__)

    # 第一遍：收集所有已存在的 tool 响应 id
    responded: set[str] = set()
    for msg in messages:
        if msg.get("role") == "tool":
            tid = msg.get("tool_call_id")
            if tid:
                responded.add(tid)

    # 第二遍：逐条检查 assistant tool_calls，缺失响应的紧跟其后补发占位消息
    repaired = 0
    result: list[dict] = []
    for msg in messages:
        result.append(msg)
        if msg.get("role") != "assistant":
            continue
        tcs = msg.get("tool_calls")
        if not tcs:
            continue
        for tc in tcs:
            tid = (tc.get("id") or "").strip()
            if tid and tid not in responded:
                result.append({
                    "role": "tool",
                    "tool_call_id": tid,
                    "content": "（工具响应缺失：该工具调用结果已被丢弃）",
                })
                responded.add(tid)
                repaired += 1

    if repaired:
        _log.warning(
            "修复消息历史: 为 %d 个缺失响应的 tool_call 补发占位 tool 消息",
            repaired,
        )
    return result
