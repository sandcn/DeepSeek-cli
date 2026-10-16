"""工程基建守卫 — CI / pre-commit / 质量门槛配置存在且自洽。

背景：项目长期「配置了 ruff / mypy 但无任何自动化执行」，导致未使用导入、
类型错误只能靠人工发现。本测试固化：

1. ``.github/workflows/ci.yml`` 存在、可解析，且包含 lint（ruff）、类型基线、
   全量测试与覆盖率四类步骤；
2. ``.pre-commit-config.yaml`` 存在、可解析，且 ruff 钩子选择与 CI 一致（F,E9）；
3. ``pyproject.toml`` 的覆盖率门槛不低于 60（防止门槛回退）；
4. ``[tool.mypy]`` 的检查范围覆盖 ``src/``。
"""

from __future__ import annotations

from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CI_FILE = _REPO_ROOT / ".github" / "workflows" / "ci.yml"
_PRECOMMIT_FILE = _REPO_ROOT / ".pre-commit-config.yaml"
_PYPROJECT = _REPO_ROOT / "pyproject.toml"

try:
    import tomllib as _toml
except ModuleNotFoundError:  # pragma: no cover - Python 3.9/3.10
    try:
        import tomli as _toml  # type: ignore[no-redef]
    except ModuleNotFoundError:  # pragma: no cover
        _toml = None  # type: ignore[assignment]


def _read(path: Path) -> str:
    assert path.is_file(), f"缺少必要文件: {path.relative_to(_REPO_ROOT)}"
    return path.read_text(encoding="utf-8")


def test_ci_workflow_exists_and_has_required_steps():
    text = _read(_CI_FILE)
    assert "actions/checkout" in text
    assert "actions/setup-python" in text
    assert "ruff" in text, "CI 缺少 ruff 检查步骤"
    assert "mypy" in text, "CI 缺少 mypy 类型基线步骤"
    assert "pytest" in text, "CI 缺少测试步骤"
    assert "--cov=src" in text, "CI 缺少覆盖率采集"
    # 与 requires-python 对齐：至少覆盖最低支持版本
    assert '"3.9"' in text, "CI 矩阵未覆盖最低支持版本 3.9"


def test_ci_uses_the_same_ruff_selection_as_precommit():
    ci_text = _read(_CI_FILE)
    hook_text = _read(_PRECOMMIT_FILE)
    assert "--select F,E9" in ci_text
    assert "F,E9" in hook_text


def test_precommit_config_has_ruff_and_safety_hooks():
    text = _read(_PRECOMMIT_FILE)
    assert "astral-sh/ruff-pre-commit" in text
    assert "ruff-format" in text
    assert "check-added-large-files" in text, "缺少大文件阻断（防 4MB+ 二进制入库）"


def test_ci_separates_correctness_from_coverage():
    """CI 必须把「完整套件（含性能预算）」与「覆盖率步骤」分开。

    coverage 的逐行插桩会让时间预算型用例失真（实测：插桩下
    ``test_stream_english_inline_perf`` 的预算断言失败，无插桩则通过）。
    因此 CI 需要：一个**不带 --cov** 的完整套件步骤 + 一个带 --cov 的覆盖率步骤。
    """
    text = _read(_CI_FILE)
    import re

    pytest_steps = re.findall(r"run: (python -m pytest[^\n]*)", text)
    assert len(pytest_steps) >= 2, f"CI 的 pytest 步骤少于两步: {pytest_steps}"
    without_cov = [s for s in pytest_steps if "--cov" not in s]
    with_cov = [s for s in pytest_steps if "--cov" in s]
    assert without_cov, "CI 缺少不带覆盖率的完整套件步骤（性能预算需在无插桩下验证）"
    assert with_cov, "CI 缺少覆盖率步骤"
    assert any("tests" in s for s in without_cov)


def test_coverage_mode_skips_timing_budget_tests():
    """覆盖率插桩模式下必须跳过时间预算型用例（防 CI 覆盖率步骤伪失败）。"""
    conftest = (_REPO_ROOT / "tests" / "conftest.py").read_text(encoding="utf-8")
    assert "_skip_timing_budget_when_measuring_coverage" in conftest
    assert "cov_source" in conftest, "跳过条件应基于显式 --cov（而非 coverage 是否已导入）"


def test_coverage_threshold_not_lowered():
    if _toml is None:  # pragma: no cover
        pytest.skip("需要 tomllib / tomli")
    with open(_PYPROJECT, "rb") as handle:
        data = _toml.load(handle)
    fail_under = data["tool"]["coverage"]["report"]["fail_under"]
    assert fail_under >= 60, f"覆盖率门槛过低: {fail_under}"


def test_mypy_scope_covers_src():
    if _toml is None:  # pragma: no cover
        pytest.skip("需要 tomllib / tomli")
    with open(_PYPROJECT, "rb") as handle:
        data = _toml.load(handle)
    files = data["tool"]["mypy"]["files"]
    assert any(f.rstrip("/") == "src" for f in files), f"mypy 检查范围未覆盖 src: {files}"


def test_pyproject_declares_lint_selection_and_dev_tools():
    if _toml is None:  # pragma: no cover
        pytest.skip("需要 tomllib / tomli")
    with open(_PYPROJECT, "rb") as handle:
        data = _toml.load(handle)
    select = data["tool"]["ruff"]["lint"]["select"]
    assert "F" in select and "E" in select
    dev = " ".join(data["project"]["optional-dependencies"]["dev"]).lower()
    for tool in ("ruff", "mypy", "pytest", "pre-commit"):
        assert tool in dev, f"dev 依赖缺少 {tool}"
