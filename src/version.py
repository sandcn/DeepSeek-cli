"""版本号单一真源。

版本号的权威来源是 ``pyproject.toml`` 的 ``project.version``（安装后经
``importlib.metadata`` 读取）。源码直接运行时（未安装）回退到常量
``_FALLBACK_VERSION``——该常量仅在无法从已安装分发包读取时使用。

用法::

    from src.version import __version__, version_string

    __version__        # "2.2.0"
    version_string()   # "v2.2.0"（带 v 前缀，供界面展示）
"""

from __future__ import annotations

#: 源码直跑（未安装为分发包）时的回退版本。
#: 发布新版本时**只需**改 pyproject.toml 与本常量（二者需一致，由
#: tests/test_version_source.py 守卫）。
_FALLBACK_VERSION = "2.2.0"

_DISTRIBUTION_NAME = "ai-chat"


def _read_installed_version() -> str:
    """读取已安装分发包的版本号；不可用时返回空串。"""
    try:
        from importlib.metadata import PackageNotFoundError, version

        try:
            return version(_DISTRIBUTION_NAME)
        except PackageNotFoundError:
            return ""
    except Exception:  # pragma: no cover - importlib.metadata 不可用（极旧解释器）
        return ""


def _resolve_version() -> str:
    installed = _read_installed_version()
    return installed or _FALLBACK_VERSION


#: 语义化版本号（无前缀），如 ``"2.2.0"``。
__version__ = _resolve_version()


def version_string() -> str:
    """带 ``v`` 前缀的版本号（终端展示用），如 ``"v2.2.0"``。"""
    return f"v{__version__}"


def user_agent(product: str = "deepseek-cli") -> str:
    """构造 ``<product>/<version>`` 形式的 User-Agent。"""
    return f"{product}/{__version__}"


__all__ = ["__version__", "version_string", "user_agent"]
