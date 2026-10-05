"""MCP server 配置解析与校验。

配置来源：``~/.chat_config/chatrc.json`` 顶层键 ``mcp_servers``（list[dict]）。

条目字段：
    name        str   必填，服务器唯一名（用于工具名前缀与状态显示）
    transport   str   "stdio"（默认）/ "http"（Streamable HTTP）/ "sse"（旧式 HTTP+SSE）
    command     str   stdio 必填，可执行文件
    args        list  stdio 可选，命令行参数
    env         dict  stdio 可选，追加/覆盖子进程环境变量
    cwd         str   stdio 可选，子进程工作目录
    url         str   http/sse 必填，MCP 端点
    headers     dict  http/sse 可选，附加请求头（如 Authorization）
    enabled     bool  是否启用（默认 true）
    agents      list  允许使用该服务器工具的 SubAgent 类型（默认 ["execute"]）
    timeout     num   单次请求超时秒数（默认 30）
    parallel_safe bool 是否声明并行安全（默认 false；影响 DAG 调度）
    inherit_env bool  stdio：是否继承当前进程全部环境变量（默认 false，
                      仅透传基础变量白名单 + env，避免凭据外泄给第三方 server）
    description str   可选的人类可读说明（仅展示用）
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from .errors import McpConfigError

_logger = logging.getLogger(__name__)

VALID_TRANSPORTS = ("stdio", "http", "sse")
VALID_AGENT_TYPES = ("map", "review", "plan", "execute")
DEFAULT_AGENT_TYPES = ("execute",)
DEFAULT_TIMEOUT = 30.0
MAX_TIMEOUT = 600.0


def valid_transports() -> tuple:
    """当前生效的 MCP 传输名（传输注册表为单一真源；读取失败回退快照）。"""
    try:
        from .transport_registry import builtin_mcp_transport_ids

        names = tuple(builtin_mcp_transport_ids())
        return names or VALID_TRANSPORTS
    except Exception:  # noqa: BLE001 - 注册表不可用时回退快照
        return VALID_TRANSPORTS


def valid_agent_types() -> tuple:
    """当前生效的 SubAgent 类型名（Agent 类型注册表为单一真源）。"""
    try:
        from ..core.agent_types import agent_type_names

        names = tuple(agent_type_names())
        return names or VALID_AGENT_TYPES
    except Exception:  # noqa: BLE001 - 注册表不可用时回退快照
        return VALID_AGENT_TYPES


@dataclass
class McpServerConfig:
    """单个 MCP server 的解析后配置。"""

    name: str
    transport: str = "stdio"
    command: str = ""
    args: list = field(default_factory=list)
    env: dict = field(default_factory=dict)
    cwd: str = ""
    url: str = ""
    headers: dict = field(default_factory=dict)
    enabled: bool = True
    agents: list = field(default_factory=lambda: list(DEFAULT_AGENT_TYPES))
    timeout: float = DEFAULT_TIMEOUT
    parallel_safe: bool = False
    description: str = ""
    #: stdio：是否把当前进程的**全部**环境变量继承给子进程（默认 False，
    #: 仅透传基础变量白名单 + ``env``，避免凭据外泄给第三方 server）
    inherit_env: bool = False

    @property
    def allowed_agents(self) -> set:
        """允许使用该服务器工具的 SubAgent 类型集合。"""
        valid = valid_agent_types()
        result = {a for a in self.agents if a in valid}
        return result or set(DEFAULT_AGENT_TYPES)

    @property
    def is_stdio(self) -> bool:
        return self.transport == "stdio"


def _as_str(value: Any, default: str = "") -> str:
    if isinstance(value, str):
        return value.strip()
    if value is None:
        return default
    return str(value).strip()


def _as_str_list(value: Any) -> list:
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if v is not None]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _as_str_dict(value: Any) -> dict:
    if not isinstance(value, dict):
        return {}
    return {str(k): str(v) for k, v in value.items() if k is not None and v is not None}


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "on")
    if isinstance(value, int):  # bool 已在上方拦截
        return bool(value)
    if isinstance(value, float):
        return value != 0.0
    return default


def _as_timeout(value: Any) -> float:
    if isinstance(value, bool):  # bool 是 int 子类：显式排除（True 不应变为 1.0）
        return DEFAULT_TIMEOUT
    try:
        timeout = float(value)
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT
    if timeout <= 0 or timeout != timeout:  # NaN 防御
        return DEFAULT_TIMEOUT
    return min(timeout, MAX_TIMEOUT)


def parse_server(raw: Any, index: int = 0) -> Optional[McpServerConfig]:
    """解析单个 server 配置条目；非法条目返回 None（记 WARNING）。"""
    if not isinstance(raw, dict):
        _logger.warning("MCP 配置第 %d 项不是对象，已跳过: %r", index, type(raw).__name__)
        return None

    name = _as_str(raw.get("name"))
    if not name:
        _logger.warning("MCP 配置第 %d 项缺少 name，已跳过", index)
        return None

    transport = _as_str(raw.get("transport"), "stdio").lower() or "stdio"
    transports = valid_transports()
    if transport not in transports:
        _logger.warning(
            "MCP server '%s' 的 transport='%s' 不受支持（可选 %s），已跳过",
            name, transport, "/".join(transports),
        )
        return None

    agents_raw = _as_str_list(raw.get("agents"))
    agents = [a for a in agents_raw if a in valid_agent_types()] or list(DEFAULT_AGENT_TYPES)

    cfg = McpServerConfig(
        name=name,
        transport=transport,
        command=_as_str(raw.get("command")),
        args=_as_str_list(raw.get("args")),
        env=_as_str_dict(raw.get("env")),
        cwd=_as_str(raw.get("cwd")),
        url=_as_str(raw.get("url")),
        headers=_as_str_dict(raw.get("headers")),
        enabled=_as_bool(raw.get("enabled"), True),
        agents=agents,
        timeout=_as_timeout(raw.get("timeout")),
        parallel_safe=_as_bool(raw.get("parallel_safe"), False),
        description=_as_str(raw.get("description")),
        inherit_env=_as_bool(raw.get("inherit_env"), False),
    )

    if cfg.is_stdio and not cfg.command:
        _logger.warning("MCP server '%s'（stdio）缺少 command，已跳过", name)
        return None
    if not cfg.is_stdio and not cfg.url:
        _logger.warning("MCP server '%s'（%s）缺少 url，已跳过", name, cfg.transport)
        return None
    if not cfg.is_stdio and not cfg.url.lower().startswith(("http://", "https://")):
        _logger.warning("MCP server '%s' 的 url 必须为 http(s) 地址，已跳过", name)
        return None
    return cfg


def parse_mcp_servers(raw: Any) -> list:
    """解析 mcp_servers 配置值 → McpServerConfig 列表（非法条目跳过，同名去重）。"""
    if not isinstance(raw, (list, tuple)):
        if raw not in (None, {}, ""):
            _logger.warning("mcp_servers 配置应为列表，实际为 %s，已忽略", type(raw).__name__)
        return []

    servers: list = []
    seen: set = set()
    for index, item in enumerate(raw):
        cfg = parse_server(item, index)
        if cfg is None:
            continue
        if cfg.name in seen:
            _logger.warning("MCP server 名称重复 '%s'，后一项已忽略", cfg.name)
            continue
        seen.add(cfg.name)
        servers.append(cfg)
    return servers


def load_mcp_servers() -> list:
    """从配置中心读取 mcp_servers 并解析（读取失败返回空列表）。"""
    try:
        from ..config import MCP_SERVERS
    except Exception:
        _logger.debug("读取 MCP_SERVERS 配置失败（回退空列表）", exc_info=True)
        return []
    return parse_mcp_servers(MCP_SERVERS)


def validate_mcp_servers(value: Any) -> list:
    """配置校验入口（供 config.schema 清洗写回值）。

    只保留结构合法的 dict 条目（不改动字段类型以外的语义），
    避免非法配置把 /config 界面与启动流程带崩。
    """
    if not isinstance(value, (list, tuple)):
        return []
    cleaned: list = []
    for item in value:
        if not isinstance(item, dict):
            continue
        entry = {str(k): v for k, v in item.items() if k is not None}
        if not _as_str(entry.get("name")):
            continue
        cleaned.append(entry)
    return cleaned


__all__ = [
    "McpServerConfig",
    "VALID_TRANSPORTS",
    "VALID_AGENT_TYPES",
    "valid_transports",
    "valid_agent_types",
    "DEFAULT_AGENT_TYPES",
    "DEFAULT_TIMEOUT",
    "parse_server",
    "parse_mcp_servers",
    "load_mcp_servers",
    "validate_mcp_servers",
    # re-export：调用方从 mcp.config 取配置异常（异常定义在 errors.py）
    "McpConfigError",
]
