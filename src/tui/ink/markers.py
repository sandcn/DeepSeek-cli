"""框架可理解的组件契约标记（host 类型与 props 键唯一真源）。

背景（2026-10-24 架构收敛）：``"input-area"`` / ``"dataInputArea"`` /
``"static-lines"`` 等字符串此前散落在 ``_fiber_utils``、``_floating``、
``_layout_measure``、``components``、``widgets.staticlines``、``session``
与 app 层 ``_input_cursor`` 中——拼写漂移会让「找输入区 / 找静态行」静默
失败（表现为光标不显示、前缀缓存不生效），且无法被静态检查发现。

本模块只放**框架可理解的最小契约**（React Ink 的 props 标记同级）：
  - 输入区容器：``type == INPUT_AREA_TYPE``（历史兼容）或
    ``props[DATA_INPUT_AREA]``（标准标记）；
  - 静态行批量 host：``type == STATIC_LINES_TYPE``。

应用私有布局（输入区长什么样、光标落在哪一行）仍由 app 层经
``InkSession(cursor_provider=...)`` 注入——本模块不引入应用依赖（Layer 0）。
"""

from __future__ import annotations

__all__ = [
    "INPUT_AREA_TYPE",
    "DATA_INPUT_AREA",
    "STATIC_LINES_TYPE",
    "is_input_area_fiber",
    "is_static_lines_fiber",
]

#: 输入区历史的 host 类型（兼容旧树；标准组件用 props 标记）。
INPUT_AREA_TYPE = "input-area"

#: 输入区标准 props 标记（``InputArea`` 容器透传）。
DATA_INPUT_AREA = "dataInputArea"

#: 静态行批量渲染 host 类型（``widgets.staticlines`` 注册）。
STATIC_LINES_TYPE = "static-lines"


def is_input_area_fiber(fiber) -> bool:
    """fiber 是否为输入区容器（host + 旧类型或标准 props 标记）。"""
    if fiber is None or not getattr(fiber, "is_host", False):
        return False
    if getattr(fiber, "type", None) == INPUT_AREA_TYPE:
        return True
    props = getattr(fiber, "props", None)
    return bool(isinstance(props, dict) and props.get(DATA_INPUT_AREA))


def is_static_lines_fiber(fiber) -> bool:
    """fiber 是否为静态行批量 host。"""
    return (
        fiber is not None
        and getattr(fiber, "is_host", False)
        and getattr(fiber, "type", None) == STATIC_LINES_TYPE
    )
