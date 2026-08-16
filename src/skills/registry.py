"""技能注册表 — 分层合并、rank 裁决、按需加载

参照 DeepSeek Harness 的 ``SkillRegistry``（`ctx.skills`）设计，
但技能只存放在项目 ``./.skills`` 一个位置：

- 项目技能：``<项目根>/.skills``（rank 100，source=project）
- GitHub 安装：``<项目根>/.skills/installed/<owner>__<repo>``
  （rank 200，source=github）
- 进程内运行时注册：``register()``（rank 250，同名 first-wins）

同名技能低 rank 胜出；目录按根目录 mtime 缓存，技能文件被修改/
新增后自动重新发现（无需常驻 watcher，``/skill refresh`` 强制刷新）。

配置（``~/.chat_config/chatrc.json`` 顶层 ``skills`` 节点）::

    "skills": {
        "enabled": true,
        "catalog_description_max_length": 500
    }
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ..config.loader import get_rc
from .discovery import parse_skill_file, scan_skill_root
from .models import (
    RANK_INSTALLED,
    RANK_PROJECT,
    RANK_RUNTIME,
    InvocationPolicy,
    SkillCandidate,
    SkillDefinition,
    SkillSummary,
    is_skill_name,
    normalize_invocation,
)

_logger = logging.getLogger(__name__)

# 项目技能目录名
SKILLS_DIR = ".skills"
# GitHub 安装子目录
INSTALLED_DIR = "installed"

# 运行时技能 source 标记
SOURCE_RUNTIME = "runtime"


def _find_project_root(cwd: Optional[str] = None) -> Path:
    """从 cwd 向上找含 .git 的目录；找不到时回退 cwd 本身。"""
    current = Path(cwd or os.getcwd()).resolve()
    if current.is_file():
        current = current.parent
    while True:
        if (current / ".git").exists():
            return current
        parent = current.parent
        if parent == current:
            return Path(cwd or os.getcwd()).resolve()
        current = parent


class SkillRegistry:
    """技能注册表 — 进程级单例（``default()``）。"""

    def __init__(self):
        self._runtime: Dict[str, SkillDefinition] = {}
        # 根目录 mtime 缓存: root_path -> (mtime, [candidates])
        self._root_cache: Dict[str, Tuple[float, List[SkillCandidate]]] = {}

    # ── 配置 ──────────────────────────────────────────────

    def _rc_skills(self) -> dict:
        try:
            rc = get_rc()
        except Exception:
            rc = {}
        value = rc.get("skills", {})
        return value if isinstance(value, dict) else {}

    def enabled(self) -> bool:
        """技能系统是否启用。"""
        return bool(self._rc_skills().get("enabled", True))

    def catalog_description_max_length(self) -> int:
        """目录描述最大长度（默认 500）。"""
        try:
            value = int(self._rc_skills().get("catalog_description_max_length", 500))
        except (TypeError, ValueError):
            return 500
        return value if value >= 3 else 3

    def auto_load_names(self) -> List[str]:
        """配置的自动加载技能名列表（``skills.auto_load``，kebab-case 过滤）。"""
        value = self._rc_skills().get("auto_load", [])
        if not isinstance(value, (list, tuple)):
            return []
        names: List[str] = []
        for name in value:
            if isinstance(name, str) and is_skill_name(name) and name not in names:
                names.append(name)
        return names

    def auto_load_skills(self, cwd: Optional[str] = None) -> List[SkillDefinition]:
        """配置的自动加载技能定义（仅模型可调用、实际存在的）。"""
        skills: List[SkillDefinition] = []
        for name in self.auto_load_names():
            skill = self.get(name, cwd=cwd)
            if skill is not None and skill.invocation.model_invocable:
                skills.append(skill)
        return skills

    # ── 根目录 ───────────────────────────────────────────

    def project_root(self, cwd: Optional[str] = None) -> Path:
        """技能项目根（git 根或 cwd）。"""
        return _find_project_root(cwd)

    def skills_dir(self, cwd: Optional[str] = None) -> Path:
        """项目技能目录 ``<项目根>/.skills``。"""
        return self.project_root(cwd) / SKILLS_DIR

    def installed_root(self, cwd: Optional[str] = None) -> Path:
        """GitHub 安装技能目录 ``<项目根>/.skills/installed``。"""
        return self.skills_dir(cwd) / INSTALLED_DIR

    def roots(self, cwd: Optional[str] = None) -> List[Tuple[Path, str, int]]:
        """返回 (路径, source, rank) 根目录列表，按 rank 升序。"""
        if not self.enabled():
            return []
        roots: List[Tuple[Path, str, int]] = [
            (self.skills_dir(cwd), "project", RANK_PROJECT),
        ]
        installed = self.installed_root(cwd)
        if installed.is_dir():
            try:
                for sub in sorted(installed.iterdir()):
                    if sub.name.startswith("."):
                        continue
                    if sub.is_dir():
                        roots.append((sub, "github", RANK_INSTALLED))
            except OSError:
                _logger.debug("读取 .skills/installed 目录异常", exc_info=True)
        return roots

    # ── 发现（带根目录 mtime 缓存） ──────────────────────

    def _discover_root(self, root: Path, source: str, rank: int) -> List[SkillCandidate]:
        """扫描单个根目录，mtime 未变时命中缓存。"""
        cache_key = str(root)
        try:
            stat = root.stat()
            mtime = stat.st_mtime
        except OSError:
            mtime = -1.0
        cached = self._root_cache.get(cache_key)
        if cached is not None and cached[0] == mtime:
            return cached[1]
        candidates: List[SkillCandidate] = []
        for cand in scan_skill_root(root):
            cand.source = source
            cand.provider = "local" if source != "github" else "github"
            cand.rank = rank
            candidates.append(cand)
        self._root_cache[cache_key] = (mtime, candidates)
        return candidates

    # ── 运行时注册 ───────────────────────────────────────

    def register(
        self,
        name: str,
        description: str,
        content: str,
        *,
        when_to_use: Optional[str] = None,
        invocation: Optional[InvocationPolicy] = None,
        source: str = SOURCE_RUNTIME,
        path: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> bool:
        """注册一个进程内运行时技能（同名 first-wins）。

        Returns:
            True 注册成功；False 同名已存在（被忽略）。
        """
        if not is_skill_name(name):
            raise ValueError(f"非法技能名: {name}")
        if not description:
            raise ValueError(f"技能 {name} 必须提供 description")
        if name in self._runtime:
            _logger.warning('运行时技能 "%s" 已存在，忽略重复注册', name)
            return False
        self._runtime[name] = SkillDefinition(
            name=name,
            description=description,
            invocation=normalize_invocation(invocation),
            source=source,
            provider="runtime",
            when_to_use=when_to_use,
            path=path,
            metadata=metadata,
            rank=RANK_RUNTIME,
            content=content,
        )
        return True

    # ── 合并与查询 ───────────────────────────────────────

    def _all_candidates(self, cwd: Optional[str] = None) -> List[SkillCandidate]:
        """所有根目录候选 + 运行时技能，按 (rank, 根顺序) 排序。"""
        merged: List[SkillCandidate] = []
        for root, source, rank in self.roots(cwd):
            merged.extend(self._discover_root(root, source, rank))
        for skill in self._runtime.values():
            merged.append(skill)
        # 稳定排序：rank 升序；同 rank 保持根顺序（root 列表本身有序）
        merged.sort(key=lambda c: c.rank)
        return merged

    def _winning_candidates(self, cwd: Optional[str] = None) -> List[SkillCandidate]:
        """同名去重后的胜者列表（低 rank 胜出）。"""
        seen: Dict[str, SkillCandidate] = {}
        for cand in self._all_candidates(cwd):
            if cand.name not in seen:
                seen[cand.name] = cand
            else:
                _logger.debug(
                    '技能 "%s" 来自 %s(%s) 被更高优先级 %s(%s) 遮蔽',
                    cand.name, cand.source, cand.rank,
                    seen[cand.name].source, seen[cand.name].rank,
                )
        return list(seen.values())

    def list(self, cwd: Optional[str] = None) -> List[SkillSummary]:
        """列出全部胜出技能摘要（按名称排序）。"""
        summaries = [self._to_summary(c) for c in self._winning_candidates(cwd)]
        summaries.sort(key=lambda s: s.name)
        return summaries

    def get(self, name: str, cwd: Optional[str] = None) -> Optional[SkillDefinition]:
        """加载完整技能定义（按需重读文件，保证正文新鲜）。"""
        if not is_skill_name(name):
            return None
        candidates = self._winning_candidates(cwd)
        for cand in candidates:
            if cand.name != name:
                continue
            if cand.provider == "runtime" or cand.path is None:
                # 运行时技能直接返回；无 path 的候选（防御）取候选自身
                return SkillDefinition(
                    **{k: getattr(cand, k) for k in (
                        "name", "description", "invocation", "source", "provider",
                        "when_to_use", "path", "metadata", "rank", "directory",
                    )},
                    content=cand.content,
                )
            parsed = parse_skill_file(Path(cand.path))
            if parsed is None:
                return None
            return SkillDefinition(
                name=parsed.name,
                description=parsed.description,
                invocation=parsed.invocation,
                source=cand.source,
                provider=cand.provider,
                when_to_use=parsed.when_to_use,
                path=cand.path,
                metadata=parsed.metadata,
                rank=cand.rank,
                directory=cand.directory,
                content=parsed.content,
            )
        return None

    # ── 目录条目（模型目录消息用） ───────────────────────

    def catalog_entries(self, cwd: Optional[str] = None, max_length: int = 500) -> List[Tuple[str, str]]:
        """模型可调用的技能目录条目 (name, description)，按名称排序。"""
        entries = []
        for skill in self.list(cwd):
            if not skill.invocation.model_invocable:
                continue
            desc = " ".join(skill.description.split())
            if len(desc) > max_length:
                desc = desc[: max_length - 3] + "..."
            entries.append((skill.name, desc))
        return entries

    # ── 缓存控制 ─────────────────────────────────────────

    def invalidate(self) -> None:
        """清空根目录缓存（技能文件变更/安装/卸载后调用）。"""
        self._root_cache.clear()

    @staticmethod
    def _to_summary(cand: SkillCandidate) -> SkillSummary:
        return SkillSummary(
            name=cand.name,
            description=cand.description,
            invocation=cand.invocation,
            source=cand.source,
            provider=cand.provider,
            when_to_use=cand.when_to_use,
            path=cand.path,
            metadata=cand.metadata,
        )


# ── 进程级默认注册表 ──────────────────────────────────────

_default_registry: Optional[SkillRegistry] = None


def default_registry() -> SkillRegistry:
    """返回进程级默认注册表（单例）。"""
    global _default_registry
    if _default_registry is None:
        _default_registry = SkillRegistry()
    return _default_registry


def reset_default_registry() -> None:
    """重置默认注册表（测试用）。"""
    global _default_registry
    _default_registry = None


__all__ = [
    "INSTALLED_DIR",
    "SKILLS_DIR",
    "SOURCE_RUNTIME",
    "SkillRegistry",
    "default_registry",
    "reset_default_registry",
]
