"""测试用路径解析 — 与测试文件所在层级**无关**的仓库根定位。

历史问题：大量用例用 ``Path(__file__).resolve().parent.parent`` /
``parents[1]`` 定位仓库根，隐含「测试文件都在 ``tests/`` 第一层」的假设。
一旦把用例按被测模块移入子目录（如 ``tests/renderer/``），这些引用会静默
指向错误位置（``tests/prompts`` 之类），且失败信息难以定位。

本模块改为**基于标记文件向上查找**：从给定起点（默认本文件）逐级上溯，
第一个含有 ``pyproject.toml`` 的目录即仓库根。用例无论放在哪一层都成立。

用法::

    from tests._paths import repo_root, prompts_dir

    MAIN_PROMPT = prompts_dir() / "prompts_export_main_empty.md"

注：``tests/`` 是包（含 ``__init__.py``），故用例须以 ``tests._paths`` 形式
导入，而非顶层 ``_paths``。
"""

from __future__ import annotations

from pathlib import Path

#: 仓库根标记文件（存在即认定为仓库根）
_ROOT_MARKERS = ("pyproject.toml",)


def repo_root(start: Path | str | None = None) -> Path:
    """返回仓库根目录（从 ``start`` 或本文件位置向上查找标记文件）。

    Args:
        start: 起始路径（文件或目录）；省略时用本文件位置。

    Returns:
        含 ``pyproject.toml`` 的最高层目录；找不到标记时回退到「本文件上溯
        两级」（兼容未打标记的临时检出，行为与历史实现一致）。
    """
    origin = Path(start) if start is not None else Path(__file__)
    current = origin.resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if all((candidate / marker).exists() for marker in _ROOT_MARKERS):
            return candidate
    return Path(__file__).resolve().parent.parent


def tests_dir() -> Path:
    """返回 ``tests/`` 目录（仓库根下）。"""
    return repo_root() / "tests"


def prompts_dir() -> Path:
    """返回 ``prompts/`` 目录（系统提示词源文件所在）。"""
    return repo_root() / "prompts"


def src_dir() -> Path:
    """返回 ``src/`` 目录。"""
    return repo_root() / "src"


__all__ = ["repo_root", "tests_dir", "prompts_dir", "src_dir"]
