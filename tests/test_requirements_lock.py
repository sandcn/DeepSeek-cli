"""依赖锁定文件守卫 — requirements*.txt 与 pyproject 保持一致。

背景：仓库只有 ``pyproject.toml`` 的区间依赖（如 ``httpx>=0.27``），没有锁定
文件，同一份代码在不同时间安装到不同版本，构建不可复现；同时缺少「声明与
锁定不同步」的检查。

本测试固化：

1. ``requirements.txt`` / ``requirements-dev.txt`` 存在且可解析；
2. ``pyproject`` 的必需依赖全部出现在 ``requirements.txt``；
3. ``pyproject`` 的 dev 分组依赖全部出现在 ``requirements-dev.txt``；
4. 两个文件中的直接依赖都带版本约束（``==`` 或 ``>=``）；
5. Cygwin / MSYS2 上不可安装的依赖（``orjson`` / ``psutil`` / ``ruff``）在
   ``requirements*.txt`` 与 ``pyproject`` 中都带 ``sys_platform`` 标记——否则
   ``pip install -r requirements-dev.txt`` 在这些平台解析阶段即失败；
6. ``pre-commit`` 的锁定按 Python 版本分支，覆盖 3.9–3.13（4.4 起要求 ≥3.10、
   4.7 起要求 ≥3.11，单一 pin 会让 3.9 上「找不到可安装版本」）。
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


def _raw_requirement_lines(path: Path, _seen: set[Path] | None = None) -> list[str]:
    """返回依赖文件的原始依赖行（保留环境标记，跟随 ``-r`` 引用）。"""
    seen = _seen if _seen is not None else set()
    resolved = path.resolve()
    if resolved in seen:
        return []
    seen.add(resolved)

    lines: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("-r "):
            lines.extend(_raw_requirement_lines(path.parent / line[3:].strip(), seen))
            continue
        if line.startswith("-"):
            continue
        lines.append(line)
    return lines


def test_pillow_pin_matches_installed_pillow():
    """Pillow 锁定版本必须与当前解释器实际安装的版本一致。

    背景：``requirements.txt`` 曾锁 ``Pillow==8.4.0``——Pillow 8.4 只支持
    Python ≤3.10，在 Python 3.11 上既没有 wheel、源码构建又缺 zlib 头文件，
    ``pip install -r requirements-dev.txt`` 直接失败。锁定版本必须适配当前
    解释器（Pillow 12 起弃 Python 3.9、13 起弃 3.10），否则安装不可复现。
    """
    try:
        from packaging.requirements import Requirement
    except ModuleNotFoundError:  # pragma: no cover - 环境缺 packaging（pytest 传递依赖）
        pytest.skip("需要 packaging 解析依赖规格")

    from importlib.metadata import PackageNotFoundError, version as _dist_version

    try:
        installed = _dist_version("Pillow")
    except PackageNotFoundError:  # pragma: no cover - 本环境未安装 Pillow
        pytest.skip("当前环境未安装 Pillow")

    active: list[Requirement] = []
    for line in _raw_requirement_lines(_REQUIREMENTS):
        try:
            requirement = Requirement(line)
        except Exception:  # pragma: no cover - 非法依赖行由其它测试兜住
            continue
        if requirement.name.lower() != "pillow":
            continue
        if requirement.marker is not None and not requirement.marker.evaluate():
            continue
        active.append(requirement)

    assert active, "requirements.txt 缺少适配当前 Python 的 Pillow 锁定条目"
    mismatched = [
        str(requirement.specifier)
        for requirement in active
        if not requirement.specifier.contains(installed)
    ]
    assert not mismatched, (
        f"Pillow 锁定 {mismatched} 与实际安装 {installed} 不一致"
        "（当前 Python 下的锁定必须可直接安装）"
    )


# ── 平台标记守卫 ─────────────────────────────────────────────
#: Cygwin / MSYS2 上没有任何可安装发行版的依赖：orjson / ruff 是 Rust 扩展
#: （无该平台 wheel，maturin 报 "Unsupported platform: x86_64-cygwin"），
#: psutil 上游 setup.py 直接拒绝 cygwin（"platform cygwin is not supported"）。
#: 它们必须带 ``sys_platform`` 标记，否则本机 ``pip install -r requirements*.txt``
#: 在解析 / 构建阶段就会失败。
_CYGWIN_UNSUPPORTED = ("orjson", "psutil", "ruff")

#: pip 支持安装本项目的 Python 版本（与 pyproject 的 requires-python 及 CI 矩阵一致）。
_SUPPORTED_PYTHONS = ("3.9", "3.10", "3.11", "3.12", "3.13")


def _marker_of(requirement_line: str):
    """返回依赖行中的环境标记（无标记返回 ``None``）。"""
    if ";" not in requirement_line:
        return None
    try:
        from packaging.markers import Marker
    except ModuleNotFoundError:  # pragma: no cover - 环境缺 packaging（pytest 传递依赖）
        pytest.skip("需要 packaging 解析环境标记")
    return Marker(requirement_line.split(";", 1)[1].strip())


def test_cygwin_unsupported_deps_are_excluded_by_marker():
    """orjson / psutil / ruff 必须用 ``sys_platform`` 标记跳过 Cygwin / MSYS2。"""
    checked: set[str] = set()
    for path in (_REQUIREMENTS, _REQUIREMENTS_DEV):
        for line in _raw_requirement_lines(path):
            name = _requirement_name(line)
            if name not in _CYGWIN_UNSUPPORTED:
                continue
            marker = _marker_of(line)
            assert marker is not None, f"{path.name}: {line!r} 缺少平台标记（Cygwin 无法安装）"
            for platform in ("cygwin", "msys"):
                assert marker.evaluate({"sys_platform": platform}) is False, (
                    f"{path.name}: {line!r} 在 {platform} 上仍会被安装，pip 将直接失败"
                )
            for platform in ("linux", "win32", "darwin"):
                assert marker.evaluate({"sys_platform": platform}) is True, (
                    f"{path.name}: {line!r} 不该在 {platform} 上被跳过"
                )
            checked.add(name)
    assert checked == set(_CYGWIN_UNSUPPORTED), (
        f"依赖锁定文件缺少以下包: {sorted(set(_CYGWIN_UNSUPPORTED) - checked)}"
    )


def test_pyproject_marks_cygwin_unsupported_deps():
    """``pyproject`` 的同类依赖也必须带标记（``pip install .`` / ``.[dev]`` 同一路径）。"""
    project = _pyproject()["project"]
    groups = [project.get("dependencies", [])]
    groups.extend(project.get("optional-dependencies", {}).values())
    checked: set[str] = set()
    for deps in groups:
        for dep in deps:
            name = _requirement_name(dep)
            if name not in _CYGWIN_UNSUPPORTED:
                continue
            marker = _marker_of(dep)
            assert marker is not None, f"pyproject: {dep!r} 缺少平台标记（Cygwin 无法安装）"
            assert marker.evaluate({"sys_platform": "cygwin"}) is False, (
                f"pyproject: {dep!r} 在 cygwin 上仍会被安装，pip 将直接失败"
            )
            assert marker.evaluate({"sys_platform": "msys"}) is False, (
                f"pyproject: {dep!r} 在 msys 上仍会被安装，pip 将直接失败"
            )
            checked.add(name)
    assert checked == set(_CYGWIN_UNSUPPORTED), (
        f"pyproject 缺少以下包的平台标记: {sorted(set(_CYGWIN_UNSUPPORTED) - checked)}"
    )


def test_pre_commit_pin_covers_supported_python_versions():
    """pre-commit 锁定必须按 Python 版本分支，每个受支持版本恰好命中一条。"""
    lines = [
        line
        for line in _raw_requirement_lines(_REQUIREMENTS_DEV)
        if _requirement_name(line) == "pre-commit"
    ]
    assert lines, "requirements-dev.txt 缺少 pre-commit 锁定"
    for version in _SUPPORTED_PYTHONS:
        active = []
        for line in lines:
            marker = _marker_of(line)
            if marker is None or marker.evaluate({"python_version": version}):
                active.append(line)
        assert len(active) == 1, (
            f"Python {version} 下 pre-commit 锁定应恰好 1 条，实际 {len(active)} 条: {active}"
        )
