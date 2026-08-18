"""特殊按键回调 — vim/switch_model/editmsg 按键工厂"""

from __future__ import annotations

import logging

from ._editor import edit_in_vim_sync

_logger = logging.getLogger(__name__)


def make_special_key_callback(loop, session, state, chat_ui, monitor=None):
    """创建特殊按键回调函数

    返回 _on_special_key(action, text) 回调，处理：
    - 'vim'：启动 vim 编辑器编辑文本
    - 'editmsg'：返回 '/editmsg' 命令
    - 'switch_model'：循环切换模型
    - 'empty_mode'：Ctrl+B 切换主 agent 空模式（系统提词替换为
      prompts_export_main_empty.md，重建 agent 系统消息）

    monitor: EscapeMonitor 实例，用于 vim 路径中的终端模式切换。
             在单线程模型中，回调在 render 线程执行，不能调用
             chat_ui.suspend()（会 join 当前线程导致死锁），
             改为直接操作终端模式 + 底部栏拆装。
    """
    def _on_special_key(action: str, text: str) -> str | None:
        if action == 'vim':
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
        elif action == 'editmsg':
            return '/editmsg'
        elif action == 'retry':
            # Claude TUI parity 步骤 3.4：Ctrl+R → 重新生成上一轮（提交 /retry）
            return '/retry'
        elif action == 'toggle_theme':
            # Claude TUI parity 步骤 3.5：Ctrl+T → dark/light 循环切换（不提交输入）
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
        elif action == 'empty_mode':
            # Ctrl+B → 切换主 agent 空模式：系统提词替换为
            # prompts_export_main_empty.md（builder 层标志 + agent 消息重建）
            try:
                from ..prompt_builder.builder import toggle_empty_mode
                empty = toggle_empty_mode()
                agent = getattr(session, '_agent', None) or getattr(session, 'agent', None)
                if agent is not None and hasattr(agent, 'rebuild_system_prompt'):
                    try:
                        agent.rebuild_system_prompt()
                    except Exception:
                        _logger.debug("rebuild_system_prompt 异常", exc_info=True)
                if chat_ui is not None:
                    chat_ui.on_notification(
                        f"+ 主 Agent 已{'进入' if empty else '退出'}空模式"
                    )
            except Exception:
                _logger.debug("empty_mode 切换异常", exc_info=True)
            return text
        return None

    return _on_special_key
