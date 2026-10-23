"""工具消息回放显示文本转换。

会话存档中的 ``role="tool"`` 消息 ``content`` 是工具返回给大模型的原始结果。
``bash`` / ``bash_opt`` 等工具返回结构化 JSON（``stdout`` / ``stderr`` /
``returncode`` / ``output`` 等），实时执行时工具卡通过输出事件显示命令输出；
``/load``、``--load``、``/editmsg`` 等历史回放若直接使用原始 content，会把
整段 JSON 灌进工具卡。

本模块把已知工具的结构化结果转换为与实时工具卡一致的人读文本：

- ``bash``：``stdout`` 在前、``stderr`` 在后；两者都为空时返回空串
  （工具卡只保留标题行）；后台任务启动 JSON 显示为启动提示。
- ``bash_opt``：有 ``stdout`` / ``stderr`` 时同 bash；有 ``output`` 时显示
  ``output``（``op=read`` / ``op=wait``）；其余结构化结果转为缩进键值文本。
- ``subagent``：启动 JSON 显示为后台任务启动提示。
- ``subagent_opt``：有 ``output`` 时显示 ``output``；其余结构化结果转为
  缩进键值文本。
- ``user_select``：与实时 ``_emit_user_select_output`` 一致，显示选择结果；
  取消 / 超时 / 非交互 / 空选项不显示正文。

兼容策略：未知工具、非 JSON 内容、无法解析的 JSON 一律原样返回；结构化
工具收到旧版纯文本结果时同样原样返回。本模块只负责显示文本，不修改消息
本体，也不改变工具完成状态。
"""

from __future__ import annotations

import json
from typing import Any

#: 会话存档中 content 为结构化 JSON 的内置工具。
_STRUCTURED_JSON_TOOLS = frozenset({
    "bash",
    "bash_opt",
    "subagent",
    "subagent_opt",
    "user_select",
})

#: 结构化 JSON 中表示命令输出的字段。
_STDIO_KEYS = ("stdout", "stderr")


def _load_json_object(content: str) -> dict | None:
    """把 content 解析为 JSON 对象；非 JSON / 非对象返回 None。"""
    text = content.lstrip()
    if not text.startswith("{"):
        return None
    try:
        data = json.loads(content)
    except (ValueError, TypeError, RecursionError):
        return None
    return data if isinstance(data, dict) else None


def _join_stdio(stdout: Any, stderr: Any) -> str:
    """按「stdout 在前、stderr 在后」拼接两个输出流。

    每个流只去掉尾部换行，内部换行与空行保留；空流跳过。
    """
    parts: list[str] = []
    for value in (stdout, stderr):
        if value is None:
            continue
        text = str(value).rstrip("\n")
        if text:
            parts.append(text)
    return "\n".join(parts)


def _has_stdio(data: dict) -> bool:
    """结构化结果是否包含 stdout / stderr 字段。"""
    return any(key in data for key in _STDIO_KEYS)


def _format_structured(value: Any, indent: int = 0) -> list[str]:
    """把 JSON 结构转为缩进键值文本（不使用 JSON 花括号 / 引号）。

    仅在已知工具没有 stdout / stderr / output 时兜底使用（如 bash_opt 的
    截图 / 窗口操作结果、subagent_opt 的 wait_all 汇总），避免工具卡再次
    出现原始 JSON。
    """
    prefix = "  " * indent
    lines: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, (dict, list)):
                lines.append(f"{prefix}{key}:")
                lines.extend(_format_structured(item, indent + 1))
                continue
            text = "" if item is None else str(item)
            if "\n" in text:
                sublines = text.splitlines()
                lines.append(f"{prefix}{key}: {sublines[0]}")
                lines.extend(f"{prefix}  {line}" for line in sublines[1:])
            else:
                lines.append(f"{prefix}{key}: {text}")
        return lines
    if isinstance(value, list):
        for item in value:
            if isinstance(item, (dict, list)):
                lines.append(f"{prefix}-")
                lines.extend(_format_structured(item, indent + 1))
            else:
                lines.append(f"{prefix}- {item}")
        return lines
    return [f"{prefix}{value}"]


def _format_stdio_payload(data: dict) -> str:
    """从结构化结果中提取 stdout / stderr 显示文本。"""
    return _join_stdio(data.get("stdout", ""), data.get("stderr", ""))


def _format_output_payload(data: dict) -> str:
    """提取 ``output`` 字段文本；非字符串值退化为结构化文本。"""
    if "output" not in data:
        return ""
    output = data.get("output")
    if output is None:
        return ""
    if isinstance(output, str):
        return output
    return "\n".join(_format_structured(output))


def _format_background_bash(data: dict, detail: str) -> str | None:
    """bash 后台启动 JSON 的显示文本；非后台启动结构返回 None。"""
    task_id = str(data.get("task_id") or "")
    if not task_id or "stdout" in data or "stderr" in data:
        return None
    status = str(data.get("status") or "")
    if status and status != "running":
        return None
    command = str(detail or "").strip()
    if command:
        if len(command) > 80:
            command = command[:80] + "..."
        return f"[后台任务 {task_id} 已启动: {command}]"
    return f"[后台任务 {task_id} 已启动]"


def _format_subagent_start(data: dict) -> str | None:
    """subagent 启动 JSON 的显示文本；非启动结构返回 None。"""
    task_id = str(data.get("task_id") or "")
    if not task_id or "output" in data:
        return None
    description = str(data.get("description") or "")
    if not description:
        return f"[后台 subagent 任务 {task_id} 已启动]"
    short = description[:60]
    suffix = "..." if len(description) > 60 else ""
    return f"[后台 subagent 任务 {task_id} 已启动: {short}{suffix}]"


def _format_user_select(data: dict) -> str:
    """user_select 结果显示文本（对齐实时 _emit_user_select_output）。

    ★ 2026-10-23（用户需求：输入完了要显示所有的输入（多行））：自定义
    答案保留原始换行逐行输出，不再折叠为单行或用逗号拼接。
    """
    action = str(data.get("action") or "")
    if action != "confirmed":
        return ""
    selected = data.get("selected")
    if not isinstance(selected, list):
        selected = [] if selected is None else [selected]
    if not selected:
        return "未选择任何项"

    def _lines(value) -> list:
        return (
            str(value).replace("\r\n", "\n").replace("\r", "\n").split("\n")
        )

    if len(selected) == 1:
        lines = _lines(selected[0])
        if len(lines) == 1:
            return f"已选择: {lines[0]}"
        return "已选择: " + lines[0] + "\n" + "\n".join(
            f"  {line}" for line in lines[1:]
        )
    out: list = [f"已选择 {len(selected)} 项:"]
    for item in selected[:3]:
        lines = _lines(item)
        out.append(f"- {lines[0]}")
        out.extend(f"  {line}" for line in lines[1:])
    if len(selected) > 3:
        out.append(f"... 还有 {len(selected) - 3} 项")
    return "\n".join(out)


def format_tool_message_content(
    tool_name: str, content: str, detail: str = "",
) -> str:
    """把工具返回结果转换为历史回放的显示文本。

    Args:
        tool_name: 工具注册名（``block.extra["tool_name"]``）。
        content: 会话中保存的 tool 消息原始 content。
        detail: 工具卡标题 detail（bash 后台启动提示使用，可为空）。

    Returns:
        工具卡正文文本；未知工具、非 JSON、旧版纯文本内容原样返回。
    """
    name = str(tool_name or "").strip()
    if name not in _STRUCTURED_JSON_TOOLS or not content:
        return content

    data = _load_json_object(content)
    if data is None:
        return content

    if name == "bash":
        if _has_stdio(data):
            return _format_stdio_payload(data)
        background = _format_background_bash(data, detail)
        if background is not None:
            return background
        return "\n".join(_format_structured(data))

    if name == "bash_opt":
        if _has_stdio(data):
            return _format_stdio_payload(data)
        if "output" in data:
            return _format_output_payload(data)
        return "\n".join(_format_structured(data))

    if name == "subagent":
        start = _format_subagent_start(data)
        if start is not None:
            return start
        return "\n".join(_format_structured(data))

    if name == "subagent_opt":
        if "output" in data:
            return _format_output_payload(data)
        return "\n".join(_format_structured(data))

    if name == "user_select":
        return _format_user_select(data)

    return content


__all__ = ["format_tool_message_content"]
