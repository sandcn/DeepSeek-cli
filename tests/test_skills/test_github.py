"""GitHub 安装器测试 — spec 解析、tarball 安装/更新/卸载、安全解压。"""

import io
import tarfile

import httpx
import pytest

from src.skills.github import (
    GithubSkillInstaller,
    SkillInstallError,
    _resolve_commit_from_url,
    parse_github_spec,
)
from src.skills.registry import SkillRegistry


def make_tarball(files) -> bytes:
    """把 {相对路径: 文本内容} 打包为 tar.gz bytes。"""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, content in files.items():
            data = content.encode("utf-8")
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def make_member_tar(members) -> bytes:
    """按原始 TarInfo 列表打包（用于构造恶意成员）。"""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for info in members:
            tar.addfile(info)
    return buf.getvalue()


def _skill_md(name: str, description: str) -> str:
    return f"---\nname: {name}\ndescription: {description}\n---\n# {name} 正文\n"


class _RepoTransport(httpx.BaseTransport):
    """按 URL 分支返回 tarball 的测试传输层。"""

    def __init__(self, tarballs, status=200, archive=None):
        self.tarballs = tarballs  # {"owner/repo/ref": bytes}
        self.status = status
        self.archive = archive  # 覆盖 tarball（用于恶意成员测试）
        self.requests = []

    def _respond(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(str(request.url))
        if self.status != 200:
            return httpx.Response(self.status, request=request)
        path = request.url.path.strip("/")
        # path 形如 owner/repo/tar.gz/ref
        parts = path.split("/")
        lookup = "/".join(parts[:2]) + "/" + "/".join(parts[3:])
        content = self.archive if self.archive is not None else self.tarballs.get(lookup)
        if content is None:
            return httpx.Response(404, request=request)
        return httpx.Response(200, content=content, request=request)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        return self._respond(request)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return self._respond(request)


def _client(transport) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=transport, follow_redirects=True)


@pytest.fixture
def install_root(tmp_path):
    root = tmp_path / "proj" / ".skills" / "installed"
    root.mkdir(parents=True)
    return root


def _project_of(install_root):
    """从安装根反推项目根（.skills/installed → 项目根）。"""
    return install_root.parent.parent


# ── spec 解析 ───────────────────────────────────────────

def test_parse_spec_forms():
    assert parse_github_spec("owner/repo").ref == "HEAD"
    s = parse_github_spec("owner/repo@main")
    assert (s.owner, s.repo, s.ref) == ("owner", "repo", "main")
    s = parse_github_spec("https://github.com/a/b/tree/dev")
    assert (s.owner, s.repo, s.ref) == ("a", "b", "dev")
    s = parse_github_spec("https://github.com/a/b.git")
    assert (s.owner, s.repo) == ("a", "b")
    assert parse_github_spec("o/r@v1.2.3").ref == "v1.2.3"
    assert parse_github_spec("o/r").install_id == "o__r"


@pytest.mark.parametrize("bad", ["", "not a spec", "onlyowner", "http://other.com/x/y",
                                 "git@github.com:o/r.git"])
def test_parse_spec_rejects(bad):
    with pytest.raises(SkillInstallError):
        parse_github_spec(bad)


def test_resolve_commit_from_url():
    assert _resolve_commit_from_url(
        "https://codeload.github.com/o/r/tar.gz/0123456789abcdef0123456789abcdef01234567"
    ) == "0123456789abcdef0123456789abcdef01234567"
    assert _resolve_commit_from_url("https://codeload.github.com/o/r/tar.gz/main") is None


# ── 安装 ────────────────────────────────────────────────

async def test_install_skills_layout(install_root):
    """仓库含 skills/ 目录：只安装技能内容 + 写元数据。"""
    tar = make_tarball({
        "repo/skills/alpha/SKILL.md": _skill_md("alpha", "技能 A"),
        "repo/skills/beta.md": _skill_md("beta", "技能 B"),
        "repo/README.md": "# 仓库说明（不应安装）",
    })
    client = _client(_RepoTransport({"o/r/HEAD": tar}))
    installer = GithubSkillInstaller(installed_root=install_root, http_client=client)

    result = await installer.install("o/r")
    assert result["skills"] == ["alpha", "beta"]
    target = install_root / "o__r"
    assert (target / "alpha" / "SKILL.md").is_file()
    assert (target / "beta.md").is_file()
    assert not (target / "README.md").exists()
    meta = installer.read_metadata(target)
    assert meta["owner"] == "o" and meta["repo"] == "r"
    assert meta["ref"] == "HEAD"
    assert meta["skills"] == ["alpha", "beta"]

    # 注册表能从 ./.skills/installed 发现安装的技能
    registry = SkillRegistry()
    proj = _project_of(install_root)
    (proj / ".git").mkdir(exist_ok=True)
    skill = registry.get("alpha", cwd=str(proj))
    assert skill is not None and skill.source == "github" and skill.rank == 200


async def test_install_single_skill_repo(install_root):
    """仓库根即技能（SKILL.md at root）。"""
    tar = make_tarball({
        "repo/SKILL.md": _skill_md("solo", "单技能仓库"),
        "repo/assets/logo.png": "not-a-real-png",
    })
    client = _client(_RepoTransport({"o/r/HEAD": tar}))
    installer = GithubSkillInstaller(installed_root=install_root, http_client=client)
    result = await installer.install("o/r")
    assert result["skills"] == ["solo"]
    target = install_root / "o__r"
    assert (target / "SKILL.md").is_file()
    assert (target / "assets" / "logo.png").is_file()  # 资源保留


async def test_install_collection_at_root(install_root):
    """仓库根即技能集合（子目录 SKILL.md）。"""
    tar = make_tarball({
        "repo/one/SKILL.md": _skill_md("one", "技能一"),
        "repo/two/SKILL.md": _skill_md("two", "技能二"),
    })
    client = _client(_RepoTransport({"o/r/HEAD": tar}))
    installer = GithubSkillInstaller(installed_root=install_root, http_client=client)
    result = await installer.install("o/r")
    assert result["skills"] == ["one", "two"]


async def test_install_commit_from_redirect(install_root):
    """codeload 重定向到 commit URL 时记录 commit SHA。"""
    commit = "a" * 40
    tar = make_tarball({"repo/skills/x/SKILL.md": _skill_md("x", "X")})

    def handler(request):
        if "tar.gz/HEAD" in str(request.url):
            return httpx.Response(
                302,
                headers={"location": f"https://codeload.github.com/o/r/tar.gz/{commit}"},
                request=request,
            )
        return httpx.Response(200, content=tar, request=request)

    installer = GithubSkillInstaller(
        installed_root=install_root,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True),
    )
    result = await installer.install("o/r")
    assert result["commit"] == commit


async def test_install_http_error(install_root):
    client = _client(_RepoTransport({}, status=404))
    installer = GithubSkillInstaller(installed_root=install_root, http_client=client)
    with pytest.raises(SkillInstallError, match="404"):
        await installer.install("o/r")
    assert not (install_root / "o__r").exists()


async def test_install_no_skills_rejected(install_root):
    tar = make_tarball({"repo/README.md": "# 没有技能"})
    client = _client(_RepoTransport({"o/r/HEAD": tar}))
    installer = GithubSkillInstaller(installed_root=install_root, http_client=client)
    with pytest.raises(SkillInstallError, match="没有找到合法技能"):
        await installer.install("o/r")


async def test_install_path_traversal_rejected(install_root):
    """恶意 tar 成员（../ 穿越）必须被拒绝。"""
    evil = tarfile.TarInfo("../evil.txt")
    evil.size = 4
    tar = make_member_tar([evil])
    client = _client(_RepoTransport({"o/r/HEAD": tar}))
    installer = GithubSkillInstaller(installed_root=install_root, http_client=client)
    with pytest.raises(SkillInstallError, match="非法路径|不安全"):
        await installer.install("o/r")


async def test_install_symlink_rejected(install_root):
    """符号链接成员必须被拒绝。"""
    link = tarfile.TarInfo("repo/skills/x/SKILL.md")
    link.type = tarfile.SYMTYPE
    link.linkname = "/etc/passwd"
    tar = make_member_tar([link])
    client = _client(_RepoTransport({"o/r/HEAD": tar}))
    installer = GithubSkillInstaller(installed_root=install_root, http_client=client)
    with pytest.raises(SkillInstallError, match="不安全"):
        await installer.install("o/r")


async def test_install_absolute_path_rejected(install_root):
    abs_member = tarfile.TarInfo("/etc/evil")
    abs_member.size = 1
    tar = make_member_tar([abs_member])
    client = _client(_RepoTransport({"o/r/HEAD": tar}))
    installer = GithubSkillInstaller(installed_root=install_root, http_client=client)
    with pytest.raises(SkillInstallError, match="非法路径"):
        await installer.install("o/r")


# ── 更新 / 卸载 / 列举 ─────────────────────────────────

async def test_update_reuses_existing_ref(install_root):
    """update 无 ref 时沿用已安装 ref。"""
    tar_main = make_tarball({"repo/skills/x/SKILL.md": _skill_md("x", "main 版")})
    tar_dev = make_tarball({"repo/skills/x/SKILL.md": _skill_md("x", "dev 版")})
    transport = _RepoTransport({
        "o/r/main": tar_main,
        "o/r/dev": tar_dev,
    })
    client = _client(transport)
    installer = GithubSkillInstaller(installed_root=install_root, http_client=client)

    result = await installer.install("o/r@main")
    assert result["ref"] == "main"

    result2 = await installer.install("o/r", reuse_ref=True)
    assert result2["ref"] == "main"
    # 两次请求都应命中 main 的 tarball
    assert any("tar.gz/main" in url for url in transport.requests)

    # 显式指定新 ref 则覆盖
    result3 = await installer.install("o/r@dev", reuse_ref=True)
    assert result3["ref"] == "dev"


async def test_uninstall_by_id_and_skill_name(install_root):
    tar = make_tarball({"repo/skills/x/SKILL.md": _skill_md("x", "X")})
    installer = GithubSkillInstaller(
        installed_root=install_root,
        http_client=_client(_RepoTransport({"o/r/HEAD": tar})),
    )
    await installer.install("o/r")
    assert (install_root / "o__r").is_dir()

    assert installer.uninstall("o__r") is True
    assert not (install_root / "o__r").exists()
    assert installer.uninstall("o__r") is False


async def test_uninstall_by_skill_name(install_root):
    tar = make_tarball({"repo/skills/known-skill/SKILL.md": _skill_md("known-skill", "K")})
    installer = GithubSkillInstaller(
        installed_root=install_root,
        http_client=_client(_RepoTransport({"o/r/HEAD": tar})),
    )
    await installer.install("o/r")
    assert installer.uninstall("known-skill") is True


async def test_list_installed(install_root):
    tar = make_tarball({"repo/skills/x/SKILL.md": _skill_md("x", "X")})
    installer = GithubSkillInstaller(
        installed_root=install_root,
        http_client=_client(_RepoTransport({"o/r/HEAD": tar})),
    )
    await installer.install("o/r")
    listed = installer.list_installed()
    assert len(listed) == 1
    assert listed[0]["id"] == "o__r"
    assert listed[0]["skills"] == ["x"]
