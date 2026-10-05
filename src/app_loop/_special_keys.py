"""特殊按键回调 — 注册表驱动的 vim/switch_model/editmsg 按键工厂。

「一切皆插件」：``make_special_key_callback`` 的 action 分发不再硬编码 if/elif
链，而是由 ``src.app_loop._special_handlers`` 注册表驱动——每个内置处理器
（vim / editmsg / retry / toggle_theme / switch_model / cycle_mode）由清单中的
独立插件条目（``special_key``）注册，可被 Patch/Overlay 单独禁用/覆盖/替换。

本模块仅保留各处理器的**工厂实现**（``make_*_handler(env)``）与回调装配；
处理器业务逻辑在此保持不变（对 ``edit_in_vim_sync`` 等模块属性的 monkeypatch
仍然生效——工厂在调用期读取模块全局）。
"""

from __future__ import annotations

import logging
from typing import Optional

from ._editor import edit_in_vim_sync

_logger = logging.getLogger(__name__)


class SpecialKeyEnv:
    """特殊键处理器的运行时上下文（loop/session/state/chat_ui/monitor）。"""

    __slots__ = ("loop", "session", "state", "chat_ui", "monitor")

    def __init__(self, loop, session, state, chat_ui, monitor=None):
        self.loop = loop
        self.session = session
        self.state = state
        self.chat_ui = chat_ui
        self.monitor = monitor


def make_vim_handler(env: SpecialKeyEnv):
    """vim 处理器：终端模式切换 + 底部栏拆装 + edit_in_vim_sync 委托。"""
    monitor = env.monitor
    chat_ui = env.chat_ui

    def _handler(text: str) -> Optional[str]:
        # ── 单线程模型：直接操作终端模式 + 底部栏拆装 ──
        # 不能调用 chat_ui.suspend()（会 join 当前 render 线程导致死锁）
        # 使用 EscapeMonitor 公开 API 操作终端模式
        if monitor is not None:
            monitor.restore_terminal_settings()
        bar_torn_down = False
        if chat_ui is not None:
            chat_ui.teardown_bottom_bar()
            bar_torn_down = True
        try:
            return edit_in_vim_sync(text)
        finally:
            if bar_torn_down and chat_ui is not None:
                chat_ui.setup_bottom_bar()
            if monitor is not None:
                monitor.apply_monitor_settings()
                # ★ vim 退出后恢复 cbreak，清空 stdin 残留字节（防止乱码注入）
                input_ = chat_ui.input if chat_ui is not None else None
                if input_ is not None:
                    input_.flush_stdin_buffer()

    return _handler


def make_editmsg_handler(env: SpecialKeyEnv):
    """editmsg 处理器：返回 '/editmsg' 命令。"""

    def _handler(text: str) -> str:
        return "/editmsg"

    return _handler


def make_retry_handler(env: SpecialKeyEnv):
    """retry 处理器：返回 '/retry' 命令（重新生成上一轮）。"""

    def _handler(text: str) -> str:
        return "/retry"

    return _handler


def make_toggle_theme_handler(env: SpecialKeyEnv):
    """toggle_theme 处理器：dark/light 循环切换（不提交输入）。"""
    chat_ui = env.chat_ui

    def _handler(text: str) -> str:
        try:
            from ..core.commands._ui_adapter import CommandUiAdapter

            adapter = CommandUiAdapter()
            names = [n for n, _d in adapter.get_theme_names_with_desc()]
            if len(names) < 2:
                return text
            current = adapter.get_active_theme()
            if current not in names:
                current = names[0]
            idx = names.index(current)
            nxt = names[(idx + 1) % len(names)]
            adapter.set_theme(nxt)
            if chat_ui is not None:
                chat_ui.on_notification(f"+ 已切换到主题 {nxt}")
        except Exception:
            _logger.debug("toggle_theme 异常", exc_info=True)
        return text

    return _handler


def make_switch_model_handler(env: SpecialKeyEnv):
    """switch_model 处理器：循环切换模型并同步 provider。"""
    session = env.session
    state = env.state
    chat_ui = env.chat_ui

    def _handler(text: str) -> Optional[str]:
        _models: list[str] = []
        try:
            from ..config import MODELS as _MODELS

            _models = _MODELS
        except Exception:
            pass
        # 合并 PROVIDERS 内置模型（去重保序）：RC 旧 models 未包含的
        # 新模型（如 deepseek-flash）也能通过 Ctrl+N 切换
        try:
            from ..core.commands._model_cmd import _merge_provider_models

            _models = _merge_provider_models(list(_models or []))
        except Exception:
            pass
        if not _models:
            try:
                from ..config.defaults import PROVIDERS as _PROVIDERS

                _seen: set[str] = set()
                for _p in _PROVIDERS.values():
                    for _m in _p.get("models", []):
                        if _m not in _seen:
                            _seen.add(_m)
                            _models.append(_m)
            except Exception:
                _models = []
        if not _models:
            return None
        current = state.model
        if not current:
            return None
        if current not in _models:
            next_model = _models[0]
        else:
            try:
                idx = _models.index(current)
                next_model = _models[(idx + 1) % len(_models)]
            except (ValueError, IndexError):
                return None
        session.model = next_model
        state.model = next_model
        # ── 同步 provider（与 /model 命令逻辑一致） ─────
        try:
            from ..core.commands._model_cmd import _infer_model_provider
            from ..config.loader import get_rc, update_config

            _inferred = _infer_model_provider(next_model)
            if _inferred is not None:
                _current_provider = get_rc().get("provider", "")
                if _inferred != _current_provider:
                    update_config("provider", _inferred)
        except (ImportError, KeyError):
            pass
        # ────────────────────────────────────────────────
        if chat_ui is not None:
            chat_ui.bottom_bar.set_model_name(next_model)
            chat_ui.on_notification(f"+ 已切换到 {next_model}")
        return text

    return _handler


def make_cycle_mode_handler(env: SpecialKeyEnv):
    """cycle_mode 处理器：Ctrl+B 循环切换主 agent 运行模式（空→简单→标准→空）。"""
    session = env.session
    chat_ui = env.chat_ui

    def _handler(text: str) -> str:
        # 系统提词按当前模式替换为 prompts_export_main_empty/simple/main.md
        # （builder 层模式状态 + agent 消息重建）。
        try:
            from ..prompt_builder.builder import cycle_mode, mode_label

            mode = cycle_mode()
            label = mode_label(mode)
            agent = getattr(session, "_agent", None) or getattr(session, "agent", None)
            if agent is not None and hasattr(agent, "rebuild_system_prompt"):
                try:
                    agent.rebuild_system_prompt()
                except Exception:
                    _logger.debug("rebuild_system_prompt 异常", exc_info=True)
            if chat_ui is not None:
                chat_ui.on_notification(f"+ 主 Agent 已切换到{label}")
        except Exception:
            _logger.debug("cycle_mode 切换异常", exc_info=True)
        return text

    return _handler


def make_special_key_callback(loop, session, state, chat_ui, monitor=None):
    """创建特殊按键回调函数。

    返回 _on_special_key(action, text) 回调，处理（均由注册表驱动，可被清单
    条目禁用/覆盖/替换）：

    - 'vim'：启动 vim 编辑器编辑文本
    - 'editmsg'：返回 '/editmsg' 命令
    - 'retry'：返回 '/retry' 命令
    - 'toggle_theme'：dark/light 循环切换（不提交输入）
    - 'switch_model'：循环切换模型
    - 'cycle_mode'：Ctrl+B 循环切换主 agent 运行模式（空模式 → 简单模式
      → 标准模式 → 空模式），'empty_mode' 为兼容别名

    monitor: EscapeMonitor 实例，用于 vim 路径中的终端模式切换。
    """
    from ._special_handlers import build_special_key_handlers

    env = SpecialKeyEnv(loop=loop, session=session, state=state, chat_ui=chat_ui, monitor=monitor)
    handlers = build_special_key_handlers(env)

    def _on_special_key(action: str, text: str) -> Optional[str]:
        handler = handlers.get(action)
        if handler is None:
            return None
        return handler(text)

    return _on_special_key


__all__ = [
    "SpecialKeyEnv",
    "make_vim_handler",
    "make_editmsg_handler",
    "make_retry_handler",
    "make_toggle_theme_handler",
    "make_switch_model_handler",
    "make_cycle_mode_handler",
    "make_special_key_callback",
]
