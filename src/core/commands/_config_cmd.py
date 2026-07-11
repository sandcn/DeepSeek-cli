"""配置命令 — 模型/系统提示词/费用/主题相关命令处理函数"""

from ..constants import GREEN, YELLOW, DIM, RESET, CYAN
from ..adapters.output import get_default_output_port
from ..internal.commands._command_core import CommandContext, show_cost

_out = get_default_output_port()


# ── /model 命令 ─────────────────────────────────────────

def _cmd_model(ctx):
    # 通过 ConfigPort 获取模型列表和当前模型
    if ctx.config_port is not None:
        models = ctx.config_port.get_models()
        default_model = ctx.config_port.get_model()
    else:
        from ...config import MODELS as models, MODEL as default_model  # 配置常量 — 函数体内延迟导入（回退）
    current = ctx.state.get("model", default_model)
    arg = ctx.arg.strip()

    # ── 优先处理直接参数：按序号或名称切换 ──────────────
    if arg:
        # 按序号：/model 2
        if arg.isdigit():
            idx = int(arg)
            if 1 <= idx <= len(models):
                selected = models[idx - 1]
                ctx.state["model"] = selected
                _out.write(f"{GREEN}  + 已切换到 {selected}{RESET}", level="raw", source="cmd")
                return True
            _out.write(f"{YELLOW}  ! 无效序号，范围 1-{len(models)}{RESET}", level="raw", source="cmd")
            return True
        # 按名称（模糊匹配）：/model deepseek-v4-pro
        matched = [m for m in models if arg.lower() in m.lower()]
        if len(matched) == 1:
            ctx.state["model"] = matched[0]
            _out.write(f"{GREEN}  + 已切换到 {matched[0]}{RESET}", level="raw", source="cmd")
            return True
        elif len(matched) > 1:
            _out.write(f"{YELLOW}  ! 匹配到多个模型: {', '.join(matched)}{RESET}", level="raw", source="cmd")
            _out.write(f"  {DIM}  请使用序号或更精确的名称{RESET}", level="raw", source="cmd")
            return True
        else:
            _out.write(f"{YELLOW}  ! 未找到匹配的模型: {arg}{RESET}", level="raw", source="cmd")
            _out.write(f"  {DIM}  可用模型: {', '.join(models)}{RESET}", level="raw", source="cmd")
            return True

    # ── 无参数：底部栏补全弹窗交互式选择 ──────────────
    if not models:
        _out.write(f"{YELLOW}  ! 没有可用的模型，请在配置文件中添加{RESET}", level="raw", source="cmd")
        return True

    # 光标定位到当前模型
    current_idx = 0
    for i, m in enumerate(models):
        if m == current:
            current_idx = i
            break

    # 构建显示项（纯文本，不含 ANSI 码 → 避免弹窗截断问题）
    display_items = []
    for m in models:
        marker = "  <-当前" if m == current else ""
        display_items.append(f"{m}{marker}")

    if ctx.ui_adapter is not None:
        result = ctx.ui_adapter.run_bottom_bar_selection(
            models, display_items, current_idx, title="模型选择",
        )
    else:
        result = {"action": "error", "index": None}

    if result["action"] == "confirmed" and result["index"] is not None:
        selected = models[result["index"]]
        if selected != current:
            ctx.state["model"] = selected
            _out.write(f"{GREEN}  + 已切换到 {selected}{RESET}", level="raw", source="cmd")
        else:
            _out.write(f"{DIM}  当前已是 {selected}{RESET}", level="raw", source="cmd")
    elif result["action"] == "cancel":
        _out.write(f"{YELLOW}  ! 已取消{RESET}", level="raw", source="cmd")
    elif result["action"] == "error":
        _out.write(f"{YELLOW}  ! 底部栏不可用，请直接指定模型名称{RESET}", level="raw", source="cmd")
        _out.write(f"  {DIM}  可用模型: {', '.join(models)}{RESET}", level="raw", source="cmd")
    return True


def _cmd_system(ctx):
    if not ctx.messages:
        _out.write(f"{YELLOW}  ! 消息列表为空，无法修改{RESET}", level="raw", source="cmd")
        return True

    # 计算插入位置：优先在最后一条非摘要 system 后，其次在所有 system 后，兜底 0
    insert_pos = 0
    for i, msg in enumerate(ctx.messages):
        if msg.get("role") == "system" and not (msg.get("content") or "").startswith("[对话摘要]"):
            insert_pos = i + 1
    if insert_pos == 0:
        # 没有非摘要 system 消息 → 放在所有 system 消息（如摘要）之后
        for i in range(len(ctx.messages) - 1, -1, -1):
            if ctx.messages[i].get("role") == "system":
                insert_pos = i + 1
                break

    if ctx.arg:
        ctx.messages.insert(insert_pos, {"role": "system", "content": ctx.arg})
        _out.write(f"{GREEN}  + 已追加系统提示词（新消息）{RESET}", level="raw", source="cmd")
    else:
        # 显示所有 system 消息（含摘要）
        system_msgs = [(i, m) for i, m in enumerate(ctx.messages)
                       if m.get("role") == "system"]
        _out.write(f"  {DIM}\u2500 系统提示词（共 {len(system_msgs)} 段）{RESET}", level="raw", source="cmd")
        for orig_idx, msg in system_msgs:
            content = msg.get("content") or ""
            # 提取标签：取第一行非空行，去掉 # 前缀
            label = ""
            for line in content.splitlines():
                stripped = line.strip()
                if stripped:
                    label = stripped.lstrip("#").strip()
                    break
            if not label:
                label = f"第 {orig_idx} 段"
            # 显示完整内容
            char_count = len(content)
            _out.write(f"  {DIM}\u2502{RESET} {CYAN}[{orig_idx}]{RESET} {DIM}{label}{RESET}  ({char_count} 字符)", level="raw", source="cmd")
            _out.write(content, level="raw", source="cmd")
            _out.write("", level="raw", source="cmd")
        _out.write(f"  {DIM}\u2514{'─' * 24}{RESET}", level="raw", source="cmd")
        new_text = ctx.get_user_input("输入新的补充内容 (回车跳过): ").strip()
        if not new_text:
            _out.write(f"{YELLOW}  ! 已取消{RESET}", level="raw", source="cmd")
            return True
        ctx.messages.insert(insert_pos, {"role": "system", "content": new_text})
        _out.write(f"{GREEN}  + 已追加{RESET}", level="raw", source="cmd")
    return True


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


# ── CommandPlugin 子类 ──────────────────────────────
# 命令通过 get_plugin_registry().register() 注册，不再使用 register_command()。
# CommandPluginRegistry.register() 内部自动调用 register_command() 确保向后兼容。

from .base import CommandPlugin, CommandMeta, get_plugin_registry


class SystemCommand(CommandPlugin):
    """设置系统提示"""
    def __init__(self):
        self.meta = CommandMeta(name="system", description="修改系统提示词")

    def execute(self, ctx: CommandContext) -> bool:
        return _cmd_system(ctx)


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


# ── 自动注册插件 ────────────────────────────────────
get_plugin_registry().register(SystemCommand())
get_plugin_registry().register(CostCommand())
get_plugin_registry().register(ThemeCommand())
