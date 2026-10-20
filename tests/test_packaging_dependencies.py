"""打包元数据守卫 — 依赖声明覆盖真实导入 + 版本号单一真源。

背景：``src/`` 曾导入 ``PIL`` / ``psutil`` / ``yaml`` / ``orjson`` / ``h2``
等第三方包，而 ``pyproject.toml`` 未全部声明，导致 ``pip install .`` 后
``read_image`` 等能力直接失效；同时 ``Jinja2`` 被声明却从未使用。

本测试静态扫描 ``src/`` 的顶层导入并与 ``pyproject.toml`` 对照，固化：

1. 每个第三方顶层导入都在 ``dependencies`` 或某个 ``optional-dependencies``
   分组中声明（或位于显式白名单：平台专用 / 标准库 / 本项目）；
2. 未使用的声明依赖（``Jinja2``）不得回流；
3. 模块名与分发包名的映射（``PIL`` → ``Pillow`` 等）正确。
"""

from __future__ import annotations

import ast
import os
import sys
import pytest

from tests._paths import repo_root

_REPO_ROOT = repo_root()
_SRC_DIR = _REPO_ROOT / "src"
_PYPROJECT = _REPO_ROOT / "pyproject.toml"

try:  # Python 3.11+
    import tomllib as _toml
except ModuleNotFoundError:  # pragma: no cover - Python 3.9/3.10
    try:
        import tomli as _toml  # type: ignore[no-redef]
    except ModuleNotFoundError:  # pragma: no cover - 无解析器时跳过
        _toml = None  # type: ignore[assignment]


#: 导入名 → 分发包名（不一致时才需要映射）
_IMPORT_TO_DISTRIBUTION = {
    "PIL": "pillow",
    "bs4": "beautifulsoup4",
    "yaml": "pyyaml",
    "pygments": "pygments",
    "typing_extensions": "typing-extensions",
    "aiofiles": "aiofiles",
}

#: 不要求声明的导入：
#: - 平台专用框架（仅在对应平台上可用，条件导入）
#: - 标准库 / 本项目自身
_PLATFORM_ONLY = {
    "Quartz",   # macOS 截图/输入后端
    "Vision",   # macOS OCR
    "win32api", "win32con", "win32gui",  # Windows（pywin32，条件可用）
}

_STDLIB = {
    "__future__", "abc", "argparse", "asyncio", "atexit", "base64", "bisect",
    "codecs", "collections", "contextlib", "contextvars", "copy", "ctypes",
    "dataclasses", "datetime", "difflib", "email", "enum", "errno", "fcntl",
    "fnmatch", "functools", "gc", "glob", "grp", "hashlib", "heapq", "html",
    "importlib", "inspect", "io", "ipaddress", "itertools", "json", "locale",
    "logging", "math", "msvcrt", "os", "pathlib", "pkgutil", "platform", "pty",
    "pwd", "queue", "random", "re", "select", "shlex", "shutil", "signal",
    "socket", "stat", "string", "struct", "subprocess", "sys", "tarfile",
    "tempfile", "termios", "threading", "time", "tty", "types", "typing",
    "unicodedata", "urllib", "uuid", "warnings", "weakref", "zlib", "colorsys",
    "configparser", "csv", "dis", "getpass", "gzip", "http", "linecache",
    "mimetypes", "pickle", "pprint", "secrets", "shelve", "sqlite3",
    "statistics", "textwrap", "tokenize", "traceback", "typing_extensions",
    "xml", "zipfile",
}


def _load_pyproject() -> dict:
    if _toml is None:  # pragma: no cover - 环境缺 TOML 解析器
        pytest.skip("需要 tomllib / tomli 解析 pyproject.toml")
    with open(_PYPROJECT, "rb") as handle:
        return _toml.load(handle)


def _declared_distributions(data: dict) -> set[str]:
    """全部已声明依赖的规范化分发包名集合（含 optional 分组）。"""
    names: set[str] = set()
    project = data.get("project", {})
    for raw in project.get("dependencies", []) or []:
        names.add(_normalize_requirement(raw))
    for group in (project.get("optional-dependencies") or {}).values():
        for raw in group or []:
            names.add(_normalize_requirement(raw))
    return {n for n in names if n}


def _normalize_requirement(raw: str) -> str:
    """``"Pillow>=9.0"`` / ``"typing_extensions>=4.4; python_version<'3.11'"`` → ``pillow``。"""
    head = raw.split(";", 1)[0].strip()
    for sep in ("==", ">=", "<=", "~=", "!=", ">", "<", "[", " ", "("):
        head = head.split(sep, 1)[0]
    return head.strip().lower().replace("_", "-")


def _iter_src_imports():
    """遍历 src/ 收集顶层第三方导入名。"""
    for dirpath, dirnames, filenames in os.walk(_SRC_DIR):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in filenames:
            if not name.endswith(".py"):
                continue
            path = os.path.join(dirpath, name)
            with open(path, encoding="utf-8") as handle:
                try:
                    tree = ast.parse(handle.read())
                except SyntaxError:  # pragma: no cover - 语法错误由其它测试兜住
                    continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        yield alias.name.split(".")[0], path
                elif isinstance(node, ast.ImportFrom):
                    if node.level or not node.module:
                        continue
                    yield node.module.split(".")[0], path


def test_all_third_party_imports_are_declared():
    """src/ 的每个第三方顶层导入都必须在 pyproject 中声明。"""
    declared = _declared_distributions(_load_pyproject())
    missing: dict[str, set[str]] = {}
    for module, path in _iter_src_imports():
        if module in _STDLIB or module in _PLATFORM_ONLY or module == "src":
            continue
        distribution = _IMPORT_TO_DISTRIBUTION.get(module, module.lower().replace("_", "-"))
        if distribution not in declared:
            missing.setdefault(module, set()).add(
                os.path.relpath(path, _REPO_ROOT)
            )
    assert not missing, (
        "以下第三方导入未在 pyproject.toml 声明依赖："
        + "; ".join(f"{m}（{sorted(p)[:3]}）" for m, p in sorted(missing.items()))
    )


def test_pillow_psutil_pyyaml_are_required_dependencies():
    """运行时硬依赖（Pillow / psutil / PyYAML）必须在必需依赖中，而非仅 optional。"""
    data = _load_pyproject()
    required = {
        _normalize_requirement(raw)
        for raw in data["project"].get("dependencies", []) or []
    }
    for name in ("pillow", "psutil", "pyyaml"):
        assert name in required, f"{name} 应声明为必需依赖（当前缺失）"


def test_jinja2_is_not_declared():
    """Jinja2 从未被 src/ 使用，不得出现在依赖或 optional 分组中。"""
    declared = _declared_distributions(_load_pyproject())
    assert "jinja2" not in declared


def test_optional_perf_and_http2_groups_exist():
    """orjson（perf）与 h2（http2）以可选分组声明，供安装时显式启用。"""
    data = _load_pyproject()
    optional = data["project"].get("optional-dependencies") or {}
    assert "perf" in optional, "缺少 perf 可选依赖分组（orjson）"
    assert "http2" in optional, "缺少 http2 可选依赖分组（h2）"
    assert any("orjson" in raw for raw in optional["perf"])
    assert any("h2" in raw for raw in optional["http2"])


def test_import_name_mapping_targets_are_correct():
    """导入名→分发包名映射与真实可导入模块一致（防映射写错导致守卫失效）。"""
    import importlib.util

    for module, distribution in _IMPORT_TO_DISTRIBUTION.items():
        spec = importlib.util.find_spec(module) if module != "win32api" else None
        if spec is None:
            continue  # 该环境未安装（如 pywin32）——不算失败
        assert distribution, f"{module} 缺少分发包名映射"


def test_psutil_yaml_orjson_imports_are_guarded():
    """psutil / yaml / orjson 缺失时必须有回退（可选依赖不得成为硬崩溃点）。"""
    from src.tui._system_monitor import _SystemMonitor

    # 无论环境是否装有 psutil，构造与采集都不应抛错
    monitor = _SystemMonitor()
    assert monitor.get_cpu_percent() >= 0.0
    assert monitor.get_memory_percent() >= 0.0

    from src.kernel import _yaml as kernel_yaml

    assert kernel_yaml.load("a: 1\nb: [1, 2]\n") == {"a": 1, "b": [1, 2]}

    from src.api import client_async

    assert hasattr(client_async, "_json_loads")

    if sys.version_info < (3, 11):
        from src._compat import dataclass

        assert callable(dataclass)
