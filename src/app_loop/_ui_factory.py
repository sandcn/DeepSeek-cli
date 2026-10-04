"""ChatUI 工厂 — 应用层创建终端界面消费者的统一入口（内核优先）。

「一切皆插件」：终端 UI 由内核 ``ctx.ui`` 插件提供；内核缺失时（单元测试、
独立调用）回退直接构造 ``ChatUIConsumer``。延迟导入保证测试对
``src.tui.consumer.ChatUIConsumer`` 的 monkeypatch 依然生效。
"""

from __future__ import annotations


def create_chat_ui():
    """创建 ChatUIConsumer（内核 ui 服务优先，回退直接构造）。"""
    from ..core.adapters.kernel_runtime import active_chat_ui_factory

    factory = active_chat_ui_factory()
    if factory is not None:
        return factory()

    from ..tui.consumer import ChatUIConsumer

    return ChatUIConsumer()


__all__ = ["create_chat_ui"]
