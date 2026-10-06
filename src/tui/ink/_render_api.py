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

from src.tui._screen import TerminalWidthCache
from .element import Element

_logger = logging.getLogger(__name__)


#: InkSession 类注册槽（session.py 定义后经 register_session_cls 注入）——
#: 避免 ``_render_api → session → _render_api`` 模块加载期循环。
_session_cls = None


def register_session_cls(cls) -> None:
    """注册 InkSession 类（session.py 定义 InkSession 后调用）。"""
    global _session_cls
    _session_cls = cls


class _SimpleModel:
    """render() 独立会话的最小模型占位（满足 InkSession 读取的属性）。

    缺省属性说明（render() 独立会话中 InkSession / 组件树可能读取的模型
    属性，缺省值经 getattr 或类属性兜底）：
      - ``width``：渲染宽度（render() 尺寸覆盖写入，缺省 80）；
      - ``input_text`` / ``input_cursor``：输入区状态（update_input echo 回调）；
      - ``status``：状态对象（系统监控采集；缺省 None 时跳过）；
      - ``tool_boxes``：工具卡片容器（缺省 None）；
      - ``parse_line``：解析进度行（缺省 None）；
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
        dict：``{"x": int, "y": int, "width": int, "height": int}``——x/y 为
        布局树坐标（相对文档原点），未测量（None/无尺寸）时返回全 0（与官方
        未挂载节点行为对齐）。

    用法::

        ref = use_ref(None)
        useLayoutEffect(lambda: print(measureElement(ref.current)), ())
        return h(BOX, {"ref": ref}, ...)
    """
    box = dom_node
    if box is not None and hasattr(box, "current"):
        box = box.current
    if box is None:
        return {"x": 0, "y": 0, "width": 0, "height": 0}
    try:
        w = max(0, int(getattr(box, "w", 0) or 0))
        h = max(0, int(getattr(box, "h", 0) or 0))
        x = int(getattr(box, "x", 0) or 0)
        y = int(getattr(box, "y", 0) or 0)
    except (TypeError, ValueError, OverflowError):
        # 畸形尺寸（inf/nan/非数值）→ 0x0（渲染错误修复一贯防御）
        w, h, x, y = 0, 0, 0, 0
    return {"x": x, "y": y, "width": w, "height": h}


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
    maxFps: float | None = None,
    isScreenReaderEnabled: bool = False,
    kittyKeyboard=None,
    onRender=None,
) -> dict:
    """React Ink ``render()`` 等价物（轻量入口）：渲染组件树到终端。

    创建独立 InkSession 渲染给定元素（不依赖 App 模型/命令管线）——适用于
    组件开发/测试/独立 UI 场景。★ 会话隔离：本会话持有独立 ``HookContext``
    （hooks 状态 / 输入 router / app control / 屏幕阅读器开关互不干扰），
    同一进程可并存多个 ``render()`` 会话。返回控制对象：
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
        maxFps: React Ink 官方「渲染帧率上限」参数——本框架渲染线程恒定
            30Hz 且不可改变，参数保留以兼容官方 API，但不再生效（传入
            任意值帧率均为 30Hz）。
        isScreenReaderEnabled: 屏幕阅读器模式（useIsScreenReaderEnabled 返回
            True；供组件输出无障碍文本）。
        kittyKeyboard: kitty 键盘协议配置——None/False 不启用；True 启用；
            ``{"mode": "auto"|"enabled"|"disabled", "flags": [...]}`` 精细控制。
            启用时 render() 向 stdout 写协议启用序列（``CSI > flags u``），
            unmount/cleanup 时写禁用序列（``CSI < u``）。
        onRender: 每帧渲染后回调 ``(metrics: dict) -> None``（``width``/
            ``height``）。

    Returns:
        dict：控制对象（waitUntilExit/unmount/cleanup/rerender/clear/
        waitUntilRenderFlush）。
    """
    InkSession = _session_cls
    if InkSession is None:
        raise RuntimeError(
            "InkSession 未注册：render() 独立会话需先导入 src.tui.ink.session"
        )

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
    # ★ P2 review：改用公开入口 ``set_dimensions``——修复前直接写
    #   私有字段 ``_width``/``_height``/时间戳，破坏封装；且 ``_fetch()`` 到期
    #   会同时覆盖宽高与时间戳，仅覆盖其一时另一维度覆盖值静默失效。
    #   ``set_dimensions`` 置 ``_override`` 标志：覆盖期间 TTL 到期不重新探测
    #   真实终端（本会话尺寸稳定），语义与旧实现兼容）。（P3 review：删除
    #   此前描述「直接写私有字段 + 时间戳设为 monotonic()+ttl」的过时注释段
    #   ——实现已改用公开入口，两段注释互相矛盾。）
    if width is not None or height is not None:
        session._width_cache.set_dimensions(width, height)

    # ── React Ink render() 扩展 options ──
    # maxFps：官方语义为「渲染帧率上限」，本框架渲染线程**恒定 30Hz 且不可
    #   改变**（用户需求 2026-10-07）——按官方 API 保留参数签名（兼容调用
    #   方），但不再覆盖 render_interval（``TuiConfig.__post_init__`` 已强制
    #   render_interval 恒为 1/30，任何路径都无法改变帧率）。
    if maxFps is not None:
        _logger.debug(
            "render maxFps=%r 已忽略：渲染线程恒定 30Hz，帧率不可改变", maxFps,
        )
    # isScreenReaderEnabled：注入屏幕阅读器开关（useIsScreenReaderEnabled）
    # ★ 多会话隔离（P0 架构修复）：写入**本会话** HookContext（修复前写模块
    #   级全局——两个 render() 会话共用，退出还原亦相互覆盖）。
    _saved_screen_reader = session._hook_ctx.screen_reader_enabled
    if isScreenReaderEnabled:
        session._hook_ctx.screen_reader_enabled = True
    # onRender：每帧渲染后回调（React Ink v6）
    if callable(onRender):
        session._on_render_callback = onRender
    # kittyKeyboard：启用 kitty 键盘协议（render 内写终端序列；unmount 时禁用）
    from .kitty import resolve_kitty_options as _resolve_kitty
    kitty_flags = _resolve_kitty(kittyKeyboard)

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

    def _restore_screen_reader() -> None:
        """还原屏幕阅读器开关（幂等；render 退出后不泄漏会话状态）。"""
        try:
            if session._hook_ctx.screen_reader_enabled != _saved_screen_reader:
                session._hook_ctx.screen_reader_enabled = _saved_screen_reader
        except Exception:
            _logger.debug("render 还原屏幕阅读器开关异常", exc_info=True)

    def _restore_stdin() -> None:
        """还原调用方 stdin 的 interrupt 配置（幂等）。"""
        if stdin is None:
            return
        try:
            stdin.set_interrupt_callback(_saved_interrupt)
            stdin.set_interrupt_routable(bool(_saved_routable))
        except Exception:
            _logger.debug("render 还原 stdin interrupt 配置异常", exc_info=True)

    try:
        session.start()
    except Exception:
        # ★ P3（review）：start() 抛异常时恢复控制台补丁——修复前无 try/finally
        #   兜底，补丁残留（sys.stdout/sys.stderr 仍为 _ConsoleProxy）。
        # ★ P2（review 修复）：同时还原调用方 stdin 的 interrupt 配置——修复前
        #   启动失败路径只恢复控制台补丁，stdin 仍持有
        #   ``lambda: session.request_exit()``（指向已失败的会话）；且
        #   ``_restore_stdin`` 定义在 try 之后（此处调用会 NameError）→ 定义
        #   上移到 try 之前并在本分支调用（幂等）。
        if patchConsole:
            try:
                patcher.restore()
            except Exception:
                _logger.debug("render start 失败后恢复控制台异常", exc_info=True)
        _restore_stdin()
        _restore_screen_reader()
        raise

    # kitty 键盘协议：启动后写启用序列（官方 render({kittyKeyboard}) 语义）
    if kitty_flags >= 0:
        try:
            from .kitty import enable_sequence as _kitty_enable
            _kitty_stream = out_stream if out_stream is not None else sys.stdout
            _kitty_stream.write(_kitty_enable(kitty_flags))
            _kitty_stream.flush()
        except Exception:
            _logger.debug("render 启用 kitty 键盘协议失败", exc_info=True)

    def _disable_kitty() -> None:
        if kitty_flags < 0:
            return
        try:
            from .kitty import disable_sequence as _kitty_disable
            _kitty_stream = out_stream if out_stream is not None else sys.stdout
            _kitty_stream.write(_kitty_disable())
            _kitty_stream.flush()
        except Exception:
            _logger.debug("render 禁用 kitty 键盘协议失败", exc_info=True)

    def _wait_until_exit():
        async def _waiter():
            import asyncio as _aio
            while session._render_running:
                await _aio.sleep(0.05)
            result = getattr(session, "exit_result", None)
            if isinstance(result, BaseException):
                raise result
            return result
        return _waiter()

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
        _restore_screen_reader()
        _disable_kitty()

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
        "waitUntilRenderFlush": session._wait_render_flush,
    }


_RENDER_TO_STRING_MAX_PASSES = 10


def renderToString(element: Element, options: dict | None = None) -> str:
    """React Ink ``renderToString()`` 等价物：同步渲染组件树为字符串。

    与 ``render()`` 不同：不写 stdout、不建立终端事件监听、不启动渲染线程，
    直接把组件树调和/布局/绘制后的整帧文本返回。适用于文档生成、测试与
    需要字符串输出的场景。

    与官方语义差异（已文档化）：
      - 终端相关 hooks（useStdin/useStdout/useStderr/useApp/useFocus 等）返回
        安全默认（不抛异常，但无真实终端能力）；
      - ``useLayoutEffect``/``useEffect`` 在渲染提交期同步执行；两者触发的
        state 更新都会被**有界重渲染**（最多 ``_RENDER_TO_STRING_MAX_PASSES``
        轮）反映到最终输出（官方仅 layout effect 更新反射——本实现为超集，
        上限保护防被动 effect 每帧 set_state 造成死循环）。

    Args:
        element: 根元素（函数组件或 Element）。
        options: ``{"columns": int}``——虚拟终端列宽（默认 80）。

    Returns:
        渲染后的字符串（行间以 ``\\n`` 连接，末尾无换行）。
    """
    from .reconciler import Reconciler
    from . import components as _components

    columns = 80
    screen_reader = False
    if isinstance(options, dict):
        try:
            columns = max(1, int(options.get("columns", 80)))
        except (TypeError, ValueError, OverflowError):
            columns = 80
        # ★ 多会话隔离（P0 架构修复）：屏幕阅读器开关按**会话**生效——字符串
        #   渲染的开关经 options 注入本会话上下文（修复前只能写模块级全局，
        #   与并发会话互相污染；测试/独立使用亦无法隔离）。
        screen_reader = bool(options.get("isScreenReaderEnabled", False))

    dirty = {"n": 0}

    def _schedule_cb() -> None:
        dirty["n"] += 1

    # ★ 多会话隔离（P0 架构修复）：字符串渲染使用**独立的 HookContext**
    #   （``Reconciler`` 私有上下文 + headless 环境注入）——此前用「快照/还原
    #   hooks 模块全局状态」实现隔离，只覆盖 11 个字段（context 注册表 / 焦点
    #   / 窗口订阅等未覆盖）且非线程安全。现在全程不触碰其它会话的状态。
    reconciler = Reconciler(schedule_callback=_schedule_cb)
    reconciler.hook_context.install_headless(columns, 24)
    if screen_reader:
        reconciler.hook_context.screen_reader_enabled = True
    root = Reconciler.create_root()
    for _ in range(_RENDER_TO_STRING_MAX_PASSES):
        dirty["n"] = 0
        reconciler.render(root, element, columns, 0)
        if dirty["n"] == 0:
            break
    frame = _components.render_frame(root, columns)
    return "\n".join(line.render() for line in frame.lines)


__all__ = ["render", "renderToString", "measureElement", "_SimpleModel"]
