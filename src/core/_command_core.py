"""命令核心 — 调度基础设施、命令注册表与上下文"""

from __future__ import annotations

import time as _time
from src._compat import dataclass
from ..config import TOKEN_PRICES
from .constants import DIM, RESET, TEAL
from ..api.stats import get_token_stats, get_session_start_time
from .constants import format_token_k


from ..core.ports.output import get_default_output_port as _get_out  # noqa: E402

# ── 命令注册表 ────────────────────────────────────────
_commands = {}


def register_command(name, handler, help_text=""):
    """注册一个命令处理函数"""
    _commands[name] = {"handler": handler, "help": help_text}


def handle_command(cmd, messages, state, build_system_prompt, get_user_input, context_manager=None, session=None, config_port=None):
    """处理 / 开头的指令，返回 True 表示已处理。"""
    parts = cmd.split(maxsplit=1)
    command = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    if command in _commands:
        persistence_port = None
        if session is not None and hasattr(session, '_persistence_port'):
            persistence_port = session._persistence_port
        # 从 session 提取 config_port（调用方未显式传入时）
        if config_port is None and session is not None and hasattr(session, '_config_port'):
            config_port = session._config_port
        ctx = CommandContext(
            messages=messages, state=state, arg=arg,
            build_system_prompt=build_system_prompt,
            get_user_input=get_user_input,
            context_manager=context_manager,
            session=session,
            persistence_port=persistence_port,
            config_port=config_port,
        )
        return _commands[command]["handler"](ctx)

    return False


@dataclass(slots=True)
class CommandContext:
    """命令执行上下文，聚合所有命令可能需要的依赖"""
    messages: list
    state: dict
    arg: str
    build_system_prompt: object
    get_user_input: object
    context_manager: object
    session: object | None = None  # ChatSession 引用，供 compress 等需要 session 方法的命令使用
    persistence_port: object | None = None  # PersistencePort，供数据命令走端口而非直连 chat_msgs
    config_port: object | None = None  # ConfigPort，供 show_cost 等走端口读配置而非直连 config 模块
    edit_msg: dict | None = None  # /editmsg 联络信号: dict 或 None


# ── 帮助文本 ──────────────────────────────────────────
COMMANDS_HELP = (
    f"\n{DIM}  \u2500 可用命令{RESET}\n"
    f"  {TEAL}/clear{RESET}    清空对话\n"
    f"  {TEAL}/loop{RESET}     循环执行 N 次指定提词（每轮第1次用用户提词，第2次用固定提词）: /loop <次数> <提词>\n"
    f"  {TEAL}/compress{RESET} 手动压缩上下文\n"
    f"  {TEAL}/pin{RESET}      标记重要消息（压缩时保留）\n"
    f"  {TEAL}/editmsg{RESET}  编辑当前会话消息 (Ctrl+O)\n"
    f"  {TEAL}/undo{RESET}     撤销上一轮对话\n"
    f"  {TEAL}/retry{RESET}    重新生成上一条回答（或 {TEAL}/r{RESET}）\n"
    f"  {TEAL}/edit{RESET}     编辑并重新发送上一条输入\n"
    f"  {TEAL}/model{RESET}    切换模型\n"
    f"  {TEAL}/system{RESET}   修改系统提示词\n"
    f"  {TEAL}/cost{RESET}     查看 token 用量和费用\n"
    f"  {TEAL}/init{RESET}     生成项目摘要文件 init.md\n"
    f"  {TEAL}/load{RESET}     加载保存的对话 /load <id>\n"
    f"  {TEAL}/sessions{RESET} 列出所有保存的对话\n"
    f"  {TEAL}/theme{RESET}    切换配色主题: /theme <dark|light|high-contrast>\n"
    f"  {TEAL}/help{RESET}     显示帮助\n"
    f"  {DIM}  exit 退出{RESET}"
)


# ── 费用计算 ──────────────────────────────────────────
def _format_cost_duration(seconds):
    if seconds < 60:
        return f"{seconds:.0f}秒"
    elif seconds < 3600:
        m, s = divmod(int(seconds), 60)
        return f"{m}分{s}秒"
    else:
        h, remainder = divmod(int(seconds), 3600)
        m, _ = divmod(remainder, 60)
        return f"{h}小时{m}分"


def _fmt_token(n: int) -> str:
    """格式化 token 数为可读形式"""
    return format_token_k(n)


def show_cost(ctx):
    """显示 token 用量和费用。

    优先通过 ctx.config_port（ConfigPort）获取价格配置，
    若不可用则回退到直接 import TOKEN_PRICES（向后兼容）。
    """
    model = ctx.state.get("model", TOKEN_PRICES and next(iter(TOKEN_PRICES), ""))
    if ctx.config_port is not None:
        prices = ctx.config_port.get_token_prices().get(model)
    else:
        prices = TOKEN_PRICES.get(model)
    if not prices:
        if ctx.config_port is not None:
            all_prices = ctx.config_port.get_token_prices()
            prices = next(iter(all_prices.values())) if all_prices else {"input": 0.01, "output": 0.03}
        elif TOKEN_PRICES:
            prices = next(iter(TOKEN_PRICES.values()))
        else:
            prices = {"input": 0.01, "output": 0.03}
    current = get_token_stats()
    input_cost = current["input"] / 1_000_000 * prices["input"]
    output_cost = current["output"] / 1_000_000 * prices["output"]
    total = input_cost + output_cost
    elapsed = _time.time() - get_session_start_time()
    duration = _format_cost_duration(elapsed)
    _get_out().write(f"\n{DIM}  \u2500 费用统计{RESET}", level="raw", source="cmd")
    _get_out().write(f"  {DIM}\u2502{RESET} 模型  {model}", level="raw", source="cmd")
    _get_out().write(f"  {DIM}\u2502{RESET} 调用  {current['calls']}", level="raw", source="cmd")
    _get_out().write(f"  {DIM}\u2502{RESET} 输入  {_fmt_token(current['input'])}t", level="raw", source="cmd")
    _get_out().write(f"  {DIM}\u2502{RESET} 输出  {_fmt_token(current['output'])}t", level="raw", source="cmd")
    _get_out().write(f"  {DIM}\u2502{RESET} 费用  ${total:.4f}", level="raw", source="cmd")
    _get_out().write(f"  {DIM}\u2502{RESET} 时长  {duration}", level="raw", source="cmd")
    _get_out().write(f"{DIM}  \u2514{'─' * 24}{RESET}", level="raw", source="cmd")


# ── 辅助函数 ──────────────────────────────────────────

def _pop_assistant_tool_messages(messages):
    """从末尾移除 role 为 assistant 或 tool 的消息，返回移除数量。"""
    removed = 0
    while messages and messages[-1]["role"] in ("assistant", "tool"):
        messages.pop()
        removed += 1
    return removed


def get_registered_command_names() -> list[str]:
    """返回所有已注册的命令名列表（排序）"""
    names = set(_commands.keys())
    return sorted(names, key=lambda x: (x != "/help", x))  # /help 排首位


def get_dynamic_help_text() -> str:
    """从命令注册表实时构建帮助文本"""
    from .constants import DIM, RESET, TEAL
    lines = [f"{DIM}  ─ 可用命令{RESET}"]
    for name, info in sorted(_commands.items(), key=lambda x: (x[0] != "/help", x[0])):
        help_text = info.get("help", "")
        sep = "  " if help_text else ""
        lines.append(f"  {TEAL}{name}{RESET}{sep}{help_text}")
    lines.append(f"  {DIM}  exit 退出{RESET}")
    return "\n".join(lines)


# ── /help 命令 ──────────────────────────────────────────
def _cmd_help(ctx):
    _get_out().write(get_dynamic_help_text(), level="raw", source="cmd")
    return True


register_command("/help", _cmd_help, "显示帮助")
