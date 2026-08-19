"""配置命令 — 费用/主题/推理等级/温度相关命令处理函数

★ 2026-08-19（模型选择界面代码独立）：/model 命令已迁至 ``_model_cmd.py``
（模型选择单一真源）；本模块保留 re-export 向后兼容（旧导入路径不变）。
"""

from __future__ import annotations

import logging
import time as _time

from ..constants import GREEN, YELLOW, DIM, RESET, CYAN
from ..adapters.output import get_default_output_port
from ..internal.commands._command_core import CommandContext, show_cost

# 向后兼容 re-export：/model 命令已独立到 _model_cmd.py
from ._model_cmd import _cmd_model, _infer_model_provider  # noqa: F401

_logger = logging.getLogger(__name__)
_out = get_default_output_port()


def _cmd_cost(ctx):
    show_cost(ctx)
    return True


def _cmd_theme(ctx):
    """切换 UI 配色主题"""
    arg = ctx.arg.strip()

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
                ctx.ui_adapter.set_theme(name)
                if ctx.config_port is not None:
                    ctx.config_port.set("theme", name)
                _out.write(f"{GREEN}  + 已切换到主题「{name}」({desc}){RESET}", level="raw", source="cmd")
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


# ── /reasoning 命令 ───────────────────────────────────────

# 推理等级允许值域（DeepSeek V4 thinking mode reasoning_effort）
_REASONING_LEVELS: list[str] = ["low", "medium", "high", "max"]


def _cmd_reasoning(ctx):
    """调整推理等级（low/medium/high/max）

    - 有参数：设置推理等级并持久化到 RC 配置
    - 无参数：显示当前推理等级 + 可选等级列表
    """
    arg = ctx.arg.strip()

    # 读取当前推理等级（优先 ConfigPort，回退到 config 模块）
    if ctx.config_port is not None:
        current = ctx.config_port.get_reasoning_effort()
    else:
        from ...config import REASONING_EFFORT as current

    if arg:
        arg_l = arg.lower()
        # ① 精确匹配
        exact = [lvl for lvl in _REASONING_LEVELS if lvl == arg_l]
        if exact:
            selected = exact[0]
        else:
            # ② 前缀模糊匹配（如 /reasoning hi → high）
            matched = [lvl for lvl in _REASONING_LEVELS if lvl.startswith(arg_l)]
            if len(matched) == 1:
                selected = matched[0]
            elif len(matched) > 1:
                _out.write(f"{YELLOW}  ! 匹配到多个推理等级: {', '.join(matched)}{RESET}", level="raw", source="cmd")
                _out.write(f"  {DIM}  可用等级: {', '.join(_REASONING_LEVELS)}{RESET}", level="raw", source="cmd")
                return True
            else:
                _out.write(f"{YELLOW}  ! 未知推理等级: {arg}{RESET}", level="raw", source="cmd")
                _out.write(f"  {DIM}  可用等级: {', '.join(_REASONING_LEVELS)}{RESET}", level="raw", source="cmd")
                return True

        if ctx.config_port is not None:
            ctx.config_port.set("reasoning_effort", selected)
        else:
            from ...config.loader import update_config as _upd
            _upd("reasoning_effort", selected)
        _out.write(f"{GREEN}  + 已设置推理等级: {selected}{RESET}", level="raw", source="cmd")
        return True

    # 无参数：显示当前推理等级 + 可选列表
    _out.write(f"\n{DIM}  \u2500 推理等级{RESET}", level="raw", source="cmd")
    _out.write(f"  {DIM}\u2502{RESET} 当前: {CYAN}{current}{RESET}", level="raw", source="cmd")
    _out.write(f"  {DIM}\u2502{RESET} 可用:", level="raw", source="cmd")
    for level in _REASONING_LEVELS:
        marker = " <-" if level == current else ""
        _out.write(f"  {DIM}\u2502{RESET}   {CYAN}{level}{RESET}{DIM}{marker}{RESET}", level="raw", source="cmd")
    _out.write(f"  {DIM}\u2514{'─' * 24}{RESET}", level="raw", source="cmd")
    _out.write(f"  {DIM} 使用: /reasoning <low|medium|high|max> 切换{RESET}", level="raw", source="cmd")
    return True


# ── /temperature 命令 ────────────────────────────────────

# 温度允许值域（DeepSeek 等 OpenAI 兼容 API：0.0~2.0）
_TEMPERATURE_RANGE: tuple[float, float] = (0.0, 2.0)


def _cmd_temperature(ctx):
    """调整大模型温度（0.0~2.0）

    - 有参数：设置温度并持久化到 RC 配置
    - 无参数：显示当前温度 + 允许范围
    """
    arg = ctx.arg.strip()

    # 读取当前温度（优先 ConfigPort，回退到 config 模块）
    if ctx.config_port is not None:
        current = float(ctx.config_port.get_temperature())
    else:
        from ...config import TEMPERATURE as _current
        current = float(_current)

    if arg:
        # 解析数值参数
        try:
            value = float(arg)
        except (ValueError, TypeError):
            _out.write(f"{YELLOW}  ! 无效温度值: {arg}{RESET}", level="raw", source="cmd")
            _out.write(f"  {DIM}  温度必须是 {_TEMPERATURE_RANGE[0]:g}~{_TEMPERATURE_RANGE[1]:g} 之间的数字{RESET}", level="raw", source="cmd")
            return True
        # 值域校验（防御 NaN/Inf 等非有限值）
        if not (_TEMPERATURE_RANGE[0] <= value <= _TEMPERATURE_RANGE[1]):
            _out.write(f"{YELLOW}  ! 温度超出范围: {value:g}{RESET}", level="raw", source="cmd")
            _out.write(f"  {DIM}  允许范围: {_TEMPERATURE_RANGE[0]:g} ~ {_TEMPERATURE_RANGE[1]:g}{RESET}", level="raw", source="cmd")
            return True
        # 规范化：最多保留 2 位小数
        value = round(value, 2)

        if ctx.config_port is not None:
            ctx.config_port.set("temperature", value)
        else:
            from ...config.loader import update_config as _upd
            _upd("temperature", value)
        _out.write(f"{GREEN}  + 已设置温度: {value:g}{RESET}", level="raw", source="cmd")
        return True

    # 无参数：显示当前温度 + 允许范围
    _out.write(f"\n{DIM}  \u2500 大模型温度{RESET}", level="raw", source="cmd")
    _out.write(f"  {DIM}\u2502{RESET} 当前: {CYAN}{current:g}{RESET}", level="raw", source="cmd")
    _out.write(f"  {DIM}\u2502{RESET} 范围: {_TEMPERATURE_RANGE[0]:g} ~ {_TEMPERATURE_RANGE[1]:g}", level="raw", source="cmd")
    _out.write(f"  {DIM}\u2502{RESET} 说明: 值越高输出越随机，越低越确定", level="raw", source="cmd")
    _out.write(f"  {DIM}\u2514{'─' * 24}{RESET}", level="raw", source="cmd")
    _out.write(f"  {DIM} 使用: /temperature <数值> 切换（如 /temperature 0.7）{RESET}", level="raw", source="cmd")
    return True


# ── /config 命令 ───────────────────────────────────────

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
        from ...tui.consumer import get_active_chat_ui
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
    from ...tui.app._state_types import ConfigViewState

    entries = build_config_entries(get_rc())
    prev_seq = getattr(model.config_view, "seq", 0)
    state = ConfigViewState(
        visible=True,
        seq=prev_seq + 1,
        entries=entries,
        # 超时兜底（默认 600s=10 分钟）：用户长时间无操作自动关闭，
        # 命令线程不永久阻塞（与 run_bottom_bar_selection 的 60s 语义同源）
        deadline=_time.monotonic() + 600,
    )
    model.config_view = state
    model.fullscreen = "config"
    try:
        chat_ui.request_bottom_redraw()
    except Exception:
        pass

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
                pass
            try:
                chat_ui.flush_input_router(2.0)
            except Exception:
                pass
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
    from ...config.view_model import resolve_config_key, format_config_value
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
    _out.write(f"{GREEN}  + 已重置 {key} = {shown} (默认){RESET}", level="raw", source="cmd")
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
# 命令通过 get_plugin_registry().register() 注册，不再使用 register_command()。
# CommandPluginRegistry.register() 内部自动调用 register_command() 确保向后兼容。

from .base import CommandPlugin, CommandMeta, get_plugin_registry


class CostCommand(CommandPlugin):
    """显示费用统计"""
    def __init__(self):
        self.meta = CommandMeta(name="cost", description="查看 token 用量和费用")

    def execute(self, ctx: CommandContext) -> bool:
        return _cmd_cost(ctx)


class ThemeCommand(CommandPlugin):
    """切换主题"""
    def __init__(self):
        self.meta = CommandMeta(name="theme", description="切换配色主题")

    def execute(self, ctx: CommandContext) -> bool:
        return _cmd_theme(ctx)


class ReasoningCommand(CommandPlugin):
    """调整推理等级"""
    def __init__(self):
        self.meta = CommandMeta(name="reasoning", description="调整推理等级 (low/medium/high/max)")

    def execute(self, ctx: CommandContext) -> bool:
        return _cmd_reasoning(ctx)


class TemperatureCommand(CommandPlugin):
    """调整大模型温度"""
    def __init__(self):
        self.meta = CommandMeta(name="temperature", description="调整大模型温度 (0.0~2.0)")

    def execute(self, ctx: CommandContext) -> bool:
        return _cmd_temperature(ctx)


class ConfigCommand(CommandPlugin):
    """显示/编辑程序配置（独立界面）"""
    def __init__(self):
        self.meta = CommandMeta(name="config", description="显示/编辑程序配置（/config 打开独立界面）")

    def execute(self, ctx: CommandContext) -> bool:
        return _cmd_config(ctx)


# ── 自动注册插件 ────────────────────────────────────
get_plugin_registry().register(CostCommand())
get_plugin_registry().register(ThemeCommand())
get_plugin_registry().register(ReasoningCommand())
get_plugin_registry().register(TemperatureCommand())
get_plugin_registry().register(ConfigCommand())
