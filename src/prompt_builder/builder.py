"""提示词构建器 — 简化版，从预合并的 prompts_export_*.md 加载系统提示词

设计说明：
- MainAgent 的完整系统提示词预合并为 prompts/prompts_export_main.md
- 各类型 SubAgent（map/review/plan/execute）各自从
  prompts_export_*.md 加载静态规则；通用 SubAgent（build_subagent_system_prompt）
  直接使用 fallback 兜底提示词
- 这些文件包含全部静态规则内容（角色设定、行为规范、代码理解、工具使用等），
  不含运行时动态信息（环境信息、Git 状态、跨对话记忆索引）
- builder.py 只负责加载静态文件 + 追加运行时动态信息
- 额外模块（security/performance 等）已清理，不再支持 extra_modules 按需注入
"""

from __future__ import annotations

import logging
import os

from ..skills.prompt_section import build_skills_prompt_section


_logger = logging.getLogger(__name__)

from .env_info import (
    _resolve_cwd,
    build_environment_info,
    build_work_md,
)
from .modes import (
    DEFAULT_MODE,
    active_modes,
    builtin_mode_names,
    default_mode,
    disable_builtin_modes,
    managed_mode_names,
    mode_order,
    register_builtin_mode,
    resolve_mode_export,
    resolve_mode_label,
    set_managed_builtin_modes,
)
from .sources import (
    builtin_prompt_source_ids,
    disable_builtin_prompt_sources,
    managed_prompt_source_ids,
    register_builtin_prompt_source,
    resolve_prompt_source,
    set_managed_builtin_prompt_sources,
)
from .sections import (
    ordered_sections,
    resolve_builder,
)
from .vcs_info import _build_vcs_info

# ── Prompts 目录路径 ────────────────────────────────────────
_PROMPTS_DIR: str | None = None

# ── 主 agent 运行模式（Ctrl+B 循环切换，默认空模式） ─────────
# 三态（规则量递增）：空模式 → 简单模式 → 标准模式 → 空模式。
#   empty    : prompts_export_main_empty.md（仅基础安全/通用规范）
#   simple   : prompts_export_main_simple.md（基础规范 + 少量工具/验证规则）
#   standard : prompts_export_main.md（完整规则集）
# 默认 empty：启动即进入空模式。
_MODE_EMPTY = "empty"
_MODE_SIMPLE = "simple"
_MODE_STANDARD = "standard"
#: Ctrl+B 循环顺序的静态兜底（规则量递增）；生效顺序来自运行模式注册表
_MODE_CYCLE = (_MODE_EMPTY, _MODE_SIMPLE, _MODE_STANDARD)
#: 当前模式（真源；模式元数据 label/export/order 来自 ``modes`` 注册表）
_MODE: str = _MODE_EMPTY
#: 兼容旧布尔标志：与 _MODE 同步（True=空模式）
_EMPTY_MODE: bool = True


def _apply_mode(mode: str) -> str:
    """内部：设置模式真源并同步旧布尔标志，返回生效模式。"""
    global _MODE, _EMPTY_MODE
    _MODE = mode
    _EMPTY_MODE = (mode == _MODE_EMPTY)
    return _MODE


def get_mode() -> str:
    """当前主 agent 运行模式（``empty`` / ``simple`` / ``standard``）。"""
    return _MODE


def set_mode(mode: str) -> str:
    """设置主 agent 运行模式（非法值回退空模式），返回生效模式。"""
    if mode not in active_modes():
        mode = DEFAULT_MODE
    return _apply_mode(mode)


def cycle_mode() -> str:
    """按运行模式注册表的 ``order`` 顺序循环切换到下一模式，返回新模式。"""
    order = mode_order() or list(_MODE_CYCLE)
    try:
        idx = order.index(_MODE)
    except ValueError:
        idx = -1
    return _apply_mode(order[(idx + 1) % len(order)])


def mode_label(mode: str | None = None) -> str:
    """模式显示名（默认当前模式）；未知模式回退空模式显示名。"""
    key = _MODE if mode is None else mode
    return resolve_mode_label(key)


def is_empty_mode() -> bool:
    """是否处于主 agent 空模式。"""
    return _MODE == _MODE_EMPTY


def is_simple_mode() -> bool:
    """是否处于主 agent 简单模式。"""
    return _MODE == _MODE_SIMPLE


def is_standard_mode() -> bool:
    """是否处于主 agent 标准模式。"""
    return _MODE == _MODE_STANDARD


def toggle_empty_mode() -> bool:
    """空 ↔ 标准 二态切换（兼容旧 API），返回新状态（True=空模式）。"""
    _apply_mode(_MODE_STANDARD if _MODE == _MODE_EMPTY else _MODE_EMPTY)
    return is_empty_mode()


def set_empty_mode(enabled: bool) -> None:
    """设置空/标准二态（兼容旧 API）：True=空模式，False=标准模式。"""
    _apply_mode(_MODE_EMPTY if enabled else _MODE_STANDARD)


def set_simple_mode(enabled: bool = True) -> None:
    """设置简单模式（测试/外部 API）：True=简单模式，False=标准模式。"""
    _apply_mode(_MODE_SIMPLE if enabled else _MODE_STANDARD)


def _get_prompts_dir() -> str:
    """获取 prompts 目录的绝对路径，带缓存"""
    global _PROMPTS_DIR
    if _PROMPTS_DIR is not None:
        return _PROMPTS_DIR
    builder_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(os.path.dirname(builder_dir))
    _PROMPTS_DIR = os.path.join(project_root, "prompts")
    return _PROMPTS_DIR


def reset_prompts_cache() -> None:
    """重置 prompts 目录缓存，供测试使用。"""
    global _PROMPTS_DIR
    _PROMPTS_DIR = None


def _load_prompt(name: str) -> str:
    """从 prompts/ 目录读取指定名称的 .md 文件内容"""
    prompts_dir = _get_prompts_dir()
    filepath = os.path.join(prompts_dir, f"{name}.md")
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        _logger.error("prompts 文件未找到: %s", filepath)
        return ""
    except (IOError, OSError) as e:
        _logger.error("读取 prompts 文件失败 %s: %s", filepath, e)
        return ""


# ── 最小安全兜底提示词（prompts 文件丢失时使用） ─────────────

_FALLBACK_CORE_RULES = """## 安全红线（一票否决）
- 禁止读写密钥/密码/token/PII
- 禁止未经确认的远程命令执行
- 禁止 rm -rf / mkfs / dd / chmod 777 / sudo / chown
- 禁止越权访问/修改未授权文件
- 禁止非明确要求的网络扫描/渗透

## 通用规范
- 密钥从环境变量读取，禁止硬编码
- 语言对应的路径安全库（如 pathlib / Node.js path / Rust std::path::Path / Java java.nio.file.Path），安全拼接，防穿越
- 语言对应的临时文件安全 API（如 tempfile / Node.js tmp / Go os.CreateTemp / Rust tempfile crate / Java Files.createTempFile），安全创建，用后清理
- 中文纯文本输出，禁止 HTML
- 操作前先输出完整计划，逐项对照执行
"""


_FALLBACK_SUB_PROMPT = f"""# 行为规则
{_FALLBACK_CORE_RULES}
## 工具使用
- 使用 read_file 读文件，update_file 改文件
- 使用 search 搜索代码，禁止 bash grep
- 修改前 read_file 确认内容

## 测试
- 新增功能/修复 Bug 同步更新测试
- 每个 Bug 修复必须附带回归测试
"""


_FALLBACK_MAIN_PROMPT = f"""# 核心目标
高效交付可运行代码，修改即验证。
{_FALLBACK_CORE_RULES}
## 工具使用
- 使用 read_file 读文件，update_file 改文件
- 使用 search 搜索代码，禁止 bash grep
- 修改前 read_file 确认内容
- 修改后执行语言对应的语法检查（如 Python `python -m py_compile` / Node.js `node --check` / Go `go vet` / Rust `cargo check` / Java `javac -Xlint`），并运行对应测试框架（如 Python pytest / Node.js Jest/Mocha / Go `go test` / Rust `cargo test` / Java JUnit）

## 测试
- 新增功能/修复 Bug 同步更新测试
- 每个 Bug 修复必须附带回归测试
"""

# ── 公共构建逻辑 ────────────────────────────────────

#: agent 名 → MCP 工具权限类型（用于提示词章节按权限过滤；
#: main/sub 走 execute 策略——主 Agent 与通用子代理可用 MCP 工具全集）
_MCP_AGENT_TYPE: dict = {
    "main": "execute",
    "sub": "execute",
    "execute": "execute",
    "map": "map",
    "review": "review",
    "plan": "plan",
}


def _resolve_mcp_agent_type(agent_name: str) -> str:
    """agent 名 → MCP 工具权限类型（注册表驱动，可插拔）。"""
    if agent_name in ("main", "sub"):
        return "execute"
    try:
        from ..core.agent_types import mcp_agent_type

        return mcp_agent_type(agent_name)
    except Exception:
        return _MCP_AGENT_TYPE.get(agent_name, "execute")


def _build_mcp_section(agent_name: str = "main") -> str:
    """构建系统提示词中的 MCP 外部工具章节（无 MCP 配置时返回空字符串）。

    先读配置门控：``mcp_servers`` 为空时直接返回，避免无 MCP 场景为构建
    提示词而导入整个 mcp 子系统（httpx / 工具适配层）。

    章节按目标 agent 类型过滤工具：只读型 SubAgent（map/review/plan）默认被
    排除的 MCP 工具不会出现在其提示词中，避免"提示词宣称可用但工具集被移除"
    导致模型反复发起被拒调用。
    """
    try:
        from ..config import MCP_SERVERS
    except Exception:
        return ""
    if not MCP_SERVERS:
        return ""
    try:
        from ..mcp import get_mcp_prompt_section
        return get_mcp_prompt_section(_resolve_mcp_agent_type(agent_name))
    except Exception:
        _logger.debug("构建 MCP 提示词章节失败", exc_info=True)
        return ""


def _build_prompt(
    agent_name:str,
    export_name: str,
    fallback: str,
    include_version_control: bool = True,
    cwd: str | None = None,
    include_global_md: bool = True,
    include_skills: bool = False,
    include_mcp: bool = True,
) -> list[str]:
    """按当前生效的提词片段注册表装配系统提示词。

    「一切皆插件」：片段顺序与装配不再硬编码，而是由
    ``src.prompt_builder.sections`` 注册表（每个片段一个清单独立条目）驱动
    ——可按 Patch/Overlay 禁用、替换或调整顺序。``attach_to`` 非空的片段
    （版本控制信息）追加到目标片段的输出，保持「环境信息 + 版本控制」为同一
    条 system 消息（与既有行为完全一致）。

    Args:
        export_name: 导出的 prompts 文件名（不含 .md 后缀）
        fallback: 文件丢失时的兜底提示词
        include_version_control: 是否包含版本控制信息
        cwd: 工作目录
        include_global_md: 是否从 global.md 加载项目摘要信息
        include_skills: 是否在环境信息后注入技能章节（主 Agent 专用，
            构建时只注入一次；技能变更后经 rebuild_system_prompt 重建）
        include_mcp: 是否在技能章节后注入 MCP 外部工具章节（无 MCP 配置时为空）
    """
    ctx = {
        "agent_name": agent_name,
        "export_name": export_name,
        "fallback": fallback,
        "cwd": _resolve_cwd(cwd),
        "include_version_control": include_version_control,
        "include_global_md": include_global_md,
        "include_skills": include_skills,
        "include_mcp": include_mcp,
    }
    parts: list[str] = []
    part_index: dict[str, int] = {}
    for section in ordered_sections():
        text = _invoke_section(section, ctx)
        if not text:
            continue
        host = section.attach_to
        if host and host in part_index:
            parts[part_index[host]] = parts[part_index[host]] + text
            continue
        part_index[section.id] = len(parts)
        parts.append(text)
    return parts


def _invoke_section(section, ctx: dict) -> str:
    """解析并调用片段构造函数；失败只记日志并跳过（不阻断提词构建）。"""
    try:
        builder = resolve_builder(section)
    except Exception:
        _logger.warning("提词片段 %s 构造引用解析失败", section.id, exc_info=True)
        return ""
    try:
        text = builder(ctx)
    except Exception:
        _logger.warning("提词片段 %s 构造失败", section.id, exc_info=True)
        return ""
    return text if isinstance(text, str) else ""


# ── 内置片段构造函数（经 sections 注册表的点分引用调用） ─────


def _section_export(ctx: dict) -> str:
    """静态提词：prompts_export_*.md（缺失时用兜底提词）。"""
    export_name = str(ctx.get("export_name") or "")
    export_content = _load_prompt(export_name) if export_name else ""
    if not export_content:
        _logger.warning("提示词文件 %s 未找到或读取失败，使用 fallback 兜底提示词", export_name)
    return export_content if export_content else str(ctx.get("fallback") or "")


def _section_global_md(ctx: dict) -> str:
    if not ctx.get("include_global_md", True):
        return ""
    return build_work_md("global.md", cwd=ctx.get("cwd"))


def _section_agent_md(ctx: dict) -> str:
    name = str(ctx.get("agent_name") or "")
    if not name:
        return ""
    return build_work_md(name + ".md", cwd=ctx.get("cwd"))


def _section_env_info(ctx: dict) -> str:
    return build_environment_info(ctx.get("cwd"))


def _section_vcs_info(ctx: dict) -> str:
    if not ctx.get("include_version_control", True):
        return ""
    info, _has_git = _build_vcs_info(_resolve_cwd(ctx.get("cwd")))
    return info or ""


def _section_skills(ctx: dict) -> str:
    if not ctx.get("include_skills"):
        return ""
    return build_skills_prompt_section(ctx.get("cwd"))


def _section_mcp(ctx: dict) -> str:
    if not ctx.get("include_mcp", True):
        return ""
    return _build_mcp_section(str(ctx.get("agent_name") or "main"))


# =================== 子代理提示词 ===================


def build_subagent_system_prompt(
    include_version_control: bool = True,
    cwd: str | None = None,
) -> list[str]:
    """构建子代理系统提示词。

    prompts_export_sub.md 不存在，直接使用 fallback 兜底提示词，
    追加运行时动态信息。环境信息后注入技能章节（每个 agent 均可使用技能）。
    """
    return _build_prompt("sub", resolve_prompt_source("sub") or "", _FALLBACK_SUB_PROMPT, include_version_control, cwd, include_global_md=True, include_skills=True)


def build_map_agent_system_prompt(
    include_version_control: bool = True,
    cwd: str | None = None,
) -> list[str]:
    """构建 map 类型子代理系统提示词。

    从 prompts_export_map.md 加载静态规则，追加运行时动态信息。
    Map 类型专用于项目代码分析，只读工具集。
    环境信息后注入技能章节（每个 agent 均可使用技能）。
    """
    return _build_prompt("map", resolve_prompt_source("map") or "", _FALLBACK_SUB_PROMPT, include_version_control, cwd, include_global_md=True, include_skills=True)


def build_review_agent_system_prompt(
    include_version_control: bool = True,
    cwd: str | None = None,
) -> list[str]:
    """构建 review 类型子代理系统提示词。

    从 prompts_export_review.md 加载静态规则，追加运行时动态信息。
    Review 类型专用于代码审查（Code Review），只读工具集，P0-P3 分级输出。
    环境信息后注入技能章节（每个 agent 均可使用技能）。
    """
    return _build_prompt("review", resolve_prompt_source("review") or "", _FALLBACK_SUB_PROMPT, include_version_control, cwd, include_global_md=True, include_skills=True)


def build_plan_agent_system_prompt(
    include_version_control: bool = True,
    cwd: str | None = None,
) -> list[str]:
    """构建 plan 类型子代理系统提示词。

    从 prompts_export_plan.md 加载静态规则，追加运行时动态信息。
    Plan 类型专用于制定可执行计划并写入 .chat/plan/ 目录，
    只读分析工具 + write_file/update_file。
    环境信息后注入技能章节（每个 agent 均可使用技能）。
    """
    return _build_prompt("plan", resolve_prompt_source("plan") or "", _FALLBACK_SUB_PROMPT, include_version_control, cwd, include_global_md=True, include_skills=True)


def build_execute_agent_system_prompt(
    include_version_control: bool = True,
    cwd: str | None = None,
) -> list[str]:
    """构建 execute 类型子代理系统提示词。

    从 prompts_export_execute.md 加载静态规则，追加运行时动态信息。
    execute 类型拥有完整读写+bash 工具集，独立上下文，
    用于执行计划文件中的具体步骤，完成后返回修改的文件列表。
    环境信息后注入技能章节（每个 agent 均可使用技能）。
    """
    return _build_prompt("execute", resolve_prompt_source("execute") or "", _FALLBACK_SUB_PROMPT, include_version_control, cwd, include_global_md=True, include_skills=True)


# =================== 主代理提示词 ===================


def build_system_prompt(
    include_version_control: bool = True,
    cwd: str | None = None,
) -> list[str]:
    """构建主代理系统提示词。

    按当前运行模式（``get_mode()``）加载对应静态规则文件：
      - ``empty``    → prompts_export_main_empty.md（默认）
      - ``simple``   → prompts_export_main_simple.md
      - ``standard`` → prompts_export_main.md
    Ctrl+B 循环切换（空模式 → 简单模式 → 标准模式 → 空模式）。
    环境信息之后注入技能章节（构建时一次；技能变更后经
    ``rebuild_system_prompt()`` 重建）。
    """
    export_name = resolve_mode_export(_MODE)
    return _build_prompt("main", export_name, _FALLBACK_MAIN_PROMPT, include_version_control, cwd, include_skills=True)


__all__ = [
    "build_environment_info",
    "build_system_prompt",
    "build_subagent_system_prompt",
    "build_map_agent_system_prompt",
    "build_review_agent_system_prompt",
    "build_plan_agent_system_prompt",
    "build_execute_agent_system_prompt",
    "reset_prompts_cache",
    "get_mode",
    "set_mode",
    "cycle_mode",
    "mode_label",
    "is_empty_mode",
    "is_simple_mode",
    "is_standard_mode",
    "toggle_empty_mode",
    "set_empty_mode",
    "set_simple_mode",
    "DEFAULT_MODE",
    "active_modes",
    "builtin_mode_names",
    "default_mode",
    "managed_mode_names",
    "mode_order",
    "register_builtin_mode",
    "resolve_mode_export",
    "resolve_mode_label",
    "set_managed_builtin_modes",
    "disable_builtin_modes",
    "builtin_prompt_source_ids",
    "managed_prompt_source_ids",
    "register_builtin_prompt_source",
    "resolve_prompt_source",
    "set_managed_builtin_prompt_sources",
    "disable_builtin_prompt_sources",
]
