"""TuiHostAdapter — 聊天 TUI 的 HostAdapter 实现（P0-3）。

把宿主 ``AppModel`` 的聊天语义（committed 历史 reflow、运行中工具标题刷新、
流式 renderer 宽度传播、静态内容区起始行）封装为框架可见的协议实现。
``ink`` 框架不再直接 import ``src.tui.app.apply`` / 探测宿主字段。
"""

from __future__ import annotations

import logging
from typing import Any

_logger = logging.getLogger(__name__)


class TuiHostAdapter:
    """聊天 TUI 宿主适配器。"""

    def prepare_frame(self, model: Any, width: int, height: int) -> None:
        if model is None:
            return
        # 终端 resize：已提交行按旧宽度 wrap，宽度变化后需重排（幂等）。
        # ★ 顺序：reflow 必须在 ``model.width = width`` **之前**——reflow
        #   以 ``width == self.width`` 判幂等，提前写宽会使重排被跳过。
        reflow = getattr(model, "reflow_committed", None)
        if callable(reflow):
            try:
                reflow(width)
            except Exception:
                _logger.debug("reflow_committed 异常", exc_info=True)
        # 渲染器 TOC 边框宽度（会话每帧更新，保持既有契约）。
        try:
            model.width = width
        except Exception:
            _logger.debug("model.width 赋值异常", exc_info=True)
        # 运行中的工具卡标题行 = 实时运行时间（已增量提交的静态行由宿主刷新）。
        refresh_titles = getattr(model, "refresh_running_tool_titles", None)
        if callable(refresh_titles):
            try:
                refresh_titles()
            except Exception:
                _logger.debug("refresh_running_tool_titles 异常", exc_info=True)
        # 开放通道 renderer 宽度传播（TOC 边框/表格宽度随 resize 刷新）。
        for name in ("reasoning_renderer", "content_renderer"):
            renderer = getattr(model, name, None)
            set_width = getattr(renderer, "set_width", None)
            if callable(set_width):
                try:
                    set_width(width)
                except Exception:
                    _logger.debug("%s.set_width 传播异常", name, exc_info=True)

    def coalesce_commands(self, commands: list) -> list:
        if not commands:
            return commands
        try:
            from src.tui.app.apply import coalesce_commands

            return coalesce_commands(commands)
        except Exception:
            _logger.debug("命令合并失败，按原批应用", exc_info=True)
            return commands

    def content_line_count(self, model: Any) -> int:
        if model is None:
            return 0
        try:
            return len(getattr(model, "committed_lines", None) or [])
        except Exception:
            return 0

    def static_region_start(self, root_fiber: Any) -> int | None:
        try:
            from src.tui.ink.components import static_region_start

            return static_region_start(root_fiber)
        except Exception:
            _logger.debug("static_region_start 探测异常", exc_info=True)
            return None

    def on_frame_rendered(self, metrics: dict) -> None:
        # 预留观测扩展点（帧耗时/行数）；当前无宿主侧消费。
        return None


__all__ = ["TuiHostAdapter"]
