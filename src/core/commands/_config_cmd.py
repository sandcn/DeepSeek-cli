"""配置命令 — 费用/主题/配置相关命令处理函数

★ 2026-08-19（模型选择界面代码独立）：/model 命令已迁至 ``_model_cmd.py``
（模型选择单一真源）；本模块保留 re-export 向后兼容（旧导入路径不变）。

★ 2026-10-17：全局 ``/reasoning`` 与 ``/temperature`` 命令已删除——温度与
思考级别改为在 ``/models`` 中按模型档案设置（见 ``config.model_profiles``
的 ``effective_temperature`` / ``effective_reasoning_effort``）。全局
``temperature`` / ``reasoning_effort`` 配置键仅作为模型档案未设置时的默认
值保留。
"""

from __future__ import annotations

import logging
import time as _time

from ..constants import GREEN, YELLOW, DIM, RESET, CYAN
from ..adapters.output import get_default_output_port
from ..internal.commands._command_core import CommandContext

# 向后兼容 re-export：/model 命令已独立到 _model_cmd.py
from ._model_cmd import _cmd_model, _infer_model_provider  # noqa: F401

__all__ = [
    "_cmd_theme",
    "_cmd_config",
    "ThemeCommand",
    "ConfigCommand",
    # 兼容面（/model 命令迁出后保留旧导入路径）
    "_cmd_model",
    "_infer_model_provider",
]

_logger = logging.getLogger(__name__)
_out = get_default_output_port()


def _cmd_theme(ctx):
    """切换 UI 配色主题（无参数且有 ChatUI 时打开主题选择器视图）"""
    arg = ctx.arg.strip()
    if not arg and _open_theme_ui(ctx):
        return True

    if ctx.ui_adapter is not None:
        themes = ctx.ui_adapter.get_theme_names_with_desc()
        current = ctx.ui_adapter.get_active_theme()
    else:
        _out.write(f"{YELLOW}  ! 主题管理不可用（无 UI 上下文）{RESET}", level="raw", source="cmd")
        return True

    if arg:
        # 直接参数切换：/theme dark
        for name, desc in themes:
            if arg == name:
                # set_theme 已负责校验 + 写 RC + 失效 palette 缓存；修复前再经
                # config_port.set 写第二遍（冗余整文件写，且第二次异常会在主题
                # 已切换后报错）。
                # 兼容旧适配器/测试桩（set_theme 返回 None）——只有显式 False
                # 才视为写入失败。
                if ctx.ui_adapter.set_theme(name) is not False:
                    _out.write(f"{GREEN}  + 已切换到主题「{name}」({desc}){RESET}", level="raw", source="cmd")
                else:
                    _out.write(f"{YELLOW}  ! 主题切换失败（配置写入失败）: {name}{RESET}", level="raw", source="cmd")
                return True
        _out.write(f"{YELLOW}  ! 未知主题: {arg}{RESET}", level="raw", source="cmd")
        _out.write(f"  {DIM}  可用主题: {', '.join(t[0] for t in themes)}{RESET}", level="raw", source="cmd")
        return True

    # 无参数：显示当前主题 + 可选列表
    _out.write(f"\n{DIM}  \u2500 配色主题{RESET}", level="raw", source="cmd")
    _out.write(f"  {DIM}\u2502{RESET} 当前: {CYAN}{current}{RESET}", level="raw", source="cmd")
    _out.write(f"  {DIM}\u2502{RESET} 可用:", level="raw", source="cmd")
    for name, desc in themes:
        marker = " <-" if name == current else ""
        _out.write(f"  {DIM}\u2502{RESET}   {CYAN}{name}{RESET}  {DIM}{desc}{marker}{RESET}", level="raw", source="cmd")
    _out.write(f"  {DIM}\u2514{'─' * 24}{RESET}", level="raw", source="cmd")
    _out.write(f"  {DIM} 使用: /theme <名称> 切换{RESET}", level="raw", source="cmd")
    return True


def _restore_original_theme(adapter, state) -> bool:
    """超时关闭时恢复打开前主题（best-effort；仅供 /theme on_timeout 使用）。

    Returns:
        True — 已是原主题或恢复写入成功；False — 无 adapter / 无原主题 /
        恢复失败。异常不外抛（超时清理路径不能因恢复失败卡住）。
    """
    original = str(getattr(state, "original", "") or "")
    if adapter is None or not original:
        return False
    try:
        current = str(adapter.get_active_theme() or "")
        if current == original:
            return True
        try:
            result = adapter.set_theme(
                original, persist=False, allow_unregistered=True,
            )
        except TypeError:
            # 兼容旧适配器/测试桩（不接受 persist / allow_unregistered）。
            try:
                result = adapter.set_theme(original, persist=False)
            except TypeError:
                result = adapter.set_theme(original)
        # 兼容旧适配器/测试桩（set_theme 返回 None）——只有显式 False 才算失败。
        return result is not False
    except Exception:
        _logger.debug("open theme: 超时恢复原主题失败", exc_info=True)
        return False


def _open_theme_ui(ctx) -> bool:
    """打开全屏主题选择器视图（有 ChatUI 时）。返回是否已打开处理。"""
    from ..adapters.ui_runtime import get_theme_view_state_cls
    from ._view_opener import open_fullscreen_view

    adapter = getattr(ctx, "ui_adapter", None)
    names: list = []
    current = ""
    if adapter is not None:
        try:
            names = list(adapter.get_theme_names_with_desc())
        except Exception:
            names = []
        try:
            current = str(adapter.get_active_theme() or "")
        except Exception:
            current = ""
    # 无主题数据时不打开空白全屏视图——回退到命令文本列表，至少可用/可诊断。
    if not names:
        return False

    # 当前主题可能已被 Profile/Patch 禁用（不在可用列表）——补一个只读入口，
    # 让选择器仍能显示“当前/原主题”，而不是把第一项误当成当前主题。
    if current and current not in {n for n, _d in names}:
        names.insert(0, (current, "当前主题（未注册或已禁用）"))

    def setup(model, state):
        state.original = current
        state.entries = [
            {"name": n, "desc": d, "active": n == current} for n, d in names
        ]
        state.selected = 0
        for i, e in enumerate(state.entries):
            if e["name"] == current:
                state.selected = i
                break

    return open_fullscreen_view(
        ctx, view_id="theme", state_attr="theme_view",
        state_cls=get_theme_view_state_cls(), setup=setup,
        on_timeout=lambda state: _restore_original_theme(adapter, state),
        close_hint="主题选择器已关闭", timeout_hint="主题选择器超时关闭",
    )


# ── /config 命令 ───────────────────────────────────────

def _rc_file_display() -> str:
    """配置文件路径显示文本（头部「来源」；异常 → 空串）。"""
    try:
        from ...config.defaults import RC_FILE
        return str(RC_FILE)
    except Exception:
        return ""


def _open_config_ui(ctx) -> bool:
    """打开全屏配置界面（ConfigView 模态全屏视图）。

    协议（与 CommandUiAdapter.run_bottom_bar_selection 同构）：设置
    ``model.config_view``（visible=True, seq+1, entries）→
    ``model.fullscreen = "config"`` → request_bottom_redraw → 轮询
    ``state.done``（deadline 超时兜底）→ finally 清理（重置 config_view
    保留 seq + fullscreen 置空 + request_bottom_redraw + flush router）。

    无活跃 ChatUI / 模型不可用（单次模式、测试桩）返回 False——调用方
    回退文本显示配置。
    """
    try:
        from ..adapters.ui_runtime import get_active_chat_ui
        chat_ui = get_active_chat_ui()
        if chat_ui is None:
            return False
        model = chat_ui.get_model() if hasattr(chat_ui, "get_model") else None
        if model is None or not hasattr(model, "config_view"):
            return False
    except Exception:
        return False

    from ...config.view_model import build_config_entries
    from ...config.loader import get_rc
    from ..adapters.ui_runtime import get_config_view_state_cls

    ConfigViewState = get_config_view_state_cls()
    entries = build_config_entries(get_rc())
    prev_seq = getattr(model.config_view, "seq", 0)
    state = ConfigViewState(
        visible=True,
        seq=prev_seq + 1,
        entries=entries,
        # ★ 2026-10-07（config 增强）：配置文件路径（头部「来源」显示）。
        rc_file=_rc_file_display(),
        # 超时兜底（默认 600s=10 分钟）：用户长时间无操作自动关闭，
        # 命令线程不永久阻塞（与 run_bottom_bar_selection 的 60s 语义同源）
        deadline=_time.monotonic() + 600,
    )
    model.config_view = state
    model.fullscreen = "config"
    try:
        chat_ui.request_bottom_redraw()
    except Exception:
        _logger.debug("忽略异常（best-effort 路径）", exc_info=True)

    try:
        while not state.done:
            if _time.monotonic() >= state.deadline:
                # 超时：原子终态写入（first-write-wins——组件恰在临界窗口
                # 已关闭则放弃覆盖，保留组件结果）
                state.try_set_final("timeout")
                break
            _time.sleep(0.05)
        if state.action == "timeout":
            _out.write(f"{YELLOW}  ! 配置界面超时关闭{RESET}", level="raw", source="cmd")
        else:
            _out.write(f"{DIM}  配置界面已关闭{RESET}", level="raw", source="cmd")
        return True
    finally:
        # 清理：重置 config_view（**保留当前 seq**——重新读取清理前的
        # ``model.config_view.seq``（即本次打开的 seq），保证 seq 单调递增
        # → App key（cv-{seq}）永不重复 → 调和器每次强制重挂载 ConfigView，
        # 不残留旧选中/旧编辑态）+ 仅当仍占用 fullscreen 时置空（用户可能
        # 已切换到其他全屏视图）+ request_bottom_redraw + flush router。
        # ★ P3（review 2026-08-20）：identity 比较防御——清理仅覆盖**本次
        #   打开**的 state（``model.config_view is state``）；若清理前用户/
        #   其他协程已重新打开新 config（新 ConfigViewState 对象），旧命令
        #   线程不覆盖新状态（命令串行执行使窗口极小，防御性兜底）。
        try:
            if not state.done:
                state.try_set_final("timeout")
            if getattr(model, "config_view", None) is state:
                cur_seq = getattr(model.config_view, "seq", 0)
                model.config_view = ConfigViewState(seq=cur_seq)
            if getattr(model, "fullscreen", "") == "config":
                model.fullscreen = ""
            try:
                chat_ui.request_bottom_redraw()
            except Exception:
                _logger.debug("忽略异常（best-effort 路径）", exc_info=True)
            try:
                chat_ui.flush_input_router(2.0)
            except Exception:
                _logger.debug("忽略异常（best-effort 路径）", exc_info=True)
        except Exception:
            _logger.debug("_open_config_ui cleanup 失败", exc_info=True)


def _show_config_text(ctx) -> bool:
    """文本显示全部配置（TUI 消息区输出 / CLI 回退显示共用）。"""
    from ...config.view_model import build_config_entries, format_config_text
    from ...config.defaults import RC_FILE
    text = format_config_text(build_config_entries(), rc_file=RC_FILE)
    _out.write("\n" + text, level="raw", source="cmd")
    return True


def _find_entry(key_input: str) -> dict | None:
    """解析用户输入的键名并返回对应配置项条目（未找到返回 None）。"""
    from ...config.view_model import resolve_config_key, build_config_entries
    key = resolve_config_key(key_input)
    if key is None:
        return None
    for e in build_config_entries():
        if e["key"] == key:
            return e
    return None


def _get_config_value(ctx, key_input: str) -> bool:
    """查询单项配置并显示。"""
    entry = _find_entry(key_input)
    if entry is None:
        _out.write(f"{YELLOW}  ! 未找到配置键: {key_input}{RESET}", level="raw", source="cmd")
        _out.write(f"  {DIM}  使用 /config 查看全部配置键{RESET}", level="raw", source="cmd")
        return True
    _out.write(f"\n{CYAN}  {entry['path']}{RESET} = {entry['value_text']}  {DIM}(默认: {entry['default_text']}){RESET}", level="raw", source="cmd")
    if entry.get("desc"):
        _out.write(f"  {DIM}  {entry['desc']}{RESET}", level="raw", source="cmd")
    return True


def _set_config_value(ctx, key_input: str, value_text: str) -> bool:
    """设置单项配置并持久化（类型校验失败给出错误提示）。"""
    entry = _find_entry(key_input)
    if entry is None:
        _out.write(f"{YELLOW}  ! 未找到配置键: {key_input}{RESET}", level="raw", source="cmd")
        _out.write(f"  {DIM}  使用 /config 查看全部配置键{RESET}", level="raw", source="cmd")
        return True
    from ...config.view_model import parse_config_value, format_config_value
    value, err = parse_config_value(entry["type"], value_text)
    if err:
        _out.write(f"{YELLOW}  ! {entry['path']}: {err}{RESET}", level="raw", source="cmd")
        return True
    try:
        from ...config.loader import update_config
        update_config(entry["key"], value)
    except Exception as e:
        _out.write(f"{YELLOW}  ! 写入配置失败: {e}{RESET}", level="raw", source="cmd")
        return True
    shown = format_config_value(
        value, entry["type"], sensitive=bool(entry.get("sensitive")),
    )
    _out.write(f"{GREEN}  + 已设置 {entry['path']} = {shown}{RESET}", level="raw", source="cmd")
    return True


def _reset_config_value(ctx, key_input: str) -> bool:
    """重置单项配置为默认值并持久化。"""
    from ...config.view_model import (
        resolve_config_key, format_config_value, get_entry_display_path,
    )
    from ...config.defaults import CONFIG_KEYS, DEFAULTS
    key = resolve_config_key(key_input)
    if key is None:
        _out.write(f"{YELLOW}  ! 未找到配置键: {key_input}{RESET}", level="raw", source="cmd")
        return True
    if key in CONFIG_KEYS:
        default = CONFIG_KEYS[key]["default"]
        typ = CONFIG_KEYS[key]["type"]
    else:
        default = DEFAULTS.get(key)
        typ = type(default) if default is not None else str
    try:
        from ...config.loader import update_config
        update_config(key, default)
    except Exception as e:
        _out.write(f"{YELLOW}  ! 写入配置失败: {e}{RESET}", level="raw", source="cmd")
        return True
    shown = format_config_value(default, typ)
    # ★ 修复（2026-10）：输出显示路径（与 get/set 一致；修复前打印大写写回键）
    _out.write(f"{GREEN}  + 已重置 {get_entry_display_path(key)} = {shown} (默认){RESET}", level="raw", source="cmd")
    return True


def _cmd_config(ctx):
    """配置管理：显示 / 编辑程序配置（/config）

    - 无参数：有 ChatUI 时打开**全屏配置界面**（ConfigView——配置列表
      浏览 + Enter 编辑 + Esc/Ctrl+H 关闭）；无 ChatUI 回退文本显示全部配置；
    - ``/config show`` / ``/config list``：文本显示全部配置；
    - ``/config get <键>``：查询单项配置；
    - ``/config set <键> <值>``（或 ``<键>=<值>``）：设置单项并持久化；
    - ``/config reset <键>``：重置为默认值。
    """
    arg = ctx.arg.strip()
    if not arg:
        # 无参数：优先打开全屏配置界面；无 ChatUI 回退文本显示
        if _open_config_ui(ctx):
            return True
        return _show_config_text(ctx)

    parts = arg.split(maxsplit=1)
    sub = parts[0].lower()
    rest = parts[1].strip() if len(parts) > 1 else ""

    if sub in ("show", "list"):
        return _show_config_text(ctx)
    if sub == "get":
        if not rest:
            _out.write(f"{YELLOW}  ! 用法: /config get <键>{RESET}", level="raw", source="cmd")
            return True
        return _get_config_value(ctx, rest)
    if sub == "set":
        # 支持两种形态：``/config set <键> <值>`` 与 ``/config set <键>=<值>``
        if "=" in rest:
            key_part, _, val_part = rest.partition("=")
        else:
            key_part, _, val_part = rest.partition(" ")
        key_part = key_part.strip()
        val_part = val_part.strip()
        if not key_part or not val_part:
            _out.write(f"{YELLOW}  ! 用法: /config set <键> <值>（或 <键>=<值>）{RESET}", level="raw", source="cmd")
            return True
        return _set_config_value(ctx, key_part, val_part)
    if sub == "reset":
        if not rest:
            _out.write(f"{YELLOW}  ! 用法: /config reset <键>{RESET}", level="raw", source="cmd")
            return True
        return _reset_config_value(ctx, rest)

    _out.write(f"{YELLOW}  ! 未知 config 子命令: {sub}{RESET}", level="raw", source="cmd")
    _out.write(f"  {DIM}  可用: show|list, get <键>, set <键> <值>, reset <键>{RESET}", level="raw", source="cmd")
    return True


# ── CommandPlugin 子类 ──────────────────────────────
# 命令在此声明（declare_command_plugin）；注册由清单条目 / ctx.commands 服务按需触发。
# 注册时内部调用 register_command() 保持向后兼容。

from .base import CommandPlugin, CommandMeta, declare_command_plugin


class ThemeCommand(CommandPlugin):
    """切换主题"""
    def __init__(self):
        self.meta = CommandMeta(
            name="theme", description="切换配色主题", group="ui", queue_only=True,
        )

    def execute(self, ctx: CommandContext) -> bool:
        return _cmd_theme(ctx)


class ConfigCommand(CommandPlugin):
    """显示/编辑程序配置（独立界面）"""
    def __init__(self):
        self.meta = CommandMeta(
            name="config", description="显示/编辑程序配置（/config 打开独立界面）",
            group="ui", queue_only=True,
        )

    def execute(self, ctx: CommandContext) -> bool:
        return _cmd_config(ctx)


# ── 声明插件（注册由清单条目 / ctx.commands 服务按需触发） ──
declare_command_plugin(ThemeCommand())
declare_command_plugin(ConfigCommand())
