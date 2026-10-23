"""类型检查基线守卫 — ``src/tui/ink`` 保持 0 个 mypy error。

背景（``pyproject.toml`` 的 ``[tool.mypy]`` 注释）：历史基线文件随 2026-10-23
测试集删除而消失，而 ``.github/workflows/ci.yml`` 仍在执行本文件——导致 CI
的「类型基线」步骤实际上跑的是一个不存在的文件。本文件是**重建的等价闸门**：

  - 只对 ``src/tui/ink``（框架层，已达成 0 error）做**零容忍**断言；
  - 其余模块的历史错误不在本闸门断言范围内（收敛路径见 pyproject 注释）；
  - mypy 未安装 / 未显式启用时 skip——本地快速测试路径不强制重型依赖，
    CI 经 ``RUN_MYPY_BASELINE=1`` 显式启用（与 ci.yml 的 env 一致）。

设计取舍：不用「错误总数不超过 N」的基线文件（历史上正是这种可下调的基线
掩盖了新增错误），改用**目录级零错误**——越界即失败，无法靠改数字放行。
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
#: 零容忍目录（框架层；已达成 0 error，新增错误必须修复而非下调门槛）。
_STRICT_PATHS = ("src/tui/ink",)


def _mypy_available() -> bool:
    return importlib.util.find_spec("mypy") is not None


def _run_mypy(*paths: str) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, "-m", "mypy", *paths],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
    )
    return proc.returncode, proc.stdout + proc.stderr


def _error_lines(output: str) -> list[str]:
    return [line for line in output.splitlines() if ": error:" in line]


def test_ink_framework_has_zero_mypy_errors():
    """``src/tui/ink`` 的 mypy 错误数必须为 0（无基线文件可下调）。"""
    if not _mypy_available():
        pytest.skip("mypy 未安装（CI 经 RUN_MYPY_BASELINE=1 启用）")
    if os.environ.get("RUN_MYPY_BASELINE") != "1":
        pytest.skip("默认跳过重型类型检查；CI 以 RUN_MYPY_BASELINE=1 执行")

    code, output = _run_mypy(*_STRICT_PATHS)
    errors = _error_lines(output)
    assert not errors, (
        f"{_STRICT_PATHS} 出现 {len(errors)} 个 mypy error（门槛为 0）：\n"
        + "\n".join(errors[:40])
    )
    assert code == 0, output


def test_mypy_config_targets_python313():
    """``pyproject.toml`` 的 mypy python_version 与注释约定一致（防静默漂移）。"""
    config = (_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'python_version = "3.13"' in config
    # 严格目录门禁的依据：pyproject 必须仍记录「ink 0 error」约定，
    # 避免有人改了约定却留下失效的门禁测试。
    assert "src/tui/ink" in config and "0 个 mypy error" in config
