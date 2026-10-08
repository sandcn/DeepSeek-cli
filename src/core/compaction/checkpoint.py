"""结构化检查点 — 对齐 dsh ``compaction-basic/summarizer``。

压缩模型收到一条**固定的最后 user 消息**（压缩指令），要求输出严格的
八段 Markdown 检查点；返回的纯文本被 ``<compacted-summary>`` 标签框定，
并前置一段「这是既有背景、请直接续接」的导言，作为替换历史的消息。

指令作为最后一条 user 消息（而非独立 system 提词）意味着回放的历史 +
系统提词 + 工具声明构成真正的前缀，提供方 KV 缓存得以复用。
"""

from __future__ import annotations

import re

#: 摘要块框定标签（识别既有检查点的唯一真源）。
SUMMARY_OPEN_TAG = "<compacted-summary>"
SUMMARY_CLOSE_TAG = "</compacted-summary>"

#: 兼容识别旧版摘要前缀（历史会话以 system 消息承载 ``[对话摘要] ...``）。
LEGACY_SUMMARY_PREFIX = "[对话摘要]"

#: 压缩指令（作为最后一条 user 消息交付给摘要模型）。
COMPACTION_INSTRUCTION = "\n".join([
    "You are now acting as a compaction engine for this AI coding assistant. "
    "Condense the conversation ABOVE into a structured checkpoint that lets "
    "another model resume the work with no loss of essential context.",
    "",
    "Output EXACTLY the Markdown structure below: keep every section, in order. "
    "Use terse bullets, not prose paragraphs. Write \"(none)\" for an empty "
    "section — never drop a section.",
    "",
    "## Primary Request and Intent",
    "- [the user's original and evolving goals; quote verbatim where the exact wording matters]",
    "",
    "## Key Technical Concepts",
    "- [technologies, frameworks, patterns, and conventions in play]",
    "",
    "## Files and Code",
    "- [exact path: why it matters, key changes or snippets]",
    "",
    "## Errors and Fixes",
    "- [error: how it was resolved, plus any related user feedback]",
    "",
    "## Pending Jobs",
    "- [explicitly requested work not yet completed]",
    "",
    "## Current Work",
    "- [precisely what was in progress at this checkpoint]",
    "",
    "## Next Step",
    "- [the single next action, directly in line with the most recent request, or \"(none)\"]",
    "",
    "## Critical Context",
    "- [decisions and their rationale, constraints, user preferences, open questions, data needed to continue]",
    "",
    "Rules:",
    "- Write concise engineering prose. Preserve exact file paths, commands, error strings, "
    "identifiers, numeric values, function signatures, and syntax fragments.",
    "- Capture user feedback and explicit instructions faithfully, especially corrections.",
    "- Do NOT mention this summarization request or that the context was compacted.",
    "- Output only the checkpoint text: do not call any tool or take any other action.",
    f"- If the conversation already contains a {SUMMARY_OPEN_TAG} block, it is a PRIOR "
    "checkpoint. Do not copy it forward verbatim: preserve still-true facts, drop stale "
    "ones, and merge newer information into a single consolidated summary under the same "
    "structure.",
])

#: 检查点导言（让替换消息成为「既已建立的背景」而非新指令）。
CHECKPOINT_PREAMBLE = (
    "This is an automatically generated checkpoint condensing an earlier span of the "
    "conversation to free up context. Treat the captured context as established "
    "background and build on it without restating it. Continue the task directly from "
    "the messages that follow, without acknowledging this checkpoint."
)

_SUMMARY_RE = re.compile(
    re.escape(SUMMARY_OPEN_TAG) + r"(.*?)" + re.escape(SUMMARY_CLOSE_TAG),
    re.DOTALL,
)


def frame_summary(summary: str) -> str:
    """把纯文本摘要包成落地检查点的内容（导言 + 标签框定）。"""
    return f"{CHECKPOINT_PREAMBLE}\n\n{SUMMARY_OPEN_TAG}\n{summary.strip()}\n{SUMMARY_CLOSE_TAG}"


def build_checkpoint_message(summary: str) -> dict:
    """构建替换历史的检查点消息（user 角色，与 dsh 一致）。"""
    return {"role": "user", "content": frame_summary(summary)}


def is_checkpoint_message(message: dict) -> bool:
    """消息是否为压缩检查点（新格式 ``<compacted-summary>`` 或旧版 ``[对话摘要]``）。"""
    if not isinstance(message, dict):
        return False
    content = message.get("content")
    if not isinstance(content, str):
        return False
    if SUMMARY_OPEN_TAG in content:
        return True
    return content.startswith(LEGACY_SUMMARY_PREFIX)


def is_legacy_summary_message(message: dict) -> bool:
    """消息是否为旧版摘要（``[对话摘要]`` system 消息）。"""
    if not isinstance(message, dict) or message.get("role") != "system":
        return False
    content = message.get("content")
    return isinstance(content, str) and content.startswith(LEGACY_SUMMARY_PREFIX)


def extract_summary(content: str) -> str:
    """从检查点内容中取出摘要正文（无标签时返回原文去空白）。"""
    if not isinstance(content, str):
        return ""
    match = _SUMMARY_RE.search(content)
    if match is not None:
        return match.group(1).strip()
    if content.startswith(LEGACY_SUMMARY_PREFIX):
        return content[len(LEGACY_SUMMARY_PREFIX):].strip()
    return content.strip()


__all__ = [
    "SUMMARY_OPEN_TAG",
    "SUMMARY_CLOSE_TAG",
    "LEGACY_SUMMARY_PREFIX",
    "COMPACTION_INSTRUCTION",
    "CHECKPOINT_PREAMBLE",
    "frame_summary",
    "build_checkpoint_message",
    "is_checkpoint_message",
    "is_legacy_summary_message",
    "extract_summary",
]
