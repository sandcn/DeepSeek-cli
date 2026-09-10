"""ink/_render_api — React Ink render() 轻量入口（独立模块）。

模块边界（2026-08-05 架构优化）：从 ``ink/session.py`` 拆分——``render()``
顶层函数与 ``_SimpleModel`` 为 React Ink ``render()`` 等价物（轻量入口），
与 ``InkSession`` 会话类解耦。本模块提供：

  - ``_SimpleModel`` — render() 独立会话的最小模型占位
  - ``render()``     — React Ink render() 等价物（渲染组件树到终端）
  - ``measureElement()`` — React Ink measureElement() 等价物（测量布局盒）

★ 官方 React Ink render() options 补齐（2026-08-16）：
  - ``stdout``：输出流（与旧 ``stream`` 参数同义；stdout 优先，stream 兼容
    旧调用）
  - ``stdin``：输入实例（注入 InkSession.set_input——useInput/useStdin 可用）
  - ``stderr``：错误流（useStderr().stderr 读取；缺省 ``sys.__stderr__``）
  - ``debug``：调试模式（True 时每渲染帧输出统计到 stderr）
  - ``exitOnCtrlC``：Ctrl+C 是否退出（True=中断请求退出；False=Ctrl+C 事件
    放行给 useInput handler，React Ink 语义）
  - ``patchConsole``：控制台补丁（替换 sys.stdout/sys.stderr 的 write 为
    代理——print()/sys.stderr 输出重定向到 TUI 流；unmount/cleanup 恢复）

依赖方向（单向无环）：
  ``_render_api`` → ``_screen``（TerminalWidthCache）/ ``element``（Element）
  + ``session``（函数体内惰性 import InkSession——避免模块加载期循环依赖）。
  ``session.py`` 顶层 re-export ``render`` / ``_SimpleModel`` 保持旧导入路径
  兼容（``from src.tui.ink.session import render`` 仍可用，测试锁定）。
"""

from __future__ import annotations

import logging
import sys
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


def measureElement(dom_node) -> dict:
    """React Ink ``measureElement()`` 等价物：测量 DOM 节点（布局盒）尺寸。

    官方 API（v3.4+）：``measureElement(domNode) -> {width, height}`` 从 DOM
    节点读取渲染尺寸。本框架非全屏流动模型下，host 元素的 ``ref`` 在布局
    完成后指向 **布局盒**（``LayoutBox(x, y, w, h)``，见 reconciler
    ``_fill_host_refs``）——"DOM 节点"等价物即布局盒。

    Args:
        dom_node: 布局盒对象（LayoutBox，即 ``use_ref`` 绑定的
            ``h(BOX, {"ref": ref})`` 之 ``ref.current``）或带 ``current`` 的
            ref 对象（``use_ref`` 返回值，未解引用时直接传入亦可）或 None
            （未测量到）。

    Returns:
        dict：``{"width": int, "height": int}``——未测量（None/无尺寸）时
        返回 0x0（与官方未挂载节点行为对齐：无测量返回 0）。

    用法::

        ref = use_ref(None)
        useLayoutEffect(lambda: print(measureElement(ref.current)), ())
        return h(BOX, {"ref": ref}, ...)
    """
    box = dom_node
    if box is not None and hasattr(box, "current"):
        box = box.current
    if box is None:
        return {"width": 0, "height": 0}
    try:
        w = max(0, int(getattr(box, "w", 0) or 0))
        h = max(0, int(getattr(box, "h", 0) or 0))
    except (TypeError, ValueError, OverflowError):
        # 畸形尺寸（inf/nan/非数值）→ 0x0（渲染错误修复一贯防御）
        w, h = 0, 0
    return {"width": w, "height": h}


class _ConsoleProxy:
    """sys.stdout/sys.stderr 替换代理（patchConsole）：write 重定向到目标流。

    React Ink ``patchConsole`` 语义：把 ``console.log``/``console.error``
    输出重定向进 TUI（Python 适配：``print()`` / ``sys.stdout.write`` 写入
    session 输出流，错误写 stderr 流）。代理转发其余属性（encoding/
    fileno 等）到目标流，保持对 print 底层（TextIOWrapper 探测）兼容。
    """

    def __init__(self, target):
        self._target = target

    def write(self, s) -> int:
        if s:
            try:
                self._target.write(s)
                self._target.flush()
            except (OSError, ValueError):
                pass
        return len(s) if isinstance(s, str) else 0

    def flush(self) -> None:
        try:
            self._target.flush()
        except (OSError, ValueError):
            pass

    def isatty(self) -> bool:
        return False

    def __getattr__(self, name):
        return getattr(self._target, name)


class _ConsolePatcher:
    """patchConsole 控制台补丁：替换 sys.stdout/sys.stderr 为代理。

    ``patch()`` 幂等（已 patch 时无操作）；``restore()`` 幂等（未 patch 时
    无操作）。异常安全：patch 中途失败自动恢复原流。
    """

    def __init__(self, out_stream, err_stream):
        self._out_stream = out_stream
        self._err_stream = err_stream
        self._saved = None

    def patch(self) -> None:
        if self._saved is not None:
            return
        saved_out = sys.stdout
        saved_err = sys.stderr
        try:
            sys.stdout = _ConsoleProxy(
                self._out_stream if self._out_stream is not None else saved_out
            )
            sys.stderr = _ConsoleProxy(
                self._err_stream if self._err_stream is not None else saved_err
            )
        except Exception:
            sys.stdout, sys.stderr = saved_out, saved_err
            raise
        self._saved = (saved_out, saved_err)

    def restore(self) -> None:
        if self._saved is not None:
            sys.stdout, sys.stderr = self._saved
            self._saved = None


def render(
    element: Element,
    stream=None,
    width: int | None = None,
    height: int | None = None,
    *,
    stdout=None,
    stdin=None,
    stderr=None,
    debug: bool = False,
    exitOnCtrlC: bool = True,
    patchConsole: bool = False,
) -> dict:
    """React Ink ``render()`` 等价物（轻量入口）：渲染组件树到终端。

    创建独立 InkSession 渲染给定元素（不依赖 App 模型/命令管线）——适用于
    组件开发/测试/独立 UI 场景。返回控制对象：
      - ``waitUntilExit()``：awaitable——app 退出（unmount/exit）后 resolve；
      - ``unmount()``：卸载 app（停止渲染线程；patchConsole 时恢复控制台）；
      - ``cleanup()``：同 unmount（React Ink 内部清理语义别名）；
      - ``rerender(new_element)``：以新元素树重新渲染；
      - ``clear()``：请求全帧清屏重绘。

    Args:
        element: 根元素（函数组件或 Element）。
        stream: 输出流（旧参数名；``stdout`` 提供时忽略）。
        width/height: 终端尺寸覆盖（默认读取 width_cache）。
        stdout: 输出流（React Ink ``render(tree, {stdout})`` 语义，与
            ``stream`` 同义；两者都提供时 stdout 优先）。
        stdin: 输入实例（注入 session——useInput/useStdin 可用；None 时
            独立会话无输入源，isAnyKeyPressed 恒 False）。
        stderr: 错误流（useStderr().stderr 读取；缺省 ``sys.__stderr__``）。
        debug: 调试模式（True 时每渲染帧输出统计到 stderr）。
        exitOnCtrlC: Ctrl+C 是否退出（默认 True：Ctrl+C 请求退出会话；
            False：Ctrl+C 事件放行给 useInput handler——React Ink 语义）。
        patchConsole: 控制台补丁（默认 False；True 时替换 sys.stdout/
            sys.stderr 的 write 为代理——print()/错误输出重定向到 TUI 流；
            unmount/cleanup 时恢复原流）。

    Returns:
        dict：控制对象（waitUntilExit/unmount/cleanup/rerender/clear）。
    """
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
    out_stream = stdout if stdout is not None else stream
    session = InkSession(
        model=model,
        build_tree=_build_tree,
        stream=out_stream if out_stream is not None else sys.stdout,
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
    # 尺寸覆盖（★ P2 review：改用公开入口 ``set_dimensions``——修复前直接写
    #   私有字段 ``_width``/``_height``/时间戳，破坏封装；且 ``_fetch()`` 到期
    #   会同时覆盖宽高与时间戳，仅覆盖其一时另一维度覆盖值静默失效。
    #   ``set_dimensions`` 置 ``_override`` 标志：覆盖期间 TTL 到期不重新探测
    #   真实终端（本会话尺寸稳定），语义与旧实现兼容）。
    if width is not None or height is not None:
        session._width_cache.set_dimensions(width, height)

    # ── React Ink render() options（官方 API 补齐） ──
    # stderr / debug / exitOnCtrlC / patchConsole / stdin
    if stderr is not None:
        session.set_stderr(stderr)
    session._debug = bool(debug)
    session.set_exit_on_ctrl_c(exitOnCtrlC)
    # ★ P2（review）：保存调用方 stdin 的 interrupt 配置——修复前
    #   ``set_interrupt_callback``/``set_interrupt_routable`` 修改传入的 Input
    #   实例且 unmount/cleanup 均不还原，会话退出后该 stdin 的 Ctrl+C 仍指向
    #   已退出会话（复用同一 stdin 开启新会话时行为取决于最后一次配置）。
    _saved_interrupt = None
    _saved_routable = None
    if stdin is not None:
        try:
            _saved_interrupt = stdin.get_interrupt_callback()
            _saved_routable = stdin.is_interrupt_routable()
        except Exception:
            _logger.debug("render 保存 stdin interrupt 配置异常", exc_info=True)
        session.set_input(stdin)
        if exitOnCtrlC:
            # Ctrl+C → 请求退出会话（interrupt 回调注入；生产 CLI 不经
            # render()，_loop 注入的 request_interrupt_async 不受影响）。
            try:
                stdin.set_interrupt_callback(lambda: session.request_exit())
            except Exception:
                _logger.debug("render exitOnCtrlC 注入 interrupt 回调异常", exc_info=True)
        else:
            # Ctrl+C 放行给 useInput handler（React Ink 语义）
            try:
                stdin.set_interrupt_routable(True)
            except Exception:
                _logger.debug("render exitOnCtrlC=False 放行 interrupt 异常", exc_info=True)
    patcher = _ConsolePatcher(out_stream, stderr)
    if patchConsole:
        try:
            patcher.patch()
        except Exception:
            _logger.debug("render patchConsole 补丁失败", exc_info=True)

    try:
        session.start()
    except Exception:
        # ★ P3（review）：start() 抛异常时恢复控制台补丁——修复前无 try/finally
        #   兜底，补丁残留（sys.stdout/sys.stderr 仍为 _ConsoleProxy）。
        if patchConsole:
            try:
                patcher.restore()
            except Exception:
                _logger.debug("render start 失败后恢复控制台异常", exc_info=True)
        raise

    def _wait_until_exit():
        async def _waiter():
            import asyncio as _aio
            while session._render_running:
                await _aio.sleep(0.05)
        return _waiter()

    def _restore_stdin() -> None:
        """还原调用方 stdin 的 interrupt 配置（幂等）。"""
        if stdin is None:
            return
        try:
            stdin.set_interrupt_callback(_saved_interrupt)
            stdin.set_interrupt_routable(bool(_saved_routable))
        except Exception:
            _logger.debug("render 还原 stdin interrupt 配置异常", exc_info=True)

    def _unmount():
        try:
            session.request_exit()
        except Exception:
            _logger.debug("render unmount 异常", exc_info=True)
        # ★ P2（review）：unmount 同时恢复控制台补丁与 stdin 配置（与文档
        #   「unmount()：停止渲染线程；patchConsole 时恢复控制台」一致）——
        #   修复前仅 cleanup() 恢复，调用方只调 unmount() 时 sys.stdout/
        #   sys.stderr 仍为 _ConsoleProxy（进程后续 print 被重定向进 TUI 流），
        #   stdin 的 Ctrl+C 仍指向已退出会话。两处恢复均幂等。
        if patchConsole:
            try:
                patcher.restore()
            except Exception:
                _logger.debug("render unmount 恢复控制台异常", exc_info=True)
        _restore_stdin()

    def _cleanup():
        """unmount + 控制台补丁/stdin 配置恢复（均幂等）。"""
        _unmount()

    def _rerender(new_element):
        _state["element"] = new_element
        session._request_render()

    return {
        "waitUntilExit": _wait_until_exit,
        "unmount": _unmount,
        "cleanup": _cleanup,
        "rerender": _rerender,
        "clear": session.request_clear,
    }


__all__ = ["render", "measureElement", "_SimpleModel"]
