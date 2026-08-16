"""技能（skill）子系统 — 参照 DeepSeek Harness 的 dsh-skill 设计

分层：
- ``models`` — 数据结构与校验（名称语法、调用策略）
- ``frontmatter`` — 技能文件 YAML frontmatter 解析（零依赖）
- ``discovery`` — 技能根目录扫描（目录包 / 扁平 Markdown）
- ``registry`` — 注册表：多根合并、rank 裁决、按需加载
- ``render`` — 模型可见的 <skill_content> 规范渲染
- ``prompt_section`` — 系统提示词技能章节（环境信息之后注入一次）
- ``gestures`` — /name 用户手势扫描与注入
- ``github`` — GitHub 技能安装/更新/卸载

磁盘约定（技能只存放在项目 ``./.skills``）：
- 项目技能：``<项目根>/.skills``（目录包 ``<name>/SKILL.md`` 或扁平 ``<name>.md``）
- GitHub 安装：``<项目根>/.skills/installed/<owner>__<repo>``
- 配置：``~/.chat_config/chatrc.json`` 顶层 ``skills`` 节点（enabled /
  catalog_description_max_length / auto_load）

技能文件形态（与 Claude Skills / DSH 相同）：:

    ---
    name: my-skill
    description: 一句话描述
    whenToUse: 可选路由提示
    disable-model-invocation: false
    user-invocable: true
    ---
    技能正文 Markdown
"""

from .discovery import parse_skill_file, scan_skill_root
from .frontmatter import parse_frontmatter
from .gestures import SKILL_GESTURE_RE, inject_skill_gestures, scan_skill_gestures
from .github import (
    GithubSkillInstaller,
    GithubSpec,
    SkillInstallError,
    parse_github_spec,
)
from .models import (
    InvocationPolicy,
    SkillCandidate,
    SkillDefinition,
    SkillSummary,
    is_model_invocable,
    is_skill_name,
    is_user_invocable,
)
from .prompt_section import build_skills_prompt_section
from .registry import SkillRegistry, default_registry, reset_default_registry
from .render import escape_text, render_skill_content

__all__ = [
    "GithubSkillInstaller",
    "GithubSpec",
    "InvocationPolicy",
    "SKILL_GESTURE_RE",
    "SkillCandidate",
    "SkillDefinition",
    "SkillInstallError",
    "SkillRegistry",
    "SkillSummary",
    "build_skills_prompt_section",
    "default_registry",
    "escape_text",
    "inject_skill_gestures",
    "is_model_invocable",
    "is_skill_name",
    "is_user_invocable",
    "parse_frontmatter",
    "parse_github_spec",
    "parse_skill_file",
    "render_skill_content",
    "reset_default_registry",
    "scan_skill_gestures",
    "scan_skill_root",
]
