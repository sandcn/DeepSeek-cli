"""特殊按键回调 — vim/switch_model/editmsg 按键工厂"""

from __future__ import annotations

import logging
import sys

from ._editor import edit_in_vim_sync

_logger = logging.getLogger(__name__)


def make_special_key_callback(loop, session, state, chat_ui, monitor=None):
    """创建特殊按键回调函数

    返回 _on_special_key(action, text) 回调，处理：
    - 'vim'：启动 vim 编辑器编辑文本
    - 'editmsg'：返回 '/editmsg' 命令
    - 'switch_model'：循环切换模型

    monitor: EscapeMonitor 实例，用于 vim 路径中的终端模式切换。
             在单线程模型中，回调在 render 线程执行，不能调用
             chat_ui.suspend()（会 join 当前线程导致死锁），
             改为直接操作终端模式 + 底部栏拆装。
    """
    def _on_special_key(action: str, text: str) -> str | None:
        if action == 'vim':
            # ── 单线程模型：直接操作终端模式 + 底部栏拆装 ──
            # 不能调用 chat_ui.suspend()（会 join 当前 render 线程导致死锁）
            # 绕过公共 API 直接调用私有方法 _restore_terminal_settings/_apply_monitor_settings
            # 因为这些方法仅操作终端模式，不需要公共 API 的额外生命周期管理
            if monitor is not None:
                monitor._restore_terminal_settings()
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
                    monitor._apply_monitor_settings()
                    # ★ vim 退出后恢复 cbreak，清空 stdin 残留字节（防止乱码注入）
                    from src._compat_termios import termios
                    try:
                        termios.tcflush(sys.stdin.fileno(), termios.TCIFLUSH)
                    except Exception:
                        pass
        elif action == 'editmsg':
            return '/editmsg'
        elif action == 'switch_model':
            _models: list[str] = []
            try:
                from ..config import MODELS as _MODELS
                _models = _MODELS
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
                from ..core.commands._config_cmd import _infer_model_provider
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
        return None

    return _on_special_key
