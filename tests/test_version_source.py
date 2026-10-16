"""版本号单一真源守卫。

版本号权威来源 = ``pyproject.toml`` 的 ``project.version``；``src/version.py``
的 ``_FALLBACK_VERSION`` 仅在未安装为分发包时使用。本测试固化：

1. 二者一致（防发版时只改一处）；
2. 各消费点（CLI / MCP / User-Agent）取到同一版本；
3. 源码中不再出现硬编码版本字面量（``"2.2.0"`` / ``"v2.2.0"``）。
"""

from __future__ import annotations

import os
import re

import pytest

from tests._paths import repo_root

_REPO_ROOT = repo_root()
_SRC_DIR = _REPO_ROOT / "src"
_PYPROJECT = _REPO_ROOT / "pyproject.toml"

try:
    import tomllib as _toml
except ModuleNotFoundError:  # pragma: no cover - Python 3.9/3.10
    try:
        import tomli as _toml  # type: ignore[no-redef]
    except ModuleNotFoundError:  # pragma: no cover
        _toml = None  # type: ignore[assignment]


def _pyproject_version() -> str:
    if _toml is None:  # pragma: no cover
        pytest.skip("需要 tomllib / tomli 解析 pyproject.toml")
    with open(_PYPROJECT, "rb") as handle:
        return _toml.load(handle)["project"]["version"]


def test_pyproject_version_matches_fallback():
    from src.version import _FALLBACK_VERSION

    assert _pyproject_version() == _FALLBACK_VERSION


def test_version_string_and_user_agent():
    from src.version import __version__, user_agent, version_string

    assert version_string() == f"v{__version__}"
    assert user_agent() == f"deepseek-cli/{__version__}"
    assert user_agent("other") == f"other/{__version__}"


def test_all_consumers_report_same_version():
    from src.version import __version__

    from src.app_init._args import VERSION
    from src.mcp.protocol import CLIENT_VERSION
    from src.tools.search_providers import DEEPSEEK_SEARCH_USER_AGENT

    assert VERSION == f"v{__version__}"
    assert CLIENT_VERSION == __version__
    assert DEEPSEEK_SEARCH_USER_AGENT == f"deepseek-cli/{__version__}"


def test_no_hardcoded_version_literals_in_src():
    """src/ 内不得再出现硬编码的包版本字面量（含注释与 docstring）。

    版本号唯一出现位置是 ``src/version.py``（权威回退常量与其文档示例）；
    其余任何位置（代码、注释、docstring）都必须经 ``src.version`` 取值——
    否则发版时极易漏改（历史问题：CLI / MCP / User-Agent / 界面 docstring
    各持一份字面量）。
    """
    version = _pyproject_version()
    pattern = re.compile(re.escape(version))
    offenders: list[str] = []
    for dirpath, dirnames, filenames in os.walk(_SRC_DIR):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in filenames:
            if not name.endswith(".py"):
                continue
            path = os.path.join(dirpath, name)
            if os.path.basename(path) == "version.py":
                continue  # 回退常量的唯一合法位置
            with open(path, encoding="utf-8") as handle:
                for lineno, line in enumerate(handle, 1):
                    if pattern.search(line):
                        offenders.append(f"{os.path.relpath(path, _REPO_ROOT)}:{lineno}")
    assert not offenders, "发现硬编码版本字面量：" + ", ".join(offenders)
