"""配置命令 — 费用/主题/推理等级/温度相关命令处理函数

★ 2026-08-19（模型选择界面代码独立）：/model 命令已迁至 ``_model_cmd.py``
（模型选择单一真源）；本模块保留 re-export 向后兼容（旧导入路径不变）。
"""

from __future__ import annotations

from ..constants import GREEN, YELLOW, DIM, RESET, CYAN
from ..adapters.output import get_default_output_port
from ..internal.commands._command_core import CommandContext, show_cost

# 向后兼容 re-export：/model 命令已独立到 _model_cmd.py
from ._model_cmd import _cmd_model, _infer_model_provider  # noqa: F401

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


# ── 自动注册插件 ────────────────────────────────────
get_plugin_registry().register(CostCommand())
get_plugin_registry().register(ThemeCommand())
get_plugin_registry().register(ReasoningCommand())
get_plugin_registry().register(TemperatureCommand())
