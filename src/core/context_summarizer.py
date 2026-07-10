#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""上下文摘要生成模块

负责构建摘要 prompt 并调用模型生成结构化摘要。
"""

import random
import time as _time

from ..config import SUMMARY_TOKEN_BUDGET
from .context_selector import message_to_text

_SUMMARY_SYSTEM = """\
【角色定位】你是对话压缩助手，负责将对话历史压缩为结构化摘要。
【核心目标】保留所有可操作信息，使后续对话能无缝继续。
【保留优先级】（从高到低）：
1. 文件路径、函数签名、行号、类名、变量名
2. 错误信息、异常堆栈、失败原因及修复方式
3. 已执行的命令和关键输出（成功/失败状态）
4. 决策理由、已排除的方案及排除原因
5. 讨论过程、推测性内容（仅保留结论）
【约束条件】
- 禁止遗漏具体的文件路径、函数签名、配置值等硬性信息
- 禁止添加原对话中不存在的内容
- 不编造、不推测未明确的信息
"""

_SUMMARY_TEMPLATE = """\
将以下对话压缩为结构化摘要。

**保留优先级**（从高到低）：
1. 文件路径、函数签名、行号、类名、变量名
2. 错误信息、异常堆栈、失败原因及修复方式
3. 已执行的命令和关键输出（成功/失败状态）
4. 决策理由、已排除的方案及排除原因
5. 待完成的任务和阻塞项
6. 讨论过程、推测性内容（仅保留结论）

**可省略**：重复确认、寒暄、过程中的试错细节（保留最终有效的方法）

**长度**：不超过原对话长度的 30%

**输出格式**（仅包含有内容的部分，省略空部分）：
- **目标** → 用户要做什么（一句话）
- **已完成** → 已做的操作和关键结果（标注文件路径和具体改动）
- **修改** → 修改的文件及具体改动（含行号或函数名、修改前后的差异）
- **待办** → 未完成的任务（含优先级和阻塞原因）
- **上下文** → 后续对话必须知道的背景（决策理由、约束条件、已排除的方案、关键配置值）

{prior_hint}--- 对话内容 ---
{conversation}"""

_PRIOR_HINT_MERGE = (
    "注意：对话中包含之前的摘要（标记为 [对话摘要]），"
    "请将其与新内容整合为一份完整摘要。\n\n"
)


def build_summary_prompt(messages_to_compress, has_prior_summary):
    """构建结构化摘要 prompt。"""
    n = len(messages_to_compress)
    if n == 0:
        return ""

    budget_chars = int(SUMMARY_TOKEN_BUDGET * 1.5)
    per_msg = max(200, budget_chars // n)

    lines = []
    for msg in messages_to_compress:
        role = msg.get("role", "unknown")
        text = message_to_text(msg)

        text = text[:per_msg]

        lines.append(f"{role}: {text}")

    conversation = "\n".join(lines)
    prior_hint = _PRIOR_HINT_MERGE if has_prior_summary else ""

    return _SUMMARY_TEMPLATE.format(
        prior_hint=prior_hint,
        conversation=conversation,
    )


def summarize(messages_to_compress, has_prior_summary, summarize_fn, model):
    """生成摘要文本（同步函数）。

    由 ContextManager 的策略链中的 SummarizeStrategy.compress() 同步调用，
    compress() 本身是同步方法（持 ContextManager._lock 执行），因此
    summarize() 必须为同步函数。重试退避使用 time.sleep() 替代 asyncio.sleep()。

    Args:
        messages_to_compress: 需要压缩的消息列表
        has_prior_summary: 是否包含旧摘要
        summarize_fn: 模型调用函数，签名 (messages, model=) -> (reasoning, content, usage, tool_calls)
        model: 模型名称

    Returns:
        tuple: (摘要文本, usage字典)

    Raises:
        Exception: 摘要生成失败时抛出
    """
    prompt = build_summary_prompt(messages_to_compress, has_prior_summary)
    if not prompt:
        raise ValueError("没有可压缩的消息")

    import logging as _logging
    _summary_logger = _logging.getLogger(__name__)
    last_exception = None
    for attempt in range(2):  # 最多重试1次
        try:
            _, summary, usage, _ = summarize_fn(
                [{"role": "system", "content": _SUMMARY_SYSTEM},
                 {"role": "user", "content": prompt}],
                model=model,
            )
            if summary and len(summary.strip()) >= 10:
                return summary.strip(), usage
            last_exception = ValueError(f"摘要内容无效（长度={len(summary.strip()) if summary else 0}）")
        except Exception as e:
            last_exception = e
            if attempt == 0:
                _summary_logger.warning("摘要生成失败，重试中: %s", e)
                # 指数退避：初始0.5s，每次翻倍，最大4s，加30%随机抖动防惊群
                backoff = min(0.5 * (2 ** attempt), 4.0)
                jitter = random.uniform(0, backoff * 0.3)  # 30% 随机抖动
                sleep_time = backoff + jitter
                _time.sleep(sleep_time)
            continue
    raise last_exception or ValueError("摘要生成失败")
