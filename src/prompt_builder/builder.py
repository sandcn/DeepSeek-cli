"""提示词构建器 — 简化版，从预合并的 prompts_export_*.md 加载系统提示词

设计说明：
- MainAgent/SubAgent 各自的完整系统提示词已预合并为两个独立文件：
  prompts/prompts_export_main.md 和 prompts/prompts_export_sub.md
- 这两个文件包含全部静态规则内容（角色设定、行为规范、代码理解、工具使用等），
  不含运行时动态信息（环境信息、Git 状态、跨对话记忆索引）
- builder.py 只负责加载静态文件 + 追加运行时动态信息
- 额外模块（security/performance 等）已清理，不再支持 extra_modules 按需注入
"""

from __future__ import annotations

import logging
import os


_logger = logging.getLogger(__name__)

from .env_info import (
    _resolve_cwd,
    build_environment_info,
    build_work_md,
)
from .vcs_info import _build_vcs_info

# ── Prompts 目录路径 ────────────────────────────────────────
_PROMPTS_DIR: str | None = None


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


def _build_prompt(
    agent_name:str,
    export_name: str,
    fallback: str,
    include_version_control: bool = True,
    cwd: str | None = None,
    include_globa_md: bool = True,
) -> list[str]:
    """构建提示词的公共逻辑。

    从 prompts_export_*.md 加载静态规则，追加运行时动态信息。

    Args:
        export_name: 导出的 prompts 文件名（不含 .md 后缀）
        fallback: 文件丢失时的兜底提示词
        include_version_control: 是否包含版本控制信息
        cwd: 工作目录
        include_globa_md: 是否从 init.md 加载项目摘要信息
    """
    cwd = _resolve_cwd(cwd)
    parts: list[str] = []

    # 加载预合并的系统提词（文件丢失时用兜底版本）
    export_content = _load_prompt(export_name)
    if not export_content:
        _logger.warning("提示词文件 %s 未找到或读取失败，使用 fallback 兜底提示词", export_name)
    parts.append(export_content if export_content else fallback)

    # 运行时：从 init.md 动态加载项目摘要（放在环境信息前）
    if include_globa_md:
        global_summary = build_work_md("global.md",cwd=os.getcwd())
        if global_summary:
            parts.append(global_summary)

    agent_summary = build_work_md(agent_name + ".md",cwd=os.getcwd())
    if agent_summary:
            parts.append(agent_summary)

    # 运行时动态信息
    env_info = build_environment_info(cwd)
    if include_version_control:
        vcs_info, _has_git = _build_vcs_info(cwd)
        env_info += vcs_info
    parts.append(env_info)

    # 过滤空字符串（文件丢失/读取失败时 _load_prompt 返回空字符串）
    return [p for p in parts if p]


# =================== 子代理提示词 ===================


def build_subagent_system_prompt(
    include_version_control: bool = True,
    cwd: str | None = None,
) -> list[str]:
    """构建子代理系统提示词。

    从 prompts_export_sub.md 加载静态规则，追加运行时动态信息。
    """
    return _build_prompt("sub","prompts_export_sub", _FALLBACK_SUB_PROMPT, include_version_control, cwd, include_globa_md=True)


def build_map_agent_system_prompt(
    include_version_control: bool = True,
    cwd: str | None = None,
) -> list[str]:
    """构建 map 类型子代理系统提示词。

    从 prompts_export_map.md 加载静态规则，追加运行时动态信息。
    Map 类型专用于项目代码分析，只读工具集。
    """
    return _build_prompt("map","prompts_export_map", _FALLBACK_SUB_PROMPT, include_version_control, cwd, include_globa_md=True)


def build_review_agent_system_prompt(
    include_version_control: bool = True,
    cwd: str | None = None,
) -> list[str]:
    """构建 review 类型子代理系统提示词。

    从 prompts_export_review.md 加载静态规则，追加运行时动态信息。
    Review 类型专用于代码审查（Code Review），只读工具集，P0-P3 分级输出。
    """
    return _build_prompt("review","prompts_export_review", _FALLBACK_SUB_PROMPT, include_version_control, cwd, include_globa_md=True)


def build_think_agent_system_prompt(
    include_version_control: bool = True,
    cwd: str | None = None,
) -> list[str]:
    """构建 think 类型子代理系统提示词。

    从 prompts_export_think.md 加载静态规则，追加运行时动态信息。
    Think 类型专用于深度推理分析，只读工具集（read_file/search/find/ls），
    在 map 分析完成后强制调用，将结论返回主 Agent。
    """
    return _build_prompt("think","prompts_export_think", _FALLBACK_SUB_PROMPT, include_version_control, cwd, include_globa_md=True)


def build_plan_agent_system_prompt(
    include_version_control: bool = True,
    cwd: str | None = None,
) -> list[str]:
    """构建 plan 类型子代理系统提示词。

    从 prompts_export_plan.md 加载静态规则，追加运行时动态信息。
    Plan 类型专用于制定可执行计划并写入 .chat/plan/ 目录，
    只读分析工具 + write_file/update_file。
    """
    return _build_prompt("plan","prompts_export_plan", _FALLBACK_SUB_PROMPT, include_version_control, cwd, include_globa_md=True)


def build_read_memory_agent_system_prompt(
    include_version_control: bool = True,
    cwd: str | None = None,
) -> list[str]:
    """构建 read_memory 类型子代理系统提示词。

    从 prompts_export_read_memory.md 加载静态规则，追加运行时动态信息。
    read_memory 类型仅保留 read_file/search/find/ls 只读工具，
    专用于搜索和读取 .chat/memory/ 目录下的记忆文件。
    """
    return _build_prompt("read_memory","prompts_export_read_memory", _FALLBACK_SUB_PROMPT, include_version_control, cwd, include_globa_md=True)


def build_write_memory_agent_system_prompt(
    include_version_control: bool = True,
    cwd: str | None = None,
) -> list[str]:
    """构建 write_memory 类型子代理系统提示词。

    从 prompts_export_write_memory.md 加载静态规则，追加运行时动态信息。
    write_memory 类型保留读工具 + write_file/update_file/mk，
    写入仅限 .chat/memory/ 目录，专用于创建和更新记忆文件。
    """
    return _build_prompt("write_memory","prompts_export_write_memory", _FALLBACK_SUB_PROMPT, include_version_control, cwd, include_globa_md=True)


def build_execute_agent_system_prompt(
    include_version_control: bool = True,
    cwd: str | None = None,
) -> list[str]:
    """构建 execute 类型子代理系统提示词。

    从 prompts_export_execute.md 加载静态规则，追加运行时动态信息。
    execute 类型拥有完整读写+bash 工具集，独立上下文，
    用于执行计划文件中的具体步骤，完成后返回修改的文件列表。
    """
    return _build_prompt("ececute","prompts_export_execute", _FALLBACK_SUB_PROMPT, include_version_control, cwd, include_globa_md=True)


# =================== 主代理提示词 ===================


def build_system_prompt(
    include_version_control: bool = True,
    cwd: str | None = None,
) -> list[str]:
    """构建主代理系统提示词。

    从 prompts_export_main.md 加载静态规则，追加运行时动态信息。
    """
    return _build_prompt("main","prompts_export_main", _FALLBACK_MAIN_PROMPT, include_version_control, cwd)


__all__ = [
    "build_environment_info",
    "build_system_prompt",
    "build_subagent_system_prompt",
    "build_map_agent_system_prompt",
    "build_review_agent_system_prompt",
    "build_plan_agent_system_prompt",
    "build_read_memory_agent_system_prompt",
    "build_write_memory_agent_system_prompt",
    "build_execute_agent_system_prompt",
    "build_think_agent_system_prompt",
    "reset_prompts_cache",
]
