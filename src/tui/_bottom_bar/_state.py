"""底部栏状态对象模块 — BottomBarStatus 跨线程安全状态对象。

从 ``_bar.py`` 提取为独立子模块（方向E·步骤9）：将 _BottomBar 的状态域
8 字段（_status_active / _model_name / _tool_count / _tool_fail_count /
_tool_total / _main_phase / _main_phase_start / _tool_phase_start）收敛为
独立状态对象，内部 ``threading.Lock`` 保护所有写，解决「_on_round_end
主线程写 + render 线程读」的非原子问题。

设计模式：状态对象（State Object）——状态域独立为对象，_BottomBar 组合持有。
"""

from __future__ import annotations

import threading
import time
from typing import Any


# ═══════════════════════════════════════════════════════════
# BottomBarStatus — 底部栏状态域状态对象
# ═══════════════════════════════════════════════════════════

class BottomBarStatus:
    """底部栏状态域（跨线程安全状态对象）。

    状态域字段（8 个）：
      - _status_active / _model_name — 状态行激活与模型名
      - _tool_count / _tool_fail_count / _tool_total / _tool_phase_start — 工具计数
      - _main_phase / _main_phase_start — 主阶段

    所有写操作在内部 ``threading.Lock`` 保护下执行；读端使用
    ``snapshot()`` 一次性取 8 字段副本，保证跨线程原子读取。
    纯状态职责：不含动画（_animator 留在 _bar）与 token 速度快照
    （_snapshot 留在 _bar，属 token 速度快照非状态域）。
    """

    _FIELDS = (
        "status_active",
        "model_name",
        "tool_count",
        "tool_fail_count",
        "tool_total",
        "main_phase",
        "main_phase_start",
        "tool_phase_start",
    )

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._status_active: bool = False
        self._model_name: str = ""
        self._tool_count: int = 0
        self._tool_fail_count: int = 0
        self._tool_total: int = 0
        self._main_phase: str = ""
        self._main_phase_start: float = 0.0
        self._tool_phase_start: float = 0.0

    # ── 状态行激活 ──────────────────────────────────

    def enable_status(self) -> None:
        """启用状态行。"""
        with self._lock:
            self._status_active = True

    def disable_status(self) -> None:
        """禁用状态行。"""
        with self._lock:
            self._status_active = False

    # ── 模型名 ──────────────────────────────────────

    def set_model_name(self, name: str) -> None:
        """设置模型名。"""
        with self._lock:
            self._model_name = name

    # ── 工具计数 ────────────────────────────────────

    def increment_tool(self) -> None:
        """工具计数 +1；首次（_tool_count==0）置位 _tool_phase_start。

        与原 _bar.py 逻辑一致：_tool_total 同步 +1（链式行为）。
        """
        with self._lock:
            if self._tool_count == 0:
                self._tool_phase_start = time.monotonic()
            self._tool_count += 1
            self._tool_total += 1

    def decrement_tool(self) -> None:
        """工具计数 -1（下限 0）。"""
        with self._lock:
            self._tool_count = max(0, self._tool_count - 1)

    def increment_tool_fail(self) -> None:
        """工具失败计数 +1。"""
        with self._lock:
            self._tool_fail_count += 1

    def reset_tool_count(self) -> None:
        """重置全部工具计数与阶段起始时间。"""
        with self._lock:
            self._tool_count = 0
            self._tool_fail_count = 0
            self._tool_total = 0
            self._tool_phase_start = 0.0

    # ── 主阶段 ──────────────────────────────────────

    def set_main_phase(self, phase: str) -> None:
        """设置主阶段；阶段变化时更新 _main_phase_start。

        与原 _bar.py 逻辑一致：phase 不变时不刷新起始时间。
        """
        with self._lock:
            if phase != self._main_phase:
                self._main_phase_start = time.monotonic()
            self._main_phase = phase

    # ── 快照与兼容写入 ──────────────────────────────

    def snapshot(self) -> dict:
        """返回 8 字段的独立副本（读端一次性取快照，线程安全）。

        返回值为独立 dict，修改副本不影响内部状态。
        """
        with self._lock:
            return {
                "status_active": self._status_active,
                "model_name": self._model_name,
                "tool_count": self._tool_count,
                "tool_fail_count": self._tool_fail_count,
                "tool_total": self._tool_total,
                "main_phase": self._main_phase,
                "main_phase_start": self._main_phase_start,
                "tool_phase_start": self._tool_phase_start,
            }

    def update(self, **fields: Any) -> None:
        """受锁保护的批量字段写入（供 _BottomBar 属性委托兼容使用）。

        仅接受 _FIELDS 中的键名；未知键静默忽略（防御性）。
        """
        with self._lock:
            for key, value in fields.items():
                if key in self._FIELDS:
                    setattr(self, f"_{key}", value)

    # ── 状态耗时查询 ────────────────────────────────

    def get_status_elapsed_seconds(self) -> float:
        """返回当前阶段/工具运行的耗时（秒）。

        P3-13 标注：**当前无生产调用方**，供状态耗时查询预留。本方法基于
        _tool_phase_start / _main_phase_start 计算阶段/工具耗时（状态对象
        语义）；``_bar.get_status_elapsed`` 为 **token 速度快照语义**
        （api.stats elapsed_seconds），两者语义不同，勿混用。

        纯状态职责：基于 _tool_phase_start / _main_phase_start 计算，
        不依赖 _snapshot（token 速度快照留在 _bar）。
        """
        with self._lock:
            if self._tool_count > 0:
                start = self._tool_phase_start
            elif self._main_phase:
                start = self._main_phase_start
            else:
                return 0.0
        if start <= 0.0:
            return 0.0
        return time.monotonic() - start


__all__ = ["BottomBarStatus"]
