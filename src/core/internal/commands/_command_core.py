"""命令核心 — 调度基础设施、命令注册表与上下文"""

from __future__ import annotations

import time as _time
from src._compat import dataclass
from ...constants import DIM, RESET, TEAL
from ....api.stats import get_token_stats, get_session_start_time
from ...constants import format_token_k


from ....core.adapters.output import get_default_output_port as _get_out  # noqa: E402

# ── 命令注册表 ────────────────────────────────────────
_commands = {}


def register_command(name, handler, help_text=""):
    """注册一个命令处理函数"""
    _commands[name] = {"handler": handler, "help": help_text}


def handle_command(cmd, messages, state, build_system_prompt, get_user_input, context_manager=None, session=None, config_port=None, ui_adapter=None):
    """处理 / 开头的指令，返回 True 表示已处理。

    Args:
        cmd: 命令字符串（含 /）
        messages: 消息列表
        state: 状态字典
        build_system_prompt: 构建系统提示词的函数
        get_user_input: 获取用户输入的函数
        context_manager: 上下文管理器
        session: ChatSession 实例
        config_port: ConfigPort 实例
        ui_adapter: CommandUiAdapter 实例（用于 UI 交互操作）
    """
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
            ui_adapter=ui_adapter,
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
    ui_adapter: object | None = None  # CommandUiAdapter 实例，供 UI 交互操作（底部栏/主题/diff 渲染等）


# ── 帮助文本 ──────────────────────────────────────────
COMMANDS_HELP = (
    f"\n{DIM}  \u2500 可用命令{RESET}\n"
    f"  {TEAL}/clear{RESET}    清空对话\n"
    f"  {TEAL}/loop{RESET}     循环执行 N 次指定提词（每轮第1次用用户提词，第2次用固定提词）: /loop <次数> <提词>\n"
    f"  {TEAL}/pin{RESET}      标记重要消息（压缩时保留）\n"
    f"  {TEAL}/editmsg{RESET}  编辑当前会话消息 (Ctrl+O)\n"
    f"  {TEAL}/undo{RESET}     撤销上一轮对话\n"
    f"  {TEAL}/retry{RESET}    重新生成上一条回答（或 {TEAL}/r{RESET}）\n"
    f"  {TEAL}/edit{RESET}     编辑并重新发送上一条输入\n"
    f"  {TEAL}/model{RESET}    切换模型\n"
    f"  {TEAL}/reasoning{RESET} 调整推理等级: /reasoning <low|medium|high|max>\n"
    f"  {TEAL}/system{RESET}   修改系统提示词\n"
    f"  {TEAL}/cost{RESET}     查看 token 用量和费用\n"
    f"  {TEAL}/load{RESET}     加载保存的对话 /load <id>\n"
    f"  {TEAL}/sessions{RESET} 列出所有保存的对话\n"
    f"  {TEAL}/theme{RESET}    切换配色主题: /theme <dark|light|high-contrast>\n"
    f"  {TEAL}/toolcard{RESET}  折叠/展开工具卡片: /toolcard [all|expand|collapse]\n"
    f"  {TEAL}/help{RESET}     显示帮助\n"
    f"  {DIM}  exit 退出{RESET}"
    # ── 快捷键说明（2026-08-05 增加操作；readline 编辑键 + 会话操作键） ──
    f"\n{DIM}  \u2500 快捷键{RESET}\n"
    f"  {TEAL}Ctrl+A/Home{RESET}   光标到行首   {TEAL}Ctrl+E/End{RESET}    光标到行尾\n"
    f"  {TEAL}Ctrl+F/→{RESET}      右移一字符   {TEAL}Ctrl+B/←{RESET}      左移一字符\n"
    f"  {TEAL}Ctrl+←/→{RESET}      词跳转       {TEAL}Alt+B/F{RESET}       词跳转\n"
    f"  {TEAL}Ctrl+W{RESET}        删前一词     {TEAL}Alt+D{RESET}         删后一词\n"
    f"  {TEAL}Ctrl+U{RESET}        删至行首     {TEAL}Ctrl+K{RESET}        删至行尾\n"
    f"  {TEAL}Ctrl+P/↑{RESET}      历史上一条   {TEAL}Ctrl+R{RESET}         反向搜索/重试\n"
    f"  {TEAL}Tab/Shift+Tab{RESET} 补全循环     {TEAL}PgUp/PgDn{RESET}       补全翻页\n"
    f"  {TEAL}Ctrl+G{RESET}        打开 vim 编辑 {TEAL}Ctrl+O{RESET}         编辑会话消息\n"
    f"  {TEAL}Ctrl+N{RESET}        切换模型     {TEAL}Ctrl+T{RESET}         切换主题\n"
    f"  {TEAL}Ctrl+L{RESET}        清屏         {TEAL}Ctrl+D{RESET}         退出(空输入)\n"
    f"  {TEAL}Ctrl+B{RESET}        主Agent空模式 {TEAL}Ctrl+Y{RESET}         折叠/展开工具卡片\n"
    f"  {TEAL}Esc{RESET}           取消输入/中断"
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


def compute_cost(stats: dict, prices: dict) -> dict:
    """计算 token 费用明细（含缓存命中折扣）。

    缓存命中计费规则：
    - 命中输入按 ``prices["input_cache_hit"]`` 计费（DeepSeek 等按未命中价
      ~1/8 优惠）；价格缺失时回退按 ``prices["input"]`` 全价（保守不低估）。
    - 未命中输入按 ``prices["input"]`` 全价计费。
    - 输出按 ``prices["output"]`` 计费。

    向后兼容：stats 缺失缓存拆分字段（旧统计）时，全部输入视为未命中，
    费用结果与旧实现一致（input 全价）。

    Args:
        stats: token_stats 快照（含 input/output/input_cache_hit/input_cache_miss）
        prices: 模型价格配置 {"input": $/M, "output": $/M, "input_cache_hit": $/M(可选)}

    Returns:
        dict: 含 total_input / input_hit / input_miss / input_cost /
        output_cost / total / saved / cache_priced。
    """
    total_input = int(stats.get("input", 0))
    input_hit = int(stats.get("input_cache_hit", 0) or 0)
    input_miss_raw = stats.get("input_cache_miss")
    if input_miss_raw is None:
        # 旧统计无缓存拆分：全部输入视为未命中（费用与历史一致）
        input_miss = total_input
        input_hit = 0
    else:
        input_miss = int(input_miss_raw or 0)
    # 防御：不允许负 token 数
    if input_hit < 0:
        input_hit = 0
    if input_miss < 0:
        input_miss = 0

    input_price = float(prices.get("input", 0.0))
    output_price = float(prices.get("output", 0.0))
    cache_hit_price = float(prices.get("input_cache_hit", input_price))

    input_cost = (input_miss * input_price + input_hit * cache_hit_price) / 1_000_000
    output_cost = int(stats.get("output", 0)) / 1_000_000 * output_price
    # 缓存命中相对全价的节省金额（>0 才有意义）
    saved = (input_hit * (input_price - cache_hit_price)) / 1_000_000

    return {
        "total_input": total_input,
        "input_hit": input_hit,
        "input_miss": input_miss,
        "input_cost": input_cost,
        "output_cost": output_cost,
        "total": input_cost + output_cost,
        "saved": saved,
        "cache_priced": "input_cache_miss" in stats or "input_cache_hit" in stats,
    }


def _fill_default_cache_price(model: str, prices: dict | None) -> dict | None:
    """若价格配置缺失 ``input_cache_hit``，从内置 provider 默认价格补充。

    用户 RC 配置为旧格式（仅 input/output）时，DeepSeek 等提供商的缓存
    命中优惠价不会自动出现；这里按模型名在内置 PROVIDERS 默认值中查找，
    补充 ``input_cache_hit``，使 /cost 能正确按优惠价计算命中费用。
    未匹配到默认价时原样返回（compute_cost 会回退按全价，保守不低估）。
    """
    if prices is not None and "input_cache_hit" in prices:
        return prices
    try:
        from ....config.defaults import PROVIDERS
        for provider_conf in PROVIDERS.values():
            tp = provider_conf.get("token_prices", {})
            if model in tp and isinstance(tp[model], dict) and "input_cache_hit" in tp[model]:
                merged = dict(prices or {})
                merged.setdefault("input_cache_hit", tp[model]["input_cache_hit"])
                return merged
    except Exception:
        pass
    return prices


def show_cost(ctx):
    """显示 token 用量和费用。

    优先通过 ctx.config_port（ConfigPort）获取价格配置，
    若不可用则回退到直接 import TOKEN_PRICES（向后兼容）。
    正确体现缓存命中：统计含缓存拆分时，输入按「命中/未命中」分别展示，
    费用按命中优惠价 + 未命中全价计算；统计为旧结构（无缓存拆分）时
    保持原输出格式、全部输入按全价计费。
    """
    from ....config import TOKEN_PRICES  # 配置常量 — 函数体内延迟导入（回退）
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
    prices = _fill_default_cache_price(model, prices)
    current = get_token_stats()
    detail = compute_cost(current, prices)
    elapsed = _time.time() - get_session_start_time()
    duration = _format_cost_duration(elapsed)
    _get_out().write(f"\n{DIM}  \u2500 费用统计{RESET}", level="raw", source="cmd")
    _get_out().write(f"  {DIM}\u2502{RESET} 模型  {model}", level="raw", source="cmd")
    _get_out().write(f"  {DIM}\u2502{RESET} 调用  {current['calls']}", level="raw", source="cmd")
    # 输入 = 未命中（真实计费输入）；缓存命中单独一行，不计入输入
    _get_out().write(f"  {DIM}\u2502{RESET} 输入  {_fmt_token(detail['input_miss'])}t", level="raw", source="cmd")
    if detail["cache_priced"]:
        _get_out().write(f"  {DIM}\u2502{RESET} 缓存  {_fmt_token(detail['input_hit'])}t (命中)", level="raw", source="cmd")
    _get_out().write(f"  {DIM}\u2502{RESET} 输出  {_fmt_token(current['output'])}t", level="raw", source="cmd")
    _get_out().write(f"  {DIM}\u2502{RESET} 费用  ${detail['total']:.4f}", level="raw", source="cmd")
    if detail["saved"] > 0:
        _get_out().write(f"  {DIM}\u2502{RESET} 缓存  -${detail['saved']:.4f} (命中优惠)", level="raw", source="cmd")
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


def get_command_help(name: str) -> str:
    """返回命令的帮助描述（注册表 ``info.help``；无则空串）。

    Claude TUI parity 步骤 3.7：补全弹窗命令描述数据源。name 可带或不带
    前导 ``/``（注册表 key 带 ``/``，如 ``/help``）。
    """
    info = _commands.get(name)
    if info is None:
        info = _commands.get("/" + name.lstrip("/"))
    if info is None:
        return ""
    return str(info.get("help", ""))


def get_dynamic_help_text() -> str:
    """从命令注册表实时构建帮助文本"""
    from ...constants import DIM, RESET, TEAL
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


# /help 命令通过 HelpCommand 插件注册（在 _data_cmd.py 中），此处不再重复注册。
