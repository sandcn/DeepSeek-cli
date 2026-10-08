"""mcp 命令 — ``/mcp`` MCP 服务器管理视图（全屏视图 / 文本回退）。

有活跃 ChatUI 时打开全屏「MCP 服务器管理」（``model.fullscreen == "mcp"``）；
无 ChatUI 回退文本列出 MCP 服务器配置与连接状态。

数据源：``src.mcp.config.load_mcp_servers``（配置）与 ``McpManager.status``
（连接状态 / 工具清单 / 错误）。重连经视图 ``applied_seq`` 回传，在命令线程
用 ``asyncio.run`` 执行 close + initialize（不阻塞 TUI 事件循环）。
"""

from __future__ import annotations

import asyncio
import logging

from ..adapters.output import get_default_output_port
from ..constants import CYAN, DIM, GREEN, RESET, YELLOW
from ..internal.commands._command_core import CommandContext

_logger = logging.getLogger(__name__)
_out = get_default_output_port()


def _build_mcp_entries() -> list:
    """MCP 服务器配置 + 连接状态 → 视图条目列表。"""
    try:
        from ...mcp.config import load_mcp_servers
    except Exception:
        return []
    try:
        cfgs = list(load_mcp_servers() or [])
    except Exception:
        cfgs = []
    status_map: dict = {}
    try:
        from ...mcp.manager import McpManager

        manager = McpManager.default()
        for row in manager.status() or []:
            if isinstance(row, dict):
                status_map[str(row.get("name", ""))] = row
    except Exception:
        _logger.debug("读取 MCP 状态失败", exc_info=True)
    entries: list = []
    for cfg in cfgs:
        name = str(getattr(cfg, "name", ""))
        st = status_map.get(name, {})
        entries.append({
            "name": name,
            "transport": str(getattr(cfg, "transport", "") or ""),
            "enabled": bool(getattr(cfg, "enabled", True)),
            "command": str(getattr(cfg, "command", "") or getattr(cfg, "url", "") or ""),
            "description": str(getattr(cfg, "description", "") or ""),
            "agents": sorted(getattr(cfg, "allowed_agents", set()) or set()),
            "connected": bool(st.get("connected")),
            "tools": list(st.get("tools") or []),
            "error": st.get("error"),
        })
    return entries


def _reconnect_mcp() -> str:
    """重连全部 MCP 服务器（关闭后重新初始化）。"""
    try:
        from ...mcp.config import load_mcp_servers
        from ...mcp.manager import McpManager

        manager = McpManager.default()
        servers = load_mcp_servers()

        async def _run() -> None:
            await manager.close()
            await manager.initialize(servers)

        asyncio.run(_run())
        return "已重连 MCP 服务器"
    except Exception as exc:
        return f"重连失败：{exc}"


def _open_mcp_ui(ctx) -> bool:
    """打开全屏 MCP 服务器管理视图（有 ChatUI 时）。"""
    from ..adapters.ui_runtime import get_mcp_view_state_cls
    from ._view_opener import open_fullscreen_view

    refresh_seq = {"v": 0}
    applied_seq = {"v": 0}

    def setup(model, state):
        state.entries = _build_mcp_entries()

    def tick(state) -> bool:
        if state.refresh_seq > refresh_seq["v"]:
            refresh_seq["v"] = state.refresh_seq
            state.entries = _build_mcp_entries()
            state.status_message = "已刷新"
            return False
        if state.applied_seq > applied_seq["v"]:
            applied_seq["v"] = state.applied_seq
            report = state.applied or {}
            if report.get("action") == "reconnect":
                state.status_message = _reconnect_mcp()
                state.entries = _build_mcp_entries()
        return False

    return open_fullscreen_view(
        ctx, view_id="mcp", state_attr="mcp_view",
        state_cls=get_mcp_view_state_cls(), setup=setup, on_tick=tick,
        close_hint="MCP 管理已关闭", timeout_hint="MCP 管理超时关闭",
    )


def _mcp_text() -> bool:
    """文本列出 MCP 服务器（无 ChatUI / 单次模式回退）。"""
    entries = _build_mcp_entries()
    if not entries:
        _out.write(f"{DIM}  - 未配置 MCP 服务器{RESET}", level="raw", source="cmd")
        return True
    _out.write(f"\n{DIM}  \u2500 MCP 服务器{RESET}", level="raw", source="cmd")
    for e in entries:
        if e.get("error"):
            state = f"{YELLOW}! 错误{RESET}"
        elif e.get("connected"):
            state = f"{GREEN}已连接{RESET}"
        elif e.get("enabled"):
            state = f"{DIM}未连接{RESET}"
        else:
            state = f"{DIM}已禁用{RESET}"
        tools = len(e.get("tools") or [])
        _out.write(
            f"  {CYAN}{e.get('name', '')}{RESET}  [{e.get('transport', '')}]  {state}"
            f"  {DIM}{tools} 工具{RESET}",
            level="raw", source="cmd",
        )
    return True


def _cmd_mcp(ctx) -> bool:
    """打开 MCP 管理视图（有 ChatUI）或文本列出（回退）。"""
    if _open_mcp_ui(ctx):
        return True
    return _mcp_text()


from .base import CommandMeta, CommandPlugin, declare_command_plugin


class McpCommand(CommandPlugin):
    """MCP 服务器管理（/mcp）。"""

    def __init__(self):
        self.meta = CommandMeta(
            name="mcp", description="MCP 服务器管理（状态 / 工具 / 重连）", group="ui",
        )

    def execute(self, ctx: CommandContext) -> bool:
        return _cmd_mcp(ctx)


declare_command_plugin(McpCommand())
