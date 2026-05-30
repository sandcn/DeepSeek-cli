"""用户输入模块 — 兼容包装层

实现在 `src/ui/tui/` 子模块中（key_bindings + completer + input_handler）。
此文件保持向后兼容，所有符号从 tui 子模块重新导出。

分层说明：
  - `src/ui/tui/key_bindings.py` — 按键绑定（create_key_bindings, KeyBindingsFactory）
  - `src/ui/tui/completer.py` — 自动补全（ChatCompleter, create_chat_completer）
  - `src/ui/tui/input_handler.py` — 输入处理（InputHandler 类）
"""

from __future__ import annotations

from prompt_toolkit.key_binding import KeyBindings

from .tui.key_bindings import create_key_bindings  # noqa: F401

from .tui.completer import (  # noqa: F401
    ChatCompleter,
    create_chat_completer,
)

from .tui.input_handler import InputHandler as _InputHandler


# ── 模块级便捷函数（使用缓存的 InputHandler 实例） ────

_handler: _InputHandler | None = None


def set_key_bindings(kb: KeyBindings) -> None:
    """设置缓存的 InputHandler 的键盘绑定，供所有后续 get_user_input 调用使用。

    Args:
        kb: 要注入的 KeyBindings 实例（由 create_key_bindings 创建）。
    """
    global _handler
    if _handler is None:
        _handler = _InputHandler()
    _handler.set_key_bindings(kb)


def get_user_input(default: str = "", show_prompt: bool = True,
                   key_bindings: KeyBindings | None = None) -> str:
    """获取用户输入（便捷函数，使用缓存的 InputHandler 实例）。

    若已通过 set_key_bindings(KB) 设置实例级绑定，则 key_bindings 参数
    可省略（推荐方式）；也可每次显式传入 key_bindings 覆盖默认。

    Args:
        default: 默认输入文本。
        show_prompt: 是否显示 ◆ 提示符。
                     输入提示，传入 False 让 prompt_toolkit 不重复渲染。
        key_bindings: 可选的 KeyBindings 实例。省略时使用实例级默认绑定。

    Returns:
        用户输入的文本（已 strip），空字符串表示无输入。
    """
    global _handler
    if _handler is None:
        _handler = _InputHandler()
    return _handler.get_user_input(
        default=default,
        show_prompt=show_prompt,
        key_bindings=key_bindings,
    )
