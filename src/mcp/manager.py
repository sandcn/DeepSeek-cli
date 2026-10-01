"""MCP 管理器 — 连接编排 + 工具注册 + 调用路由 + 权限策略。

进程级单例（``McpManager.default()``）：应用启动时 ``setup_mcp()`` 一次性
连接配置中的所有 MCP server、发现工具并注册进 ``ToolRegistry``；运行时
MCP 工具的 ``execute()`` 经 ``call_tool()`` 路由到对应 server；退出时
``shutdown_mcp()`` 关闭全部连接并注销注册表条目与权限策略。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, List, Optional

from .client import McpClient
from .config import (
    DEFAULT_AGENT_TYPES,
    VALID_AGENT_TYPES,
    McpServerConfig,
    load_mcp_servers,
)
from .errors import McpError
from .tool import build_mcp_tool_class, is_mcp_tool, mcp_tool_name

_logger = logging.getLogger(__name__)

#: 提示词中 server instructions 的截断长度
_INSTRUCTIONS_MAX = 500


def register_tool_policy(tool_names: List[str], allowed_agents: Optional[set] = None) -> None:
    """把 MCP 工具名写入 SubAgent 工具排除表（默认仅 execute 型可用）。

    实现方式：动态维护 ``src.core.subagent._TOOL_EXCLUSION_MAP``——
    未授权该工具的 agent 类型把工具名加入排除集合，授权的移除。
    主 Agent 不受该表约束（其工具列表为注册表全集）。
    """
    allowed = set(allowed_agents or DEFAULT_AGENT_TYPES)
    try:
        from ..core.subagent import _TOOL_EXCLUSION_MAP
    except Exception:
        _logger.debug("导入 _TOOL_EXCLUSION_MAP 失败，跳过 MCP 权限策略", exc_info=True)
        return
    for name in tool_names:
        for agent_type in VALID_AGENT_TYPES:
            excluded = _TOOL_EXCLUSION_MAP.get(agent_type)
            if not isinstance(excluded, set):
                continue
            if agent_type in allowed:
                excluded.discard(name)
            else:
                excluded.add(name)


def unregister_tool_policy(tool_names: List[str]) -> None:
    """从所有 agent 类型的排除集合中移除 MCP 工具名（关闭时清理）。"""
    try:
        from ..core.subagent import _TOOL_EXCLUSION_MAP
    except Exception:
        return
    for name in tool_names:
        for agent_type in VALID_AGENT_TYPES:
            excluded = _TOOL_EXCLUSION_MAP.get(agent_type)
            if isinstance(excluded, set):
                excluded.discard(name)


class McpManager:
    """MCP 连接与工具注册管理器（进程级单例）。"""

    _default: Optional["McpManager"] = None

    def __init__(self, registry=None):
        self._registry = registry
        self._clients: dict = {}
        self._tool_names: dict = {}
        self._errors: dict = {}
        self._server_cfgs: list = []
        self._initialized = False
        self._lock: Optional[asyncio.Lock] = None

    # ── 单例 ──────────────────────────────────────────────

    @classmethod
    def default(cls) -> "McpManager":
        if cls._default is None:
            cls._default = cls()
        return cls._default

    @classmethod
    def reset_default(cls) -> None:
        """丢弃单例（测试用；调用方需自行 await close()）。"""
        cls._default = None

    def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    @property
    def initialized(self) -> bool:
        return self._initialized

    # ── 初始化 ────────────────────────────────────────────

    async def initialize(self, servers: Optional[list] = None, registry=None,
                         force: bool = False) -> "McpManager":
        """连接全部启用的 MCP server 并注册其工具（幂等）。"""
        async with self._get_lock():
            if self._initialized and not force:
                return self
            if force:
                # 重连前回收既有连接与注册（否则旧 stdio 子进程/HTTP 客户端泄漏）
                await self._teardown_locked()
            self._initialized = True
            if registry is not None:
                self._registry = registry
            if servers is None:
                servers = load_mcp_servers()
            self._server_cfgs = list(servers)
            for cfg in self._server_cfgs:
                if not cfg.enabled:
                    continue
                await self._connect_one(cfg)
            if self._clients:
                _logger.info(
                    "MCP 初始化完成：%d 个 server 已连接（%d 个工具）",
                    len(self._clients),
                    sum(len(v) for v in self._tool_names.values()),
                )
            return self

    async def _teardown_locked(self) -> None:
        """关闭并注销全部既有连接（调用方需已持有锁）。"""
        registry = self._registry
        for names in self._tool_names.values():
            if registry is not None:
                for name in names:
                    try:
                        registry.unregister(name)
                    except Exception:
                        _logger.debug("注销 MCP 工具 '%s' 异常", name, exc_info=True)
            unregister_tool_policy(names)
        for client in list(self._clients.values()):
            try:
                await client.close()
            except Exception:
                _logger.debug("关闭 MCP client 异常", exc_info=True)
        self._clients.clear()
        self._tool_names.clear()

    async def _connect_one(self, cfg: McpServerConfig) -> None:
        client = McpClient(cfg)
        try:
            await client.connect()
            tools = await client.list_tools()
        except Exception as e:
            self._errors[cfg.name] = str(e) or type(e).__name__
            _logger.warning("MCP server '%s' 初始化失败（已跳过）: %s", cfg.name, e)
            try:
                await client.close()
            except Exception:
                _logger.debug("清理失败的 MCP server '%s' 异常", cfg.name, exc_info=True)
            return

        self._clients[cfg.name] = client
        self._errors.pop(cfg.name, None)

        registry = self._registry
        if registry is None:
            from ..tools.registry import ToolRegistry
            registry = ToolRegistry.default()
            self._registry = registry
        # 确保内置工具已完成自动发现（注册表懒初始化），MCP 工具追加在后
        existing: set = set()
        try:
            existing = set(registry.get_tools())
        except Exception:
            _logger.debug("触发注册表初始化失败", exc_info=True)

        names: list = []
        for tool_def in tools:
            cls = build_mcp_tool_class(cfg.name, tool_def, parallel_safe=cfg.parallel_safe)
            if cls.name in existing or cls.name in names:
                # 名称经字符清洗后碰撞（注册表 register 会静默覆盖）→ 加哈希消歧
                _logger.warning(
                    "MCP 工具名 '%s'（server=%s, tool=%s）与现有工具冲突，"
                    "已追加哈希后缀", cls.name, cfg.name, tool_def.name,
                )
                cls = build_mcp_tool_class(
                    cfg.name, tool_def,
                    parallel_safe=cfg.parallel_safe, disambiguate=True,
                )
            try:
                registry.register(cls)
            except Exception as e:
                _logger.warning(
                    "注册 MCP 工具 '%s'（server=%s）失败: %s", cls.name, cfg.name, e,
                )
                continue
            existing.add(cls.name)
            names.append(cls.name)
        self._tool_names[cfg.name] = names
        register_tool_policy(names, cfg.allowed_agents)
        _logger.info("MCP server '%s' 提供 %d 个工具", cfg.name, len(names))

    # ── 调用 ──────────────────────────────────────────────

    async def call_tool(self, server: str, tool: str, arguments: Optional[dict] = None) -> Any:
        """路由一次 MCP 工具调用。"""
        client = self._clients.get(server)
        if client is None:
            reason = self._errors.get(server) or "未连接"
            raise McpError(f"MCP server '{server}' 不可用: {reason}")
        return await client.call_tool(tool, arguments)

    def get_client(self, server: str) -> Optional[McpClient]:
        return self._clients.get(server)

    # ── 关闭 ──────────────────────────────────────────────

    async def close(self) -> None:
        """关闭所有连接并注销注册表条目与权限策略。"""
        async with self._get_lock():
            if not self._initialized and not self._clients and not self._server_cfgs:
                return
            await self._teardown_locked()
            self._errors.clear()
            self._server_cfgs = []
            self._initialized = False

    # ── 状态与提示词片段 ──────────────────────────────────

    def status(self) -> list:
        """返回各 server 的连接状态（供 /config、诊断与测试使用）。"""
        rows: list = []
        for cfg in self._server_cfgs:
            name = cfg.name
            client = self._clients.get(name)
            rows.append({
                "name": name,
                "transport": cfg.transport,
                "enabled": cfg.enabled,
                "connected": client is not None,
                "tools": list(self._tool_names.get(name, [])),
                "error": self._errors.get(name),
                "agents": sorted(cfg.allowed_agents),
            })
        return rows

    def connected_tool_names(self) -> list:
        names: list = []
        for tool_names in self._tool_names.values():
            names.extend(tool_names)
        return names

    def _excluded_for(self, agent_type: Optional[str]) -> set:
        """指定 agent 类型被排除的工具名集合（与 SubAgent 权限同一真源）。"""
        if not agent_type:
            return set()
        try:
            from ..core.subagent import _TOOL_EXCLUSION_MAP
        except Exception:
            return set()
        excluded = _TOOL_EXCLUSION_MAP.get(agent_type)
        return set(excluded) if isinstance(excluded, set) else set()

    def build_prompt_section(self, agent_type: Optional[str] = None) -> str:
        """系统提示词中的 MCP 章节（无已连接 server 时返回空串）。

        Args:
            agent_type: 目标 agent 类型（map/review/plan/execute）。给定时会
                按 ``_TOOL_EXCLUSION_MAP`` 过滤掉该类型不可用的 MCP 工具，
                避免提示词宣称的工具与 SubAgent 实际工具集不一致
                （模型发起必然被拒的调用）。None 表示不过滤（主 Agent 全量）。
        """
        if not self._clients:
            return ""
        excluded = self._excluded_for(agent_type)
        lines: list = []
        for cfg in self._server_cfgs:
            if cfg.name not in self._clients:
                continue
            tools = [n for n in self._tool_names.get(cfg.name, []) if n not in excluded]
            if not tools:
                continue
            names = "、".join(f"`{n}`" for n in tools)
            lines.append(f"- `{cfg.name}`（{cfg.transport}）: {names}")
            client = self._clients[cfg.name]
            instructions = (client.instructions or "").strip()
            if instructions:
                if len(instructions) > _INSTRUCTIONS_MAX:
                    instructions = instructions[:_INSTRUCTIONS_MAX] + "…"
                lines.append(f"  - 服务器说明: {instructions}")
        if not lines:
            return ""
        return "\n".join([
            "## MCP 外部工具（Model Context Protocol）",
            "以下工具来自外部 MCP 服务器，调用方式与内置工具一致：",
            *lines,
        ])


# ═══════════════════════════════════════════════════════════════
# 便捷入口
# ═══════════════════════════════════════════════════════════════

async def setup_mcp(servers: Optional[list] = None, registry=None) -> McpManager:
    """应用启动入口：初始化 MCP 并注册工具（未配置时零开销）。"""
    return await McpManager.default().initialize(servers=servers, registry=registry)


async def shutdown_mcp() -> None:
    """应用退出入口：关闭全部 MCP 连接并清理注册。"""
    await McpManager.default().close()


def get_mcp_prompt_section(agent_type: Optional[str] = None) -> str:
    """系统提示词 MCP 章节（供 prompt_builder 调用；可按 agent 类型过滤）。"""
    return McpManager.default().build_prompt_section(agent_type)


def get_mcp_status() -> list:
    """当前 MCP 状态快照。"""
    return McpManager.default().status()


__all__ = [
    "McpManager",
    "setup_mcp",
    "shutdown_mcp",
    "get_mcp_prompt_section",
    "get_mcp_status",
    "register_tool_policy",
    "unregister_tool_policy",
    "is_mcp_tool",
    "mcp_tool_name",
]
