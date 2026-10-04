"""显示目标适配器 — 依赖倒置工厂实现（桥接表现层）

core 领域层经 ``core.display_target`` 协议访问显示目标；本模块提供默认实现，
负责延迟导入表现层（tui）。适配器层允许依赖表现层（桥接职责）。

拆分说明：协议（DisplayTarget/OutputPublisher）保留在 ``core.display_target``，
工厂实现归入本适配器模块，使 core 顶层协议模块不直接 import tui。
"""

from __future__ import annotations

from typing import Optional


def get_display_target():
    """返回当前活跃的显示目标（TUI 模式为 ChatUIConsumer，无头模式 None）。"""
    from ...tui.consumer import get_active_chat_ui
    return get_active_chat_ui()


def get_output_publisher():
    """返回当前活跃的输出发布函数（publish_output）。"""
    from ...tui.events import publish_output
    return publish_output


__all__ = ["get_display_target", "get_output_publisher"]
