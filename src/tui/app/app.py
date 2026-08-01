"""App — 根组件：<Box><Static>ChatView</Static><SubAgentPanel/><StatusBar/><input-area/></Box>。

组件树结构（非全屏流动模型）：
  - Static 包裹聊天历史（静态内容，首差异行之前永不重写）
  - SubAgentPanel / StatusBar / input-area 为尾部 live 区（随内容流动）
"""

from __future__ import annotations

from src.tui.ink import h, APP, STATIC, BOX, TEXT, StyledRun
from .chat_view import ChatView, register as _register_committed
from .status_bar import StatusBar
from .subagent_panel import SubAgentPanel
from . import input_area as _input_area


def App(props) -> object:
    """App 根组件。

    Props:
        model: AppModel 实例。
        width: 终端宽度。
    """
    model = props["model"]
    width = props.get("width", 80)

    input_props = {
        "text": model.input_text,
        "cursor_pos": model.input_cursor,
        "prompt": "> ",
        "completion": model.completion,
        "status_active": model.status.status_active,
        "cpu": model.status.cpu,
        "mem": model.status.mem,
        # 方向D 步骤14：Ctrl+R 反向历史搜索状态（input-area 渲染覆盖行）
        "history_search": model.history_search,
    }

    children = [
        h(STATIC, None, [h(ChatView, {"model": model})]),
        h(_ParseLine, {"model": model}),
        h(SubAgentPanel, {"model": model, "width": width}),
        h(StatusBar, {"model": model, "width": width}),
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
    """构建根元素（session 渲染入口）。

    animator: 保留参数（兼容旧调用面），App 组件已不使用动画上下文。
    # deprecated: animator 参数已废弃，仅兼容旧调用面
    """
    return h(App, {"model": model, "width": width})


# 模块导入时注册 input-area / committed-chat host 组件
_input_area.register()
_register_committed()

__all__ = ["App", "build_app_element"]
