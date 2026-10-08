"""上下文压缩能力包 — 对齐 DeepSeek Harness（dsh）的 compaction seam。

本包把 dsh 的压缩机制完整移植到本项目，结构与职责一一对应：

- ``types``       —— 结果类型、触发来源、手动失败分类（对应 dsh ``compaction/types``）
- ``config``      —— 阈值/保留策略解析（对应 dsh ``compaction-basic/config``）
- ``checkpoint``  —— 结构化检查点指令与框定（对应 dsh ``compaction-basic/summarizer``）
- ``summarizer``  —— 一次性摘要调用（回放前缀 + 压缩指令）
- ``region``      —— 保留尾部范围选择与工具配对平衡边界（对应 ``compaction-basic/region``）
- ``pruner``      —— 无模型的工具结果头/中/尾剪枝（对应 ``compaction-tool-result-pruner``）
- ``events``      —— 压缩过程的显示事件发布（TUI 显示）
- ``engine``      —— 压缩引擎（自动压力/溢出恢复/手动三种入口）

对外入口为 ``CompactionEngine``；``ContextManager`` 持有引擎并驱动它，
主 Agent 与全部 SubAgent 共用同一套能力。
"""

from __future__ import annotations

from .types import (
    CompactionResult,
    CompactionTrigger,
    ManualCompactionError,
    ManualCompactionErrorCode,
    PruneResult,
    SummaryResult,
)
from .config import (
    CompactionConfig,
    CompactionPolicy,
    ResolvedCompactSpec,
    TargetPressureConfigError,
    resolve_compact_spec,
    resolve_config,
    resolve_target_policy,
)
from .checkpoint import (
    CHECKPOINT_PREAMBLE,
    COMPACTION_INSTRUCTION,
    SUMMARY_CLOSE_TAG,
    SUMMARY_OPEN_TAG,
    build_checkpoint_message,
    frame_summary,
    is_checkpoint_message,
)
from .engine import CompactionEngine

__all__ = [
    "CompactionResult",
    "CompactionTrigger",
    "ManualCompactionError",
    "ManualCompactionErrorCode",
    "PruneResult",
    "SummaryResult",
    "CompactionConfig",
    "CompactionPolicy",
    "ResolvedCompactSpec",
    "TargetPressureConfigError",
    "resolve_compact_spec",
    "resolve_config",
    "resolve_target_policy",
    "CHECKPOINT_PREAMBLE",
    "COMPACTION_INSTRUCTION",
    "SUMMARY_CLOSE_TAG",
    "SUMMARY_OPEN_TAG",
    "build_checkpoint_message",
    "frame_summary",
    "is_checkpoint_message",
    "CompactionEngine",
]
