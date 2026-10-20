"""RenderErrorPolicy — 渲染错误分级策略（P2-4）。

背景：修复前渲染线程异常一律走「自动重启 + warning」，编程错误与可恢复
的渲染错误混在一起；单帧异常则 60Hz 无限重试（后改指数退避）。用户可能
长期处于降级状态而不知情。

本模块给出显式策略：

  - ``strict``（开发/测试，``TuiConfig.strict_render``）：渲染错误直接抛出
    （fail fast，定位编程错误）；
  - ``resilient``（生产默认）：单帧错误按指数退避重试；连续失败超过阈值后
    进入**显式降级**（停止重试、输出一次可见提示、保留输入处理）；
  - 渲染线程级崩溃：最多重启 ``max_recover`` 次，超过后降级停止。
"""

from __future__ import annotations

import logging

_logger = logging.getLogger(__name__)


class RenderErrorPolicy:
    """渲染错误分级（帧级 / 循环级）。"""

    #: 单帧连续失败进入降级的阈值
    FRAME_FAILURE_LIMIT = 20

    def __init__(
        self,
        strict: bool = False,
        max_recover: int = 3,
        frame_failure_limit: int | None = None,
    ) -> None:
        self.strict = bool(strict)
        self.max_recover = max(0, int(max_recover))
        self.frame_failure_limit = int(frame_failure_limit or self.FRAME_FAILURE_LIMIT)
        self.degraded = False
        self.degraded_reason = ""
        self._announced = False

    # ── 帧级 ─────────────────────────────────────────

    def on_frame_error(self, exc: Exception, consecutive: int) -> str:
        """返回 ``"retry"`` / ``"degrade"`` / ``"raise"``。"""
        if self.strict:
            return "raise"
        if consecutive >= self.frame_failure_limit:
            self.degrade(f"连续渲染失败 {consecutive} 次: {exc}")
            return "degrade"
        return "retry"

    # ── 循环级 ───────────────────────────────────────

    def on_loop_error(self, exc: Exception, attempts: int) -> str:
        """返回 ``"restart"`` / ``"degrade"`` / ``"raise"``。"""
        if self.strict:
            return "raise"
        if attempts >= self.max_recover:
            self.degrade(f"渲染线程崩溃 {attempts} 次: {exc}")
            return "degrade"
        return "restart"

    # ── 降级 ─────────────────────────────────────────

    def degrade(self, reason: str) -> None:
        self.degraded = True
        self.degraded_reason = reason

    def take_announcement(self) -> str | None:
        """返回一次性降级提示（只返回一次）。"""
        if not self.degraded or self._announced:
            return None
        self._announced = True
        return f"[ChatUI] 渲染已降级：{self.degraded_reason}"


__all__ = ["RenderErrorPolicy"]
