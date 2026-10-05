"""轨迹记录数据模型（从 trace.py 拆分）。

``TraceRecord``（台账行 + 检查器详情数据源）、记录种类顺序、块种类映射。
纯数据定义，供 ``trace``（构建）/``trace_view``（渲染）等共享。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.declarative import LiveSequence
from src.presentation_data import LiveMapping, trace_kind_order

#: 记录种类（展示顺序；「一切皆插件」：实时委托表现层数据注册表
#: ``presentation_data`` → ``trace_kind_order`` 表，可按 Patch/Overlay 覆盖/禁用）
TRACE_KIND_ORDER = LiveSequence(trace_kind_order)

#: 块种类 → 轨迹记录种类（separator/splash 跳过——非业务记录）；
#: 「一切皆插件」：实时委托 ``presentation_data`` → ``trace_block_kind`` 表。
_BLOCK_KIND_MAP = LiveMapping("trace_block_kind")


@dataclass
class TraceRecord:
    """一条轨迹记录（台账行 + 检查器详情的数据源）。

    Attributes:
        index: 1-based 记录号（#N）。
        kind: 记录种类（system/user/reasoning/content/tool/subagent/context）。
        summary: 单行摘要（台账行主文本）。
        status: 状态（tool/subagent：running/done/fail/error；其余空串）。
        time_seconds: 耗时秒数；None=未知（运行中为构建时快照）。
        time_started: 运行中起始时间戳（渲染层实时计算耗时用）。
        time_started_monotonic: time_started 时间基准（True=单调时钟）。
        tokens: token 统计 dict。
        result: 工具返回首行预览。
        lines: 详情行（纯文本；system/subagent 记录内联携带）。
        source_block: 来源 ChatBlock（块记录惰性提取详情用；否则 None）。
        subagent_label: 关联 subagent label（Enter 进入其轨迹用）。
        tool_call_id: 工具调用唯一 ID。
        tool_args: 工具调用原始参数（str JSON 或 dict；None=无）。
        tool_result: 工具返回原始文本。
        images: 多模态图片元信息列表（缩略图渲染用）。
    """

    index: int = 0
    kind: str = "context"
    summary: str = ""
    status: str = ""
    time_seconds: float | None = None
    time_started: float | None = None
    time_started_monotonic: bool = True
    tokens: dict = field(default_factory=dict)
    result: str = ""
    lines: list = field(default_factory=list)
    source_block: object | None = None
    subagent_label: str = ""
    tool_call_id: str = ""
    tool_args: object = None
    tool_result: str = ""
    images: list = field(default_factory=list)


__all__ = ["TRACE_KIND_ORDER", "TraceRecord", "_BLOCK_KIND_MAP"]
