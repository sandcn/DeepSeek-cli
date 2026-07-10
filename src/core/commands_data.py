"""数据命令 — 文件读写/会话管理相关命令处理函数"""

import os
from .constants import GREEN, YELLOW, RED, DIM, RESET, CYAN, filter_system, filter_non_system
from ..core.ports.output import get_default_output_port
from ..config import MODEL
from ..core.ports.model import DefaultAsyncModelAdapter
from ..prompt_builder.project_summary import generate_summary_prompt
from ..chat_msgs import save_session as _save_direct, load_session as _load_direct, list_sessions as _list_direct
from ..core.sandbox_manager import get_sandbox_manager
from ._command_core import register_command

_out = get_default_output_port()
_model_port = DefaultAsyncModelAdapter()

_SUMMARY_MAX_CHARS = 50000    # 摘要文件最大字符数，超出截断
_SESSION_ID_TRUNCATE = 12     # 会话ID显示截断长度


def _cmd_init(ctx):
    filepath = "init.md"
    if os.path.exists(filepath):
        _out.write(f"{YELLOW}  ! init.md 已存在{RESET}", level="raw", source="cmd")
        response = ctx.get_user_input("是否覆盖？(y/N): ").strip().lower()
        if response != 'y':
            _out.write(f"{GREEN}  + 已取消{RESET}", level="raw", source="cmd")
            return True
    try:
        model = ctx.state.get("model", MODEL)
        prompt = generate_summary_prompt()
        if not prompt:
            _out.write(f"{RED}  x 未读取到项目文件，无法生成摘要{RESET}", level="raw", source="cmd")
            return True
        summary = _model_port.call_sync_blocking(
            [{"role": "user", "content": prompt}],
            model=model,
        )
        # 校验模型输出安全性和有效性
        if not summary.content:
            _out.write(f"{RED}  x 生成项目摘要失败（模型无返回）{RESET}", level="raw", source="cmd")
            return True
        summary_text = summary.content
        # 限制输出大小，防止写出超大文件
        if len(summary_text) > _SUMMARY_MAX_CHARS:
            _out.write(f"{YELLOW}  ! 摘要过长({len(summary_text)}字符)，已截断至{_SUMMARY_MAX_CHARS}字符{RESET}", level="raw", source="cmd")
            summary_text = summary_text[:_SUMMARY_MAX_CHARS]
        from ..tools.utils import atomic_write_file
        atomic_write_file(filepath, summary_text)
        _out.write(f"{GREEN}  + 已生成 {filepath}{RESET}", level="raw", source="cmd")
        _out.write(f"  {DIM}项目摘要已由模型生成，包含项目名称、描述、技术栈、结构等信息。{RESET}", level="raw", source="cmd")
    except Exception as e:
        _out.write(f"{RED}  x 生成文件失败: {e}{RESET}", level="raw", source="cmd")
    return True


def _cmd_load(ctx):
    """加载保存的对话（自动保存当前会话并清空沙盒）"""
    arg = ctx.arg.strip()
    if not arg:
        _out.write(f"{YELLOW}  ! 用法: /load <会话ID>{RESET}", level="raw", source="cmd")
        _out.write(f"  {DIM}  输入 /sessions 查看所有保存的对话{RESET}", level="raw", source="cmd")
        return True

    # ── 第1步：自动保存当前会话（如有非 system 消息） ──────────
    non_system_current = filter_non_system(ctx.messages)
    _save_fn = ctx.persistence_port.save_session if ctx.persistence_port else _save_direct
    if non_system_current:
        current_model = ctx.state.get("model", MODEL)
        try:
            saved_id = _save_fn(non_system_current, model=current_model)
            _out.write(f"{DIM}  + 已自动保存当前会话: {saved_id[:_SESSION_ID_TRUNCATE]}{RESET}", level="raw", source="cmd")
        except Exception as e:
            _out.write(f"{YELLOW}  ! 自动保存当前会话失败: {e}{RESET}", level="raw", source="cmd")

    # ── 第2步：清空文件沙盒 ──────────────────────────────────
    sandbox = get_sandbox_manager()
    if sandbox:
        sandbox.clear()
        _out.write(f"{DIM}  + 文件沙盒已清空{RESET}", level="raw", source="cmd")

    # ── 第3步：加载目标会话 ──────────────────────────────────
    _load_fn = ctx.persistence_port.load_session if ctx.persistence_port else _load_direct
    data = _load_fn(arg)
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

    model = data.get("model", ctx.state.get("model", MODEL))
    ctx.state["model"] = model

    title = data.get("title", "")
    title_info = f"「{title}」 " if title else ""
    _out.write(f"{GREEN}  + 已加载会话 {title_info}{arg} ({len(loaded_msgs)} 条消息, 模型: {model}){RESET}", level="raw", source="cmd")

    # 显示恢复的消息摘要（用项目流式渲染器回放）
    from ..ui.tui._message_display import _display_messages
    non_system = filter_non_system(ctx.messages)
    _display_messages(non_system, speed=1000)

    # 检查最后一条消息角色
    if ctx.messages and ctx.messages[-1].get("role") in ("assistant", "tool"):
        _out.write(f"  {DIM}  继续输入开始新的对话{RESET}", level="raw", source="cmd")
    elif ctx.messages and ctx.messages[-1].get("role") == "user":
        _out.write(f"  {DIM}  最后一条是用户消息，将自动继续生成回复…{RESET}", level="raw", source="cmd")
        ctx.state["retry"] = True
    return True


def _cmd_sessions(ctx):
    """列出所有保存的对话"""
    _list_fn = ctx.persistence_port.list_sessions if ctx.persistence_port else _list_direct
    sessions = _list_fn()
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


# ── 注册数据命令 ──────────────────────────────────────
register_command("/init", _cmd_init, "生成项目摘要文件")
register_command("/load", _cmd_load, "加载保存的对话")
register_command("/sessions", _cmd_sessions, "列出所有保存的对话")
