"""``/loop`` 循环浮窗控制 — 插件版 ``/loop`` 的唯一入口。

背景（2026-10-21 收敛）：``/loop`` 的实现已统一到
``src/core/commands/plugins/loop_plugin.py``，开窗 / 更新进度 / 关窗的调用
细节（方法名、参数兼容、异常降级）只在本模块维护——``/loop`` 编排只表达
**语义**，不重复处理 ChatUI 差异。

设计约束：
  - 浮窗是**展示增强**：旧 ChatUI / 测试桩未实现对应方法时必须静默跳过，
    绝不因为浮窗失败中断循环（异常只记 debug）；
  - 本模块只依赖标准库（logging）——``loop_plugin`` 在异步执行期延迟导入
    （避免 ``app_loop`` 包初始化 → tui 的级联依赖）。
"""

from __future__ import annotations

import logging
import time

_logger = logging.getLogger(__name__)


def _call(chat_ui, method: str, *args, **kwargs) -> None:
    """调用 ChatUI 的浮窗方法（不存在/异常时静默降级，不阻断循环）。"""
    fn = getattr(chat_ui, method, None) if chat_ui is not None else None
    if not callable(fn):
        return
    try:
        fn(*args, **kwargs)
    except Exception:
        _logger.debug("/loop 浮窗调用失败 method=%s", method, exc_info=True)


def open_loop_window(chat_ui, total, prompt, runs_per_round: int = 1) -> None:
    """打开 ``/loop`` 循环浮窗并重置进度。

    ``total`` 为**批次数**；``runs_per_round`` 为保留字段（批内运行次数不设
    上限，固定传 1）。生产实现 ``ChatUIConsumer`` 有默认值；旧 ChatUI / 测试桩
    缺少该方法时由 ``_call`` 静默降级。
    """
    _call(
        chat_ui, "open_loop_floating_window", total, prompt,
        runs_per_round=runs_per_round,
    )


def set_loop_window_status(chat_ui, **fields) -> None:
    """更新 ``/loop`` 浮窗进度（total=批次数 / round=当前批次 /
    run=批内运行次数 / runs_per_round=保留 / prompt / active）。

    ★ 计时锚点（2026-10-21 用户需求：单轮已运行时间动态刷新；
    2026-10-22 增加「当前批运行时间」）：
      - 传 ``new_batch=True``（批开始时由 ``/loop`` 编排传入）→ 自动补
        ``round_started_at`` / ``batch_started_at``（本批开始时间戳）；
      - 传 ``round``（兼容旧调用方）→ 同样补 ``round_started_at``；
      - 传 ``run`` → 自动补 ``run_started_at``（本次运行开始时间戳）；
      - 调用方显式给出锚点时不覆盖。
    ``/loop`` 编排在批内每次运行**只传 run、不传 round**，因此本批计时不会
    被后续运行重置。浮窗每帧按 ``now - batch_started_at`` / ``now -
    run_started_at`` 计算并刷新显示。
    """
    payload = dict(fields)
    now = time.monotonic()
    # ``new_batch`` 只是本函数的控制标记，不写入 UI 状态。
    new_batch = bool(payload.pop("new_batch", False))
    if new_batch or "round" in payload:
        payload.setdefault("round_started_at", now)
    if new_batch:
        payload.setdefault("batch_started_at", now)
    if "run" in payload:
        payload.setdefault("run_started_at", now)
    _call(chat_ui, "set_loop_status", **payload)


def close_loop_window(chat_ui) -> None:
    """关闭 ``/loop`` 浮窗并重置循环状态（幂等：正常结束 / 中断 / 异常都调用）。"""
    _call(chat_ui, "close_loop_floating_window")


__all__ = [
    "close_loop_window",
    "open_loop_window",
    "set_loop_window_status",
]
