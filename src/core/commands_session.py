"""会话命令 — 对话管理 + 沙盒查看相关命令处理函数"""

from __future__ import annotations

from ..core.ports.output import get_default_output_port
from ..core.constants import GREEN, YELLOW, DIM, RESET, CYAN
from ..ui.diff_renderer import render_diff_to_ansi
from .context_selector import total_chars
from .sandbox_manager import get_sandbox_manager
from ._command_core import register_command, _pop_assistant_tool_messages

_out = get_default_output_port()


def _cmd_clear(ctx):
    """清空对话，保留所有 system 消息（含用户通过 /system 追加的内容）。"""
    # 保留所有 system 消息（含用户追加），与 session.clear_messages() 行为一致
    system_msgs = [m for m in ctx.messages if m.get("role") == "system"]
    ctx.messages[:] = system_msgs
    if not ctx.messages:
        # 没有任何 system 消息时，用 build_system_prompt 兜底重建
        for part in ctx.build_system_prompt():
            ctx.messages.append({"role": "system", "content": part})
    sm = get_sandbox_manager()
    if sm:
        sm.clear()
    _out.write(f"{GREEN}  + 对话已清空（系统提词已保留）{RESET}", level="raw", source="cmd")
    return True


def _cmd_compress(ctx):
    from ..config import MAX_CONTEXT_CHARS, MODEL as _default_model

    tc = total_chars(ctx.messages)
    _out.write(f"{DIM}  当前消息数: {len(ctx.messages)}，总字符数: {tc}{RESET}", level="raw", source="cmd")
    _out.write(f"{DIM}  最大上下文限制: {MAX_CONTEXT_CHARS // 1000}k 字符{RESET}", level="raw", source="cmd")

    if ctx.session is not None:
        # 委托给 ChatSession.compress()（复用 ContextManager 实例和阈值检查）
        ctx.session.compress(force=True)
    else:
        non_system_count = sum(1 for m in ctx.messages
                               if m.get("role") != "system"
                               or (m.get("content") or "").startswith("[对话摘要]"))
        if non_system_count <= 2:
            _out.write(f"{YELLOW}  ! 非系统消息太少（≤2），无需压缩{RESET}", level="raw", source="cmd")
            return True

        cm = ctx.context_manager
        if cm is None:
            from ..core.context_manager import ContextManager
            cm = ContextManager(ctx.messages, ctx.state.get("model", _default_model))
        cm.check_and_compress(force=True)

    new_total = total_chars(ctx.messages)
    compressed_count = tc - new_total
    if compressed_count > 0:
        _out.write(f"{GREEN}  + 压缩完成 消息数: {len(ctx.messages)}，字符数: {new_total}（减少 {compressed_count}）{RESET}", level="raw", source="cmd")
    else:
        _out.write(f"{GREEN}  + 压缩完成 消息数: {len(ctx.messages)}，字符数: {new_total}{RESET}", level="raw", source="cmd")
    return True


def _cmd_pin(ctx):
    messages = ctx.messages
    arg = ctx.arg

    if len(messages) <= 1:
        _out.write(f"{YELLOW}  ! 没有可标记的消息{RESET}", level="raw", source="cmd")
        return True
    if arg and arg.isdigit():
        idx = int(arg)
        if 1 <= idx < len(messages):
            msg = messages[idx]
            msg["pinned"] = not msg.get("pinned", False)
            role = msg.get("role", "")
            content_preview = (msg.get("content") or "")[:60]
            if msg["pinned"]:
                _out.write(f"{GREEN}  * 已标记第 {idx} 条消息 ({role}): {content_preview}...{RESET}", level="raw", source="cmd")
            else:
                _out.write(f"{GREEN}  + 已取消标记第 {idx} 条消息{RESET}", level="raw", source="cmd")
        else:
            _out.write(f"{YELLOW}  ! 无效序号，范围 1-{len(messages)-1}{RESET}", level="raw", source="cmd")
    else:
        # 找到最后一条 user 消息
        last_user_idx = None
        for i in range(len(messages) - 1, -1, -1):
            if messages[i]["role"] == "user":
                last_user_idx = i
                break

        if last_user_idx is None:
            _out.write(f"{YELLOW}  ! 没有找到可标记的用户消息{RESET}", level="raw", source="cmd")
            return True

        pinned_count = 0
        for i in range(last_user_idx, len(messages)):
            messages[i]["pinned"] = True
            pinned_count += 1

        _out.write(f"{GREEN}  * 已标记最近一轮对话 {pinned_count} 条消息（压缩时将保留）{RESET}", level="raw", source="cmd")
    return True


def _cmd_undo(ctx):
    removed = _pop_assistant_tool_messages(ctx.messages)
    if len(ctx.messages) > 1 and ctx.messages[-1]["role"] == "user":
        ctx.messages.pop()
        removed += 1
    # 撤销后如果最后一条是 user 消息，标记需要重新生成
    if ctx.messages and ctx.messages[-1].get("role") == "user":
        ctx.state["retry"] = True
    _out.write(f"{GREEN}  + 已撤销 {removed} 条消息{RESET}", level="raw", source="cmd")
    return True


def _cmd_retry(ctx):
    removed = _pop_assistant_tool_messages(ctx.messages)
    if removed > 0 and len(ctx.messages) > 1 and ctx.messages[-1].get("role") == "user":
        _out.write(f"{GREEN}  + 重新生成中...{RESET}", level="raw", source="cmd")
        ctx.state["retry"] = True
    elif removed > 0:
        _out.write(f"{YELLOW}  ! 删除了回答但未找到对应的用户消息，请手动输入{RESET}", level="raw", source="cmd")
    else:
        _out.write(f"{YELLOW}  ! 没有可重试的回答{RESET}", level="raw", source="cmd")
    return True


def _cmd_edit(ctx):
    last_user_idx = None
    for i in range(len(ctx.messages) - 1, -1, -1):
        if ctx.messages[i]["role"] == "user":
            last_user_idx = i
            break
    if last_user_idx is None:
        _out.write(f"{YELLOW}  ! 没有可编辑的输入{RESET}", level="raw", source="cmd")
        return True

    old_content = ctx.messages[last_user_idx]["content"]
    _out.write(f"  {DIM}原内容: {old_content[:100]}{'...' if len(old_content) > 100 else ''}{RESET}", level="raw", source="cmd")
    new_content = ctx.get_user_input()
    if not new_content:
        _out.write(f"{YELLOW}  ! 已取消{RESET}", level="raw", source="cmd")
        return True

    original_len = len(ctx.messages)
    ctx.messages[last_user_idx:] = []
    # ★ 同步沙盒索引：已删除的消息对应的文件修改记录失效
    sm = get_sandbox_manager()
    if sm:
        sm.remap_indices(list(range(last_user_idx, original_len)))
    ctx.messages.append({"role": "user", "content": new_content})
    _out.write(f"{GREEN}  + 已更新输入，重新生成中...{RESET}", level="raw", source="cmd")
    ctx.state["retry"] = True
    return True


def _cmd_loop(ctx):
    """循环命令：在 consumer 中被拦截执行，此处仅做校验和展示（使 /help 可见）。"""
    parts = ctx.arg.split(maxsplit=1)
    if not parts or not parts[0].isdigit() or int(parts[0]) < 1:
        _out.write(f"{YELLOW}  ! 用法: /loop <次数> <提词>{RESET}", level="raw", source="cmd")
        _out.write(f"{YELLOW}    ⚠  次数必须是正整数{RESET}", level="raw", source="cmd")
        return True
    count = int(parts[0])
    if len(parts) < 2 or not parts[1].strip():
        _out.write(f"{YELLOW}  ! 用法: /loop <次数> <提词>{RESET}", level="raw", source="cmd")
        _out.write(f"{YELLOW}    ⚠  提词不能为空{RESET}", level="raw", source="cmd")
        return True
    prompt = parts[1].strip()
    _out.write(f"{GREEN}  + 准备循环 {count} 次: \"{prompt[:60]}{'...' if len(prompt) > 60 else ''}\"{RESET}", level="raw", source="cmd")
    _out.write(f"{DIM}  ⚠  循环由异步 consumer 实际执行，请稍候…{RESET}", level="raw", source="cmd")
    return True


def _cmd_editmsg(ctx):
    """编辑当前会话消息（由 app.py 异步执行实际的编辑操作）

    设置 edit_msg 联络信号，让 app.py 执行异步编辑流程。
    """
    from ..ui.tui.message_editor import MessageEditor
    ctx.edit_msg = {
        "handler": MessageEditor().edit_current_messages,
        "model": ctx.state.get("model", ""),
        "retry": ctx.state.get("retry", False),
        "prefill": ctx.state.get("prefill", ""),
    }
    return True


# ── 注册会话命令 ──────────────────────────────────────
register_command("/clear", _cmd_clear, "清空对话")
register_command("/loop", _cmd_loop, "循环执行 N 次指定提词（每轮前自动清空对话）")
register_command("/compress", _cmd_compress, "手动压缩上下文")
register_command("/pin", _cmd_pin, "标记重要消息")
register_command("/undo", _cmd_undo, "撤销上一轮对话")
register_command("/retry", _cmd_retry, "重新生成上一条回答")
register_command("/r", _cmd_retry, "重新生成上一条回答（/retry 的快捷方式）")
register_command("/edit", _cmd_edit, "编辑并重新发送上一条输入")
register_command("/editmsg", _cmd_editmsg, "编辑当前会话消息 (Ctrl+O)")


# ── /changes 沙盒命令 ───────────────────────────────────

def _cmd_changes(ctx):
    """显示文件沙盒中被改变文件的 diff"""
    sandbox = get_sandbox_manager()
    if not sandbox:
        _out.write(f"{YELLOW}  ! 文件沙盒未初始化{RESET}", level="raw", source="cmd")
        return True

    all_records = sandbox.get_all_file_changes()
    if not all_records:
        _out.write(f"{DIM}  - 文件沙盒中没有修改记录{RESET}", level="raw", source="cmd")
        return True

    file_groups: dict[str, list] = {}
    for record in all_records:
        file_groups.setdefault(record.file_path, []).append(record)

    arg = ctx.arg.strip()
    target_files: set[str] | None = None
    if arg:
        target_files = {fp for fp in file_groups if arg in fp}
        if not target_files:
            _out.write(f"{YELLOW}  ! 未找到包含 '{arg}' 的文件{RESET}", level="raw", source="cmd")
            return True

    if target_files:
        filtered_groups = {fp: recs for fp, recs in file_groups.items() if fp in target_files}
        total_changes = sum(len(recs) for recs in filtered_groups.values())
        total_files = len(filtered_groups)
    else:
        filtered_groups = file_groups
        total_changes = len(all_records)
        total_files = len(file_groups)

    _out.write(f"\n{DIM}  ─ 文件沙盒变更 (共 {total_files} 个文件, {total_changes} 次修改){RESET}",
               level="raw", source="cmd")

    for file_path, records in sorted(filtered_groups.items()):
        first, last = records[0], records[-1]
        before, after = first.content_before, last.content_after

        if before is None and after is not None:
            change_label = "新建"
        elif before is not None and after is None:
            change_label = "删除"
        elif before == after:
            change_label = "无变化"
        else:
            change_label = "修改"

        _out.write(
            f"\n  {CYAN}{file_path}{RESET}  {DIM}({change_label}, "
            f"{len(records)} 次修改, 消息索引: {records[0].message_index}-{records[-1].message_index}){RESET}",
            level="raw", source="cmd",
        )

        if before == after:
            continue

        diff_text = render_diff_to_ansi(file_path, before or "", after or "")
        if not diff_text:
            _out.write(f"  {DIM}  (无差异内容){RESET}", level="raw", source="cmd")
        else:
            for line in diff_text.split('\n'):
                _out.write(f"  {line}", level="raw", source="cmd")

    _out.write("", level="raw", source="cmd")
    return True


register_command("/changes", _cmd_changes, "显示文件沙盒中被改变文件的 diff（可加文件名过滤）")
