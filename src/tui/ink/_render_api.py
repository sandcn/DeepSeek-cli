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
import time

from src.tui._screen import TerminalWidthCache
from .element import Element

_logger = logging.getLogger(__name__)


class _SimpleModel:
    """render() 独立会话的最小模型占位（满足 InkSession 读取的属性）。

    缺省属性说明（render() 独立会话中 InkSession / 组件树可能读取的模型
    属性，缺省值经 getattr 或类属性兜底）：
      - ``width``：渲染宽度（render() 尺寸覆盖写入，缺省 80）；
      - ``input_text`` / ``input_cursor``：输入区状态（update_input echo 回调）；
      - ``status``：状态对象（``status_active`` 动画驱动 / 系统监控采集；
        缺省 None——``_needs_animation`` 判 None 跳过）；
      - ``tool_boxes``：工具卡片容器（``_needs_animation`` 动画驱动探测；
        缺省 None 时跳过）；
      - ``parse_line``：解析进度行（``_needs_animation`` 动画驱动探测；
        缺省 None 时跳过）；
      - ``reflow_committed``：resize 重排回调（``_render_frame`` 经 getattr
        探测，缺省 None 时跳过——桩模型无需重排）；
      - ``reasoning_renderer`` / ``content_renderer``：开放通道 renderer
        （resize 宽度传播，``_render_frame`` 经 getattr 探测；缺省 None 时
        跳过——独立会话无开放通道）。
    """

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
    # ★ P1-1 修复（review 方向）：写覆盖值的同时**同步延长时间戳**——修复前
    #   仅写 ``_width/_height`` 不更新 ``_last_width_fetch/_last_height_fetch``：
    #   60s TTL 过期后 ``get_width()``/``get_height()`` 重新执行
    #   ``_get_terminal_size()`` 覆盖覆盖值（尺寸覆盖静默失效）。时间戳设为
    #   ``monotonic() + ttl``（等价于把 TTL 起点延后到未来——``_is_expired``
    #   判 ``monotonic() - last_fetch > ttl`` 恒 False，覆盖值在本会话期间
    #   不再被 TTL 刷新覆盖）。优先方案（为 ``TerminalWidthCache`` 增加公开
    #   ``set_dimensions(w, h)``）因 ``_screen.py`` 不在本次修改范围而放弃，
    #   采用等价方案 b（保持向后兼容，不破坏 _screen.py 现有测试）。
    if width is not None:
        session._width_cache._width = width
        session._width_cache._last_width_fetch = (
            time.monotonic() + getattr(session._width_cache, "_ttl", 60.0)
        )
    if height is not None:
        session._width_cache._height = height
        session._width_cache._last_height_fetch = (
            time.monotonic() + getattr(session._width_cache, "_ttl", 60.0)
        )

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
