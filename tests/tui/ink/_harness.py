"""ink 测试公共夹具：独立调试会话（Reconciler + layout + render_frame）。"""

from __future__ import annotations

from src.tui.ink.reconciler import Reconciler
from src.tui.ink import components as _components


class Harness:
    """持久调和会话：同一 root 跨多帧渲染（保留 hooks 状态）。"""

    def __init__(self, width: int = 80, schedule_callback=None):
        self.width = width
        self.scheduled = 0

        def _cb():
            self.scheduled += 1
            if schedule_callback is not None:
                schedule_callback()

        self.reconciler = Reconciler(schedule_callback=_cb)
        self.root = Reconciler.create_root()

    @property
    def hook_context(self):
        """本调试会话的 hooks 上下文（多会话隔离后测试注入/断言用）。"""
        return self.reconciler.hook_context

    def render(self, element, height: int = 0):
        """渲染元素，返回整帧纯文本字符串（去 ANSI）。"""
        self.reconciler.render(self.root, element, self.width, height)
        frame = _components.render_frame(self.root, self.width)
        return "\n".join(line.plain for line in frame.lines)

    def frame(self, element, height: int = 0):
        self.reconciler.render(self.root, element, self.width, height)
        return _components.render_frame(self.root, self.width)


def render_once(element, width: int = 80) -> str:
    """单帧渲染为纯文本（不保留状态）。"""
    h = Harness(width)
    return h.render(element)
