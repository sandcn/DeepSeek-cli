"""app 层不得依赖 ink 私有模块（2026-10-24）。

``docs/architecture/tui.md`` 把 ``src/tui/ink/_*`` 定义为框架内部实现；
app/pipeline 直接 ``from ...ink._xxx import ...`` 会让内部符号变成事实上的
公共 API（重构即破坏）。此测试把"私有导入"固定为 0：
  - 需要的能力应经 ``src.tui.ink`` 门面或公开模块（如 ``markers``）暴露；
  - 新增违规会直接失败，而不是靠 review 记忆。
"""

from __future__ import annotations

import re
from pathlib import Path

_IMPORT_RE = re.compile(
    r"from\s+(?:src\.tui\.ink|\.\.ink|\.ink)\._[A-Za-z0-9_]+\s+import"
)
_SCAN_ROOTS = ("src/tui/app", "src/tui/pipeline")


def test_app_layer_has_no_private_ink_imports() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    offenders: list[str] = []
    for root in _SCAN_ROOTS:
        for path in (repo_root / root).rglob("*.py"):
            for lineno, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1,
            ):
                if _IMPORT_RE.search(line):
                    offenders.append(
                        f"{path.relative_to(repo_root)}:{lineno}: {line.strip()}"
                    )
    assert not offenders, "app 层出现 ink 私有模块导入：\n" + "\n".join(offenders)
