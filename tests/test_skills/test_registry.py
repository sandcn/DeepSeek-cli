"""注册表测试 — ./.skills 单根布局、rank 裁决、缓存、运行时注册。"""

from pathlib import Path

import pytest

from src.skills import (
    SkillRegistry,
    is_model_invocable,
    is_user_invocable,
)
import src.skills.registry as registry_module


def _make_skill(root: Path, name: str, description: str, **extra) -> Path:
    """在技能根下创建目录包技能（extra 键为 frontmatter 字段名）。"""
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    lines = ["---", f"name: {name}", f"description: {description}"]
    for key, value in extra.items():
        lines.append(f"{key}: {value}")
    lines.append("---")
    lines.append(f"# {name} 正文")
    (skill_dir / "SKILL.md").write_text("\n".join(lines), encoding="utf-8")
    return skill_dir


def _make_flat(root: Path, name: str, description: str) -> Path:
    path = root / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n# {name}\n",
        encoding="utf-8",
    )
    return path


def _project(tmp_path: Path) -> Path:
    proj = tmp_path / "proj"
    (proj / ".git").mkdir(parents=True)
    return proj


@pytest.fixture
def registry():
    return SkillRegistry()


def test_merge_dir_and_flat_skills(registry, tmp_path):
    proj = _project(tmp_path)
    _make_skill(proj / ".skills", "dir-skill", "目录包技能")
    _make_flat(proj / ".skills", "flat-skill", "扁平技能")
    names = {s.name for s in registry.list(cwd=str(proj))}
    assert names == {"dir-skill", "flat-skill"}
    assert registry.get("dir-skill", cwd=str(proj)).content.startswith("# dir-skill")


def test_skills_only_under_dot_skills(registry, tmp_path):
    """技能只从 ./.skills 发现：.dsh/skills、.agents/skills、~/.chat_config/skills 均忽略。"""
    proj = _project(tmp_path)
    _make_skill(proj / ".dsh" / "skills", "dsh-skill", "不应被发现")
    _make_skill(proj / ".agents" / "skills", "agents-skill", "不应被发现")
    _make_skill(tmp_path / "home" / ".chat_config" / "skills", "user-skill", "不应被发现")
    assert registry.list(cwd=str(proj)) == []


def test_rank_shadowing_project_wins_over_installed(registry, tmp_path):
    proj = _project(tmp_path)
    _make_skill(proj / ".skills", "same-name", "项目级版本")
    _make_skill(proj / ".skills" / "installed" / "o__r", "same-name", "安装版")
    skill = registry.get("same-name", cwd=str(proj))
    assert skill.description == "项目级版本"
    assert skill.source == "project"

    # 移除项目级后，安装版胜出
    import shutil
    shutil.rmtree(proj / ".skills" / "same-name")
    skill = registry.get("same-name", cwd=str(proj))
    assert skill.source == "github"
    assert skill.rank == 200


def test_flat_shadowed_by_dir(registry, tmp_path):
    proj = _project(tmp_path)
    root = proj / ".skills"
    _make_skill(root, "dup", "目录版")
    _make_flat(root, "dup", "扁平版")
    assert registry.get("dup", cwd=str(proj)).description == "目录版"


def test_runtime_register_and_first_wins(registry, tmp_path):
    proj = _project(tmp_path)
    assert registry.register("runtime-skill", "运行时技能", "正文")
    assert registry.register("runtime-skill", "重复注册", "应被忽略") is False
    skill = registry.get("runtime-skill", cwd=str(proj))
    assert skill is not None and skill.description == "运行时技能"
    assert skill.provider == "runtime"


def test_runtime_register_validation(registry):
    with pytest.raises(ValueError):
        registry.register("Bad Name", "d", "c")
    with pytest.raises(ValueError):
        registry.register("ok-name", "", "c")


def test_invocation_policy_from_frontmatter(registry, tmp_path):
    proj = _project(tmp_path)
    _make_skill(proj / ".skills", "model-only", "仅模型",
                **{"user-invocable": "false"})
    _make_skill(proj / ".skills", "user-only", "仅用户",
                **{"disable-model-invocation": "true"})
    model_only = registry.get("model-only", cwd=str(proj))
    user_only = registry.get("user-only", cwd=str(proj))
    assert is_model_invocable(model_only) and not is_user_invocable(model_only)
    assert not is_model_invocable(user_only) and is_user_invocable(user_only)


def test_mtime_cache_and_invalidate(registry, tmp_path):
    proj = _project(tmp_path)
    skill_file = _make_skill(proj / ".skills", "live", "版本一")
    assert registry.get("live", cwd=str(proj)).description == "版本一"

    # 修改文件 → mtime 变化 → get 重读正文（无需 invalidate）
    skill_file.joinpath("SKILL.md").write_text(
        "---\nname: live\ndescription: 版本二\n---\n新正文\n", encoding="utf-8"
    )
    assert registry.get("live", cwd=str(proj)).description == "版本二"

    # 新增技能 → 目录 mtime 变化 → list 自动发现
    _make_flat(proj / ".skills", "fresh", "新技能")
    names = {s.name for s in registry.list(cwd=str(proj))}
    assert "fresh" in names

    # 显式 invalidate 后仍可读
    registry.invalidate()
    assert registry.get("live", cwd=str(proj)) is not None


def test_enabled_false_disables_discovery(registry, tmp_path, monkeypatch):
    proj = _project(tmp_path)
    _make_skill(proj / ".skills", "hidden", "被禁用")
    monkeypatch.setattr(registry_module, "get_rc", lambda: {"skills": {"enabled": False}})
    assert registry.list(cwd=str(proj)) == []
    assert registry.get("hidden", cwd=str(proj)) is None


def test_auto_load_names_from_config(registry, monkeypatch):
    """auto_load 配置读取：kebab-case 过滤 + 去重。"""
    monkeypatch.setattr(
        registry_module, "get_rc",
        lambda: {"skills": {"auto_load": ["pdf", "pdf", "Bad Name", "docx", 42]}},
    )
    assert registry.auto_load_names() == ["pdf", "docx"]
    assert registry.auto_load_names() == registry.auto_load_names()  # 稳定


def test_auto_load_skills_resolves_definitions(registry, tmp_path, monkeypatch):
    """auto_load_skills 只返回存在且模型可调用的技能。"""
    proj = _project(tmp_path)
    _make_skill(proj / ".skills", "alpha", "技能 A")
    _make_skill(proj / ".skills", "beta", "仅用户",
                **{"disable-model-invocation": "true"})
    monkeypatch.setattr(
        registry_module, "get_rc",
        lambda: {"skills": {"auto_load": ["alpha", "beta", "missing"]}},
    )
    skills = registry.auto_load_skills(cwd=str(proj))
    assert [s.name for s in skills] == ["alpha"]


def test_catalog_entries_filter_and_truncate(registry, tmp_path):
    proj = _project(tmp_path)
    _make_skill(proj / ".skills", "long-desc", "x" * 800)
    _make_skill(proj / ".skills", "user-only", "仅用户",
                **{"disable-model-invocation": "true"})
    entries = registry.catalog_entries(cwd=str(proj), max_length=100)
    names = {name for name, _ in entries}
    assert names == {"long-desc"}  # user-only 被过滤
    desc = dict(entries)["long-desc"]
    assert len(desc) <= 100 and desc.endswith("...")


def test_list_sorted_by_name(registry, tmp_path):
    proj = _project(tmp_path)
    root = proj / ".skills"
    for name in ("zeta", "alpha", "mike"):
        _make_skill(root, name, f"技能{name}")
    names = [s.name for s in registry.list(cwd=str(proj))]
    assert names == sorted(names)


def test_invalid_frontmatter_skipped(registry, tmp_path):
    proj = _project(tmp_path)
    root = proj / ".skills"
    bad = root / "bad" / "SKILL.md"
    bad.parent.mkdir(parents=True)
    bad.write_text("---\nname: bad\n---\n缺 description\n", encoding="utf-8")
    not_md = root / "bad2" / "SKILL.md"
    not_md.parent.mkdir(parents=True)
    not_md.write_text("# 没有 frontmatter\n", encoding="utf-8")
    assert registry.list(cwd=str(proj)) == []
    assert registry.get("bad", cwd=str(proj)) is None


def test_installed_dir_hidden_entries_skipped(registry, tmp_path):
    proj = _project(tmp_path)
    installed = proj / ".skills" / "installed" / "o__r"
    _make_skill(installed, "real", "真实技能")
    (installed / ".hidden-skill").mkdir(exist_ok=True)
    (installed / ".hidden-skill" / "SKILL.md").write_text(
        "---\nname: hidden-skill\ndescription: 应被跳过\n---\n", encoding="utf-8"
    )
    names = {s.name for s in registry.list(cwd=str(proj))}
    assert names == {"real"}


def test_project_root_walks_up_to_git(registry, tmp_path):
    proj = _project(tmp_path)
    subdir = proj / "src" / "nested"
    subdir.mkdir(parents=True)
    _make_skill(proj / ".skills", "top-skill", "git 根技能")
    # 从子目录发现 git 根 .skills
    names = {s.name for s in registry.list(cwd=str(subdir))}
    assert names == {"top-skill"}
    assert registry.skills_dir(str(subdir)) == proj / ".skills"
