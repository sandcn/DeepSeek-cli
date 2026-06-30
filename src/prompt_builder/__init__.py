"""提示词构建模块 — 集中管理所有系统提示词的构建逻辑，解决循环依赖

设计说明：
- 各模块的提示词文本存储在项目根目录 prompts/*.md 中
- builder.py 负责从 .md 文件读取并组装完整/子代理系统提示词
- 运行时动态信息（环境、Git）由 env_info.py / vcs_info.py 生成
"""
from .builder import (
    build_environment_info,
    build_execute_agent_system_prompt,
    build_map_agent_system_prompt,
    build_plan_agent_system_prompt,
    build_read_memory_agent_system_prompt,
    build_review_agent_system_prompt,
    build_think_agent_system_prompt,
    build_write_memory_agent_system_prompt,
    build_system_prompt,
    build_subagent_system_prompt,
    reset_prompts_cache,
)
from .env_info import build_init_md_summary
from .vcs_info import check_version_control

__all__ = [
    "build_environment_info",
    "build_init_md_summary",
    "build_execute_agent_system_prompt",
    "build_map_agent_system_prompt",
    "build_plan_agent_system_prompt",
    "build_read_memory_agent_system_prompt",
    "build_review_agent_system_prompt",
    "build_think_agent_system_prompt",
    "build_write_memory_agent_system_prompt",
    "build_system_prompt",
    "build_subagent_system_prompt",
    "check_version_control",
    "reset_prompts_cache",
]
