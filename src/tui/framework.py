"""
TUI 框架统一入口 — ``Framework`` 单例 + 配置管理。

Framework 是 TUI 框架的轻量单例入口，职责精简为：
  - 生命周期管理（start / stop / is_running）
  - 配置管理（get_config / set_config）

设计原则：
  - 单例管理：通过 ``SingletonMeta`` 元类自动提供（get_default / reset_default）
  - 线程安全：API 调用使用 threading.Lock 保护
  - 零 I/O：不涉及终端或文件 I/O，纯管理职责
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

from .core.singleton import SingletonMeta

if TYPE_CHECKING:
    from .config import TuiConfig

_logger = logging.getLogger(__name__)


__all__: list[str] = [
    "Framework",
]


# ═══════════════════════════════════════════════════════════
# Framework — 全局单例框架管理器
# ═══════════════════════════════════════════════════════════


class Framework(metaclass=SingletonMeta):
    """TUI 框架全局单例管理器。

    职责：
      1. 生命周期管理（start / stop / is_running）
      2. 配置管理（get_config / set_config）

    单例行为由 ``SingletonMeta`` 元类自动提供（get_default / reset_default）。

    使用示例：
        >>> from src.tui.framework import Framework
        >>> fw = Framework.get_default()
        >>> cfg = fw.get_config()
        >>> print(cfg.max_error_length)
        200
        >>> fw.is_running()
        False
    """

    def __init__(self) -> None:
        """初始化框架实例（私有构造器，通过 get_default() 获取）。"""
        self._lock = threading.Lock()
        self._config: TuiConfig | None = None
        self._running: bool = False
        self._lifecycle_lock = threading.Lock()

    # 单例访问由 SingletonMeta 提供：
    #   Framework.get_default() → 线程安全单例获取（DCL）
    #   Framework.reset_default() → 线程安全单例重置（供测试使用）

    # ── 生命周期 ──────────────────────────────────────

    def start(self) -> None:
        """启动框架。幂等操作。"""
        with self._lifecycle_lock:
            if self._running:
                return
            self._running = True

    def stop(self) -> None:
        """停止框架。幂等操作。"""
        with self._lifecycle_lock:
            if not self._running:
                return
            self._running = False

    def is_running(self) -> bool:
        """查询框架是否处于运行状态。

        Returns:
            True 如果 Framework 已调用 start() 且尚未 stop()。
        """
        return self._running

    # ── 配置管理 ──────────────────────────────────────

    def get_config(self) -> TuiConfig:
        """获取当前 TUI 配置。

        返回 TuiConfig 默认配置。可通过 set_config() 覆盖。

        Returns:
            TuiConfig 实例（frozen=True，不可变）。
        """
        if self._config is None:
            from .config import TuiConfig
            self._config = TuiConfig.defaults()
        return self._config

    def set_config(self, config: TuiConfig) -> None:
        """设置 TUI 配置。

        Args:
            config: TuiConfig 实例。
        """
        self._config = config
