"""通用全屏视图打开器 — 命令线程打开模态全屏视图的共享协议。

背景：``/config``、``/plugin``、``/models`` 三个命令此前各自复刻「构建视图状态
→ 设置 ``model.<state>`` → ``model.fullscreen = <id>`` → ``request_bottom_redraw``
→ 轮询 ``state.done`` → finally 清理」模板（逐行等价、字段名不同）。新增多个
全屏视图（sessions / changes / theme / skill / mcp / usage / search /
keymap / notify / export）后集中收敛为本模块，避免多份模板漂移。

协议（与既有 `_open_config_ui` / `_open_plugin_ui` / `_open_model_view` 同构）：

  1. 读取当前视图状态（保留 ``seq``，每次打开递增——App 用 key 强制组件重挂载）；
  2. ``setup(model, state)`` 回调填充数据（如 entries）；
  3. ``model.<state_attr> = state``；``model.fullscreen = view_id``；
  4. ``request_bottom_redraw``；
  5. 轮询 ``state.done``（每轮调用 ``on_tick(state)`` 处理回传；``on_tick`` 返回
     True 请求关闭；deadline 超时兜底）；
  6. finally 清理：重置状态（保留清理前的 ``seq``，保证单调不重复）、仅当仍
     占用 ``fullscreen`` 时置空（用户可能已切到其他全屏视图）、
     ``request_bottom_redraw``、``flush_input_router``。

无活跃 ChatUI / 模型不可用（单次模式、测试桩）返回 False，调用方回退文本显示。
"""

from __future__ import annotations

import logging
import threading
import time as _time

from ..adapters.output import get_default_output_port

_logger = logging.getLogger(__name__)
_out = get_default_output_port()

#: 视图默认超时（秒）——用户长时间无操作自动关闭，命令线程不永久阻塞。
DEFAULT_TIMEOUT = 600.0
#: 轮询间隔（秒）。
_POLL_INTERVAL = 0.05


def open_fullscreen_view(
    ctx,
    *,
    view_id: str,
    state_attr: str,
    state_cls,
    setup=None,
    bg_setup=None,
    on_tick=None,
    timeout: float = DEFAULT_TIMEOUT,
    close_hint: str = "界面已关闭",
    timeout_hint: str = "界面超时关闭",
) -> bool:
    """打开模态全屏视图并阻塞轮询至关闭 / 超时。

    Args:
        ctx: 命令上下文（仅用于日志；不强制依赖具体字段）。
        view_id: 全屏视图 id（写入 ``model.fullscreen``）。
        state_attr: 模型上的视图状态属性名（如 ``"sessions_view"``）。
        state_cls: 视图状态类（``visible/seq/deadline/done`` 等字段 + ``try_set_final``）。
        setup: ``setup(model, state)`` 回调——同步填充（可为 None）。
        bg_setup: ``bg_setup(state, refresh)`` 回调——在**后台线程**执行
            （视图初始化数据异步加载，如会话列表/预览构建）；执行期间界面
            显示加载占位，完成后调用 ``refresh()`` 请求重绘（动态更新界面）。
        on_tick: ``on_tick(state) -> bool`` 回调——每轮轮询调用；返回 True 请求
            关闭视图（置终态后 break）。用于处理组件回传（如应用选中项）。
        timeout: 超时秒数（默认 600）。
        close_hint: 正常关闭时的文本提示。
        timeout_hint: 超时关闭时的文本提示。

    Returns:
        True — 视图已打开并正常关闭 / 超时（调用方无需回退）；
        False — 无活跃 ChatUI / 模型不可用（调用方回退文本显示）。
    """
    try:
        from ..adapters.ui_runtime import get_active_chat_ui

        chat_ui = get_active_chat_ui()
        if chat_ui is None:
            return False
        model = chat_ui.get_model() if hasattr(chat_ui, "get_model") else None
        if model is None or not hasattr(model, state_attr):
            return False
    except Exception:
        return False

    prev_seq = getattr(getattr(model, state_attr, None), "seq", 0)
    state = state_cls(
        visible=True,
        seq=prev_seq + 1,
        deadline=_time.monotonic() + timeout,
    )
    if setup is not None:
        try:
            setup(model, state)
        except Exception:
            _logger.debug("open_fullscreen_view setup 失败（忽略）", exc_info=True)

    setattr(model, state_attr, state)
    model.fullscreen = view_id
    try:
        chat_ui.request_bottom_redraw()
    except Exception:
        pass

    # ★ 2026-10-10（初始化性能异步加载）：视图数据（会话列表 / 预览）在
    #   后台线程构建，界面先显示加载占位，完成后请求重绘动态更新。
    if bg_setup is not None:
        _start_bg_setup(bg_setup, state, chat_ui, view_id)

    try:
        while not state.done:
            if on_tick is not None:
                try:
                    if on_tick(state):
                        state.try_set_final("done")
                        break
                except Exception:
                    _logger.debug("open_fullscreen_view on_tick 异常", exc_info=True)
            if _time.monotonic() >= state.deadline:
                state.try_set_final("timeout")
                break
            _time.sleep(_POLL_INTERVAL)
        if getattr(state, "action", "") == "timeout":
            _out.write(f"  ! {timeout_hint}", level="raw", source="cmd")
        else:
            _out.write(f"  {close_hint}", level="raw", source="cmd")
        return True
    finally:
        _cleanup(model, state_attr, state, view_id, state_cls, chat_ui)


def _start_bg_setup(bg_setup, state, chat_ui, view_id: str) -> None:
    """在后台线程执行视图数据构建（``bg_setup(state, refresh)``）。

    ``refresh`` 请求渲染线程重绘（数据就绪后界面动态更新）。视图已关闭
    （``state.done``）时 bg_setup 应自行跳过写回（本函数不做强制，保持
    bg_setup 对自身数据流负责）。
    """

    def _refresh() -> None:
        try:
            chat_ui.request_bottom_redraw()
        except Exception:
            _logger.debug("bg_setup 重绘请求失败", exc_info=True)

    def _work() -> None:
        try:
            bg_setup(state, _refresh)
        except Exception:
            _logger.debug("bg_setup 执行失败（忽略）", exc_info=True)

    threading.Thread(
        target=_work, name=f"view-bg-{view_id}", daemon=True,
    ).start()


def _cleanup(model, state_attr, state, view_id, state_cls, chat_ui) -> None:
    """清理视图状态（保留 seq 单调 + 仅当仍占用 fullscreen 时置空）。"""
    try:
        if not state.done:
            state.try_set_final("timeout")
        if getattr(model, state_attr, None) is state:
            cur_seq = getattr(model, state_attr, None)
            cur_seq = getattr(cur_seq, "seq", 0)
            setattr(model, state_attr, state_cls(seq=cur_seq))
        if getattr(model, "fullscreen", "") == view_id:
            model.fullscreen = ""
        try:
            chat_ui.request_bottom_redraw()
        except Exception:
            pass
        try:
            chat_ui.flush_input_router(2.0)
        except Exception:
            pass
    except Exception:
        _logger.debug("open_fullscreen_view cleanup 失败", exc_info=True)


__all__ = ["open_fullscreen_view", "DEFAULT_TIMEOUT"]
