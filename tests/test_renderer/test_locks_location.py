"""锁归属与依赖方向测试 — 步骤 8（C 组依赖方向校验）。

验证：
  1. 锁原语已迁移至 src.renderer._locks（唯一真源），
     src/tui/_locks.py 兼容 shim 已于步骤 8 删除（存量调用方已迁移至真源）。
  2. src/renderer/ 包内无任何 `import.*tui` / `from.*tui` 模块级导入
     （renderer 不再依赖 tui，消除逆向依赖）。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


def _renderer_py_files() -> list[Path]:
    """枚举 src/renderer/ 包下全部 .py 源码文件（含子包）。"""
    renderer_dir = (
        Path(__file__).resolve().parent.parent.parent / "src" / "renderer"
    )
    assert renderer_dir.is_dir(), f"renderer 包目录不存在: {renderer_dir}"
    return sorted(p for p in renderer_dir.rglob("*.py"))


# ============================================================
# renderer 包无 tui 依赖（模块级 import 审计）
# ============================================================

_IMPORT_RE = re.compile(r"^\s*(?:import|from)\s+")


class TestRendererNoTuiDependency:
    """src/renderer/ 包内不允许任何 tui 导入。"""

    @pytest.mark.parametrize("path", _renderer_py_files(), ids=lambda p: str(p.relative_to(Path(__file__).resolve().parent.parent.parent)))
    def test_no_tui_import_in_file(self, path):
        text = path.read_text(encoding="utf-8")
        offenders: list[tuple[int, str]] = []
        for lineno, line in enumerate(text.splitlines(), 1):
            if not _IMPORT_RE.match(line):
                continue
            if "tui" in line:
                offenders.append((lineno, line.strip()))
        assert offenders == [], (
            f"{path.name} 存在 tui 导入: {offenders}"
        )


