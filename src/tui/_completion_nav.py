"""补全导航策略 — Tab/箭头/翻页/Shift+Tab 的补全弹窗交互（策略类）。

模块边界（2026-08-05 架构优化）：从 ``_input_dispatcher.py`` 拆分——补全
弹窗导航逻辑（Tab 补全 / 箭头移动高亮 / 翻页 / 关闭 / 自动补全）独立为
策略类 ``_CompletionNavHandler``，InputDispatcher 组合持有并委托。策略
通过宿主 dispatcher 访问回调引用与输入缓冲编辑器（``_buffer_editor``）。

设计模式：策略（Strategy）——补全导航算法族与事件分发胶水解耦。
"""

from __future__ import annotations

import logging

_logger = logging.getLogger(__name__)


class _CompletionNavHandler:
    """补全弹窗导航策略（依赖宿主 dispatcher 的回调/缓冲）。

    宿主须提供字段：``_completion_callback``（Tab 补全）、
    ``_completion_navigate_callback``（箭头/翻页导航）、
    ``_dismiss_completion_callback``（关闭弹窗）、
    ``_auto_completion_callback``（自动补全）、``_buffer_editor``
    （输入缓冲编辑）。
    """

    #: 翻页步进说明（P3 review 2026-08-22）：PageUp/PageDown 步进量由
    #: ``_input_dispatcher`` 传入（±5），此处不定义死常量——修复前
    #: ``_PAGE_STEP = 5`` 定义后全模块无引用（与 dispatcher 的 ±5 双真源）。

    def __init__(self, dispatcher) -> None:
        self._d = dispatcher

    def handle_tab(self) -> None:
        """处理 Tab 键：调用补全回调，失败则插入制表符。"""
        d = self._d
        cb = d._completion_callback
        if cb is None:
            d._buffer_editor.handle_char('\t')
            return
        text = d._buffer_editor.get_current_text()
        try:
            result = cb(text)
        except Exception:
            _logger.debug("补全回调异常", exc_info=True)
            result = None
        if result is None:
            d._buffer_editor.handle_char('\t')
        elif result != text:
            # ★ 2026-08-06：仅 result 变化时 set_buffer——修复前无条件
            #   set_buffer(result)：首次 Tab（_first_tab 返回原 text，result
            #   == text）也会清除 _submitted_text/_input_ready（用户刚 Enter
            #   提交、编排器未消费时竞态窗口内按 Tab → 先前提交丢失）并重置
            #   光标（词中间按 Tab 光标被强制移到行尾）。
            d._buffer_editor.set_buffer(result)
            d._buffer_editor._echo(result)
            self.trigger_auto_completion()

    def handle_arrow_up(self) -> None:
        """处理上箭头：补全弹窗可见时仅移动高亮，否则历史浏览。"""
        d = self._d
        cb = d._completion_navigate_callback
        if cb is not None:
            try:
                text = d._buffer_editor.get_current_text()
                result = cb(-1, text)
            except Exception:
                _logger.debug("补全导航回调异常", exc_info=True)
                result = None
            if result is not None:
                if result != text:
                    d._buffer_editor.set_buffer(result)
                    d._buffer_editor._echo(result)
                    self.trigger_auto_completion()
                return
        d._buffer_editor._up()

    def handle_arrow_down(self) -> None:
        """处理下箭头：补全弹窗可见时仅移动高亮，否则历史浏览。"""
        d = self._d
        cb = d._completion_navigate_callback
        if cb is not None:
            try:
                text = d._buffer_editor.get_current_text()
                result = cb(1, text)
            except Exception:
                _logger.debug("补全导航回调异常", exc_info=True)
                result = None
            if result is not None:
                if result != text:
                    d._buffer_editor.set_buffer(result)
                    d._buffer_editor._echo(result)
                    self.trigger_auto_completion()
                return
        d._buffer_editor._down()

    def handle_page_nav(self, delta: int) -> None:
        """处理 PageUp/PageDown：补全弹窗可见时按页步进高亮，否则 no-op。

        2026-08-05（增加操作）：补全弹窗候选多时逐项 ↑↓ 效率低——PageUp/
        PageDown 一次移动一页（每页 ±5 项，与弹窗可见行数相当）。复用
        ``_completion_navigate_callback``（delta 传 ±5——补全循环实现按
        delta 步进并钳制/回绕）；补全不可见或回调未消费时 no-op（不改变
        输入缓冲/光标，与 Shift+Tab 语义一致）。
        """
        d = self._d
        cb = d._completion_navigate_callback
        if cb is None:
            return
        try:
            text = d._buffer_editor.get_current_text()
            result = cb(delta, text)
        except Exception:
            _logger.debug("补全翻页回调异常", exc_info=True)
            return
        if result is not None and result != text:
            d._buffer_editor.set_buffer(result)
            d._buffer_editor._echo(result)
            self.trigger_auto_completion()

    def handle_shift_tab_reverse(self) -> None:
        """处理 Shift+Tab：补全弹窗可见时反向循环，否则 no-op。

        方向A 步骤1：CSI u Shift+Tab（keycode=9, modifier=2）→ tab/modifier=2
        事件分发至此；补全导航回调未消费（补全不可见）时 no-op（不插入制表符）。
        """
        d = self._d
        cb = d._completion_navigate_callback
        if cb is None:
            return
        try:
            text = d._buffer_editor.get_current_text()
            result = cb(-1, text)
        except Exception:
            _logger.debug("补全导航回调异常", exc_info=True)
            return
        if result is not None and result != text:
            d._buffer_editor.set_buffer(result)
            d._buffer_editor._echo(result)
            self.trigger_auto_completion()

    def handle_editmsg_tab(self) -> None:
        """editmsg 模式 Tab：正向循环补全高亮（不写缓冲、不确认）。

        方向2（editmsg Tab 误写输入缓冲修复）：editmsg 选择期间 Tab 经
        ``_completion_navigate_callback(1, text)``（等价 ``_CmplHandler.on_navigate
        (+1)`` cycle_completion）移动高亮——弹窗可见时仅循环不高亮写入
        （on_navigate 返回 text 不应用，``_CmplHandler.on_navigate`` 已实现
        ``return text`` 不应用），不写输入缓冲、不确认。``_suppress_enter``
        为 editmsg 模式的代理标志（当前唯一场景）。
        """
        d = self._d
        cb = d._completion_navigate_callback
        if cb is None:
            return
        try:
            text = d._buffer_editor.get_current_text()
            cb(1, text)
        except Exception:
            _logger.debug("editmsg Tab 补全导航回调异常", exc_info=True)

    def dismiss_completion(self) -> None:
        """如果补全弹窗可见，关闭它。"""
        cb = self._d._dismiss_completion_callback
        if cb is not None:
            try:
                cb()
            except Exception:
                _logger.debug("关闭补全回调异常", exc_info=True)

    def maybe_dismiss_completion(self) -> None:
        """关闭补全弹窗（editmsg 选择期间除外——_suppress_enter=True 时不触发）。

        方向2（editmsg 选择期间 backspace 等误触发确认修复）：editmsg 选择期间
        ``_suppress_enter=True``，message_editor 将 dismiss 回调替换为确认信号
        （``_editmsg_dismiss`` 设置 ``_selection_ready``）——backspace/home/end/
        delete/unknown 等**非确认键**若触发 dismiss 会提前确认选择。仅 Enter
        保持无条件 ``_dismiss_completion()``（正常确认机制，不可改动——
        message_editor 依赖 dismiss 回调作为 Enter 确认信号）。
        """
        if self._d.get_suppress_enter():
            return
        # ★ 委托宿主 dispatcher 的 ``_dismiss_completion``（非策略自身方法）——
        #   测试契约 ``patch("..._dispatcher._dismiss_completion")`` 拦截计数。
        self._d._dismiss_completion()

    def trigger_auto_completion(self) -> None:
        """获取当前文本并调用自动补全回调。"""
        cb = self._d._auto_completion_callback
        if cb is None:
            return
        text = self._d._buffer_editor.get_current_text()
        try:
            cb(text)
        except Exception:
            _logger.debug("自动补全回调异常", exc_info=True)


__all__ = ["_CompletionNavHandler"]
