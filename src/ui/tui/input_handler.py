"""TUI 用户输入处理 — 封装 prompt_toolkit 输入交互

职责：
  - 管理用户输入流程（单行输入，Enter 提交）
  - 管理历史记录 & 草稿保存
  - 注入 key_bindings / completer（通过 InputHandler 实例）

使用模式：
    handler = InputHandler()
    handler.set_key_bindings(kb)
    text = handler.get_user_input()
"""

from __future__ import annotations

import logging
from pathlib import Path

from prompt_toolkit import prompt
from prompt_toolkit.history import FileHistory
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.key_binding import KeyBindings

from ...config import INPUT_HISTORY_FILE
from .._lock import locked_print
from .completer import ChatCompleter, create_chat_completer
from ...paths import CHAT_DIR

_logger = logging.getLogger(__name__)


# ── InputHandler 类 ─────────────────────────────────────


class InputHandler:
    """用户输入处理器 — 封装 prompt_toolkit 输入交互。

    实例级状态：
      - key_bindings: 键盘绑定实例
      - completer: 自动补全器（惰性创建，实例级缓存）
      - history: FileHistory 实例（惰性创建，实例级缓存）
      - draft_path: 草稿文件路径（可通过构造函数注入）

    线程安全：所有操作均为单线程 UI 操作，无需锁。
    """

    def __init__(self, draft_path: str | Path | None = None) -> None:
        self._key_bindings: KeyBindings | None = None
        self._completer: ChatCompleter | None = None
        self._history: FileHistory | None = None
        self._draft_path = Path(draft_path) if draft_path is not None else (CHAT_DIR / "draft.txt")

    # ── 配置 ─────────────────────────────────────────────

    def set_key_bindings(self, kb: KeyBindings) -> None:
        """设置键盘绑定实例。"""
        self._key_bindings = kb

    def get_key_bindings(self) -> KeyBindings | None:
        """获取当前键盘绑定实例。"""
        return self._key_bindings

    # ── 核心方法 ─────────────────────────────────────────

    def get_user_input(self, default: str = "", show_prompt: bool = True,
                       key_bindings: KeyBindings | None = None) -> str:
        """获取用户输入，Enter 提交。

        Args:
            default: 默认输入文本。
            show_prompt: 是否显示 ◆ 提示符。传入 False 让 prompt_toolkit 不重复渲染。
            key_bindings: 可选的 KeyBindings 实例。
                          不传时使用实例级默认。

        Returns:
            用户输入的文本（已 strip），空字符串表示无输入。
        """
        history = self._get_or_create_history()
        completer = self._get_or_create_completer()
        prompt_prefix = HTML('  <style fg="ansicyan">◆</style> ') if show_prompt else HTML('  ')
        kb_to_use = key_bindings or self._key_bindings

        try:
            result = prompt(
                prompt_prefix,
                default=default,
                key_bindings=kb_to_use,
                history=history,
                completer=completer,
                complete_while_typing=True,
                multiline=False,
            )
            result = result.strip()
        except EOFError:
            return ""
        except Exception:  # 宽捕获是故意的：prompt_toolkit 可能抛出多种异常类型，均需降级为返回空输入
            _logger.exception("获取用户输入时发生异常")
            locked_print("\n⚠ 输入处理异常，请重试")
            return default or ""

        # ── 草稿保存：仅非命令、非空输入 ──
        if result and not result.startswith('/'):
            try:
                self._draft_path.write_text(result, encoding='utf-8')
            except (OSError, IOError):
                _logger.warning("草稿保存失败: %s", self._draft_path)

        return result

    # ── 内部方法 ─────────────────────────────────────────

    def _get_or_create_history(self) -> FileHistory:
        """获取或创建 FileHistory 实例（实例级缓存，惰性创建）。"""
        if self._history is None:
            # mkdir 仅在首次调用时执行（_history=None 时），后续缓存命中跳过
            INPUT_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
            self._history = FileHistory(str(INPUT_HISTORY_FILE))
        return self._history

    def _get_or_create_completer(self):
        """获取或创建 ChatCompleter 实例（实例级缓存，惰性创建）。"""
        if self._completer is None:
            self._completer = create_chat_completer()
        return self._completer


__all__ = [
    "InputHandler",
]
