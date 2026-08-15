"""Web UI 显示适配器 — UIDisplayAdapter

将 WebDisplay 适配为 DisplayPort 接口，供 Agent 通过 _display_port 调用。
桥接 webui 层与核心层的显示契约，使 Web 模式下的 Agent 工具回调能正确
路由到 WebDisplay 的 JSON 序列化发送路径。

迁移说明：
  原 UIDisplayAdapter 位于已废弃的 src/ui/adapters.py，现移至 src/webui/adapter.py，
  保持 webui 层自包含，不污染 tui 层。

接口兼容性确认（2026-07-15）：
  ✅ 所有 DisplayPort 方法签名与原 src/ui/adapters.py 中的定义一致
  ✅ is_web = True 属性保留，Agent 侧 _tool_callbacks.py 检测逻辑不变
  ✅ WebDisplay 所有委托方法均已实现，无缺失
  ✅ 依赖方向正确：webui → tui，无反向依赖
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Optional

_logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from .display import WebDisplay


class UIDisplayAdapter:
    """Web UI 显示适配器 — 将 WebDisplay 包装为 DisplayPort 兼容接口。

    所有方法调用委托给内部 WebDisplay 实例，由 WebDisplay 负责
    将调用序列化为 JSON 消息经 WebSocket 发送到前端。

    is_web = True 属性供 Agent 检测运行模式（见 _tool_callbacks.py）。
    """

    is_web: bool = True

    # 核心方法列表 — 缺失时升级为 warning（非静默跳过）
    _CORE_METHODS: tuple[str, ...] = (
        "start", "stop", "tool_start", "tool_done",
    )

    def __init__(self, display: "WebDisplay"):
        """初始化适配器。

        Args:
            display: WebDisplay 实例，提供实际的 WebSocket 发送能力。
        """
        self._display = display

    # ── 防御性委托工具 ──────────────────────────────────

    def _safe_delegate(self, method_name: str, *args, **kwargs):
        """安全委托方法调用到内部 WebDisplay。

        若 WebDisplay 缺少对应方法（属性缺失），核心方法升级为 warning，
        扩展方法记录 debug 后静默跳过。

        Args:
            method_name: WebDisplay 方法名。
            *args: 位置参数。
            **kwargs: 关键字参数。

        Returns:
            委托方法返回值，方法不存在时返回 None。
        """
        method = getattr(self._display, method_name, None)
        if method is None:
            if method_name in self._CORE_METHODS:
                _logger.warning(
                    "UIDisplayAdapter: 核心方法 %s 缺失，可能导致功能异常", method_name
                )
            else:
                _logger.debug("UIDisplayAdapter: WebDisplay 缺少方法 %s，静默跳过", method_name)
            return None
        result = method(*args, **kwargs)
        # 防御：若委托方法返回协程但调用方期望同步结果，
        # 检查是否已有运行中的事件循环（无法在此同步上下文中 await 协程）
        if asyncio.iscoroutine(result):
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                _logger.warning(
                    "UIDisplayAdapter: 方法 %s 返回协程但无运行中的事件循环", method_name
                )
                return None
            _logger.debug(
                "UIDisplayAdapter: 方法 %s 返回协程对象（调用方需自行 await）", method_name
            )
        return result

    async def _safe_delegate_async(self, method_name: str, *args, **kwargs):
        """异步安全委托 — 支持 WebDisplay 的异步方法。

        Args:
            method_name: WebDisplay 方法名。
            *args: 位置参数。
            **kwargs: 关键字参数。

        Returns:
            委托方法返回值（经 await），方法不存在时返回 None。
        """
        method = getattr(self._display, method_name, None)
        if method is None:
            _logger.debug("UIDisplayAdapter: WebDisplay 缺少异步方法 %s", method_name)
            return None
        result = method(*args, **kwargs)
        if asyncio.iscoroutine(result):
            result = await result
        return result

    # ── 生命周期 ────────────────────────────────────────

    def start(self) -> None:
        _logger.debug("UIDisplayAdapter.start()")
        self._safe_delegate("start")

    def stop(self, final: bool = False) -> None:
        _logger.debug("UIDisplayAdapter.stop(final=%s)", final)
        self._safe_delegate("stop", final=final)

    # ── 工具调用 ────────────────────────────────────────

    def tool_start(self, tool_label: str, tool_name: str, detail: str,
                   metadata: Optional[dict] = None) -> None:
        _logger.debug("UIDisplayAdapter.tool_start(%s, %s)", tool_label, tool_name)
        self._safe_delegate("tool_start", tool_label, tool_name, detail, metadata)

    def tool_done(self, tool_label: str, tool_name: str = "",
                  success: bool = True, metadata: Optional[dict] = None) -> None:
        self._safe_delegate("tool_done", tool_label, tool_name, success, metadata)

    def tool_parsing(self, label: str, tool_name: str, arguments: str = "",
                     tool_id: str = "") -> None:
        self._safe_delegate("tool_parsing", label, tool_name, arguments, tool_id=tool_id)

    def update_status(self, label: str, status: str) -> None:
        self._safe_delegate("update_status", label, status)

    def capture_and_print(self, display_func) -> str:
        result = self._safe_delegate("capture_and_print", display_func)
        return result if result is not None else ""

    async def capture_and_print_async(self, display_func) -> str:
        result = await self._safe_delegate_async("capture_and_print_async", display_func)
        return result if result is not None else ""

    # ── 批量工具 ────────────────────────────────────────

    def tool_batch_start(self, label: str, names: list[str]) -> None:
        self._safe_delegate("tool_batch_start", label, names)

    def update_parse_info(self, label: str, tool_name: str, tokens: int,
                          elapsed: float) -> None:
        self._safe_delegate("update_parse_info", label, tool_name, tokens, elapsed)

    def parse_info_done(self, label: str) -> None:
        self._safe_delegate("parse_info_done", label)

    # ── 代理状态与实时指标 ──────────────────────────────

    def update_model_phase(self, label: str, phase: str, message: str = "") -> None:
        # 注意：Adapter 使用 DisplayPort 约定的参数名 "message"，
        # WebDisplay 侧对应参数名为 "info"，位置传递兼容。
        self._safe_delegate("update_model_phase", label, phase, message)

    def update_usage(self, label: str, usage: dict, replace: bool = False) -> None:
        self._safe_delegate("update_usage", label, usage, replace)

    def update_speed(self, label: str, speed: float) -> None:
        self._safe_delegate("update_speed", label, speed)

    def update_live_input(self, label: str, tokens: int) -> None:
        self._safe_delegate("update_live_input", label, tokens)

    def update_live_output(self, label: str, tokens: int) -> None:
        self._safe_delegate("update_live_output", label, tokens)

    def update_agent_status(self, label: str, status: str) -> None:
        self._safe_delegate("update_agent_status", label, status)

    def add_agent(self, label: str, description: str, status: str = "running") -> None:
        self._safe_delegate("add_agent", label, description, status)
