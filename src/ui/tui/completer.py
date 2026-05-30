from __future__ import annotations

from collections.abc import Callable, Iterable
from prompt_toolkit.completion import Completer, Completion, PathCompleter

from ...core.commands import get_registered_command_names
from ...chat_msgs import list_sessions as _list_sessions
from ._ttl_cache import TTLCache


# ── 默认命令源 ────────────────────────────────────────────

def _default_commands_source() -> list[str]:
    """默认命令列表获取函数（可被 create_chat_completer 覆盖）。"""
    return get_registered_command_names()


def create_chat_completer(
    commands_source: Callable[[], list[str]] | None = None,
) -> "ChatCompleter":
    """创建 ChatCompleter 实例（工厂函数）。

    通过注入 commands_source 可替换命令列表获取方式（测试/定制），
    每个实例拥有独立的命令缓存，无模块级全局共享状态。

    Args:
        commands_source: 命令列表获取函数，默认使用 get_registered_command_names。

    Returns:
        新创建的 ChatCompleter 实例
    """
    return ChatCompleter(commands_source=commands_source)


class ChatCompleter(Completer):
    """混合补全：/ 开头补全指令，否则补全文件路径。

    实例级命令缓存（替代模块级全局 _command_cache），
    每个实例拥有独立的缓存，避免模块级共享可变状态。
    """

    # ── 参数补全命令表 ──
    _PARAM_COMMANDS: frozenset[str] = frozenset({
        '/model', '/theme', '/load', '/review',
    })

    # ── 参数选项常量 ──
    _REVIEW_OPTIONS: frozenset[str] = frozenset({'git', 'diff', 'commit'})

    def __init__(
        self,
        commands_source: Callable[[], list[str]] | None = None,
    ) -> None:
        self.path_completer: PathCompleter = PathCompleter(expanduser=True)
        source = commands_source or _default_commands_source
        # 使用通用 TTL 缓存替代手动缓存 / cached_property 实现
        self._commands_cache = TTLCache(fetcher=source, ttl=60.0)
        self._sessions_cache = TTLCache(fetcher=_list_sessions, ttl=60.0)
        self._models_cache = TTLCache(fetcher=self._fetch_models, ttl=60.0)
        self._theme_names_cache = TTLCache(fetcher=self._fetch_themes, ttl=60.0)

    # 惰性加载缓存（使用 TTLCache 替代 cached_property，支持运行时刷新）
    @staticmethod
    def _fetch_models() -> list[str]:
        from ...config import MODELS as _MODELS
        return list(_MODELS)

    @staticmethod
    def _fetch_themes() -> list[tuple[str, str]]:
        from ..theme import get_theme_names_with_desc as _get_theme_names
        return list(_get_theme_names())

    def _get_commands(self) -> list[str]:
        """获取缓存的命令列表（实例级，线程安全）。

        使用通用 TTLCache 替代手动缓存实现。
        """
        return self._commands_cache.get()

    def _get_cached_sessions(self) -> list[dict]:
        """获取缓存的会话列表（60s TTL，/load 参数补全专用）。

        避免每次按键都调用 list_sessions() 扫描文件系统。
        """
        return self._sessions_cache.get()

    def get_completions(self, document, complete_event) -> Iterable[Completion]:
        text = document.text_before_cursor
        # 获取光标前的最后一个词
        words = text.split()
        last_word = words[-1] if words else ''

        if last_word.startswith('/'):
            # ── 1. 补全指令名称 — 自动弹出 ──
            for cmd in self._get_commands():
                if cmd.startswith(last_word):
                    yield Completion(cmd, start_position=-len(last_word))

            # ── 2. 参数补全：命令已完整输入且有后面的参数部分 ──
            parts = text.split(maxsplit=1)
            if len(parts) >= 2:
                cmd_name = parts[0]
                if cmd_name not in self._PARAM_COMMANDS:
                    return
                param_part = parts[1]
                param_words = param_part.split()
                param_last = param_words[-1] if param_words else ''
                start = -len(param_last)

                if cmd_name == '/model':
                    for m in self._models_cache.get():
                        if m.startswith(param_last):
                            yield Completion(m, start_position=start)

                elif cmd_name == '/theme':
                    for name, _desc in self._theme_names_cache.get():
                        if name.startswith(param_last):
                            yield Completion(name, start_position=start)

                elif cmd_name == '/load':
                    for s in self._get_cached_sessions():
                        sid: str = s.get("id", "")
                        title = s.get("title", "")
                        if sid.startswith(param_last) or title.startswith(param_last):
                            display = sid[:8] + " - " + title
                            yield Completion(sid, start_position=start, display=display)

                elif cmd_name == '/review':
                    for opt in self._REVIEW_OPTIONS:
                        if opt.startswith(param_last):
                            yield Completion(opt, start_position=start)
        else:
            # 补全文件路径 — 仅按 Tab 时弹出
            if complete_event.completion_requested:
                yield from self.path_completer.get_completions(document, complete_event)
