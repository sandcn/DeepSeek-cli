"""ModelsPlugin — 模型选择器 (/models)

打开全屏 ModelView（选择 / 新增 / 编辑模型档案——name/model/base_url/
api_key/provider 五项）；交互由 render 线程渲染循环的 INPUT 阶段驱动
（与 ModelPlugin 同约束：不 suspend/stop render 线程，否则弹窗收不到按键）。

无 ChatUI（单次模式 / 非交互）时回退文本列出全部模型。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from .base import InteractiveCommandPlugin
from ..base import CommandMeta, declare_command_plugin

_logger = logging.getLogger(__name__)


class ModelsPlugin(InteractiveCommandPlugin):
    """模型选择器 (/models)。"""

    def __init__(self):
        super().__init__()
        self.meta = CommandMeta(
            name="models",
            description="模型选择器（选择/新增/编辑模型档案：name/model/url/key/provider）",
            group="model",
        )

    async def async_execute(self, ctx: Any) -> bool:
        """异步执行：清残留输入后在工作线程打开/轮询 ModelView。"""
        loop = self._loop
        if loop is None:
            _logger.error("ModelsPlugin 未绑定 InteractiveLoop")
            return False
        chat_ui = loop._chat_ui
        monitor = loop._monitor
        self._prepare_input(chat_ui, monitor)
        try:
            await asyncio.to_thread(self._run, ctx)
        finally:
            if monitor is not None:
                try:
                    monitor.clear_interrupted()
                except Exception:
                    _logger.debug("ModelsPlugin clear_interrupted 异常", exc_info=True)
        return True

    @staticmethod
    def _prepare_input(chat_ui, monitor) -> None:
        """入口清残留输入（stdin 残留字节 + 中断标志）——防误确认/误取消。"""
        try:
            from ...interrupt_state import flush_stdin
            flush_stdin(input_instance=chat_ui.get_input() if chat_ui is not None else None)
        except Exception:
            _logger.debug("ModelsPlugin flush_stdin 异常", exc_info=True)
        if monitor is not None:
            try:
                monitor.clear_interrupted()
            except Exception:
                _logger.debug("ModelsPlugin clear_interrupted 异常", exc_info=True)

    @staticmethod
    def _run(ctx) -> bool:
        from ...commands._model_cmd import _cmd_models
        return _cmd_models(ctx)

    def execute(self, ctx: Any) -> bool:
        """同步路径（无 render 线程）：文本列出模型，不打开交互视图。"""
        from ...commands._model_cmd import _list_models_text
        _list_models_text(ctx)
        return True


declare_command_plugin(ModelsPlugin())

__all__ = ["ModelsPlugin"]
