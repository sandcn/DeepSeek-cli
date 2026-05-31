"""TUI 键盘绑定 — KeyBindingsFactory 类封装

所有 handler 逻辑封装为 KeyBindingsFactory 的实例方法，
通过构造函数注入状态树，消除模块级 handler 函数 + 闭包注入模式。

用法：
    factory = KeyBindingsFactory(tree, on_switch_model=callback)
    kb = factory.create()
"""

from __future__ import annotations

import os
import sys
import asyncio
import tempfile
import shutil
import logging
from typing import Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from ._state import TUIStateTree

from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.key_binding.key_processor import KeyPressEvent
from prompt_toolkit.filters import buffer_has_focus
from prompt_toolkit.application.run_in_terminal import in_terminal, run_in_terminal

from .._lock import locked_print

_logger = logging.getLogger(__name__)

_ACCENT = "\033[1;96m"
_ACCENT2 = "\033[38;5;39m"
_DIM = "\033[2m"
_RESET = "\033[0m"
_GREEN = "\033[32m"
_BRIGHT = "\033[1m"
_DGRAY = "\033[90m"


# ═══════════════════════════════════════════════════════════
# KeyBindingsFactory 类
# ═══════════════════════════════════════════════════════════

class KeyBindingsFactory:
    """键盘绑定工厂 — 封装 handler 逻辑与状态注入。

    将 4 个 handler 作为实例方法管理，通过构造函数注入 TUIStateTree，
    消除模块级函数 + 闭包捕获模式，提供更清晰的依赖关系和可测试性。

    用法：
        factory = KeyBindingsFactory(tree, on_switch_model=callback)
        kb = factory.create()
    """

    def __init__(
        self,
        tree: TUIStateTree,
        on_switch_model: Callable[[str], None] | None = None,
    ) -> None:
        self._tree = tree
        self._on_switch_model = on_switch_model
        try:
            from ...config import MODELS as _MODELS
        except Exception:
            _MODELS = []
        self._models = _MODELS

    def create(self) -> KeyBindings:
        """创建注入状态的 KeyBindings 实例。"""
        kb = KeyBindings()

        @kb.add('escape', filter=buffer_has_focus, eager=True)
        def _handle_esc(event):
            self._handle_esc(event)

        @kb.add('c-g', filter=buffer_has_focus, eager=True)
        async def _handle_vim(event):
            await self._handle_vim(event)

        @kb.add('c-o', filter=buffer_has_focus, eager=True)
        def _handle_editmsg(event):
            self._handle_editmsg(event)

        @kb.add('c-n', filter=buffer_has_focus, eager=True)
        async def _handle_switch_model(event):
            await self._handle_switch_model(event)

        return kb

    # ── Handler 实现 ───────────────────────────────────

    def _handle_esc(self, event: KeyPressEvent) -> None:
        """Esc 键处理：双击清空输入内容。"""
        try:
            if self._tree.input.record_esc_press():
                event.current_buffer.text = ''
        except Exception:
            _logger.exception("Esc 处理异常")

    async def _handle_vim(self, event: KeyPressEvent) -> None:
        """Ctrl+G：vim 编辑当前输入内容。

        vim 是交互式 TUI 编辑器，必须独占终端控制权。
        使用 in_terminal() 暂停 prompt_toolkit、切回 cooked mode 后，
        异步运行 subprocess（asyncio.create_subprocess_exec），
        退出后自动恢复 TUI 渲染。
        """
        text = event.current_buffer.text
        async with in_terminal():
            edited = await self._edit_in_vim(text)
        if edited is not None:
            event.current_buffer.text = edited
        else:
            async with in_terminal():
                locked_print(f"\n  {_DGRAY}\u2514{_RESET} {_ACCENT2}\u26a0{_RESET} {_DIM}\u7f16\u8f91\u5668\u672a\u627e\u5230\uff0c\u8bf7\u5b89\u88c5 vim \u6216\u8bbe\u7f6e EDITOR \u73af\u5883\u53d8\u91cf{_RESET}")

    def _handle_editmsg(self, event: KeyPressEvent) -> None:
        """Ctrl+O：编辑当前会话消息。"""
        event.current_buffer.text = '/editmsg'
        event.current_buffer.validate_and_handle()

    async def _handle_switch_model(self, event: KeyPressEvent) -> None:
        """Ctrl+N：循环切换模型。"""
        _MODELS = self._models
        if not _MODELS:
            return

        current = self._tree.session.model
        if not current or current not in _MODELS:
            current = _MODELS[0]

        try:
            idx = _MODELS.index(current)
            next_model = _MODELS[(idx + 1) % len(_MODELS)]
        except (ValueError, IndexError):
            return

        # 通过回调直接更新模型（单数据源：UISessionState.model）
        if self._on_switch_model is not None:
            self._on_switch_model(next_model)

        # ★ 美化：在终端底部显示切换通知（模型名高亮 + 方向箭头指示）
        await run_in_terminal(lambda: locked_print(
            f"\n  {_ACCENT}\u25c9{_RESET} {_BRIGHT}{_ACCENT}{next_model}{_RESET}"
            f"  {_DGRAY}\u2502{_RESET}"
            f"  {_DIM}\u2318N{_RESET}  {_GREEN}\u27a1{_RESET}"
            f"  {_ACCENT2}\u2714{_RESET}"
            f"  {_DGRAY}\u7a97\u53e3\u5df2\u5207\u6362{_RESET}"
        ))

    # ── 工具方法 ───────────────────────────────────────

    @staticmethod
    async def _edit_in_vim(initial_text: str = "") -> str | None:
        """打开 vim 编辑内容，返回编辑后的文本（异步 subprocess）。

        使用 ``asyncio.create_subprocess_exec`` 替代同步 ``subprocess.call``，
        避免在 ``in_terminal()`` 上下文中阻塞事件循环。
        """
        tmpfile: str | None = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False, encoding='utf-8') as f:
                f.write(initial_text)
                tmpfile = f.name
            editor = os.environ.get('EDITOR', 'vim')
            editor_path = shutil.which(editor)
            if not editor_path:
                _logger.warning("vim 编辑器未找到: %s", editor)
                return None
            proc = await asyncio.create_subprocess_exec(
                editor_path, tmpfile,
                stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr,
            )
            ret = await proc.wait()
            if ret != 0:
                _logger.warning("vim 退出码: %d", ret)
            with open(tmpfile, 'r', encoding='utf-8') as f:
                result = f.read()
            return result
        except FileNotFoundError:
            _logger.warning("vim 未安装，请先安装 vim")
            return None
        except OSError as e:
            _logger.error("vim 编辑失败: %s", e)
            return None
        finally:
            if tmpfile is not None:
                try:
                    os.unlink(tmpfile)
                except OSError:
                    pass


# ═══════════════════════════════════════════════════════════
# 向后兼容入口
# ═══════════════════════════════════════════════════════════

def create_key_bindings(
    tree: TUIStateTree,
    on_switch_model: Callable[[str], None] | None = None,
) -> KeyBindings:
    """创建按键绑定（向后兼容函数 → 委托 KeyBindingsFactory）。

    Args:
        tree: TUI 统一状态树实例
        on_switch_model: 模型切换回调

    Returns:
        注入状态的 KeyBindings 实例
    """
    return KeyBindingsFactory(tree, on_switch_model=on_switch_model).create()


__all__ = ["create_key_bindings", "KeyBindingsFactory"]
