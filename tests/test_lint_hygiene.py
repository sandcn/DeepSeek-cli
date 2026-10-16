"""静态检查卫生守卫 — ``src`` / ``tests`` 零 Pyflakes 级缺陷（F 组 + E9）。

背景：项目长期「配置了 ruff / pre-commit 但无任何自动化执行」，未使用导入
（F401）、未定义名（F821）、未使用局部变量（F841）、无效全局声明等**真实
缺陷**只能靠人工发现。CI（``ruff check --select F,E9 .``）与 pre-commit 钩子
已把它们列为阻塞项（配置自洽性见 ``tests/test_ci_config.py``）；本文件在测试
套件内再加一道**本地**闸门，使「没跑 CI 的本地开发」与 CI 结论一致：

  - 优先用 **ruff**（与 CI 同源，支持 ``# noqa`` 抑制）；
  - ruff 不可用时回退 **pyflakes**（规则集等价且更严格：不识别 ``# noqa``，
    因此 ``src`` / ``tests`` 内不得保留 ``# noqa: F401`` 一类 F 组抑制——
    需要「副作用导入」处一律改用 ``importlib.import_module("…")`` 显式表达）；
  - 两者都不可用时跳过（不为测试引入新的强制依赖）。

覆盖范围与 pre-commit 钩子的 ``files`` 一致（``src`` + ``tests``）。
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests._paths import repo_root

_REPO_ROOT = repo_root()
_LINT_TARGETS = ("src", "tests")

#: 工具输出里属于「进度 / 统计 / 提示」而非缺陷的行前缀
_NOISE_PREFIXES = (
    "All checks passed",
    "Found ",
    "No issues found",
    "warning:",
    "note:",
    "help:",
)


def _tool_command(module: str, argv: list[str]) -> list[str] | None:
    """构造工具命令：优先控制台脚本，其次 ``python -m``，都不可用返回 None。"""
    if shutil.which(module) is not None:
        return [module, *argv]
    if importlib.util.find_spec(module) is not None:
        return [sys.executable, "-m", module, *argv]
    return None


def _lint_command() -> tuple[str, list[str]] | None:
    """返回 (工具名, 命令)；ruff 优先，其次 pyflakes，均无则 None。"""
    ruff = _tool_command("ruff", ["check", "--select", "F,E9", *_LINT_TARGETS])
    if ruff is not None:
        return "ruff", ruff
    pyflakes = _tool_command("pyflakes", list(_LINT_TARGETS))
    if pyflakes is not None:
        return "pyflakes", pyflakes
    return None


def _findings(output: str) -> list[str]:
    """从工具输出中筛出缺陷行（丢弃噪音行与缓存目录噪音）。"""
    findings: list[str] = []
    for raw in output.splitlines():
        line = raw.strip()
        if not line or "__pycache__" in line:
            continue
        if line.startswith(_NOISE_PREFIXES):
            continue
        findings.append(line)
    return findings


def _run(command: list[str], *extra: str) -> tuple[int, list[str]]:
    proc = subprocess.run(
        [*command, *extra], cwd=str(_REPO_ROOT), capture_output=True, text=True,
    )
    return proc.returncode, _findings(proc.stdout + proc.stderr)


def test_src_and_tests_have_no_pyflakes_level_findings():
    """``src`` 与 ``tests`` 不得有 F 组（Pyflakes）/ E9（语法）级缺陷。"""
    tool = _lint_command()
    if tool is None:
        pytest.skip("ruff 与 pyflakes 均不可用")
    name, command = tool
    _, findings = _run(command)
    assert not findings, (
        f"{name} 在 {_LINT_TARGETS} 报告 {len(findings)} 处缺陷：\n"
        + "\n".join(findings[:40])
    )


def test_linter_actually_reports_planted_defects(tmp_path: Path):
    """自检：所选工具确实在检查（能检出植入缺陷），避免路径写错导致静默通过。"""
    tool = _lint_command()
    if tool is None:
        pytest.skip("ruff 与 pyflakes 均不可用")
    name, command = tool
    planted = tmp_path / "planted_defect.py"
    planted.write_text("import os\n\n\nprint('hi')\n", encoding="utf-8")
    code, findings = _run(command, str(planted))
    assert code != 0 and findings, f"{name} 未检出植入的未使用导入（输出为空）"


def test_lint_scope_matches_precommit_hook():
    """守卫范围与 pre-commit 钩子一致（同选 ``F,E9``、同覆盖 ``src`` + ``tests``）。"""
    hooks = (_REPO_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    assert "F,E9" in hooks
    assert "^(src|tests)/" in hooks
