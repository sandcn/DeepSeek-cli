"""widgets/_widget_common — 控件库公共纯辅助（无状态）。

模块边界（2026-08-05 架构优化）：收敛控件库中跨模块重复的纯辅助函数——
``_clamp_index`` / ``_children`` / ``_color`` / ``_call`` 原在多个控件模块
各自定义（行为逐字一致），收敛至本模块**单一真源**；原定义模块保留
re-export / 薄委托保持旧导入路径与测试 patch 兼容。

函数清单：
  - ``_clamp_index`` — 内部索引钳制到合法范围 [0, total-1]（空列表返回 0）
  - ``_children``    — 读取 reconciler 注入的 children（归一化为 Element 元组）
  - ``_color``       — 解析颜色 shorthand（颜色名/int）为 256 色号，解析失败回退 default
  - ``_call``        — 安全调用可选回调（异常仅记录日志，不阻断输入分发）

依赖约束：Layer 0/1——仅依赖 ``_style_utils._parse_color``（helpers 门面），
不依赖任何控件模块（避免循环）。
"""

from __future__ import annotations

import logging

from ..helpers import _parse_color

_logger = logging.getLogger(__name__)


def _clamp_index(idx: int, total: int) -> int:
    """将内部索引钳制到合法范围 ``[0, total-1]``。

    ``total <= 0``（空列表）返回 0；``idx < 0`` 钳到 0；``idx >= total``
    钳到 ``total-1``；正常范围原样返回。

    用途：SelectInput/MultiSelect 的 ``items`` 在挂载后被外部缩小（如异步候选
    刷新），内部 ``selected``/``cursor_idx`` 可能越界——Enter/space 分支读取
    ``items[selected_ref.current]`` 时越界被 router 吞掉（事件丢失/回调不触发）。
    """
    if total <= 0:
        return 0
    if idx < 0:
        return 0
    if idx >= total:
        return total - 1
    return idx


def _children(props: dict):
    """读取 reconciler 注入的 children（Element 元组；无子级时空元组）。"""
    children = props.get("children", ())
    if children is None:
        return ()
    if isinstance(children, (list, tuple)):
        return tuple(children)
    return (children,)


def _color(value, default: int = 6) -> int | None:
    """解析颜色 shorthand（颜色名/int）为 256 色号；解析失败回退 default。"""
    if value is None:
        return default
    parsed = _parse_color(value)
    return parsed if parsed is not None else default


def _call(fn, *args) -> None:
    """安全调用可选回调（异常仅记录日志，不阻断输入分发）。"""
    if fn is None:
        return
    try:
        fn(*args)
    except Exception:
        _logger.debug("控件回调异常", exc_info=True)


__all__ = ["_clamp_index", "_children", "_color", "_call"]
