"""微信命令解析与执行 — 斜杠指令 + AI 对话兜底。

远程命令格式（微信消息）：
- /help               显示帮助
- /shell <命令>       远程执行 shell 命令并回显结果
- /clear              清空当前会话上下文
- /new                开始新会话
- /time               显示连接剩余时间
- /status             显示模型与会话状态
- /model <模型名>     切换模型
- 其他文本            走 AI 对话（DeepSeek 会话引擎，可自动调用工具）
"""

from __future__ import annotations

import asyncio
import logging
from typing import Tuple

_logger = logging.getLogger(__name__)

HELP_TEXT = (
    "🤖 DeepSeek ClawBot 远程控制\n"
    "────────────────────────\n"
    "发送普通消息 → AI 对话（自动调用工具）\n"
    "\n"
    "可用指令：\n"
    "/help          显示本帮助\n"
    "/shell <命令>  远程执行 shell 命令\n"
    "/clear         清空当前会话上下文\n"
    "/new           开始新会话\n"
    "/time          显示连接剩余时间\n"
    "/status        显示模型与会话状态\n"
    "/model <名称>  切换模型\n"
    "\n"
    "💡 示例：/shell ls -la\n"
    "💡 示例：帮我看看当前目录下有哪些文件"
)

SHELL_USAGE = "用法：/shell <命令>\n例：/shell ls -la"

# 默认 shell 超时（秒）
SHELL_TIMEOUT = 120.0
# 回显输出上限（字符）
SHELL_MAX_OUTPUT = 6000


def parse_command(text: str) -> Tuple[str, str]:
    """解析消息为 (指令名, 参数)。

    非斜杠消息返回 ("", 原文)。指令名小写化，参数为剩余部分。
    """
    stripped = (text or "").strip()
    if stripped.startswith("/"):
        parts = stripped.split(maxsplit=1)
        name = parts[0][1:].lower()
        arg = parts[1] if len(parts) > 1 else ""
        return name, arg
    return "", stripped


async def run_shell_command(command: str, timeout: float = SHELL_TIMEOUT,
                            max_output: int = SHELL_MAX_OUTPUT) -> str:
    """异步执行 shell 命令并捕获输出（stdout+stderr 合并）。

    Args:
        command: shell 命令文本
        timeout: 超时秒数，超时后终止进程
        max_output: 回显输出上限（字符），超出截断

    Returns:
        命令输出文本（含退出码信息或错误描述）
    """
    if not (command or "").strip():
        return "(空命令)"
    proc = await asyncio.create_subprocess_shell(
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            _logger.warning("shell 命令超时后进程未能及时退出: %s", command)
        return f"(命令执行超时 {timeout:.0f}s，已终止)"
    except Exception as e:
        proc.kill()
        return f"(命令执行失败: {e})"

    text = (out or b"").decode(errors="replace")
    if not text.strip():
        return f"(命令执行完成，无输出，退出码 {proc.returncode})"
    if len(text) > max_output:
        text = text[:max_output] + f"\n...(输出过长，已截断至 {max_output} 字符)"
    return text
