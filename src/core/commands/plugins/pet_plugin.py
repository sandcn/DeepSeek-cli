"""PetPlugin — ``/pet`` 蓝色大肥鱼宠物浮窗。

交互语义（2026-10-22 用户需求）：

  - ``/pet``：召唤宠物；已显示时再按一次收回（开关切换）；
  - ``/pet on`` / ``/pet open``：强制召唤（已在显示则重新开窗、动画重置）；
  - ``/pet off``：收回宠物；
  - 宠物浮窗无边框、背景透明，只显示实际绘制的行内容（见
    ``src.tui.app.pet.pet_window_spec``）。

关闭不绑定 Esc：Esc 在 TUI 中承担中断 / 清空输入语义，避免宠物浮窗抢键。
"""

from __future__ import annotations

import logging
from typing import Any

from ...constants import BRIGHT_CYAN, DIM, RESET, YELLOW
from ..base import CommandMeta, declare_command_plugin
from .base import InteractiveCommandPlugin

_logger = logging.getLogger(__name__)

#: 收回宠物的参数（大小写不敏感）。
_OFF_WORDS = frozenset({"off", "close", "hide", "stop", "关", "关闭", "收起", "收回"})

#: 强制召唤的参数（已在显示时重新开窗）。
_ON_WORDS = frozenset({"on", "open", "show", "打开", "召唤", "开始"})


def _write_line(chat_ui, text: str) -> None:
    """向聊天区写一行（无 ChatUI / 测试桩异常时静默降级）。"""
    if chat_ui is None:
        return
    try:
        chat_ui.write_line(text)
    except Exception:
        _logger.debug("/pet 输出失败", exc_info=True)


def _call_bool(obj, method: str, *args) -> bool | None:
    """调用可选方法并转 bool；方法不存在 / 异常返回 None。"""
    fn = getattr(obj, method, None) if obj is not None else None
    if not callable(fn):
        return None
    try:
        return bool(fn(*args))
    except Exception:
        _logger.debug("/pet 调用失败 method=%s", method, exc_info=True)
        return None


def _active_chat_ui(loop) -> object | None:
    """取当前 ChatUIConsumer：循环已绑定优先，其次活跃消费者桥接。"""
    chat_ui = getattr(loop, "_chat_ui", None) if loop is not None else None
    if chat_ui is not None:
        return chat_ui
    try:
        from ...adapters.ui_runtime import get_active_chat_ui

        return get_active_chat_ui()
    except Exception:
        _logger.debug("/pet 获取活跃 ChatUI 失败", exc_info=True)
        return None


class PetPlugin(InteractiveCommandPlugin):
    """``/pet``：召唤 / 收回蓝色大肥鱼宠物浮窗。"""

    def __init__(self):
        super().__init__()
        self.meta = CommandMeta(
            name="pet",
            description="召唤/收回蓝色大肥鱼宠物（无边框透明浮窗，会跳来跳去）",
            group="general",
            usage="[on|off]",
        )

    def _emit(self, chat_ui, text: str) -> None:
        """输出到聊天区；无 ChatUI 时回退命令输出端口（单次模式）。"""
        if chat_ui is not None:
            _write_line(chat_ui, text)
            return
        try:
            self.output(text)
        except Exception:
            _logger.debug("/pet 输出失败", exc_info=True)

    def execute(self, ctx: Any) -> bool:
        """执行 ``/pet``（同步：只操作浮窗状态，无需异步等待）。"""
        arg = str(getattr(ctx, "arg", "") or "").strip().lower()
        chat_ui = _active_chat_ui(self._loop)

        if arg and arg not in _OFF_WORDS and arg not in _ON_WORDS:
            self._emit(
                chat_ui,
                f"  {YELLOW}用法: /pet [on|off]{RESET}"
                f"{DIM}  （无参数 = 召唤/收回切换）{RESET}",
            )
            return True

        if arg in _OFF_WORDS:
            closed = _call_bool(chat_ui, "close_pet_floating_window")
            if closed:
                self._emit(chat_ui, f"  {BRIGHT_CYAN}🐟 大肥鱼娘游走了～{RESET}")
            elif closed is False:
                self._emit(chat_ui, f"  {DIM}宠物浮窗本来就没开哦{RESET}")
            else:
                self._emit(chat_ui, f"  {YELLOW}当前界面不支持宠物浮窗{RESET}")
            return True

        if chat_ui is None:
            self._emit(
                chat_ui,
                f"  {YELLOW}宠物浮窗只在交互式终端界面可用{RESET}",
            )
            return True

        is_open = _call_bool(chat_ui, "is_pet_floating_window_open")
        if arg == "" and is_open:
            _call_bool(chat_ui, "close_pet_floating_window")
            self._emit(chat_ui, f"  {BRIGHT_CYAN}🐟 大肥鱼娘游走了～{RESET}")
            return True

        opened = _call_bool(chat_ui, "open_pet_floating_window")
        if opened:
            self._emit(
                chat_ui,
                f"  {BRIGHT_CYAN}🐟 蓝色大肥鱼娘出现啦！{RESET}"
                f"{DIM}  （/pet 收回；她会跳来跳去）{RESET}",
            )
        elif opened is False:
            self._emit(
                chat_ui,
                f"  {YELLOW}宠物浮窗打开失败（当前界面不支持浮动窗口）{RESET}",
            )
        else:
            self._emit(chat_ui, f"  {YELLOW}当前界面不支持宠物浮窗{RESET}")
        return True


# 模块级自注册
declare_command_plugin(PetPlugin())
