"""HostAdapter — Ink 框架与宿主应用之间的唯一契约（P0-3 架构修复）。

背景：修复前 ``ink`` 框架层通过 ``getattr(model, "reflow_committed")`` /
``getattr(model, "refresh_running_tool_titles")`` / ``model.committed_lines``
等方式直接探测宿主 ``AppModel`` 的具体字段与方法，并直接 import
``src.tui.app.apply.coalesce_commands``——框架层依赖应用层，名义上的通用
React Ink 风格框架实际无法脱离本应用使用。

本模块把「框架需要宿主提供的能力」收敛为一个显式协议：

  - ``prepare_frame``      — 帧开始前同步宿主状态（宽度 reflow、静态行刷新、
                             流式 renderer 宽度传播）；
  - ``coalesce_commands``  — 宿主命令合并策略（框架不依赖 app.apply）；
  - ``content_line_count`` — 已提交内容行数（输出历史基线）；
  - ``static_region_start``— 静态内容区在文档中的起始行（无静态区返回 None）；
  - ``on_frame_rendered``  — 帧渲染完成回调（指标/观测）。

``NullHostAdapter`` 为无宿主场景（``render()`` 独立会话、测试）的默认实现，
所有方法均为安全空操作——框架不再需要 ``getattr`` 探测。
"""

from __future__ import annotations

import logging
from typing import Any, Iterable, Protocol

_logger = logging.getLogger(__name__)


class HostAdapter(Protocol):
    """宿主能力协议（结构类型，无需继承）。"""

    def prepare_frame(self, model: Any, width: int, height: int) -> None:
        """帧开始前同步宿主状态（幂等）。"""

    def coalesce_commands(self, commands: list) -> list:
        """合并本帧命令（返回应用顺序不变的新列表）。"""

    def content_line_count(self, model: Any) -> int:
        """已提交内容行数（输出历史基线；无内容区返回 0）。"""

    def static_region_start(self, root_fiber: Any) -> int | None:
        """静态内容区起始行（无静态区返回 None）。"""

    def on_frame_rendered(self, metrics: dict) -> None:
        """帧渲染完成回调（异常由框架吞掉并记录）。"""


class NullHostAdapter:
    """无宿主适配器（严格空实现；``renderToString`` 等无模型场景）。"""

    def prepare_frame(self, model: Any, width: int, height: int) -> None:
        return None

    def coalesce_commands(self, commands: list) -> list:
        return commands

    def content_line_count(self, model: Any) -> int:
        return 0

    def static_region_start(self, root_fiber: Any) -> int | None:
        return None

    def on_frame_rendered(self, metrics: dict) -> None:
        return None


class DefaultHostAdapter(NullHostAdapter):
    """默认适配器：仅处理**通用渲染契约**（宽度 / 可选宿主钩子）。

    ``InkSession`` 未显式注入 ``TuiHostAdapter``（测试、嵌入式、轻量模型）
    时使用：宿主有 ``width`` 字段则同步；有 ``reflow_committed`` /
    ``refresh_running_tool_titles`` / 流式 renderer 则调用。所有探测都在
    适配器内部完成——框架层不 getattr 宿主。
    """

    def prepare_frame(self, model: Any, width: int, height: int) -> None:
        if model is None:
            return
        reflow = getattr(model, "reflow_committed", None)
        if callable(reflow):
            try:
                reflow(width)
            except Exception:
                _logger.debug("reflow_committed 异常", exc_info=True)
        if hasattr(model, "width"):
            try:
                model.width = width
            except Exception:
                _logger.debug("model.width 赋值异常", exc_info=True)
        refresh = getattr(model, "refresh_running_tool_titles", None)
        if callable(refresh):
            try:
                refresh()
            except Exception:
                _logger.debug("refresh_running_tool_titles 异常", exc_info=True)
        for name in ("reasoning_renderer", "content_renderer"):
            renderer = getattr(model, name, None)
            set_width = getattr(renderer, "set_width", None)
            if callable(set_width):
                try:
                    set_width(width)
                except Exception:
                    _logger.debug("%s.set_width 异常", name, exc_info=True)

    def content_line_count(self, model: Any) -> int:
        if model is None:
            return 0
        try:
            return len(getattr(model, "committed_lines", None) or [])
        except Exception:
            return 0


class AdapterChain:
    """组合多个适配器（可选扩展点；按顺序调用，后者覆盖前者返回值）。

    生产路径只用一个 ``TuiHostAdapter``；本类供插件/测试组合观测适配器，
    避免框架层再引入 ``getattr`` 分支。
    """

    def __init__(self, adapters: Iterable[HostAdapter] = ()) -> None:
        self._adapters = [a for a in adapters if a is not None]

    def prepare_frame(self, model: Any, width: int, height: int) -> None:
        for adapter in self._adapters:
            adapter.prepare_frame(model, width, height)

    def coalesce_commands(self, commands: list) -> list:
        out = commands
        for adapter in self._adapters:
            out = adapter.coalesce_commands(out)
        return out

    def content_line_count(self, model: Any) -> int:
        count = 0
        for adapter in self._adapters:
            count = adapter.content_line_count(model)
        return count

    def static_region_start(self, root_fiber: Any) -> int | None:
        start = None
        for adapter in self._adapters:
            start = adapter.static_region_start(root_fiber)
        return start

    def on_frame_rendered(self, metrics: dict) -> None:
        for adapter in self._adapters:
            adapter.on_frame_rendered(metrics)


__all__ = ["HostAdapter", "NullHostAdapter", "DefaultHostAdapter", "AdapterChain"]
