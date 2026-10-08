"""压缩能力公共类型词汇 — 对齐 DeepSeek Harness 的 compaction seam。

- ``CompactionTrigger`` 对应 dsh ``CompactionTrigger``（``pressure`` /
  ``context-overflow``）：说明自动策略为何要求后端考虑压缩。
- ``ManualCompactionErrorCode`` / ``ManualCompactionError`` 对应 dsh
  ``ManualCompactionErrorCode`` 与 ``ManualCompactionError``：显式空闲会话
  压缩请求的预期失败分类。
- ``CompactionResult`` 对应 dsh ``CompactionResult``：一次成功压缩的记账
  （被遮蔽条数与 token、摘要、替换位置）。
"""

from __future__ import annotations

from dataclasses import field
from enum import Enum

from src._compat import dataclass


class CompactionTrigger(str, Enum):
    """自动策略请求后端考虑压缩的原因。"""

    PRESSURE = "pressure"
    CONTEXT_OVERFLOW = "context-overflow"


class ManualCompactionErrorCode(str, Enum):
    """显式空闲会话压缩请求的预期失败分类。"""

    BUSY = "busy"
    CANCELLED = "cancelled"
    CHANGED = "changed"
    SUMMARY = "summary"
    COMMIT = "commit"
    PERSISTENCE = "persistence"


class ManualCompactionError(Exception):
    """手动压缩的预期失败（``code`` 标明分类，供人读命令给出对应提示）。"""

    def __init__(self, code, message: str, cause: BaseException | None = None) -> None:
        super().__init__(message)
        self.code = code if isinstance(code, ManualCompactionErrorCode) else ManualCompactionErrorCode(code)
        self.message = message
        if cause is not None:
            self.__cause__ = cause


@dataclass(slots=True)
class CompactionResult:
    """一次压缩操作的结果。

    Attributes:
        success: 是否成功完成摘要替换。
        trigger: 触发来源（``pressure`` / ``context-overflow`` / ``manual``）。
        removed_indices: 被替换的历史消息原始索引（升序）。
        inserted_index: 检查点消息插入位置（-1 表示未插入）。
        summary: 摘要文本。
        shadowed_count: 被遮蔽的历史条数。
        shadowed_tokens: 被遮蔽内容的估算 token。
        summary_tokens: 检查点消息的估算 token。
        saved_tokens: 释放的估算 token（被遮蔽 token - 检查点 token）。
        chars_saved: 释放的字符数。
        pruned_count: 本操作顺带剪枝的工具结果条数。
        pruned_chars: 剪枝移除的字符数。
        elapsed: 摘要调用耗时（秒）。
        usage: 摘要调用的模型 usage。
        stats: 附加统计。
    """

    success: bool = True
    trigger: str = ""
    removed_indices: list = field(default_factory=list)
    inserted_index: int = -1
    summary: str = ""
    shadowed_count: int = 0
    shadowed_tokens: int = 0
    summary_tokens: int = 0
    saved_tokens: int = 0
    chars_saved: int = 0
    pruned_count: int = 0
    pruned_chars: int = 0
    elapsed: float = 0.0
    usage: dict = field(default_factory=dict)
    stats: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.removed_indices = self.removed_indices or []
        self.usage = self.usage or {}
        self.stats = self.stats or {}


@dataclass(slots=True)
class SummaryResult:
    """一次摘要模型调用的产出。"""

    summary: str = ""
    usage: dict = field(default_factory=dict)
    provider: str = ""
    model: str = ""
    max_tokens: int = 0

    def __post_init__(self) -> None:
        self.usage = self.usage or {}


@dataclass(slots=True)
class PruneResult:
    """一次工具结果剪枝遍历的聚合产出。"""

    pruned: list = field(default_factory=list)
    chars_removed: int = 0

    def __post_init__(self) -> None:
        self.pruned = self.pruned or []


__all__ = [
    "CompactionTrigger",
    "ManualCompactionErrorCode",
    "ManualCompactionError",
    "CompactionResult",
    "SummaryResult",
    "PruneResult",
]
