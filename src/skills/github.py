"""GitHub 技能安装器 — 从 GitHub 仓库安装/更新/卸载技能

实现方式（不依赖 git 命令）：
- 通过 ``https://codeload.github.com/{owner}/{repo}/tar.gz/{ref}`` 下载
  tarball（httpx 流式，带大小上限），Python tarfile 安全解压
  （拒绝绝对路径、``..`` 穿越、符号链接、设备文件）；
- 识别仓库中的技能根：``skills/`` 目录 > 仓库根（单技能 SKILL.md 或
  技能集合），校验至少解析出一个合法技能后才落盘；
- 安装到 ``<项目根>/.skills/installed/{owner}__{repo}/``，
  目录内写 ``.skill-source.json`` 元数据（owner/repo/ref/commit/时间）；
- 注册表把每个安装目录视为一个 rank=200 的技能根（source=github）。

用法（/skill 命令）：::

    /skill install owner/repo
    /skill install owner/repo@tag-or-branch
    /skill install https://github.com/owner/repo/tree/main
    /skill update owner/repo
    /skill remove owner/repo        # 或 owner__repo，或技能名
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import tarfile
import time
from pathlib import Path
from typing import List, Optional

import httpx

from .discovery import parse_skill_file, scan_skill_root
from .registry import INSTALLED_DIR, SKILLS_DIR, _find_project_root

_logger = logging.getLogger(__name__)

# ── 常量 ───────────────────────────────────────────────────
_METADATA_FILE = ".skill-source.json"
_MAX_ARCHIVE_BYTES = 30 * 1024 * 1024      # 30 MB 压缩包上限
_MAX_UNPACKED_BYTES = 60 * 1024 * 1024     # 60 MB 解压后上限
_MAX_MEMBERS = 2000                        # 成员数量上限
_DOWNLOAD_TIMEOUT = 60.0

_GITHUB_SPEC_RE = re.compile(
    r"^(?:https?://github\.com/)?"
    r"(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)"
    r"(?:\.git)?"
    r"(?:/tree/(?P<ref>[^/]+))?"
    r"(?:@(?P<ref2>[^/]+))?"
    r"/?$"
)
_COMMIT_IN_URL_RE = re.compile(r"/tar\.gz/([0-9a-f]{40})(?:[?/]|$)")
_INSTALL_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+__[A-Za-z0-9_.-]+$")


class GithubSpec:
    """解析后的 GitHub 技能仓库引用。"""

    __slots__ = ("owner", "repo", "ref", "source")

    def __init__(self, owner: str, repo: str, ref: str = "HEAD", source: str = ""):
        self.owner = owner
        self.repo = repo
        self.ref = ref or "HEAD"
        self.source = source

    @property
    def install_id(self) -> str:
        return f"{self.owner}__{self.repo}"

    @property
    def url(self) -> str:
        return f"https://github.com/{self.owner}/{self.repo}"

    def __repr__(self) -> str:  # pragma: no cover - 调试辅助
        return f"GithubSpec({self.owner}/{self.repo}@{self.ref})"


class SkillInstallError(Exception):
    """技能安装过程错误（含用户可读消息）。"""


def parse_github_spec(spec: str) -> GithubSpec:
    """解析安装目标：``owner/repo[@ref]`` 或 GitHub URL。"""
    text = spec.strip()
    if not text:
        raise SkillInstallError("空的仓库地址")
    match = _GITHUB_SPEC_RE.match(text)
    if not match:
        raise SkillInstallError(
            f"无法解析仓库地址 \"{spec}\"，支持格式：owner/repo、"
            "owner/repo@branch、https://github.com/owner/repo"
        )
    ref = match.group("ref") or match.group("ref2") or "HEAD"
    if ref == "HEAD":
        ref = "HEAD"
    return GithubSpec(match.group("owner"), match.group("repo"), ref, source=text)


def _resolve_commit_from_url(url: str) -> Optional[str]:
    """从 codeload 重定向后的 URL 提取解析出的 commit SHA。"""
    match = _COMMIT_IN_URL_RE.search(url)
    return match.group(1) if match else None


class GithubSkillInstaller:
    """GitHub 技能安装器。"""

    def __init__(
        self,
        installed_root: Optional[Path] = None,
        http_client: Optional[httpx.AsyncClient] = None,
    ):
        """
        Args:
            installed_root: 安装根目录；None 取
                ``<项目根>/.skills/installed``。
            http_client: 注入的 httpx 客户端（测试用）；None 时每次
                下载创建独立客户端。
        """
        self.installed_root = Path(installed_root) if installed_root is not None else (
            _find_project_root() / SKILLS_DIR / INSTALLED_DIR
        )
        self._http_client = http_client

    # ── 元数据 ───────────────────────────────────────────

    def _metadata_path(self, install_dir: Path) -> Path:
        return install_dir / _METADATA_FILE

    def read_metadata(self, install_dir: Path) -> Optional[dict]:
        meta_path = self._metadata_path(install_dir)
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except (OSError, json.JSONDecodeError):
            return None

    def list_installed(self) -> List[dict]:
        """列出全部已安装的仓库（含元数据）。"""
        if not self.installed_root.is_dir():
            return []
        result = []
        try:
            entries = sorted(self.installed_root.iterdir())
        except OSError:
            return []
        for entry in entries:
            if entry.name.startswith(".") or not entry.is_dir():
                continue
            meta = self.read_metadata(entry)
            if meta is None:
                continue
            result.append({
                "id": entry.name,
                "dir": str(entry),
                "owner": meta.get("owner", ""),
                "repo": meta.get("repo", ""),
                "ref": meta.get("ref", ""),
                "commit": meta.get("commit", ""),
                "installed_at": meta.get("installed_at", ""),
                "skills": meta.get("skills", []),
            })
        return result

    # ── 安装 ─────────────────────────────────────────────

    async def install(
        self,
        spec: str,
        *,
        reuse_ref: bool = False,
        on_change=None,
    ) -> dict:
        """安装（或更新）一个 GitHub 技能仓库。

        Args:
            spec: ``owner/repo[@ref]`` 或 GitHub URL。
            reuse_ref: True 时若该仓库已安装，沿用原 ref（update 用）。
            on_change: 安装成功后回调（通常为 registry.invalidate）。

        Returns:
            安装结果字典（id/owner/repo/ref/commit/skills/dir）。

        Raises:
            SkillInstallError: 下载/解压/校验失败。
        """
        parsed = parse_github_spec(spec)
        if reuse_ref:
            existing = self._find_install_dir(parsed.install_id)
            if existing is not None and parsed.ref == "HEAD":
                # 未显式指定 ref 时沿用已安装 ref
                meta = self.read_metadata(existing) or {}
                if meta.get("ref"):
                    parsed.ref = meta["ref"]

        install_id = parsed.install_id
        target = self.installed_root / install_id
        tmp = self.installed_root / f".tmp-{install_id}"

        try:
            self.installed_root.mkdir(parents=True, exist_ok=True)
            if tmp.exists():
                shutil.rmtree(tmp, ignore_errors=True)
            tmp.mkdir(parents=True, exist_ok=True)

            commit = await self._download_and_extract(parsed, tmp)

            # 识别技能根并校验
            skill_root = self._find_skill_root(tmp)
            candidates = scan_skill_root(skill_root)
            if not candidates:
                raise SkillInstallError(
                    f"仓库 {parsed.owner}/{parsed.repo} 中没有找到合法技能 "
                    "（需要 skills/ 目录、根目录 SKILL.md 或技能子目录）"
                )
            skill_names = sorted({c.name for c in candidates})

            # 落盘：把技能根内容搬进 tmp 顶层（去掉下载目录层级）
            self._flatten_skill_root(tmp, skill_root)

            # 写元数据
            self._metadata_path(tmp).write_text(
                json.dumps({
                    "owner": parsed.owner,
                    "repo": parsed.repo,
                    "ref": parsed.ref,
                    "commit": commit or "",
                    "installed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    "skills": skill_names,
                    "source": parsed.source,
                }, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            # 原子替换：旧目录备份 → tmp 转正 → 清理备份
            backup = self.installed_root / f".old-{install_id}"
            if backup.exists():
                shutil.rmtree(backup, ignore_errors=True)
            if target.exists():
                target.rename(backup)
            try:
                tmp.rename(target)
            except OSError:
                if backup.exists() and not target.exists():
                    backup.rename(target)
                raise
            if backup.exists():
                shutil.rmtree(backup, ignore_errors=True)

            if on_change is not None:
                try:
                    on_change()
                except Exception:
                    _logger.debug("安装后 on_change 回调异常", exc_info=True)

            return {
                "id": install_id,
                "dir": str(target),
                "owner": parsed.owner,
                "repo": parsed.repo,
                "ref": parsed.ref,
                "commit": commit or "",
                "skills": skill_names,
            }
        except httpx.HTTPError as e:
            raise SkillInstallError(f"下载失败: {e}") from e
        finally:
            if tmp.exists():
                shutil.rmtree(tmp, ignore_errors=True)

    # ── 下载与解压 ───────────────────────────────────────

    async def _download_and_extract(self, spec: GithubSpec, dest: Path) -> Optional[str]:
        """下载 tarball 并安全解压到 dest，返回解析出的 commit（尽力而为）。"""
        url = f"https://codeload.github.com/{spec.owner}/{spec.repo}/tar.gz/{spec.ref}"
        headers = {"User-Agent": "ai-chat-skill-installer/2.2"}
        commit: Optional[str] = None

        if self._http_client is not None:
            client = self._http_client
            own_client = False
        else:
            client = httpx.AsyncClient(
                follow_redirects=True,
                timeout=_DOWNLOAD_TIMEOUT,
                headers=headers,
            )
            own_client = True
        try:
            async with client.stream("GET", url) as resp:
                if resp.status_code != 200:
                    raise SkillInstallError(
                        f"下载失败: {resp.status_code} "
                        f"({spec.owner}/{spec.repo}@{spec.ref} 可能不存在或仓库为私有)"
                    )
                for history in resp.history:
                    commit = _resolve_commit_from_url(history.headers.get("location", "")) or commit
                commit = _resolve_commit_from_url(str(resp.url)) or commit

                archive_path = dest / "_archive.tar.gz"
                total = 0
                with open(archive_path, "wb") as f:
                    async for chunk in resp.aiter_bytes():
                        total += len(chunk)
                        if total > _MAX_ARCHIVE_BYTES:
                            raise SkillInstallError(
                                f"压缩包超过 {_MAX_ARCHIVE_BYTES // (1024 * 1024)} MB 上限"
                            )
                        f.write(chunk)
                if total == 0:
                    raise SkillInstallError("下载内容为空")
        finally:
            if own_client:
                await client.aclose()

        self._safe_extract(archive_path, dest)
        archive_path.unlink(missing_ok=True)
        return commit

    def _safe_extract(self, archive_path: Path, dest: Path) -> None:
        """安全解压 tar.gz（防路径穿越/符号链接/超大文件）。"""
        try:
            tar = tarfile.open(archive_path, "r:gz")
        except tarfile.TarError as e:
            raise SkillInstallError(f"压缩包解析失败: {e}") from e
        try:
            members = tar.getmembers()
            if len(members) > _MAX_MEMBERS:
                raise SkillInstallError(
                    f"压缩包成员超过 {_MAX_MEMBERS} 个上限"
                )
            dest_resolved = dest.resolve()
            total_size = 0
            for member in members:
                if member.issym() or member.islnk() or member.isdev():
                    raise SkillInstallError(
                        f"压缩包包含不安全的成员类型: {member.name}"
                    )
                if member.name.startswith("/") or ".." in Path(member.name).parts:
                    raise SkillInstallError(f"压缩包包含非法路径: {member.name}")
                total_size += member.size
                if total_size > _MAX_UNPACKED_BYTES:
                    raise SkillInstallError(
                        f"解压后总大小超过 {_MAX_UNPACKED_BYTES // (1024 * 1024)} MB 上限"
                    )
            tar.extractall(dest, members=members)
        except tarfile.TarError as e:
            raise SkillInstallError(f"解压失败: {e}") from e
        finally:
            tar.close()
        # 二次校验：解压后无文件逃逸
        dest_resolved = dest.resolve()
        for path in dest.rglob("*"):
            if not path.resolve().is_relative_to(dest_resolved):
                raise SkillInstallError(f"解压内容逃逸目标目录: {path}")

    # ── 技能根识别 ───────────────────────────────────────

    @staticmethod
    def _find_skill_root(extracted: Path) -> Path:
        """在解压目录中定位技能根。"""
        # 解压后通常只有一个顶层目录（仓库名）
        subdirs = [p for p in extracted.iterdir() if p.is_dir() and not p.name.startswith(".")]
        # 多顶层目录（非常规仓库）：逐个尝试定位
        if len(subdirs) != 1:
            for sub in sorted(subdirs):
                skills_dir = sub / "skills"
                if skills_dir.is_dir():
                    return skills_dir
                if (sub / "SKILL.md").is_file():
                    return sub
                if _looks_like_skill_collection(sub):
                    return sub
            raise SkillInstallError("无法识别仓库中的技能目录（未找到 skills/ 或 SKILL.md）")
        root = subdirs[0]
        # 1) skills/ 目录
        skills_dir = root / "skills"
        if skills_dir.is_dir():
            return skills_dir
        # 2) 根目录 SKILL.md（单技能仓库）
        if (root / "SKILL.md").is_file():
            return root
        # 3) 根目录技能集合（有 SKILL.md 子目录或 .md 文件）
        if _looks_like_skill_collection(root):
            return root
        raise SkillInstallError(
            "仓库中没有找到合法技能（需要 skills/ 目录、根目录 SKILL.md 或技能子目录）"
        )

    @staticmethod
    def _flatten_skill_root(install_dir: Path, skill_root: Path) -> None:
        """把技能根内容上移到安装目录顶层（去除仓库/技能根层级）。"""
        if skill_root == install_dir:
            return
        for item in list(skill_root.iterdir()):
            if item.name.startswith(".") and item.name not in (_METADATA_FILE,):
                continue
            dest = install_dir / item.name
            if dest.exists():
                if dest.is_dir():
                    shutil.rmtree(dest, ignore_errors=True)
                else:
                    dest.unlink(missing_ok=True)
            shutil.move(str(item), str(dest))
        # 清理已搬空的源树（递归移除空目录壳）
        try:
            for item in list(install_dir.iterdir()):
                if item.is_dir() and not any(item.rglob("*")):
                    shutil.rmtree(item, ignore_errors=True)
        except OSError:
            pass

    # ── 卸载 / 查找 ──────────────────────────────────────

    def uninstall(self, target: str, on_change=None) -> bool:
        """卸载已安装的仓库。

        Args:
            target: ``owner/repo``、``owner__repo`` 或技能名。

        Returns:
            True 已卸载；False 未找到。
        """
        install_dir = self._find_install_dir(target)
        if install_dir is None:
            return False
        shutil.rmtree(install_dir, ignore_errors=True)
        if on_change is not None:
            try:
                on_change()
            except Exception:
                _logger.debug("卸载后 on_change 回调异常", exc_info=True)
        return True

    def _find_install_dir(self, target: str) -> Optional[Path]:
        """按 id / owner__repo / owner-repo 形态 / 技能名查找安装目录。"""
        if not self.installed_root.is_dir():
            return None
        text = target.strip()
        # 形态 1：owner/repo 或 owner__repo
        candidate = text.replace("/", "__")
        if _INSTALL_ID_RE.match(candidate):
            path = self.installed_root / candidate
            if path.is_dir():
                return path
        # 形态 2：技能名 → 扫描各安装目录的元数据 skills 列表
        try:
            for entry in sorted(self.installed_root.iterdir()):
                if entry.name.startswith(".") or not entry.is_dir():
                    continue
                meta = self.read_metadata(entry) or {}
                if text in meta.get("skills", []):
                    return entry
        except OSError:
            pass
        return None


def _looks_like_skill_collection(root: Path) -> bool:
    """根目录是否像技能集合（含 SKILL.md 子目录或 .md 文件）。"""
    try:
        for entry in root.iterdir():
            if entry.name.startswith("."):
                continue
            if entry.is_dir() and (entry / "SKILL.md").is_file():
                return True
            if entry.is_file() and entry.name.endswith(".md"):
                candidate = parse_skill_file(entry)
                if candidate is not None:
                    return True
    except OSError:
        return False
    return False


__all__ = [
    "GithubSkillInstaller",
    "GithubSpec",
    "SkillInstallError",
    "parse_github_spec",
]
