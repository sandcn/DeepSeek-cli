"""特殊按键处理器模块 — 从 app_loop.py 提取

包含 _SpecialKeyHandler 类，封装 Ctrl+G (vim编辑)、Ctrl+O (/editmsg)、
Ctrl+N/Ctrl+R (模型切换) 等特殊按键的同步回调逻辑。
这些回调在 EscapeMonitor 的 monitor 线程中执行，不能使用异步操作。

架构改进: 将 InteractiveLoop.run() 中的 _edit_in_vim_sync 函数和
_on_special_key 闭包提取为独立类，降低 app_loop.py 的闭包复杂度 (~1200行)。
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import tempfile
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..app_loop import InteractiveLoop, SessionState
    from .session import ChatSession

_logger = logging.getLogger(__name__)


class _SpecialKeyHandler:
    """特殊按键处理器 — 封装 _on_special_key 回调 + _edit_in_vim_sync

    将 InteractiveLoop.run() 中的嵌套函数/闭包提取为类方法，降低
    app_loop.py 的职责耦合，同时保持与原行为完全一致。

    该类作为 callable 传递给 EscapeMonitor.set_special_key_callback，
    在 monitor 线程中同步执行（不可使用 async），通过持有的
    InteractiveLoop 引用访问其属性和方法。
    """

    def __init__(
        self,
        loop: InteractiveLoop,
        session: ChatSession,
        state: SessionState,
    ) -> None:
        """初始化特殊按键处理器。

        Args:
            loop: InteractiveLoop 实例 — 用于访问 _chat_ui 等属性
            session: 当前 ChatSession 实例 — 用于模型切换等操作
            state: SessionState 实例 — 用于读写 state.model 等状态
        """
        self._loop = loop
        self._session = session
        self._state = state
        # ★ P3 修复：在 __init__ 中缓存模型列表，避免每次回调时动态 import
        self._models_cache: list[str] = []
        try:
            from ..config import MODELS as _MODELS
            self._models_cache = list(_MODELS)
        except Exception:
            pass
        if not self._models_cache:
            try:
                from ..config.defaults import PROVIDERS as _PROVIDERS
                _seen: set[str] = set()
                for _p in _PROVIDERS.values():
                    for _m in _p.get("models", []):
                        if _m not in _seen:
                            _seen.add(_m)
                            self._models_cache.append(_m)
            except Exception:
                self._models_cache = []

    def edit_in_vim_sync(self, initial_text: str) -> str | None:
        """同步版 vim 编辑 — 在 monitor 线程中直接调用 Popen.communicate(timeout=30)。

        原为 InteractiveLoop.run() 中的嵌套函数 (_edit_in_vim_sync)，提取为类方法。
        因在 monitor 线程中执行，不能使用 asyncio.create_subprocess_exec（需要事件循环），
        改用同步 subprocess.Popen + communicate(timeout=30) 打开编辑器，
        等待用户编辑完成后返回；编辑器挂起 30 秒后超时终止并返回 None。
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
            proc = subprocess.Popen([editor_path, tmpfile])
            try:
                proc.communicate(timeout=30)
                ret = proc.returncode
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                _logger.warning("vim 编辑器超时（30s），已强制终止")
                return None
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

    def __call__(self, action: str, text: str) -> str | None:
        """处理特殊按键回调 — 替代原 _on_special_key 闭包。

        作为 callable 传递给 EscapeMonitor.set_special_key_callback，
        在 monitor 线程中同步执行。

        支持的动作:
            'vim'         — Ctrl+G: 用 vim 编辑当前输入文本
            'editmsg'     — Ctrl+O: 注入 /editmsg 命令
            'switch_model' — Ctrl+N/R: 循环切换模型
        """
        if action == 'vim':
            # 暂停 ChatUI（render 线程 + 底部栏），恢复后 vim 可独占终端
            if self._loop._chat_ui is not None:
                self._loop._chat_ui.suspend()
            try:
                return self.edit_in_vim_sync(text)
            finally:
                if self._loop._chat_ui is not None:
                    self._loop._chat_ui.resume()

        elif action == 'editmsg':
            # 注入 /editmsg 命令到输入缓冲区
            return '/editmsg'

        elif action == 'switch_model':
            _models = self._models_cache
            if not _models:
                return None
            current = self._state.model
            if not current:
                return None
            # 当前模型不在列表中 → 切到列表第一个
            if current not in _models:
                next_model = _models[0]
            else:
                try:
                    idx = _models.index(current)
                    next_model = _models[(idx + 1) % len(_models)]
                except (ValueError, IndexError):
                    return None
            # ★ P1-1: state.model 和 text 返回值同步设置（回调必须同步返回）
            self._state.model = next_model
            # 将 session.model 切换调度到主事件循环（线程安全）
            if self._loop._event_loop is not None:
                asyncio.run_coroutine_threadsafe(
                    self._switch_model_async(next_model),
                    self._loop._event_loop,
                )
            # 保留当前输入文本（不清空缓冲区）
            return text

        return None

    async def _switch_model_async(self, next_model: str) -> None:
        """在事件循环中异步切换模型（由 run_coroutine_threadsafe 调度）

        此方法在主事件循环中执行，避免 monitor 线程直接修改
        session.model 导致的线程竞态（P1-1）。
        """
        self._session.model = next_model
        if self._loop._chat_ui is not None:
            self._loop._chat_ui.bottom_bar.set_model_name(next_model)
            self._loop._chat_ui.on_notification(f"+ 已切换到 {next_model}")
