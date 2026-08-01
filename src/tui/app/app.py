"""App — 根组件：<Box><Static>ChatView</Static><SubAgentPanel/><StatusBar/><input-area/></Box>。

组件树结构（非全屏流动模型）：
  - Static 包裹聊天历史（静态内容，首差异行之前永不重写）
  - SubAgentPanel / StatusBar / input-area 为尾部 live 区（随内容流动）
"""

from __future__ import annotations

from src.tui.ink import h, APP, STATIC, BOX, TEXT, StyledRun
from src.tui._animator import AnimatorContext
from .chat_view import ChatView
from .status_bar import StatusBar
from .subagent_panel import SubAgentPanel
from . import input_area as _input_area


def App(props) -> object:
    """App 根组件。

    Props:
        model: AppModel 实例。
        width: 终端宽度。
        animator: 动画时钟（可选）。
    """
    model = props["model"]
    width = props.get("width", 80)
    animator = props.get("animator") or AnimatorContext.get_default()

    input_props = {
        "text": model.input_text,
        "cursor_pos": model.input_cursor,
        "prompt": "> ",
        "completion": model.completion,
        "status_active": model.status.status_active,
        "cpu": model.status.cpu,
        "mem": model.status.mem,
        "animator": animator,
    }

    children = [
        h(STATIC, None, [h(ChatView, {"model": model})]),
        h(_ParseLine, {"model": model}),
        h(SubAgentPanel, {"model": model, "width": width}),
        h(StatusBar, {"model": model, "width": width, "animator": animator}),
        h("input-area", input_props),
    ]
    return h(APP, {"width": width}, children)


def _ParseLine(props) -> object:
    """实时解析进度行（同位置刷新；model.parse_line 为 None 时不占行）。"""
    model = props["model"]
    line = model.parse_line
    if line is None:
        return h(BOX, None, [])
    runs = [StyledRun(r.text, r.style) for r in line.runs if r.text]
    return h(BOX, None, [h(TEXT, {"styled": runs})])


def build_app_element(model, width: int, animator=None) -> object:
    """构建根元素（session 渲染入口）。"""
    return h(App, {"model": model, "width": width, "animator": animator})


# 模块导入时注册 input-area host 组件
_input_area.register()

__all__ = ["App", "build_app_element"]
