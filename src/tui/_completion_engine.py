"""终端补全引擎 — 纯计算型，不依赖 prompt_toolkit。

供 ``_CmplHandler``（``_completion.py``，补全 UI 交互流程）委托的**纯计算**
模块：装配链为 ``TuiAssembly → _CmplHandler → CompletionEngine``（Tab/自动
补全回调经 ``_CmplHandler`` 调用本引擎计算候选项）。与 ``_completion.py``
职责互补（交互 vs 计算），非平行重复实现。

支持三种补全：
  - 命令补全（/ 开头）：从命令注册表获取
  - 路径补全（非 / 开头）：文件系统路径
  - 参数补全（命令已完整输入后）：/model /theme /load 的参数选项
"""

from __future__ import annotations

import os
import re
import threading
import glob as _glob_module
import logging
from dataclasses import dataclass
from typing import Any, Callable

from ._async_source import AsyncSource

_logger = logging.getLogger(__name__)

#: 路径补全「目录类型预扫描」阈值（匹配数不超过该值时逐项 ``os.path.isdir``，
#: 超过时先一次 ``os.scandir`` 建立类型映射）。小目录下逐项 stat 更便宜，
#: 大目录（数千项）下 scandir 快一个数量级。
_DIR_SCAN_THRESHOLD = 32

# ★ P3（review 2026-08-22）：get_command_help 模块级惰性缓存——修复前
#   ``_complete_command`` 每次 Tab 重复 ``from ... import get_command_help`` +
#   try/except（Python 缓存 import 但函数体每 Tab 仍解析一次模块查找）。
_get_command_help_ready = False
_get_command_help_impl = None


def _get_command_help() -> Callable | None:
    """返回 get_command_help 函数引用（模块级缓存；导入失败回退 None）。"""
    global _get_command_help_ready, _get_command_help_impl
    if not _get_command_help_ready:
        try:
            from ..core.internal.commands._command_core import get_command_help
            _get_command_help_impl = get_command_help
        except Exception:
            _get_command_help_impl = None
        _get_command_help_ready = True
    return _get_command_help_impl

# ── 异步数据源（键级缓存 + 后台加载） ──────────────────────
# 补全数据源（命令 / 会话 / 模型 / 主题 / 配置键）统一经 ``AsyncSource``
# 承载：**同步模式**（默认）未命中即在调用线程加载，行为与旧 TTL 缓存一致；
# **异步模式**（``enable_async``，TUI 装配启用）未命中仅触发后台加载并立即
# 返回「未就绪」，界面显示「加载中…」占位，数据就绪后经监听器动态刷新弹窗。

#: 常驻数据源键（warmup 预热集合）。**不含** ``sessions``——会话列表体积大、
#: 逐条解析耗时，改为**按需懒加载**：仅在补全菜单弹出（输入 ``/load`` 或
#: ``/load ``）时由 ``_complete_param`` 经 ``_cached`` 触发后台加载，
#: 避免拖慢程序启动（用户需求：启动不加载 /load 补全列表）。
_RESIDENT_KEYS = ("commands", "models", "themes", "config_keys")

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


def _current_config_value(key: str) -> str:
    """读取当前配置值（补全弹窗「当前」标注用；失败回退空串）。"""
    try:
        from ..config.proxy import config

        value = config.get(key)
        return str(value) if value else ""
    except Exception:
        return ""


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


@dataclass
class CompletionContext:
    """补全分发的上下文（文本 / 词列表 / 当前词）。"""

    text: str
    words: list
    last_word: str


# ── 内置补全提供者实现（清单条目经点分引用解析到这些函数） ──────


def _command_provider(engine: "CompletionEngine", ctx: CompletionContext) -> list:
    return engine._complete_command(ctx.last_word)


def _param_provider(engine: "CompletionEngine", ctx: CompletionContext) -> list:
    return engine._complete_param(ctx.text)


def _path_provider(engine: "CompletionEngine", ctx: CompletionContext) -> list:
    return engine._complete_path(ctx.last_word)


class CompletionEngine:
    """终端补全引擎：/ 开头补全命令，否则补全文件路径。

    无外部依赖（不依赖 prompt_toolkit），纯计算型。
    命令缓存 TTL 60s，避免每次按键都扫描注册表。
    """

    # 支持参数补全的命令
    # ★ 2026-08-20（用户需求：config 命令独立界面）：/config 加入参数补全
    #   ——子命令（show/list/get/set/reset）+ 配置键名（view_model 构建）。
    _PARAM_COMMANDS: frozenset = frozenset({"/model", "/theme", "/load", "/config"})

    def __init__(
        self, commands_source: Callable[[], list[str]] | None = None,
        *, async_mode: bool = False,
    ):
        source = commands_source or _default_commands_source
        #: 数据源（键级缓存 + 可选后台加载）；同步模式未命中即在调用线程加载。
        self._source = AsyncSource("completion")
        self._source.register("commands", source, ttl=60.0)
        # 会话列表：**流式**数据源——逐条 emit（每解析出一个会话即增量可见），
        # /load 补全弹窗随加载进度增长（数据就绪一条即界面增加一条候选）。
        self._source.register_stream("sessions", self._stream_sessions, ttl=60.0)
        self._source.register("models", self._fetch_models, ttl=60.0)
        self._source.register("themes", self._fetch_themes, ttl=60.0)
        self._source.register("config_keys", self._fetch_config_keys, ttl=60.0)
        #: 异步模式（TUI 装配启用）：未就绪返回空 + 界面显示「加载中…」占位。
        self._async_mode = bool(async_mode)
        #: 本次 complete 中处于「等待后台加载」的数据源键（pending 判定）。
        self._deferred: set[str] = set()
        self._lock = threading.RLock()

    # ── 异步开关 / 预热 / 就绪监听 ──────────────────────

    def enable_async(self) -> None:
        """启用异步数据加载（未就绪不阻塞渲染线程，界面显示加载占位）。"""
        self._async_mode = True

    @property
    def async_mode(self) -> bool:
        return self._async_mode

    @property
    def pending(self) -> bool:
        """最近一次 ``complete`` 是否命中「数据仍在后台加载」的数据源。"""
        return bool(self._deferred)

    def warmup(self) -> None:
        """后台预热常驻数据源（启动时调用，首次补全零等待）。

        仅预热轻量数据源（``commands`` / ``models`` / ``themes`` /
        ``config_keys``）；``sessions``（``/load`` 补全列表）**不在**其中——
        弹出 ``/load`` 补全菜单时才按需加载（见 ``_RESIDENT_KEYS``）。
        """
        self._source.prefetch(_RESIDENT_KEYS)

    def add_listener(self, listener: Callable[[str], None]) -> Callable[[], None]:
        """注册数据就绪监听器（后台线程调用；界面据此动态刷新）。"""
        return self._source.add_listener(listener)

    def close(self) -> None:
        """关闭后台加载线程（幂等）。"""
        self._source.close()

    def invalidate(self, key: str | None = None) -> None:
        """使数据源缓存失效（key=None 清全部；下次读取重新加载）。"""
        self._source.invalidate(key)

    def register_source(self, key: str, fetcher: Callable[[], Any],
                        ttl: float = 60.0) -> None:
        """覆盖/注册数据源 fetcher（扩展点：外部注入自定义数据源并立即使缓存失效）。

        内置键：``commands`` / ``sessions`` / ``models`` / ``themes`` /
        ``config_keys``。
        """
        self._source.register(key, fetcher, ttl)
        self._source.invalidate(key)

    def register_stream_source(self, key: str, producer: Callable[[Callable], None],
                               ttl: float = 60.0) -> None:
        """覆盖/注册**流式**数据源（``producer(emit)`` 逐条 emit 增量结果）。

        每次 emit 即刻更新缓存并按节流通知（补全弹窗随加载进度逐条增长）。
        """
        self._source.register_stream(key, producer, ttl)
        self._source.invalidate(key)

    def _cached(self, key: str, default: Any = None) -> Any:
        """读取数据源；未就绪时记录 pending 并返回 default（不阻塞）。"""
        ready, value = self._source.peek(key, sync_fallback=not self._async_mode)
        if ready:
            return value
        with self._lock:
            self._deferred.add(key)
        return default

    # ── 缓存 fetcher ───────────────────────────────────

    @staticmethod
    def _fetch_sessions() -> list[dict]:
        try:
            from ..chat_msgs import list_sessions
            return list_sessions()
        except Exception:
            return []

    @staticmethod
    def _stream_sessions(emit) -> None:
        """会话列表流式 fetcher（逐条 emit 累积列表）。

        每解析出一个会话即 emit 当前累积列表（缓存即时更新 + 节流通知界面，
        补全弹窗随加载进度逐条增长）；异常回退空列表（不阻塞后续数据源）。
        """
        try:
            from ..chat_msgs import iter_sessions
        except Exception:
            emit([])
            return
        out: list[dict] = []
        try:
            for session in iter_sessions():
                out.append(session)
                emit(list(out))
        except Exception:
            _logger.debug("会话列表流式加载失败", exc_info=True)
        if not out:
            emit([])

    @staticmethod
    def _fetch_models() -> list[str]:
        """配置的模型列表（模型档案 + RC 顶层 ``models``；不列内置模型）。

        ``/model <Tab>`` 参数补全与 Ctrl+N/``/model`` 的选择范围一致——只列
        用户配置的模型。
        """
        try:
            from ..config.model_profiles import configured_models
            return configured_models()
        except Exception:
            return []

    @staticmethod
    def _fetch_themes() -> list[tuple[str, str]]:
        """主题列表 fetcher（与 _fetch_sessions/_fetch_models 一致：异常返回 []）。

        ★ P2-2（review 方向）：统一加 try/except 返回 []——修复前无异常
        捕获，CommandUiAdapter 导入/构造/查询任一异常直接冒泡，经
        _TTLCache.get() 传播至补全按键路径（Tab 补全 /theme 崩溃）。
        """
        try:
            # 延迟导入避免循环依赖：主题名来自 core 层 CommandUiAdapter
            # （原 from ..core.theme 指向不存在的模块，2026-07-31 修复幽灵导入）
            from src.core.commands._ui_adapter import CommandUiAdapter
            global _THEME_ADAPTER
            if _THEME_ADAPTER is None:
                with _THEME_ADAPTER_LOCK:
                    if _THEME_ADAPTER is None:
                        _THEME_ADAPTER = CommandUiAdapter()
            return list(_THEME_ADAPTER.get_theme_names_with_desc())
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
        with self._lock:
            self._deferred.clear()
        if not text:
            return []

        # 截取到光标位置
        if cursor_pos is not None and cursor_pos >= 0:
            text = text[:cursor_pos]

        # 获取最后一个词（P2-8 修复：按空白切分 \s+——原 split(" ") 对含 \t
        # 输入不切分（cd\t/src 中 last_word 取整段 "cd\t/src"，路径补全失效）；
        # re.split 保留尾随空串（"cd " → ['cd', '']，与 split(" ") 空格语义
        # 一致，且兼容制表符）。与 _complete_param 的空白切分口径统一。
        words = re.split(r"\s+", text)
        last_word = words[-1] if words else ""
        return self._dispatch(CompletionContext(text=text, words=words, last_word=last_word))

    def _dispatch(self, ctx: "CompletionContext") -> list[CompletionItem]:
        """按启用的补全提供者分发（「一切皆插件」——每个提供者一个清单条目）。

        分发语义与旧内联分支等价：
          - 行首 ``/`` 词：命令补全（精确匹配已完成命令 → 尝试参数补全）；
            命令无结果 → 参数补全 → 绝对路径词回退路径补全；
          - 行首 ``/`` 但当前词非 ``/``：参数补全；
          - 非行首 ``/`` 词：绝对路径补全；
          - 其余：路径补全。

        提供者被 Patch/Overlay 禁用即从 ``active_provider_ids`` 缺席，对应
        分支跳过（返回空）。
        """
        from ._completion_providers import active_provider_ids

        active = set(active_provider_ids())
        last_word = ctx.last_word
        if "command" in active and last_word.startswith("/") and ctx.text.startswith("/"):
            # ── 命令补全（行首命令 + / 开头的词） ──
            items = self._call_provider("command", ctx)
            if items:
                # 精确匹配已完成命令 → 跳过命令补全，尝试参数补全。
                # ★ 候选含**精确匹配**即视为「用户已输入完整命令名」——不再要求
                #   候选唯一（修复 /model 与 /models 共存时输入 /model 被当作
                #   命令前缀列举、参数补全失效）：输入 /model 仍进入模型参数
                #   补全；输入 /mod 候选无精确项则照常列举命令。
                if "param" in active and any(it.text == last_word for it in items):
                    param_items = self._call_provider("param", ctx)
                    if param_items:
                        return param_items
                return items
            # ★ P3（review）：命令补全无结果时，若词形为**绝对路径**（含
            #   ``os.sep`` 且非单个 "/"）→ 回退路径补全。
            if "param" in active:
                param_items = self._call_provider("param", ctx)
                if param_items:
                    return param_items
            if "path" in active and (os.sep in last_word[1:] or last_word.endswith(os.sep)):
                return self._call_provider("path", ctx)
            return []
        elif "param" in active and ctx.text.startswith("/"):
            # /xxx yyy → 参数补全（行首命令 + 非 / 词）
            return self._call_provider("param", ctx)
        elif "path" in active and last_word.startswith("/"):
            # ★ 绝对路径补全修复：普通命令后的 / 开头的词（如 ``cd /tmp/fo``、
            #   ``ls /usr/``）是绝对路径。
            return self._call_provider("path", ctx)
        elif "path" in active:
            # ── 路径补全 ──
            return self._call_provider("path", ctx)
        return []

    def _call_provider(self, pid: str, ctx: "CompletionContext") -> list[CompletionItem]:
        """调用某提供者的实现（注册表解析；缺席返回空）。"""
        from ._completion_providers import resolve_provider

        fn = resolve_provider(pid)
        if fn is None:
            return []
        return fn(self, ctx)

    # ── 命令补全 ───────────────────────────────────────

    def _complete_command(self, prefix: str) -> list[CompletionItem]:
        """补全命令名（/ 开头）。

        方向D 步骤13：候选语义排序——精确匹配 > 前缀匹配（长度升序）>
        子串包含（长度升序）；同优先级按字母序（稳定排序保持注册表序为次级）。
        """
        commands = self._cached("commands", [])
        ranked = _ranked(commands, prefix)
        # ★ P3（review 2026-08-22）：``get_command_help`` 经模块级惰性缓存
        #   （``_get_command_help``）取得——修复前每 Tab 重复 from import +
        #   try/except。
        get_command_help = _get_command_help()
        result: list[CompletionItem] = []
        for cmd in ranked:
            # Claude TUI parity 步骤 3.7：命令描述（注册表 help；无则空串）
            desc = ""
            if get_command_help is not None:
                try:
                    desc = get_command_help(cmd)
                except Exception:
                    # ★ P3（review 2026-08-22）：修复前 ``except Exception: pass``
                    #   静默丢弃命令描述获取失败——补 debug 日志（exc_info）。
                    _logger.debug(
                        "get_command_help(%r) 失败", cmd, exc_info=True,
                    )
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
            models = self._cached("models", [])
            current = _current_config_value("model")
            # 方向D 步骤13：语义排序（精确 > 前缀 > 子串，长度升序）
            return [
                CompletionItem(
                    f"{cmd_name} {m}" if replace_full else m,
                    start_pos=start, item_type="param",
                    # ★ 2026-10-07（补全弹窗增强）：当前值标注（弹窗描述列显示）
                    desc="当前" if m == current else "",
                )
                for m in _ranked(models, param_last)
            ]

        elif cmd_name == "/theme":
            themes = self._cached("themes", [])
            current = _current_config_value("theme")
            ranked = _ranked([name for name, _desc in themes], param_last)
            return [
                CompletionItem(
                    f"{cmd_name} {name}" if replace_full else name,
                    start_pos=start, item_type="param",
                    # ★ 2026-10-07（补全弹窗增强）：当前值标注
                    desc="当前" if name == current else "",
                )
                for name in ranked
            ]

        elif cmd_name == "/load":
            # ★ 2026-10-10（用户需求）：会话列表**懒加载**——未预热，首次读取
            #   （补全菜单弹出）时触发后台流式加载，界面先显示「加载中…」占位，
            #   数据就绪后经 _CmplHandler 监听器动态刷新候选。
            sessions = self._cached("sessions", [])
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

        elif cmd_name == "/config":
            return self._complete_config_param(
                parts, param_last, replace_full, start,
            )

        return []

    # ── /config 参数补全（2026-08-20 用户需求） ─────────────

    @staticmethod
    def _config_subcommands() -> list[str]:
        """/config 子命令列表（与 _cmd_config 分支一致）。"""
        return ["show", "list", "get", "set", "reset"]

    def _config_key_names(self) -> list[str]:
        """配置键名列表（显示路径；异常回退 []——补全失败不崩溃）。

        经数据源缓存（TTL 60s）——避免每次 Tab 重建全部配置条目
        （``build_config_entries`` 内部 ``get_rc()`` + MODEL 聚合 PROVIDERS）；
        异步模式下未就绪返回空并触发后台加载（界面显示「加载中…」占位）。
        """
        return self._cached("config_keys", [])

    def _fetch_config_keys(self) -> list[str]:
        """配置键名 fetcher（TTL 缓存底层；异常回退 []）。"""
        try:
            from src.config.view_model import build_config_entries
            return [str(e["path"]) for e in build_config_entries()]
        except Exception:
            return []

    def _complete_config_param(
        self, parts: list[str], param_last: str, replace_full: bool, start: int,
    ) -> list[CompletionItem]:
        """/config 参数补全：子命令 + 配置键名（二级结构，替换参数独立计算）。

        形态：
          - ``/config`` / ``/config <子命令前缀>`` → 补全子命令
            （show/list/get/set/reset）；
          - ``/config get|set|reset <前缀>`` → 补全配置键名（view_model 构建）。

        ★ P1（review 2026-08-20）：``param_last``/``start``/``replace_full``
        由外层按「最后一个词」统一计算，对 /config 二级结构不适用——子命令
        后**无参数**（``/config set`` + Tab）时按最后一个词替换会把子命令
        整体替换为键名（``/config model``，子命令丢失）。本函数内部分支
        重新计算（统一词边界替换语义，见 ``_completion._apply_completion``
        的 orig_prefix 分支）：
          - 无参数 → 候选 ``set {key}`` + start_pos=-len("set")——弹窗
            orig_prefix=last_word("set") 词边界匹配 ``" set"`` → 拼接为
            ``/config set model``（子命令保留）；
          - 有参数 → 候选 ``key`` + start_pos=-len(key_prefix)——替换最后词。
        """
        result: list[CompletionItem] = []
        if len(parts) < 2 or not parts[1].strip():
            # 无参数 → 补全子命令（空前缀匹配全部）
            for sub in _ranked(self._config_subcommands(), param_last):
                result.append(CompletionItem(
                    f"/config {sub}" if replace_full else sub,
                    start_pos=start, item_type="param",
                ))
            return result
        words = parts[1].split()
        sub = words[0].lower()
        if sub in ("show", "list"):
            # 无更多参数可补全
            return result
        if sub in ("get", "set", "reset"):
            if len(words) == 1:
                # 子命令后无参数：候选 ``{sub} {key}`` + 替换子命令词
                # （词边界拼接后保留 ``/config {sub}`` 前缀）
                for key in _ranked(self._config_key_names(), ""):
                    result.append(CompletionItem(
                        f"{sub} {key}", start_pos=-len(sub), item_type="param",
                    ))
                return result
            # 已有参数词：替换最后一个词（key_prefix）
            key_prefix = words[-1]
            for key in _ranked(self._config_key_names(), key_prefix):
                result.append(CompletionItem(
                    key, start_pos=-len(key_prefix), item_type="param",
                ))
            return result
        # 子命令前缀输入（如 ``/config se``）→ 补全完整子命令
        for s in _ranked(self._config_subcommands(), sub):
            result.append(CompletionItem(
                f"/config {s}" if replace_full else s,
                start_pos=start, item_type="param",
            ))
        return result

    # ── 路径补全 ───────────────────────────────────────

    def _complete_path(self, prefix: str) -> list[CompletionItem]:
        """补全文件系统路径。

        支持 ~ 展开、相对/绝对路径、目录尾缀 /。
        """
        # ★ P1（review）：空前缀直接返回空——避免 ``os.path.expanduser("")``
        #   回退 "." 后 ``file_prefix=os.path.basename(".")="."`` 非空，绕过
        #   下方「空前缀不搜索」守卫，导致 ``cd ``（尾随空格）+ Tab glob 出
        #   当前目录 dotfiles（``"./.*"``）。与注释「空前缀不搜索」语义一致。
        if not prefix:
            return []
        try:
            # ★ P3（review）：去掉死分支 ``if prefix else "."``——上方已保证
            #   prefix 非空。
            expanded = os.path.expanduser(prefix)
        except Exception:
            return []

        # 确定搜索基准目录和前缀
        # ★ 2026-08-06：`.`/`..` 前缀特殊处理——修复前 prefix="." 时
        #   dirname(".") or "." 使 file_prefix="" 且 `not prefix.endswith(os.sep)`
        #   → 返回空（用户输入 "." 按 Tab 无结果）；prefix=".." 时
        #   file_prefix=".." → glob("..*") 匹配祖父目录项，补全出父级之上的
        #   目录（行为错误）。现在对 "." / ".." 视为「枚举当前/上级目录」。
        # ★ P2-3（review 方向）："~" 加入特判集——修复前 prefix="~" 落入
        #   常规分支（file_prefix=home basename），守卫后 glob 匹配 home 同级
        #   目录下以该 basename 开头的项（错误）或返回空（home 为根目录）。
        #   现视为「枚举 home 目录」（expanded 即 expanduser 展开结果）。
        if prefix in (".", "..", "~"):
            search_dir = expanded
            file_prefix = ""
        elif prefix.endswith(os.sep):
            search_dir = expanded
            file_prefix = ""
        else:
            search_dir = os.path.dirname(expanded) or "."
            file_prefix = os.path.basename(expanded)

        # 如果前缀为空，不搜索（避免列出当前目录所有文件）。
        # ★ 2026-08-06："." / ".." 例外——视为「枚举当前/上级目录」请求
        #   （file_prefix 为空但语义明确，不应被本守卫拦截）。
        # ★ P2-3："~" 同例外（枚举 home）。
        if not file_prefix and not prefix.endswith(os.sep) and prefix not in (".", "..", "~"):
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
        # ★ 性能（大目录路径补全）：先一次性 ``os.scandir`` 建立
        #   「entry 名 → 是否目录」映射，排序 key 直接查表——修复前排序 key 对
        #   每个匹配逐项 ``os.path.isdir``（每次一次 stat；Cygwin 上 3000 项目录
        #   实测 ~240ms，每次 Tab / TTL 过期后重现，直接卡住输入）。``os.scandir``
        #   的 ``entry.is_dir()`` 通常由目录项类型信息给出而无额外 stat，同目录
        #   3000 项实测 ~3ms（~86x）。glob 结果均为 ``search_dir`` 直接子项，
        #   故 basename 必然命中映射；缺失（scandir 失败/竞态删除）时回退
        #   ``os.path.isdir``。匹配数不超过 ``_DIR_SCAN_THRESHOLD`` 时不做预扫
        #   （小目录逐项 isdir 更便宜，且避免为空结果白扫整目录）。
        _dir_flags: dict[str, bool] = {}
        if len(matches) > _DIR_SCAN_THRESHOLD:
            try:
                with os.scandir(search_dir) as _entries:
                    for _entry in _entries:
                        try:
                            _dir_flags[_entry.name] = _entry.is_dir()
                        except OSError:
                            _dir_flags[_entry.name] = False
            except OSError:
                _dir_flags = {}

        def _is_dir(path: str) -> bool:
            if _dir_flags:
                flag = _dir_flags.get(os.path.basename(path))
                if flag is not None:
                    return flag
            try:
                return os.path.isdir(path)
            except OSError:
                return False

        matches.sort(key=lambda p: (not _is_dir(p), os.path.basename(p).lower()))

        # 候选数量不设上限：有多少个文件就有多少个选项（弹窗内由可见行数
        # + ↑↓/PgUp/PgDn 滚动承载，用户可浏览全部匹配）。

        # 找到公共前缀用于计算 start_pos
        if prefix.endswith(os.sep):
            base = prefix
        elif prefix in (".", ".."):
            # "." / ".." 补全结果带 "./" / "../" 前缀（与用户输入路径形态一致）
            base = prefix + os.sep
        elif prefix == "~":
            # P2-3（review 方向）："~" 枚举 home——候选替换文本带展开后的
            # home 绝对路径前缀（如 ``/Users/alice/``），与用户输入形态一致
            # （区别于 "." 的相对前缀；expanded 即 expanduser("~") 结果）。
            base = expanded if expanded.endswith(os.sep) else expanded + os.sep
        else:
            base = os.path.dirname(prefix)
            if base and not base.endswith(os.sep):
                base += os.sep

        result: list[CompletionItem] = []
        for p in matches:
            name = os.path.basename(p)
            is_dir = _is_dir(p)
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
