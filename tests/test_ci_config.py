"""CI / pre-commit 配置自洽守卫。

背景：2026-10-23 的 ``chore(tests): 删除 tests 目录`` 之后，``.github/workflows/
ci.yml`` 仍在执行 ``tests/test_type_baseline.py`` 并引用 ``tests/conftest.py``，
两者都已不存在——CI 的类型基线步骤变成一个必然失败的空转步骤，而本地全量
测试完全无感（因为 pytest 只收集现存文件）。

本文件把「CI 引用的测试路径必须真实存在」变成可执行断言，防止同类问题复发；
同时校验 CI 与 pre-commit 的 ruff 选择集一致（两处口径分裂会导致「本地绿、
CI 红」）。
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _ci_config() -> str:
    return (_REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")


def _precommit_config() -> str:
    return (_REPO_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")


def test_ci_references_only_existing_test_paths():
    """``ci.yml`` 中出现的 ``tests/...py`` 路径必须全部存在。"""
    referenced = sorted(set(re.findall(r"tests/[\w./-]+\.py", _ci_config())))
    assert referenced, "ci.yml 未引用任何测试文件（配置可能被改坏）"
    missing = [path for path in referenced if not (_REPO_ROOT / path).exists()]
    assert not missing, f"ci.yml 引用了不存在的测试路径：{missing}"


def test_ci_references_existing_excludes():
    """``ci.yml`` 中 ``--cov=...`` / ``-o addopts`` 等引用的配置项仍有效。"""
    config = _ci_config()
    assert "--cov=src" in config
    # 覆盖率门槛由 pyproject 提供，CI 依赖它生效；两者必须同时存在。
    pyproject = (_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "[tool.coverage.report]" in pyproject
    assert "fail_under" in pyproject


def test_ci_and_precommit_agree_on_ruff_select():
    """CI 的阻塞 ruff 选择集与 pre-commit 一致（``F,E9``）。"""
    assert "--select F,E9" in _ci_config()
    assert '["--select", "F,E9"]' in _precommit_config() or "F,E9" in _precommit_config()


def test_ci_runs_tests_without_coverage_first():
    """CI 必须先跑「不带 coverage」的全量测试（时间预算用例需要无插桩环境）。"""
    config = _ci_config()
    plain = config.find("run: python -m pytest tests\n")
    covered = config.find("--cov=src")
    assert plain != -1, "ci.yml 缺少不带 --cov 的全量测试步骤"
    assert covered != -1 and plain < covered, "覆盖率先于无插桩全量测试执行（顺序颠倒）"


def test_type_baseline_gate_is_wired():
    """类型基线的启用开关（``RUN_MYPY_BASELINE``）在 CI 与测试中同时存在。"""
    assert "RUN_MYPY_BASELINE" in _ci_config()
    baseline = (_REPO_ROOT / "tests/test_type_baseline.py").read_text(encoding="utf-8")
    assert "RUN_MYPY_BASELINE" in baseline
