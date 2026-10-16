"""依赖锁定文件守卫 — requirements*.txt 与 pyproject 保持一致。

背景：仓库只有 ``pyproject.toml`` 的区间依赖（如 ``httpx>=0.27``），没有锁定
文件，同一份代码在不同时间安装到不同版本，构建不可复现；同时缺少「声明与
锁定不同步」的检查。

本测试固化：

1. ``requirements.txt`` / ``requirements-dev.txt`` 存在且可解析；
2. ``pyproject`` 的必需依赖全部出现在 ``requirements.txt``；
3. ``pyproject`` 的 dev 分组依赖全部出现在 ``requirements-dev.txt``；
4. 两个文件中的直接依赖都带版本约束（``==`` 或 ``>=``）。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests._paths import repo_root

_REPO_ROOT = repo_root()
_REQUIREMENTS = _REPO_ROOT / "requirements.txt"
_REQUIREMENTS_DEV = _REPO_ROOT / "requirements-dev.txt"
_PYPROJECT = _REPO_ROOT / "pyproject.toml"

try:
    import tomllib as _toml
except ModuleNotFoundError:  # pragma: no cover - Python 3.9/3.10
    try:
        import tomli as _toml  # type: ignore[no-redef]
    except ModuleNotFoundError:  # pragma: no cover
        _toml = None  # type: ignore[assignment]

_SPEC_RE = re.compile(r"^([A-Za-z0-9_.\-]+)\s*([=><~!]+)")


def _normalize(name: str) -> str:
    return name.strip().lower().replace("_", "-")


def _parse_requirements(path: Path, _seen: set[Path] | None = None) -> list[tuple[str, bool]]:
    """返回 ``[(规范化包名, 是否带版本约束)]``；忽略注释与空行，跟随 ``-r`` 引用。"""
    seen = _seen if _seen is not None else set()
    resolved = path.resolve()
    if resolved in seen:
        return []
    seen.add(resolved)

    entries: list[tuple[str, bool]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("-r "):
            entries.extend(_parse_requirements((path.parent / line[3:].strip()), seen))
            continue
        if line.startswith("-"):
            continue
        head = line.split(";", 1)[0].strip()
        match = _SPEC_RE.match(head)
        if match:
            entries.append((_normalize(match.group(1)), True))
        else:
            entries.append((_normalize(head.split("[", 1)[0]), False))
    return entries


def _pyproject() -> dict:
    if _toml is None:  # pragma: no cover
        pytest.skip("需要 tomllib / tomli")
    with open(_PYPROJECT, "rb") as handle:
        return _toml.load(handle)


def _requirement_name(raw: str) -> str:
    head = raw.split(";", 1)[0].strip()
    for sep in ("==", ">=", "<=", "~=", "!=", ">", "<", "[", " ", "("):
        head = head.split(sep, 1)[0]
    return _normalize(head)


def test_requirements_files_exist_and_parse():
    assert _REQUIREMENTS.is_file()
    assert _REQUIREMENTS_DEV.is_file()
    assert _parse_requirements(_REQUIREMENTS), "requirements.txt 未解析出任何依赖"
    assert _parse_requirements(_REQUIREMENTS_DEV)


def test_runtime_requirements_cover_pyproject_dependencies():
    declared = {_requirement_name(dep) for dep in _pyproject()["project"]["dependencies"]}
    locked = {name for name, _ in _parse_requirements(_REQUIREMENTS)}
    missing = declared - locked
    assert not missing, f"requirements.txt 缺少 pyproject 必需依赖: {sorted(missing)}"


def test_dev_requirements_cover_dev_group():
    dev = _pyproject()["project"]["optional-dependencies"]["dev"]
    declared = {_requirement_name(dep) for dep in dev}
    locked = {name for name, _ in _parse_requirements(_REQUIREMENTS_DEV)}
    missing = declared - locked
    assert not missing, f"requirements-dev.txt 缺少 dev 依赖: {sorted(missing)}"


def test_dev_requirements_include_runtime_file():
    text = _REQUIREMENTS_DEV.read_text(encoding="utf-8")
    assert "-r requirements.txt" in text, "开发依赖应复用运行时锁定文件"


def test_all_requirements_are_version_constrained():
    """直接依赖必须带版本约束（避免『锁了等于没锁』）。"""
    unconstrained = [
        name
        for name, constrained in _parse_requirements(_REQUIREMENTS)
        + _parse_requirements(_REQUIREMENTS_DEV)
        if not constrained
    ]
    assert not unconstrained, f"以下依赖缺少版本约束: {sorted(set(unconstrained))}"


def test_requirements_do_not_pin_platform_specific_markers_wrongly():
    """带环境标记的依赖必须保留标记（``; python_version < "3.11"`` 等）。"""
    text = _REQUIREMENTS.read_text(encoding="utf-8")
    assert 'python_version < "3.11"' in text
