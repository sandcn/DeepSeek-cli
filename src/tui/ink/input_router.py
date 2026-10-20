"""InputRouter — 单一输入路由对象（P1-5）。

背景：修复前输入有两条通道——``use_input`` composite router 与
``InputDispatcher`` 的输入缓冲；模态组件靠 router 末尾「吞掉未消费事件」
避免字符落入缓冲，弹窗关闭又需要 ``flush_input_router`` 等下一帧，语义
依赖帧时序。

本模块把「输入路由」收敛为一个显式对象：

  - ``route(event)``：按当前焦点栈 / 模态栈调用注册的 handler；
  - ``dispatch(event, legacy)``：路由未消费时调用**唯一兜底**
    （legacy 输入缓冲处理），调用方不再自行判断双通道；
  - ``push_modal(token)`` / ``pop_modal(token)``：模态是栈的一层；
  - ``set_fallback``：兜底处理函数（InputDispatcher 注册）。

``Reconciler`` 每帧构建的 composite handler 包装为 ``InputRouter`` 并发布；
``InputDispatcher`` 通过 ``dispatch`` 统一入口消费。既有「未消费放行」行为
保持不变，但「谁处理、是否吞掉」的仲裁集中在 Router 内。
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable

_logger = logging.getLogger(__name__)


class InputRouter:
    """输入路由（hooks handler + 模态栈 + 单一兜底）。"""

    def __init__(
        self,
        handler: Callable[[Any], bool] | None = None,
        focus_next: Callable[[], Any] | None = None,
        focus_previous: Callable[[], Any] | None = None,
    ) -> None:
        self._handler = handler
        self._fallback: Callable[[Any], Any] | None = None
        self._modals: list[Any] = []
        self._focus_stack: list[Any] = []
        self._focus_enabled = False
        self._focus_next = focus_next
        self._focus_previous = focus_previous
        self._lock = threading.RLock()

    # ── 模态 / 焦点栈 ────────────────────────────────

    def push_modal(self, token: Any) -> None:
        with self._lock:
            self._modals.append(token)

    def pop_modal(self, token: Any) -> None:
        with self._lock:
            for i in range(len(self._modals) - 1, -1, -1):
                if self._modals[i] is token:
                    del self._modals[i]
                    return

    @property
    def modal_depth(self) -> int:
        with self._lock:
            return len(self._modals)

    def push_focus(self, token: Any) -> None:
        with self._lock:
            self._focus_stack.append(token)

    def pop_focus(self, token: Any) -> None:
        with self._lock:
            for i in range(len(self._focus_stack) - 1, -1, -1):
                if self._focus_stack[i] is token:
                    del self._focus_stack[i]
                    return

    # ── 路由 ─────────────────────────────────────────

    def set_fallback(self, fallback: Callable[[Any], Any] | None) -> None:
        self._fallback = fallback

    def set_modals(self, tokens: list) -> None:
        """替换当前模态 token 集合（reconciler 每帧同步 active 全屏/弹窗）。"""
        with self._lock:
            self._modals = [t for t in tokens if t is not None]

    def set_focus_enabled(self, enabled: bool) -> None:
        """启用/停用 Tab 焦点仲裁（存在 useFocus 注册时）。"""
        self._focus_enabled = bool(enabled)

    def route(self, event: Any) -> bool:
        """hooks handler 优先；Tab 焦点 → 模态吞掉 → 未消费放行。"""
        # ★ P1-5：Tab/Shift+Tab 焦点切换收敛到 Router（不再依赖 composite
        #   闭包）。Shift+Tab 以 CSI u modifier==2 精确识别。
        if self._focus_enabled and getattr(event, "kind", None) == "tab":
            if getattr(event, "modifier", 0) == 2:
                if self._focus_previous is not None:
                    self._focus_previous()
            elif self._focus_next is not None:
                self._focus_next()
            return True
        handler = self._handler
        if handler is not None:
            try:
                if handler(event):
                    return True
            except Exception:
                _logger.debug("InputRouter handler 异常", exc_info=True)
        # ★ P1-5：模态是路由栈的一层——未消费事件在模态期间被吞掉，
        #   不再依赖 composite 闭包末尾的 fullscreen 判断。
        if self.modal_depth > 0:
            return True
        return False

    def dispatch(self, event: Any, legacy: Callable[[Any], Any] | None = None) -> bool:
        """统一入口：hooks 优先 → 模态吞掉 → 兜底处理。

        Returns:
            True — 事件已消费；False — 无 handler 且无兜底。
        """
        if self.route(event):
            return True
        if self.modal_depth > 0:
            return True
        fallback = self._fallback or legacy
        if fallback is not None:
            try:
                fallback(event)
            except Exception:
                _logger.debug("InputRouter fallback 异常", exc_info=True)
            return True
        return False

    def __call__(self, event: Any) -> bool:
        return self.route(event)


__all__ = ["InputRouter"]
