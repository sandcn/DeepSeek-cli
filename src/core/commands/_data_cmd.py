"""数据命令 — 文件读写/会话管理相关命令处理函数"""

from ..constants import GREEN, YELLOW, DIM, RESET, CYAN, filter_system, filter_non_system
from ..adapters.output import get_default_output_port
from ...config import MODEL
from ..sandbox_manager import get_sandbox_manager
from ..internal.commands._command_core import CommandContext

_out = get_default_output_port()

_SESSION_ID_TRUNCATE = 12     # 会话ID显示截断长度


def _cmd_load(ctx):
    """加载保存的对话（自动保存当前会话并清空沙盒）"""
    arg = ctx.arg.strip()
    if not arg:
        _out.write(f"{YELLOW}  ! 用法: /load <会话ID>{RESET}", level="raw", source="cmd")
        _out.write(f"  {DIM}  输入 /sessions 查看所有保存的对话{RESET}", level="raw", source="cmd")
        return True

    # ── 第1步：自动保存当前会话（如有非 system 消息） ──────────
    non_system_current = filter_non_system(ctx.messages)
    from ..adapters.persistence import JsonFilePersistence
    _p = ctx.persistence_port if ctx.persistence_port is not None else JsonFilePersistence()
    if non_system_current:
        current_model = ctx.state.get("model", MODEL)
        # 连同 SubAgent 记录一起保存（含完整聊天信息）
        current_subagents = []
        if ctx.session is not None:
            agent = getattr(ctx.session, "agent", None)
            if agent is not None:
                current_subagents = list(getattr(agent, "_subagent_records", None) or [])
        try:
            saved_id = _p.save_session(non_system_current, model=current_model,
                                       subagents=current_subagents)
            _out.write(f"{DIM}  + 已自动保存当前会话: {saved_id[:_SESSION_ID_TRUNCATE]}{RESET}", level="raw", source="cmd")
        except Exception as e:
            _out.write(f"{YELLOW}  ! 自动保存当前会话失败: {e}{RESET}", level="raw", source="cmd")

    # ── 第2步：清空文件沙盒 ──────────────────────────────────
    sandbox = get_sandbox_manager()
    if sandbox:
        sandbox.clear()
        _out.write(f"{DIM}  + 文件沙盒已清空{RESET}", level="raw", source="cmd")

    # ── 第3步：加载目标会话 ──────────────────────────────────
    data = _p.load_session(arg)
    if data is None:
        _out.write(f"{YELLOW}  ! 未找到会话 '{arg}'{RESET}", level="raw", source="cmd")
        return True

    loaded_msgs = data.get("messages", [])
    if not loaded_msgs:
        _out.write(f"{YELLOW}  ! 该会话没有消息{RESET}", level="raw", source="cmd")
        return True

    # 替换当前消息（保留 system 消息）
    system_msgs = filter_system(ctx.messages)
    ctx.messages[:] = system_msgs
    for msg in loaded_msgs:
        ctx.messages.append(msg)

    # 恢复 SubAgent 记录（含完整聊天信息，供 /export 导出）
    loaded_subagents = data.get("subagents") or []
    if ctx.session is not None:
        agent = getattr(ctx.session, "agent", None)
        if agent is not None:
            setattr(agent, "_subagent_records", list(loaded_subagents))

    model = data.get("model", ctx.state.get("model", MODEL))
    ctx.state["model"] = model

    title = data.get("title", "")
    title_info = f"「{title}」 " if title else ""
    _out.write(f"{GREEN}  + 已加载会话 {title_info}{arg} ({len(loaded_msgs)} 条消息, 模型: {model}){RESET}", level="raw", source="cmd")

    # 显示恢复的消息摘要（用项目流式渲染器回放）
    non_system = filter_non_system(ctx.messages)
    if ctx.ui_adapter is not None:
        ctx.ui_adapter.display_messages(non_system, speed=1000)

    # 检查最后一条消息角色
    if ctx.messages and ctx.messages[-1].get("role") in ("assistant", "tool"):
        _out.write(f"  {DIM}  继续输入开始新的对话{RESET}", level="raw", source="cmd")
    elif ctx.messages and ctx.messages[-1].get("role") == "user":
        _out.write(f"  {DIM}  最后一条是用户消息，将自动继续生成回复…{RESET}", level="raw", source="cmd")
        ctx.state["retry"] = True
    return True


def _cmd_sessions(ctx):
    """列出所有保存的对话"""
    from ..adapters.persistence import JsonFilePersistence
    _p = ctx.persistence_port if ctx.persistence_port is not None else JsonFilePersistence()
    sessions = _p.list_sessions()
    if not sessions:
        _out.write(f"{YELLOW}  ! 没有保存的对话{RESET}", level="raw", source="cmd")
        return True
    _out.write(f"\n{DIM}  \u2500 已保存的对话{RESET}", level="raw", source="cmd")
    for s in sessions:
        msg_count = s.get("message_count", 0)
        title = s.get("title", "")
        if title:
            # 标题和ID并排显示，限制标题宽度
            _out.write(f"  {CYAN}  {s['id'][:8]}{RESET}  {DIM}{title}{RESET}", source="cmd")
            _out.write(f"  {DIM}     {s['model']}  {msg_count}条  {s['saved_at']}{RESET}", source="cmd")
        else:
            _out.write(f"  {CYAN}  {s['id']}{RESET}  {DIM}{s['model']}  {msg_count}条消息  {s['saved_at']}{RESET}", source="cmd")
    _out.write("", level="raw", source="cmd")
    return True


# ── CommandPlugin 子类 ──────────────────────────────
# 命令通过 get_plugin_registry().register() 注册，不再使用 register_command()。
# CommandPluginRegistry.register() 内部自动调用 register_command() 确保向后兼容。

from .base import CommandPlugin, CommandMeta, get_plugin_registry


class LoadCommand(CommandPlugin):
    """加载历史对话"""
    def __init__(self):
        self.meta = CommandMeta(name="load", description="加载保存的对话")

    def execute(self, ctx: CommandContext) -> bool:
        return _cmd_load(ctx)


class SessionsCommand(CommandPlugin):
    """列出所有对话"""
    def __init__(self):
        self.meta = CommandMeta(name="sessions", description="列出所有保存的对话")

    def execute(self, ctx: CommandContext) -> bool:
        return _cmd_sessions(ctx)


class HelpCommand(CommandPlugin):
    """显示帮助"""
    def __init__(self):
        self.meta = CommandMeta(name="help", description="显示帮助")

    def execute(self, ctx: CommandContext) -> bool:
        from ..internal.commands._command_core import _cmd_help
        return _cmd_help(ctx)


# ── 自动注册插件 ────────────────────────────────────
get_plugin_registry().register(LoadCommand())
get_plugin_registry().register(SessionsCommand())
get_plugin_registry().register(HelpCommand())
