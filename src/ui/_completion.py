"""终端补全引擎 — 纯计算型，不依赖 prompt_toolkit。

供 EscapeMonitor Tab 键回调使用，与 ChatCompleter（prompt_toolkit 专用）
平行存在，服务于新的流式输入系统。

支持三种补全：
  - 命令补全（/ 开头）：从命令注册表获取
  - 路径补全（非 / 开头）：文件系统路径
  - 参数补全（命令已完整输入后）：/model /theme /load 的参数选项
"""

from __future__ import annotations

import os
import glob as _glob_module
from pathlib import Path
from typing import Callable

# ── 类型 ────────────────────────────────────────────────


class CompletionItem:
    """单个补全项。"""

    __slots__ = ("text", "display", "start_pos")

    def __init__(self, text: str, display: str = "", start_pos: int = 0):
        self.text = text          # 替换文本
        self.display = display or text  # 显示文本
        self.start_pos = start_pos     # 从光标前多少字符开始替换


def _default_commands_source() -> list[str]:
    """默认命令列表获取函数。"""
    from ..core.commands import get_registered_command_names
    return get_registered_command_names()


# ── 补全引擎 ────────────────────────────────────────────


class CompletionEngine:
    """终端补全引擎：/ 开头补全命令，否则补全文件路径。

    无外部依赖（不依赖 prompt_toolkit），纯计算型。
    命令缓存 TTL 60s，避免每次按键都扫描注册表。
    """

    # 支持参数补全的命令
    _PARAM_COMMANDS: frozenset = frozenset({"/model", "/theme", "/load"})

    def __init__(
        self, commands_source: Callable[[], list[str]] | None = None,
    ):
        from .common.ttl_cache import TTLCache
        source = commands_source or _default_commands_source
        self._commands_cache = TTLCache(fetcher=source, ttl=60.0)
        self._sessions_cache = TTLCache(
            fetcher=self._fetch_sessions, ttl=60.0,
        )
        self._models_cache = TTLCache(
            fetcher=self._fetch_models, ttl=300.0,
        )
        self._theme_cache = TTLCache(
            fetcher=self._fetch_themes, ttl=300.0,
        )

    # ── 缓存 fetcher ───────────────────────────────────

    @staticmethod
    def _fetch_sessions() -> list[dict]:
        try:
            from ..chat_msgs import list_sessions
            return list_sessions()
        except Exception:
            return []

    @staticmethod
    def _fetch_models() -> list[str]:
        try:
            from ..config import MODELS
            return list(MODELS) if MODELS else []
        except Exception:
            return []

    @staticmethod
    def _fetch_themes() -> list[tuple[str, str]]:
        try:
            from .theme import get_theme_names_with_desc
            return list(get_theme_names_with_desc())
        except Exception:
            return []

    # ── 主入口 ─────────────────────────────────────────

    def complete(self, text: str, cursor_pos: int | None = None) -> list[CompletionItem]:
        """根据当前输入文本计算补全项列表。

        Args:
            text: 当前输入文本（不含提示符）。
            cursor_pos: 光标位置（None=末尾）。

        Returns:
            补全项列表，可能为空。第一项为"当前最佳匹配"。
        """
        if not text:
            return []

        # 截取到光标位置
        if cursor_pos is not None and cursor_pos >= 0:
            text = text[:cursor_pos]

        # 获取最后一个词（空格分隔）
        words = text.split()
        last_word = words[-1] if words else ""

        if last_word.startswith("/"):
            # ── 命令补全 ──
            items = self._complete_command(last_word)
            if items:
                # 精确匹配已完成命令 → 跳过命令补全，尝试参数补全
                if len(items) == 1 and items[0].text == last_word:
                    param_items = self._complete_param(text)
                    if param_items:
                        return param_items
                return items
            # 命令补全无结果时也尝试参数补全
            return self._complete_param(text)
        elif text.startswith("/"):
            # /xxx yyy → 参数补全
            return self._complete_param(text)
        else:
            # ── 路径补全 ──
            return self._complete_path(last_word)

    # ── 命令补全 ───────────────────────────────────────

    def _complete_command(self, prefix: str) -> list[CompletionItem]:
        """补全命令名（/ 开头）。"""
        commands = self._commands_cache.get()
        result: list[CompletionItem] = []
        for cmd in commands:
            if cmd.startswith(prefix):
                result.append(CompletionItem(cmd, start_pos=-len(prefix)))
        return result

    # ── 参数补全 ───────────────────────────────────────

    def _complete_param(self, text: str) -> list[CompletionItem]:
        """补全命令参数。

        只有命令名无参数时（如 "/model"），返回全部参数作为候选项，
        确保选中命令后自动弹出参数补全弹窗，无需先输入空格。
        """
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            cmd_name = text.strip()
            if cmd_name not in self._PARAM_COMMANDS:
                return []
            # 无参数部分 → 返回所有参数（空前缀匹配全部）
            param_last = ""
            start = 0
        else:
            cmd_name = parts[0]
            if cmd_name not in self._PARAM_COMMANDS:
                return []
            param_part = parts[1]
            param_words = param_part.split()
            param_last = param_words[-1] if param_words else ""
            start = -len(param_last)

        if cmd_name == "/model":
            models = self._models_cache.get()
            return [
                CompletionItem(m, start_pos=start)
                for m in models if m.startswith(param_last)
            ]

        elif cmd_name == "/theme":
            themes = self._theme_cache.get()
            return [
                CompletionItem(name, start_pos=start)
                for name, _desc in themes if name.startswith(param_last)
            ]

        elif cmd_name == "/load":
            sessions = self._sessions_cache.get()
            result: list[CompletionItem] = []
            for s in sessions:
                sid: str = s.get("id", "")
                title: str = s.get("title", "")
                if sid.startswith(param_last) or title.startswith(param_last):
                    display = f"{sid[:8]} - {title}" if title else sid[:8]
                    result.append(CompletionItem(sid, display=display, start_pos=start))
            return result

        return []

    # ── 路径补全 ───────────────────────────────────────

    def _complete_path(self, prefix: str) -> list[CompletionItem]:
        """补全文件系统路径。

        支持 ~ 展开、相对/绝对路径、目录尾缀 /。
        """
        try:
            expanded = os.path.expanduser(prefix) if prefix else "."
        except Exception:
            return []

        # 确定搜索基准目录和前缀
        if prefix.endswith(os.sep):
            search_dir = expanded
            file_prefix = ""
        else:
            search_dir = os.path.dirname(expanded) or "."
            file_prefix = os.path.basename(expanded)

        # 如果前缀为空，不搜索（避免列出当前目录所有文件）
        if not file_prefix and not prefix.endswith(os.sep):
            return []

        try:
            search_pattern = os.path.join(search_dir, file_prefix + "*")
            matches = _glob_module.glob(search_pattern)
        except Exception:
            return []

        # 排序：目录优先，然后按字母
        matches.sort(key=lambda p: (not os.path.isdir(p), os.path.basename(p).lower()))

        # 限制数量
        max_items = 20

        # 找到公共前缀用于计算 start_pos
        if prefix.endswith(os.sep):
            base = prefix
        else:
            base = os.path.dirname(prefix)
            if base and not base.endswith(os.sep):
                base += os.sep

        result: list[CompletionItem] = []
        for p in matches[:max_items]:
            name = os.path.basename(p)
            if os.path.isdir(p):
                name += os.sep
            # 计算替换范围：从 base 末尾到词尾
            display = name
            result.append(CompletionItem(
                text=base + name if base else name,
                display=display,
                start_pos=-len(prefix),
            ))
        return result
