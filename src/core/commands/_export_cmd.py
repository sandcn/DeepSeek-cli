"""导出命令 — 将当前对话导出为 Markdown（含 SubAgent 聊天信息）

命令: /export [文件路径]

无参数时默认导出到当前目录 ``chat_export_<时间戳>.md``；
指定路径时必须位于当前工作目录内（安全校验，与 chat_msgs.export_session 一致）。

导出内容：
1. 主对话记录（user / assistant / tool 消息，跳过 system 系统提示词）
2. SubAgent 任务详情（description / agent_type / 任务指令 / 执行结果 /
   完整内部对话——system/user/assistant/tool 全部往返）

SubAgent 完整对话由 SubAgent.run() 结束时经 ``_record_to_parent()``
挂到父 Agent 的 ``_subagent_records`` 属性上，本命令通过 ctx.session.agent
读取该记录。无 session（部分测试/工具路径）时仅导出主对话。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Iterable

from ...core.constants import GREEN, RESET, YELLOW
from ..adapters.output import get_default_output_port
from ..internal.commands._command_core import CommandContext

_out = get_default_output_port()

# ── 角色中文标签 ──────────────────────────────────────
_ROLE_LABELS = {
    "system": "系统提示词",
    "user": "用户",
    "assistant": "助手",
    "tool": "工具结果",
}

# 默认导出文件名时间戳
_TIMESTAMP_FMT = "%Y%m%d_%H%M%S"


# ── Markdown 辅助 ────────────────────────────────────

def _code_fence(text: str) -> str:
    """生成包裹文本的代码栅栏，动态加长避免内容中的反引号冲突。"""
    text = text or ""
    max_run = 0
    cur = 0
    for ch in text:
        if ch == "`":
            cur += 1
            if cur > max_run:
                max_run = cur
        else:
            cur = 0
    fence = "`" * max(3, max_run + 1)
    return fence


def _code_block(text: str, lang: str = "") -> str:
    """将文本包裹为 markdown 代码块（自动适配栅栏长度）。

    文本为空/空白时返回占位文案，避免导出空代码块。
    """
    text = text or ""
    if not text.strip():
        return "(空)"
    fence = _code_fence(text)
    lang_part = f"{lang}" if lang else ""
    return f"{fence}{lang_part}\n{text}\n{fence}"


def _msg_content(msg: dict) -> str:
    """提取消息正文（content 可能为 None / str / list[dict] content blocks）。"""
    content = msg.get("content")
    if content is None:
        return ""
    if isinstance(content, list):
        # 多模态 content blocks（text + image_url）——提取文本部分
        try:
            from ...api.multimodal import content_to_text
            return content_to_text(content)
        except Exception:
            # 防御回退：不输出原始 list 字面量（避免图片 data URI/base64 泄出）
            return ""
    return str(content)


def _tool_calls_to_md(msg: dict) -> list[str]:
    """将 assistant 消息的 tool_calls 渲染为 markdown 片段列表（每块自带尾空行）。"""
    blocks: list[str] = []
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function") or {}
        name = fn.get("name", "?")
        arguments = fn.get("arguments", "")
        blocks.append(f"#### 🔧 工具调用：{name}\n\n"
                      f"{_code_block(str(arguments), 'json')}\n")
    return blocks


def _render_main_message(msg: dict) -> list[str]:
    """渲染主对话单条消息，返回 markdown 行列表（每块自带尾空行）。"""
    role = msg.get("role", "?")
    label = _ROLE_LABELS.get(role, role)
    lines: list[str] = []

    if role == "assistant":
        # 推理内容（如有）
        reasoning = msg.get("reasoning_content")
        if reasoning:
            lines.append(f"### 🧠 {label}（思考）\n\n{_code_block(str(reasoning))}\n")
        content = _msg_content(msg)
        if content:
            lines.append(f"### 🧠 {label}\n\n{_code_block(content)}\n")
        lines.extend(_tool_calls_to_md(msg))
        return lines

    if role == "tool":
        content = _msg_content(msg)
        title = f"### 🔧 {label}"
        tc_id = msg.get("tool_call_id")
        if tc_id:
            title += f"（id: {tc_id}）"
        lines.append(f"{title}\n\n{_code_block(content)}\n")
        return lines

    # user / system / 其他
    content = _msg_content(msg)
    icon = {"user": "🤖", "system": "⚙️", "tool": "🔧"}.get(role, "📄")
    lines.append(f"### {icon} {label}\n\n{_code_block(content)}\n")
    return lines


def _render_subagent_message(msg: dict) -> list[str]:
    """渲染 SubAgent 内部对话单条消息（结构更丰富，每块自带尾空行）。"""
    role = msg.get("role", "?")
    label = _ROLE_LABELS.get(role, role)
    lines: list[str] = []

    if role == "assistant":
        reasoning = msg.get("reasoning_content")
        if reasoning:
            lines.append(f"**[assistant] 思考**\n\n{_code_block(str(reasoning))}\n")
        content = _msg_content(msg)
        if content:
            lines.append(f"**[assistant] 回复**\n\n{_code_block(content)}\n")
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            name = fn.get("name", "?")
            arguments = fn.get("arguments", "")
            lines.append(f"**[assistant] 工具调用：{name}**\n\n"
                         f"{_code_block(str(arguments), 'json')}\n")
        return lines

    if role == "tool":
        content = _msg_content(msg)
        lines.append(f"**[tool] 工具结果**\n\n{_code_block(content)}\n")
        return lines

    # system / user / 其他
    content = _msg_content(msg)
    lines.append(f"**[{role}] {label}**\n\n{_code_block(content)}\n")
    return lines


def _render_subagent(record: dict) -> list[str]:
    """渲染单个 SubAgent 任务的完整聊天记录。"""
    description = record.get("description") or record.get("label") or "?"
    agent_type = record.get("agent_type", "execute")
    status = record.get("status", "done")
    lines: list[str] = []

    lines.append(f"### 🤖 SubAgent：{description}（`{agent_type}`）")
    lines.append("")
    lines.append("| 项目 | 值 |")
    lines.append("|---|---|")
    lines.append(f"| 状态 | {status} |")
    lines.append(f"| 工具调用次数 | {record.get('tool_calls_count', 0)} |")
    lines.append(f"| 错误 | {record.get('error') or '（无）'} |")
    lines.append("")

    prompt = record.get("prompt", "")
    if prompt:
        lines.append("**任务指令：**")
        lines.append("")
        lines.append(_code_block(str(prompt)))
        lines.append("")

    result = record.get("result", "")
    if result:
        lines.append("**执行结果：**")
        lines.append("")
        lines.append(_code_block(str(result)))
        lines.append("")

    messages = record.get("messages") or []
    if messages:
        lines.append("**内部对话记录：**")
        lines.append("")
        for m in messages:
            lines.extend(_render_subagent_message(m))
    return lines


def build_markdown(messages: list[dict], subagent_records: Iterable[dict],
                   model: str, exported_at: str | None = None) -> str:
    """构建完整导出 markdown 文本。

    Args:
        messages: 主对话消息列表（自动跳过 system 角色）
        subagent_records: SubAgent 记录列表
        model: 模型名
        exported_at: 导出时间 ISO 字符串，None 时取当前时间

    Returns:
        markdown 文本
    """
    if exported_at is None:
        exported_at = datetime.now().isoformat(timespec="seconds")

    main_msgs = [m for m in messages if m.get("role") != "system"]
    subagent_records = list(subagent_records)

    lines: list[str] = []
    lines.append("# 对话导出")
    lines.append("")
    lines.append("| 项目 | 值 |")
    lines.append("|---|---|")
    lines.append(f"| 导出时间 | {exported_at} |")
    lines.append(f"| 模型 | {model or '?'} |")
    lines.append(f"| 主对话消息数 | {len(main_msgs)} |")
    lines.append(f"| SubAgent 任务数 | {len(subagent_records)} |")
    lines.append("")
    lines.append("---")
    lines.append("")

    lines.append("## 对话记录")
    lines.append("")
    if not main_msgs:
        lines.append("（无对话消息）")
        lines.append("")
    else:
        for i, msg in enumerate(main_msgs, 1):
            lines.append(f"### 消息 {i}")
            lines.append("")
            lines.extend(_render_main_message(msg))

    if subagent_records:
        lines.append("---")
        lines.append("")
        lines.append("## 🤖 SubAgent 任务详情")
        lines.append("")
        for record in subagent_records:
            lines.extend(_render_subagent(record))
    return "\n".join(lines)


# ── 输出路径解析 ────────────────────────────────────

def _resolve_output_path(arg: str) -> Path | None:
    """解析导出文件路径，返回 None 表示参数非法（已输出错误提示）。"""
    cwd = Path.cwd()

    if arg.strip():
        p = Path(arg.strip())
        # 非绝对路径时基于 cwd 解析
        resolved = p if p.is_absolute() else (cwd / p)
        resolved = resolved.resolve()
        try:
            resolved.relative_to(cwd)
        except ValueError:
            _out.write(f"{YELLOW}  ! 错误: 导出路径必须在当前目录下: {resolved}{RESET}",
                       level="raw", source="cmd")
            return None
        if resolved.exists():
            _out.write(
                f"{YELLOW}  ! 错误: 文件已存在: {resolved}（避免覆盖，请换路径）{RESET}",
                level="raw", source="cmd",
            )
            return None
        return resolved

    # 默认文件名（秒级时间戳），冲突时追加序号避免覆盖
    ts = datetime.now().strftime(_TIMESTAMP_FMT)
    candidate = cwd / f"chat_export_{ts}.md"
    n = 1
    while candidate.exists():
        candidate = cwd / f"chat_export_{ts}_{n}.md"
        n += 1
    return candidate


def _collect_subagent_records(ctx: CommandContext) -> list[dict]:
    """从会话 Agent 收集 SubAgent 记录（无 session/agent 时返回空列表）。"""
    session = getattr(ctx, "session", None)
    if session is None:
        return []
    agent = getattr(session, "agent", None)
    if agent is None:
        return []
    return list(getattr(agent, "_subagent_records", None) or [])


def _cmd_export(ctx: CommandContext) -> bool:
    """将当前对话导出为 markdown（含 SubAgent 聊天信息）。"""
    out_path = _resolve_output_path(ctx.arg)
    if out_path is None:
        return True

    model = ctx.state.get("model", "") if getattr(ctx, "state", None) else ""
    records = _collect_subagent_records(ctx)

    md = build_markdown(ctx.messages, records, model)

    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(md, encoding="utf-8")
    except OSError as exc:
        _out.write(f"{YELLOW}  ! 导出失败: {exc}{RESET}", level="raw", source="cmd")
        return True

    main_count = len([m for m in ctx.messages if m.get("role") != "system"])
    _out.write(f"{GREEN}  + 已导出 {main_count} 条主对话消息、"
               f"{len(records)} 个 SubAgent 任务到: {out_path}{RESET}",
               level="raw", source="cmd")
    return True


# ── CommandPlugin 子类 ──────────────────────────────
# 命令通过 get_plugin_registry().register() 注册，不再使用 register_command()。
# CommandPluginRegistry.register() 内部自动调用 register_command() 确保向后兼容。

from .base import CommandPlugin, CommandMeta, get_plugin_registry


class ExportCommand(CommandPlugin):
    """导出当前对话为 Markdown（含 SubAgent 聊天信息）"""
    def __init__(self):
        self.meta = CommandMeta(
            name="export",
            description="导出当前对话为 markdown（含 SubAgent 聊天信息）: /export [文件路径]",
        )

    def execute(self, ctx: CommandContext) -> bool:
        return _cmd_export(ctx)


get_plugin_registry().register(ExportCommand())
