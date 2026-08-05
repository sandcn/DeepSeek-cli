"""ink/_render_api — React Ink render() 轻量入口（独立模块）。

模块边界（2026-08-05 架构优化）：从 ``ink/session.py`` 拆分——``render()``
顶层函数与 ``_SimpleModel`` 为 React Ink ``render()`` 等价物（轻量入口），
与 ``InkSession`` 会话类解耦。本模块提供：

  - ``_SimpleModel`` — render() 独立会话的最小模型占位
  - ``render()``     — React Ink render() 等价物（渲染组件树到终端）

依赖方向（单向无环）：
  ``_render_api`` → ``_screen``（TerminalWidthCache）/ ``element``（Element）
  + ``session``（函数体内惰性 import InkSession——避免模块加载期循环依赖）。
  ``session.py`` 顶层 re-export ``render`` / ``_SimpleModel`` 保持旧导入路径
  兼容（``from src.tui.ink.session import render`` 仍可用，测试锁定）。
"""

from __future__ import annotations

import logging

from src.tui._screen import TerminalWidthCache
from .element import Element

_logger = logging.getLogger(__name__)


class _SimpleModel:
    """render() 独立会话的最小模型占位（满足 InkSession 读取的属性）。"""

    width: int = 80
    input_text: str = ""
    input_cursor: int = 0
    status: object = None

    def reset_display(self) -> None:
        pass


def render(
    element: Element,
    stream=None,
    width: int | None = None,
    height: int | None = None,
) -> dict:
    """React Ink ``render()`` 等价物（轻量入口）：渲染组件树到终端。

    创建独立 InkSession 渲染给定元素（不依赖 App 模型/命令管线）——适用于
    组件开发/测试/独立 UI 场景。返回控制对象：
      - ``waitUntilExit()``：awaitable——app 退出（unmount/exit）后 resolve；
      - ``unmount()``：卸载 app（停止渲染线程）；
      - ``cleanup()``：同 unmount（React Ink 内部清理语义别名）；
      - ``rerender(new_element)``：以新元素树重新渲染；
      - ``clear()``：请求全帧清屏重绘。

    Args:
        element: 根元素（函数组件或 Element）。
        stream: 输出流（默认 ``sys.stdout``）。
        width/height: 终端尺寸覆盖（默认读取 width_cache）。

    Returns:
        dict：控制对象（waitUntilExit/unmount/cleanup/rerender/clear）。
    """
    import sys as _sys

    # 惰性 import InkSession——避免 ``_render_api → session → _render_api``
    # 模块加载期循环（session.py 顶层 re-export 本模块 render）。
    from .session import InkSession

    model = _SimpleModel()
    if width is not None:
        model.width = width

    # 组件函数形式的根元素：包装为固定构建函数（每帧返回最新 element）
    _state = {"element": element}

    def _build_tree(m, w):
        return _state["element"]

    # ★ 独立宽度缓存实例（架构修复，2026-08-05）：render() 的尺寸覆盖
    #   （width/height 参数）直接写缓存字段——若复用全局单例
    #   ``TerminalWidthCache.get_default()``，会污染后续所有读取终端高度的
    #   模块（如 ``_input_metrics._completion_item_rows``），产生测试间
    #   状态泄漏（test_react_ink_complete → test_input_metrics 顺序依赖）。
    #   改为独立实例，覆盖只影响本次 render() 会话。
    session = InkSession(
        model=model,
        build_tree=_build_tree,
        stream=stream if stream is not None else _sys.stdout,
        width_cache=TerminalWidthCache(),
    )
    # 尺寸覆盖（TerminalWidthCache 只读接口——直接写独立缓存字段）
    if width is not None:
        session._width_cache._width = width
    if height is not None:
        session._width_cache._height = height

    session.start()

    def _wait_until_exit():
        async def _waiter():
            import asyncio as _aio
            while session._render_running:
                await _aio.sleep(0.05)
        return _waiter()

    def _unmount():
        try:
            session.request_exit()
        except Exception:
            _logger.debug("render unmount 异常", exc_info=True)

    def _rerender(new_element):
        _state["element"] = new_element
        session._request_render()

    return {
        "waitUntilExit": _wait_until_exit,
        "unmount": _unmount,
        "cleanup": _unmount,
        "rerender": _rerender,
        "clear": session.request_clear,
    }


__all__ = ["render", "_SimpleModel"]
