"""``/loop`` 循环浮窗控制 — 两处 /loop 实现共用的唯一入口。

背景（2026-10-21 收敛）：``/loop`` 有两套实现——
  - ``src/core/commands/plugins/loop_plugin.py``（插件版，生产主路径）；
  - ``src/app_loop/_loop.py``（旧版 ``InteractiveLoop._handle_loop_cmd``）。
两者此前各自复制一份「静默降级调用 ChatUI 浮窗方法」+ 三处开/更/关调用，
改一处漏一处。现统一收敛到本模块：``/loop`` 编排只表达**语义**（开窗 / 更新
进度 / 关窗），调用细节（方法名、参数兼容、异常降级）只在这里维护。

设计约束：
  - 浮窗是**展示增强**：旧 ChatUI / 测试桩未实现对应方法时必须静默跳过，
    绝不因为浮窗失败中断循环（异常只记 debug）；
  - 本模块只依赖标准库（logging）——``loop_plugin`` 在异步执行期延迟导入
    （避免 ``app_loop`` 包初始化 → tui 的级联依赖），``_loop.py`` 同包直接
    导入。
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


def open_loop_window(chat_ui, total, prompt, runs_per_round: int = 2) -> None:
    """打开 ``/loop`` 循环浮窗并重置进度。

    ``runs_per_round`` 仅在非默认值时以**关键字**传递——兼容旧 ChatUI /
    测试桩只实现 ``(total, prompt)`` 两参的签名（生产实现有默认值 2，
    不传等价于传 2）。
    """
    if runs_per_round == 2:
        _call(chat_ui, "open_loop_floating_window", total, prompt)
    else:
        _call(
            chat_ui, "open_loop_floating_window", total, prompt,
            runs_per_round=runs_per_round,
        )


def set_loop_window_status(chat_ui, **fields) -> None:
    """更新 ``/loop`` 浮窗进度（total/round/run/runs_per_round/prompt/active）。

    ★ 计时锚点（2026-10-21 用户需求：单轮已运行时间动态刷新）：
    传入 ``round`` 时自动补 ``round_started_at``、传入 ``run`` 时自动补
    ``run_started_at``（调用方显式给出时不覆盖）——两处 /loop 实现
    （插件版 + 旧版主循环）无需各自记时间戳，计时真源唯一在这里。
    浮窗每帧按 ``time.monotonic() - round_started_at`` 计算并刷新显示。
    """
    payload = dict(fields)
    now = time.monotonic()
    if "round" in payload:
        payload.setdefault("round_started_at", now)
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
