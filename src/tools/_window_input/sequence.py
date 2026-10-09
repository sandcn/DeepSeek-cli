"""输入动作序列（``bash_opt`` 的 ``op=sequence`` 解析层）。

「操作一个应用」往往是一串连续动作：点击输入框 → 输入文本 → 回车 → 截图
核对。逐条调用工具不仅往返多，还容易在两次调用之间被其它窗口抢走焦点。
本模块把这种序列描述成一份**纯数据**清单，由工具层在同一轮调用里按序执行。

支持的步骤（每项一个 dict，``op`` 指定类型）：

  - 输入动作：``click`` / ``move`` / ``hover`` / ``drag`` / ``scroll`` /
    ``key`` / ``type``（参数与单独调用时一致，键名相同）；
  - ``wait``：等待一段时间（``seconds`` / ``settle``，秒，可为小数）；
  - ``screenshot``：把当前窗口截图存盘（``path`` 必填，可选 ``crop`` / ``grid``）；
  - ``window``：控制窗口（``window_action`` + 可选 ``x``/``y``/``width``/``height``）。

每个步骤都可以带 ``window``（覆盖块的默认窗口选择器）、``settle``（本步注入
后等待秒数）、``shot``（本步注入后自动截图）等参数；具体校验由各自的动作
构建器完成，本模块只做**结构校验**（是否为 dict、是否含 ``op``、步数上限）。

扩展方式：新增步骤类型只需在 :data:`SEQUENCE_KINDS` 登记并（在工具层）
补一个处理分支，其余解析规则不变。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .action import INPUT_OPS
from .result import ActionError

#: 序列里可用的步骤类型（输入动作 + 三种特殊步骤）
SEQUENCE_KINDS: tuple[str, ...] = (*INPUT_OPS, "wait", "screenshot", "window")

#: 一次序列允许的最大步数（防止误传超长序列长时间占用）
MAX_SEQUENCE_STEPS = 50

#: 单步 ``wait`` 的最大秒数（避免误传超大值长期挂住）
MAX_STEP_WAIT = 60.0


class SequenceError(ActionError):
    """序列结构非法（不是数组、步骤缺少 op、步数超限等）。"""


@dataclass(frozen=True)
class SequenceStep:
    """序列中的一步（纯数据，执行由工具层负责）。

    Attributes:
        index: 步序号（1 起）。
        kind: 步骤类型（:data:`SEQUENCE_KINDS` 之一）。
        params: 该步的参数（已去掉 ``op`` 键）。
        raw: 原始 dict（错误提示与结果回显用）。
    """

    index: int
    kind: str
    params: Mapping[str, Any] = field(default_factory=dict)
    raw: Mapping[str, Any] = field(default_factory=dict)

    @property
    def window(self) -> str | None:
        """本步的窗口选择器（空 = 用块的默认选择器）。"""
        value = self.params.get("window")
        text = str(value).strip() if value is not None else ""
        return text or None

    @property
    def settle(self) -> float | None:
        """本步注入后的等待秒数（未指定为 None）。"""
        raw = self.params.get("settle")
        if raw is None or isinstance(raw, bool):
            return None
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return None
        return value if value >= 0 else None

    @property
    def shot(self) -> Any:
        return self.params.get("shot")

    def describe(self) -> str:
        """一行摘要（结果回显用）。"""
        detail = {key: value for key, value in self.params.items()
                  if key not in ("window",)}
        if self.kind == "wait":
            return f"wait {self.params.get('seconds', self.params.get('settle'))}s"
        if self.kind == "screenshot":
            return f"screenshot {detail.get('path', '')}"
        if self.kind == "window":
            return f"window {detail.get('window_action', '')}"
        return f"{self.kind} {detail}"


def parse_sequence(raw: Any) -> list[SequenceStep]:
    """把 ``actions`` 参数解析为步骤列表。

    Args:
        raw: 步骤数组（或单个步骤 dict，等价于只含一步的数组）。

    Raises:
        SequenceError: 结构非法（非 dict / 缺 op / 未知步骤类型 / 步数超限）。
    """
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        raise SequenceError(
            "sequence 需要 actions 参数：步骤数组，每项形如 "
            '{"op": "click", "x": 10, "y": 20}；可用 op: '
            + "、".join(SEQUENCE_KINDS)
        )
    if isinstance(raw, Mapping):
        items: Sequence[Any] = [raw]
    elif isinstance(raw, (list, tuple)):
        items = list(raw)
    else:
        raise SequenceError(
            f"actions 必须是步骤数组（当前: {type(raw).__name__}）"
        )
    if not items:
        raise SequenceError("actions 不能为空数组")
    if len(items) > MAX_SEQUENCE_STEPS:
        raise SequenceError(
            f"actions 步骤过多（{len(items)} > {MAX_SEQUENCE_STEPS}）："
            f"请拆成多次调用"
        )
    steps: list[SequenceStep] = []
    for position, item in enumerate(items, start=1):
        steps.append(_parse_step(item, position))
    return steps


def _parse_step(item: Any, index: int) -> SequenceStep:
    """解析单个步骤（结构校验；参数细节交给各自的构建器）。"""
    if not isinstance(item, Mapping):
        raise SequenceError(
            f"第 {index} 步必须是对象（dict），当前: {type(item).__name__}"
        )
    raw = dict(item)
    op = raw.pop("op", None)
    if op is None:
        op = raw.pop("action", None)
    kind = str(op or "").strip().lower()
    if not kind:
        raise SequenceError(f"第 {index} 步缺少 op 参数（支持: {'、'.join(SEQUENCE_KINDS)}）")
    if kind not in SEQUENCE_KINDS:
        raise SequenceError(
            f"第 {index} 步的 op 未知: {op!r}。支持: {'、'.join(SEQUENCE_KINDS)}"
        )
    if kind == "wait":
        _validate_wait(raw, index)
    if kind == "screenshot" and not str(raw.get("path") or "").strip():
        raise SequenceError(f"第 {index} 步 screenshot 需要 path 参数（截图保存路径）")
    if kind == "window" and not str(raw.get("window_action") or "").strip():
        raise SequenceError(f"第 {index} 步 window 需要 window_action 参数")
    return SequenceStep(index=index, kind=kind, params=raw, raw=dict(item))


def _validate_wait(params: dict, index: int) -> None:
    """校验 wait 步的等待时长。"""
    raw = params.get("seconds", params.get("settle"))
    if raw is None:
        raise SequenceError(
            f"第 {index} 步 wait 需要 seconds 参数（等待秒数，支持小数）"
        )
    if isinstance(raw, bool):
        raise SequenceError(f"第 {index} 步 wait 的 seconds 必须是数值，当前: {raw!r}")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise SequenceError(
            f"第 {index} 步 wait 的 seconds 必须是数值，当前: {raw!r}"
        ) from None
    if value < 0:
        raise SequenceError(f"第 {index} 步 wait 的 seconds 不能为负，当前: {value}")
    if value > MAX_STEP_WAIT:
        raise SequenceError(
            f"第 {index} 步 wait 的 seconds 过大（{value} > {MAX_STEP_WAIT}）"
        )


def wait_seconds(step: SequenceStep) -> float:
    """取 wait 步的等待秒数（已通过 :func:`_validate_wait` 校验）。"""
    raw = step.params.get("seconds", step.params.get("settle"))
    return float(raw)


__all__ = [
    "MAX_SEQUENCE_STEPS",
    "MAX_STEP_WAIT",
    "SEQUENCE_KINDS",
    "SequenceError",
    "SequenceStep",
    "parse_sequence",
    "wait_seconds",
]
