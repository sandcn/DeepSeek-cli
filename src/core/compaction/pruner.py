"""工具结果剪枝 — 对齐 dsh ``compaction-tool-result-pruner``。

无模型的确定性头/中/尾剪枝：超过字符阈值的工具结果只保留头部 N 个
字符与尾部 M 个字符，被移除的中间段替换为固定标记。文本切片按 Unicode
码点进行（不拆代理对）。

剪枝在压缩触发条件满足后运行；当剪枝后的对话已在阈值之内时完全跳过摘要
（不发起模型调用）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import (
    DEFAULT_PRUNE_HEAD_CHARS,
    DEFAULT_PRUNE_TAIL_CHARS,
    DEFAULT_PRUNE_THRESHOLD_CHARS,
)

#: 被移除的中间段替换标记（与 dsh 原文一致）。
PRUNE_MARKER = "\n\n[... tool result middle pruned ...]\n\n"


@dataclass
class PruneConfig:
    """剪枝预算。"""

    threshold_chars: int = DEFAULT_PRUNE_THRESHOLD_CHARS
    head_chars: int = DEFAULT_PRUNE_HEAD_CHARS
    tail_chars: int = DEFAULT_PRUNE_TAIL_CHARS


@dataclass
class PrunedEntry:
    """一次工具结果替换的记账。"""

    index: int = 0
    chars_before: int = 0
    chars_after: int = 0


@dataclass
class PruneOutcome:
    """一次剪枝遍历的聚合结果。"""

    pruned: list = field(default_factory=list)
    chars_removed: int = 0

    def __post_init__(self) -> None:
        self.pruned = self.pruned or []


def code_point_length(text: str) -> int:
    """按 Unicode 码点计数（不拆代理对）。"""
    return len(text)


def prune_text(text: str, config: PruneConfig) -> str | None:
    """超预算文本的头/中/尾剪枝；未超预算返回 None。

    Raises:
        ValueError: 预算配置无法产出更小的结果（防御性）。
    """
    if not isinstance(text, str):
        return None
    total = code_point_length(text)
    if total <= config.threshold_chars:
        return None

    removed_start = config.head_chars
    removed_end = total - config.tail_chars
    points = list(text)
    head = "".join(points[:removed_start])
    tail = "".join(points[removed_end:]) if removed_end < total else ""
    pruned = head + PRUNE_MARKER + tail
    if code_point_length(pruned) >= total:
        raise ValueError("工具结果剪枝：替换结果必须小于原文")
    return pruned


def prune_message_content(content, config: PruneConfig):
    """按内容形态剪枝（str 或 OpenAI 兼容 content blocks list）。

    - ``str``：整体头/中/尾剪枝；
    - ``list``：文本块按块内字符累计预算剪枝（跨块累计，保证头/尾预算
      对齐原文的连续字符区间）；非文本块保持原顺序原样保留。

    Returns:
        剪枝后的 content；未超预算返回 None。
    """
    if isinstance(content, str):
        return prune_text(content, config)
    if not isinstance(content, list):
        return None

    total = sum(
        code_point_length(block.get("text", ""))
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    )
    if total <= config.threshold_chars:
        return None

    removed_start = config.head_chars
    removed_end = total - config.tail_chars
    pruned: list = []
    consumed = 0
    marker_inserted = False
    for block in content:
        if not (isinstance(block, dict) and block.get("type") == "text"):
            pruned.append(block)
            continue
        points = list(str(block.get("text", "")))
        block_start = consumed
        block_end = block_start + len(points)
        head_end = min(len(points), max(0, removed_start - block_start))
        tail_start = min(len(points), max(0, removed_end - block_start))
        intersects = block_start < removed_end and block_end > removed_start
        marker = PRUNE_MARKER if (intersects and not marker_inserted) else ""
        if marker:
            marker_inserted = True
        text = "".join(points[:head_end]) + marker + "".join(points[tail_start:])
        if text:
            new_block = dict(block)
            new_block["text"] = text
            pruned.append(new_block)
        consumed = block_end

    if not marker_inserted:
        return None
    return pruned


def prune_messages(messages: list, config: PruneConfig) -> PruneOutcome:
    """就地把当前消息列表中超预算的工具结果替换为剪枝版本。

    仅处理 ``role == "tool"`` 的消息（工具结果），保持其顺序不变。
    返回的 ``PrunedEntry.index`` 为消息在列表中的原始索引。
    """
    outcome = PruneOutcome()
    for index, message in enumerate(messages):
        if not isinstance(message, dict) or message.get("role") != "tool":
            continue
        original = message.get("content")
        if isinstance(original, str):
            before = code_point_length(original)
        elif isinstance(original, list):
            before = sum(
                code_point_length(b.get("text", ""))
                for b in original if isinstance(b, dict) and b.get("type") == "text"
            )
        else:
            continue
        pruned = prune_message_content(original, config)
        if pruned is None:
            continue
        after = (code_point_length(pruned) if isinstance(pruned, str)
                 else sum(code_point_length(b.get("text", ""))
                          for b in pruned if isinstance(b, dict) and b.get("type") == "text"))
        message["content"] = pruned
        outcome.pruned.append(PrunedEntry(index=index, chars_before=before, chars_after=after))
        outcome.chars_removed += max(0, before - after)
    return outcome


__all__ = [
    "PRUNE_MARKER",
    "PruneConfig",
    "PrunedEntry",
    "PruneOutcome",
    "code_point_length",
    "prune_text",
    "prune_message_content",
    "prune_messages",
]
