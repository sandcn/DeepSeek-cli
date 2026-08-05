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
import threading
import time
import glob as _glob_module
from typing import Callable, TypeVar

T = TypeVar("T")

# ── 简易 TTL 缓存 ────────────────────────────────────────

class _TTLCache:
    """简易 TTL 缓存 — 替换已删除的 core/ttl_cache.py。"""

    def __init__(self, fetcher: Callable[[], T], ttl: float = 60.0):
        self._fetcher = fetcher
        self._ttl = ttl
        self._value: T | None = None
        self._expires: float = 0.0

    def get(self) -> T:
        now = time.monotonic()
        if self._value is None or now >= self._expires:
            self._value = self._fetcher()
            self._expires = now + self._ttl
        return self._value  # type: ignore[return-value]

    def clear(self) -> None:
        self._value = None
        self._expires = 0.0

    def refresh(self) -> T:
        self.clear()
        return self.get()

# ── 类型 ────────────────────────────────────────────────


class CompletionItem:
    """单个补全项。"""

    __slots__ = ("text", "display", "start_pos", "item_type", "desc")

    def __init__(self, text: str, display: str = "", start_pos: int = 0,
                 item_type: str = "", desc: str = ""):
        self.text = text          # 替换文本
        self.display = display or text  # 显示文本
        self.start_pos = start_pos     # 从光标前多少字符开始替换
        self.item_type = item_type     # 补全项类型：command/dir/file/param/session
        self.desc = desc               # 描述（斜杠命令菜单，Claude parity 3.7）


def _default_commands_source() -> list[str]:
    """默认命令列表获取函数。"""
    from ..core.commands import get_registered_command_names
    return get_registered_command_names()


def _ranked(items: list[str], prefix: str) -> list[str]:
    """候选语义排序（方向D 步骤13）：精确匹配 > 前缀匹配（长度升序）> 子串包含（长度升序）。

    同优先级按字母序（大小写不敏感次级键；稳定排序保持输入序为最终次级）。
    路径补全不经过本函数（保持目录优先 + 字母序）。
    """
    if not prefix:
        return sorted(items, key=lambda s: s.lower())
    exact: list[str] = []
    prefix_matches: list[tuple[str, int]] = []
    substring_matches: list[tuple[str, int]] = []
    for item in items:
        if item == prefix:
            exact.append(item)
        elif item.startswith(prefix):
            prefix_matches.append((item, len(item)))
        elif prefix in item:
            substring_matches.append((item, len(item)))
    prefix_matches.sort(key=lambda t: (t[1], t[0].lower()))
    substring_matches.sort(key=lambda t: (t[1], t[0].lower()))
    exact.sort(key=lambda s: s.lower())
    return exact + [t[0] for t in prefix_matches] + [t[0] for t in substring_matches]


def _ranked_sessions(
    matched: list[tuple[str, str]], prefix: str,
) -> list[tuple[str, str]]:
    """/load 会话候选语义排序（P1-1 回归修复）。

    /load 支持 sid 与 title 双重匹配（``sid.startswith(prefix) or
    title.startswith(prefix)``），但候选须按 **多键加权** 排序而非二次过滤
    （修复前 ``_ranked([sid for sid, _t in matched], prefix)`` 仅保留 sid
    匹配项，title 匹配但 sid 不匹配的会话被丢弃返回空）。

    排序权重（低值优先）：
      0  sid 精确 > 1 sid 前缀 > 2 title 前缀 > 3 sid 子串 > 4 title 子串
    同级按 sid 长度升序 + 字母序（稳定排序保持输入序为最终次级）。
    空前缀（``/load`` 无参数）→ 保持注册表顺序（全部候选）。

    Args:
        matched: 已按 sid/title 前缀过滤的 ``(sid, title)`` 对列表。
        prefix: 参数最后词（``/load`` 后的匹配前缀）。

    Returns:
        排序后的 ``(sid, title)`` 对列表。
    """
    if not prefix:
        return matched
    categories: list[tuple[int, str, str]] = []
    for sid, title in matched:
        if sid == prefix:
            cat = 0
        elif sid.startswith(prefix):
            cat = 1
        elif title.startswith(prefix):
            cat = 2
        elif prefix in sid:
            cat = 3
        elif prefix in title:
            cat = 4
        else:
            continue  # 已过滤，防御性跳过
        categories.append((cat, sid, title))
    categories.sort(key=lambda t: (t[0], len(t[1]), t[1].lower()))
    return [(sid, title) for _cat, sid, title in categories]


# ── 补全引擎 ────────────────────────────────────────────


# ── 主题适配器（模块级懒加载单例） ──────────────────────────
# CommandUiAdapter 无状态，复用同一实例避免每次缓存刷新（TTL 60s）
# 重复构造。延迟导入保留（避免 core→tui 循环依赖）。
# 双检锁保证多线程并发首次访问时只构造一次（线程安全单例）。
_THEME_ADAPTER = None
_THEME_ADAPTER_LOCK = threading.Lock()


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
        source = commands_source or _default_commands_source
        self._commands_cache = _TTLCache(fetcher=source, ttl=60.0)
        self._sessions_cache = _TTLCache(
            fetcher=self._fetch_sessions, ttl=60.0,
        )
        # ★ review 方向：模型/主题缓存 TTL 从 300s 降至 60s——模型列表变更
        #   （插件/配置更新）后最长 5 分钟补全不刷新的延迟过长；60s 平衡缓存
        #   收益与新鲜度（与命令/会话缓存 TTL 一致）。
        self._models_cache = _TTLCache(
            fetcher=self._fetch_models, ttl=60.0,
        )
        self._theme_cache = _TTLCache(
            fetcher=self._fetch_themes, ttl=60.0,
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
            if MODELS:
                return list(MODELS)
            # MODELS 为空时从所有 PROVIDERS 聚合模型（去重）
            from ..config.defaults import PROVIDERS
            _seen: set[str] = set()
            result: list[str] = []
            for _p in PROVIDERS.values():
                for _m in _p.get("models", []):
                    if _m not in _seen:
                        _seen.add(_m)
                        result.append(_m)
            return result
        except Exception:
            return []

    @staticmethod
    def _fetch_themes() -> list[tuple[str, str]]:
        # 延迟导入避免循环依赖：主题名来自 core 层 CommandUiAdapter
        # （原 from ..core.theme 指向不存在的模块，2026-07-31 修复幽灵导入）
        from src.core.commands._ui_adapter import CommandUiAdapter
        global _THEME_ADAPTER
        if _THEME_ADAPTER is None:
            with _THEME_ADAPTER_LOCK:
                if _THEME_ADAPTER is None:
                    _THEME_ADAPTER = CommandUiAdapter()
        return list(_THEME_ADAPTER.get_theme_names_with_desc())

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

        # 获取最后一个词（空格分隔；方向1 修复：用 ``split(" ")`` 保留末尾
        # 空词——``text.split()`` 丢弃末尾空串，输入 ``"cd "`` 时 last_word
        # 取到 "cd" 把 "cd" 当文件前缀补全，而非枚举当前目录）。
        words = text.split(" ")
        last_word = words[-1] if words else ""

        if last_word.startswith("/") and text.startswith("/"):
            # ── 命令补全（行首命令 + / 开头的词） ──
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
            # /xxx yyy → 参数补全（行首命令 + 非 / 词）
            return self._complete_param(text)
        elif last_word.startswith("/"):
            # ★ 绝对路径补全修复：普通命令后的 / 开头的词（如 ``cd /tmp/fo``、
            #   ``ls /usr/``）是绝对路径——修复前落入 ``_complete_command``
            #   （命令注册表无匹配返回 []）→ ``_complete_param``（cd 非参数
            #   命令返回 []）→ 永远走不到路径补全，Tab 还会插入制表符。
            return self._complete_path(last_word)
        else:
            # ── 路径补全 ──
            return self._complete_path(last_word)

    # ── 命令补全 ───────────────────────────────────────

    def _complete_command(self, prefix: str) -> list[CompletionItem]:
        """补全命令名（/ 开头）。

        方向D 步骤13：候选语义排序——精确匹配 > 前缀匹配（长度升序）>
        子串包含（长度升序）；同优先级按字母序（稳定排序保持注册表序为次级）。
        """
        commands = self._commands_cache.get()
        ranked = _ranked(commands, prefix)
        # ★ review 方向：``get_command_help`` 导入移出循环（原每候选命令
        #   try/except 重复导入——命令描述查询可缓存）。
        try:
            from ..core.internal.commands._command_core import get_command_help
        except Exception:
            get_command_help = None
        result: list[CompletionItem] = []
        for cmd in ranked:
            # Claude TUI parity 步骤 3.7：命令描述（注册表 help；无则空串）
            desc = ""
            if get_command_help is not None:
                try:
                    desc = get_command_help(cmd)
                except Exception:
                    pass
            result.append(CompletionItem(
                cmd, start_pos=-len(prefix), item_type="command", desc=desc,
            ))
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
            # 方向2（命令前缀保留）：无参数分支候选文本为完整替换串
            #   ``f"{cmd_name} {m}"``——修复前候选仅 ``m`` 且 start_pos=0 →
            #   _apply_completion 走 start_pos==0 分支返回纯参数（/model 被
            #   替换为 deepseek-chat，命令前缀丢失）。完整替换串 + start_pos=0
            #   应用后保留 ``/model <param>``。
            replace_full = True
        else:
            cmd_name = parts[0]
            if cmd_name not in self._PARAM_COMMANDS:
                return []
            param_part = parts[1]
            param_words = param_part.split()
            param_last = param_words[-1] if param_words else ""
            start = -len(param_last)
            # 方向3（参数空串前缀丢失修复）：参数部分为空（如 ``"/model "``
            # 带尾随空格 → ``split(maxsplit=1)`` 产 ``["/model", ""]``，
            # ``param_words=[]``）时 ``start=-0=0`` → ``_apply_completion``
            # 整行替换会丢弃 ``/model `` 命令前缀。与无参数分支一致改用
            # **完整替换串**（``f"{cmd_name} {m}"``）+ start_pos=0 → 应用后
            # 保留 ``/model <param>``。
            replace_full = not param_words

        if cmd_name == "/model":
            models = self._models_cache.get()
            # 方向D 步骤13：语义排序（精确 > 前缀 > 子串，长度升序）
            return [
                CompletionItem(
                    f"{cmd_name} {m}" if replace_full else m,
                    start_pos=start, item_type="param",
                )
                for m in _ranked(models, param_last)
            ]

        elif cmd_name == "/theme":
            themes = self._theme_cache.get()
            ranked = _ranked([name for name, _desc in themes], param_last)
            return [
                CompletionItem(
                    f"{cmd_name} {name}" if replace_full else name,
                    start_pos=start, item_type="param",
                )
                for name in ranked
            ]

        elif cmd_name == "/load":
            sessions = self._sessions_cache.get()
            matched: list[tuple[str, str]] = []
            for s in sessions:
                sid: str = s.get("id", "")
                title: str = s.get("title", "")
                # 前缀 + 子串双匹配：与 _ranked_sessions 的 cat 0-4 对齐——
                # 仅前缀过滤会丢弃 title/sid 子串命中（cat 3/4 成为死代码）。
                if (
                    sid.startswith(param_last) or title.startswith(param_last)
                    or param_last in sid or param_last in title
                ):
                    matched.append((sid, title))
            # P1-1 回归修复：多键加权排序（sid 精确 > sid 前缀 > title 前缀 >
            # sid 子串 > title 子串）替代二次 sid 过滤——title 匹配但 sid 不匹配
            # 的会话不再被丢弃。
            ranked = _ranked_sessions(matched, param_last)
            result: list[CompletionItem] = []
            for sid, title in ranked:
                # 方向F·步骤15（渲染错误修复）：title 可能含换行符（多行用户
                # 消息作为会话标题，如 "tui:\n1.分析...\n2.完善..."）——
                # Line 内嵌字面换行会把一"行"拆成多行，破坏帧行号/diff/光标
                # 定位。构造 display 时统一归一化为空格。
                title_disp = title.replace("\n", " ") if title else ""
                display = f"{sid[:8]} - {title_disp}" if title_disp else sid[:8]
                result.append(CompletionItem(
                    f"{cmd_name} {sid}" if replace_full else sid,
                    display=display, start_pos=start, item_type="session",
                ))
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
            # 方向2（glob 通配符转义）：file_prefix 含 `[`/`]`/`?` 等被 glob
            # 解释为通配符 → 前缀经 ``glob.escape`` 转义（保留尾部 ``*`` 匹配
            # 后缀）——前缀按字面匹配，不误命中通配语义。
            search_pattern = os.path.join(
                search_dir, _glob_module.escape(file_prefix) + "*",
            )
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
            is_dir = os.path.isdir(p)
            if is_dir:
                name += os.sep
            # 计算替换范围：从 base 末尾到词尾
            display = name
            result.append(CompletionItem(
                text=base + name if base else name,
                display=display,
                start_pos=-len(prefix),
                item_type="dir" if is_dir else "file",
            ))
        return result
