"""外部编辑器集成 — 模态弹窗内 Ctrl+G 的终端接管与恢复。

★ 2026-10-21（用户需求：user_select 自定义输入支持 Ctrl+G 用 vim 输入）：
主输入区的 Ctrl+G 由 ``src.app_loop._special_keys.make_vim_handler`` 处理
（恢复终端设置 → 拆底部栏 → 调编辑器 → 恢复底部栏/cbreak → flush stdin）。
模态弹窗（``UserSelectPopup``）在 input router 中先消费按键，无法走到主循环
的特殊键回调，因此本模块复用同一套终端接管步骤，供弹窗直接调用。

依赖方向：本模块仅依赖 L0（``src._editor``）；ChatUI 与 EscapeMonitor
在函数内延迟导入（避免 tui 表现层与 app_loop / consumer 的模块级循环）。
"""

from __future__ import annotations

import logging

from src._editor import edit_in_vim_sync

_logger = logging.getLogger(__name__)

__all__ = ["run_external_editor"]


def run_external_editor(text: str) -> "str | None":
    """在模态弹窗内同步调用外部编辑器（``EDITOR``，默认 vim）。

    步骤与主输入区 Ctrl+G 一致：
      1. ``EscapeMonitor.restore_terminal_settings()`` 恢复 cooked/echo；
      2. ``ChatUIConsumer.teardown_bottom_bar()`` 拆除底部栏/弹窗；
      3. 调 ``edit_in_vim_sync(text)``（同步阻塞当前 render 线程）；
      4. ``setup_bottom_bar()`` 恢复底部区域；
      5. ``apply_monitor_settings()`` 恢复 cbreak；
      6. flush stdin 残留字节（防止 vim 退出后的按键乱码注入）；
      7. request_bottom_redraw 让弹窗按新状态重绘。

    Args:
        text: 预填到编辑器中的当前自定义输入文本。

    Returns:
        编辑后的文本；编辑器未找到/启动失败/异常时 None（调用方保持原文本）。
    """
    chat_ui = None
    monitor = None
    try:
        from src.core.display_target import get_display_target

        chat_ui = get_display_target()
    except Exception:
        _logger.debug("Ctrl+G: 获取 ChatUI 失败，按无显示目标处理", exc_info=True)
    try:
        from src.api.escape_monitor import get_active_monitor

        monitor = get_active_monitor()
    except Exception:
        _logger.debug("Ctrl+G: 获取 EscapeMonitor 失败，按无监视器处理", exc_info=True)

    bar_torn_down = False
    try:
        if monitor is not None:
            try:
                monitor.restore_terminal_settings()
            except Exception:
                _logger.debug("Ctrl+G: 恢复终端设置失败", exc_info=True)
        if chat_ui is not None:
            try:
                chat_ui.teardown_bottom_bar()
                bar_torn_down = True
            except Exception:
                _logger.debug("Ctrl+G: 拆除底部栏失败", exc_info=True)
        return edit_in_vim_sync(text)
    except Exception:
        _logger.warning("Ctrl+G: 外部编辑器失败", exc_info=True)
        return None
    finally:
        if bar_torn_down and chat_ui is not None:
            try:
                chat_ui.setup_bottom_bar()
            except Exception:
                _logger.debug("Ctrl+G: 恢复底部栏失败", exc_info=True)
        if monitor is not None:
            try:
                monitor.apply_monitor_settings()
            except Exception:
                _logger.debug("Ctrl+G: 恢复 cbreak 失败", exc_info=True)
        try:
            input_ = getattr(chat_ui, "input", None) if chat_ui is not None else None
            if input_ is not None and hasattr(input_, "flush_stdin_buffer"):
                input_.flush_stdin_buffer()
        except Exception:
            _logger.debug("Ctrl+G: flush stdin 失败", exc_info=True)
        if chat_ui is not None:
            try:
                chat_ui.request_bottom_redraw()
            except Exception:
                _logger.debug("Ctrl+G: 请求重绘失败", exc_info=True)
