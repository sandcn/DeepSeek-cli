"""版本控制信息 — 检测 Git 仓库、分支和状态"""

from __future__ import annotations

import os
import subprocess


from .env_info import _resolve_cwd

_GIT_TIMEOUT = 2


def check_version_control(cwd: str | None = None) -> str:
    cwd = _resolve_cwd(cwd)
    info, _ = _build_vcs_info(cwd)
    return info


def _build_vcs_info(cwd: str) -> tuple:
    has_git, info_line = _detect_git_root(cwd)
    if not has_git:
        return "# 版本控制\n未检测到版本控制系统\n\n", False

    branch_line, status_line = _get_branch_and_status(cwd)
    info = info_line + branch_line + status_line
    return info + "\n", True


def _detect_git_root(cwd: str) -> tuple:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            cwd=cwd, capture_output=True, text=True, timeout=_GIT_TIMEOUT,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return False, ""

        git_root = result.stdout.strip()
        if os.path.basename(git_root) == ".git":
            info_line = "# 版本控制\n- **Git仓库**：当前目录\n"
        else:
            abs_root = os.path.abspath(os.path.join(cwd, git_root))
            info_line = f"# 版本控制\n- **Git仓库**：位于父级仓库中（根目录：{abs_root}）\n"
        return True, info_line
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False, ""


def _get_branch_and_status(cwd: str) -> tuple:
    """获取 git 分支名和状态，合并为单次 git status --branch --porcelain 调用。

    返回格式：(branch_line, status_line)
      - branch_line: "  - 分支：master\n" 或 "  - 分支：detached HEAD\n"
      - status_line: "  - 状态：有未提交更改\n" 或 "  - 状态：clean\n"
    """
    try:
        result = subprocess.run(
            ["git", "status", "--branch", "--porcelain"],
            cwd=cwd, capture_output=True, text=True, timeout=_GIT_TIMEOUT,
        )
        if result.returncode != 0:
            return "", ""

        lines = result.stdout.splitlines()
        if not lines:
            return "", ""

        first_line = lines[0]
        if first_line.startswith("## "):
            branch_raw = first_line[3:].strip()
            if "HEAD" in branch_raw:
                branch = ""
            else:
                branch = branch_raw.split("...")[0] if "..." in branch_raw else branch_raw
        else:
            branch = ""

        branch_line = f"  - 分支：{branch if branch else 'detached HEAD'}\n"
        has_changes = len(lines) > 1
        status_line = f"  - 状态：{'有未提交更改' if has_changes else 'clean'}\n"
        return branch_line, status_line
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return "", ""


__all__ = [
    "check_version_control",
    "_build_vcs_info",
]
