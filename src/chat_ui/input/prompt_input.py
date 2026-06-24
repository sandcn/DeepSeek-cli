"""prompt_toolkit 输入管理器 — 可选依赖，替代自研 EscapeMonitor。

通过环境变量 CHAT_UI_USE_PROMPT_TOOLKIT=1 启用。
不可用时自动回退到 EscapeMonitor。
"""

from __future__ import annotations

import logging
from typing import Callable

_logger = logging.getLogger(__name__)

_PROMPT_TOOLKIT_AVAILABLE = False
try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import InMemoryHistory
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.completion import Completer, Completion
    from prompt_toolkit.styles import Style
    _PROMPT_TOOLKIT_AVAILABLE = True
except ImportError:
    _logger.info("prompt_toolkit 不可用，回退到 EscapeMonitor 输入系统")


if _PROMPT_TOOLKIT_AVAILABLE:

    class ChatCompleter(Completer):
        """prompt_toolkit Completer 适配器，桥接 CompletionEngine。

        将 CompletionEngine 的补全结果转换为 prompt_toolkit 的 Completion 对象。
        """

        def __init__(self, completion_engine=None):
            self._engine = completion_engine

        def set_engine(self, engine):
            """动态设置或替换 CompletionEngine 实例。"""
            self._engine = engine

        def get_completions(self, document, complete_event):
            """prompt_toolkit 自动补全回调。

            由 prompt_toolkit 在用户输入时自动调用（异步上下文）。
            """
            if self._engine is None:
                return
            text = document.text_before_cursor
            items = self._engine.complete(text)
            for item in items:
                yield Completion(
                    item.text,
                    start_position=item.start_pos,
                    display=item.display,
                )


    class PromptInputManager:
        """prompt_toolkit 输入管理器。

        封装 PromptSession，提供与 EscapeMonitor 兼容的回调接口。
        通过 try/except ImportError 实现优雅降级——prompt_toolkit
        不可用时 available 为 False，外部调用方应回退到 EscapeMonitor。
        """

        def __init__(self):
            self._session: PromptSession | None = None
            self._history = None
            self._completer = ChatCompleter()
            self._on_input_callback: Callable[[str, int], None] | None = None
            self._on_model_switch: Callable[[int], None] | None = None
            self._available = _PROMPT_TOOLKIT_AVAILABLE
            self._queued_input: str | None = None

            self._history = InMemoryHistory()
            self._session = PromptSession(
                history=self._history,
                completer=self._completer,
                multiline=True,
                style=self._get_style(),
                key_bindings=self._get_key_bindings(),
                prompt_continuation='· ',
                wrap_lines=True,
            )

        @property
        def available(self) -> bool:
            """prompt_toolkit 是否可用。"""
            return self._available

        def set_on_input(self, callback: Callable[[str, int], None]) -> None:
            """设置输入回调 (text, cursor_pos)。

            prompt_toolkit 模式下此回调在每次输入变化时触发，
            用于实时更新底部栏输入预览。
            """
            self._on_input_callback = callback

        def set_completion_engine(self, engine) -> None:
            """设置补全引擎。"""
            self._completer.set_engine(engine)

        def set_model_switch_callback(self, callback: Callable[[int], None]) -> None:
            """设置模型切换回调。

            callback 签名: (delta: int) -> None
              delta: +1 正向切换，-1 反向切换。
            """
            self._on_model_switch = callback

        def set_prefill(self, text: str) -> None:
            """预填充文本。

            当前实现为已知限制：prompt_toolkit 的 PromptSession.prompt()
            不直接支持预填充文本。作为回退，将文本存入内部队列，
            外部可通过 get_queued_input() 获取。
            """
            self._queued_input = text

        def get_queued_input(self) -> str | None:
            """获取预填充文本（消费语义——读取后清空）。

            与 EscapeMonitor 的 StreamInputHandler.get_queued_input() 接口兼容。
            """
            text = self._queued_input
            self._queued_input = None
            return text

        def set_completion_callback(self, callback) -> None:
            """设置 Tab 补全回调（prompt_toolkit 内部处理补全，此方法为接口兼容保留）。

            prompt_toolkit 的 Completer 自动处理补全，无需外部回调。
            """
            pass

        def set_dismiss_completion_callback(self, callback) -> None:
            """设置补全关闭回调（prompt_toolkit 内部管理，此方法为接口兼容保留）。"""
            pass

        def set_completion_navigate_callback(self, callback) -> None:
            """设置补全导航回调（prompt_toolkit 内部管理，此方法为接口兼容保留）。"""
            pass

        def set_auto_completion_callback(self, callback) -> None:
            """设置自动补全回调（prompt_toolkit 内部管理，此方法为接口兼容保留）。"""
            pass

        async def run_async(self) -> str:
            """异步运行输入循环（在 asyncio 事件循环中调用）。

            返回用户提交的文本；EOFError/KeyboardInterrupt 时返回空字符串。
            """
            if self._session is None:
                return ""
            try:
                text = await self._session.prompt_async(
                    "",
                    multiline=True,
                )
                return text
            except (EOFError, KeyboardInterrupt):
                return ""

        def run_sync(self, prefill: str = "") -> str:
            """同步运行输入循环。

            返回用户提交的文本；EOFError/KeyboardInterrupt 时返回空字符串。
            prefill 参数为接口兼容保留（当前不支持 prompt_toolkit 预填充）。
            """
            if self._session is None:
                return ""
            try:
                text = self._session.prompt(
                    "",
                    multiline=True,
                )
                return text
            except (EOFError, KeyboardInterrupt):
                return ""

        @staticmethod
        def _get_style() -> Style:
            from prompt_toolkit.styles import Style as PTStyle
            return PTStyle.from_dict({
                'prompt': '#0087d7 bold',           # 青色提示符
                'continuation': '#6c6c6c',          # 灰色续行
                'completion-menu': 'bg:#303030 #ffffff',
                'completion-menu.completion': 'bg:#303030 #ffffff',
                'completion-menu.completion.current': 'bg:#444444 #ffffff',
            })

        def _get_key_bindings(self) -> KeyBindings:
            kb = KeyBindings()

            @kb.add('c-n')
            def _(event):
                """Ctrl+N 切换模型（正向）。"""
                if self._on_model_switch:
                    self._on_model_switch(1)

            @kb.add('c-p')
            def _(event):
                """Ctrl+P 切换模型（反向）。"""
                if self._on_model_switch:
                    self._on_model_switch(-1)

            @kb.add('escape', 'enter')
            def _(event):
                """Escape+Enter 发送消息。"""
                event.current_buffer.validate_and_handle()

            return kb

else:
    # prompt_toolkit 不可用时，ChatCompleter 和 PromptInputManager 设为 None
    ChatCompleter = None  # type: ignore[assignment]
    PromptInputManager = None  # type: ignore[assignment]
