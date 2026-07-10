"""特殊按键回调 — vim/switch_model/editmsg 按键工厂"""

from __future__ import annotations

import logging

from ._editor import edit_in_vim_sync

_logger = logging.getLogger(__name__)


def make_special_key_callback(loop, session, state, chat_ui):
    """创建特殊按键回调函数

    返回 _on_special_key(action, text) 回调，处理：
    - 'vim'：启动 vim 编辑器编辑文本
    - 'editmsg'：返回 '/editmsg' 命令
    - 'switch_model'：循环切换模型
    """
    def _on_special_key(action: str, text: str) -> str | None:
        if action == 'vim':
            if chat_ui is not None:
                chat_ui.suspend()
            try:
                return edit_in_vim_sync(text)
            finally:
                if chat_ui is not None:
                    chat_ui.resume()
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
            if chat_ui is not None:
                chat_ui.bottom_bar.set_model_name(next_model)
                chat_ui.on_notification(f"+ 已切换到 {next_model}")
            return text
        return None

    return _on_special_key
