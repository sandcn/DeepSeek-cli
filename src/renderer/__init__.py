"""renderer 包 — 流式 Markdown 解析与 ANSI 渲染。

渲染路径：
  - **TUI 内容路径**：``src.renderer.ansi.AnsiStreamRenderer``（自绘 ANSI，
    零第三方渲染依赖）；
  - **解析层**：``RecursiveDescentParser`` / ``TokenPipeline`` 产出 ``Token``
    流，供 ANSI 引擎消费。

本模块仅做惰性 re-export（PEP 562）：首次访问符号时才导入对应子模块，
避免 ``import src.renderer.<submodule>`` 时加载整条渲染链。
"""

from __future__ import annotations

from importlib import import_module

#: 惰性导出表：公开名 → (包内相对模块名, 模块内属性名)。
_LAZY_EXPORTS = {
    # ANSI 渲染入口
    "AnsiStreamRenderer": (".ansi", "AnsiStreamRenderer"),
    # 解析层
    "RecursiveDescentParser": (".recursive_parser", "RecursiveDescentParser"),
    "TokenPipeline": (".pipeline", "TokenPipeline"),
    "RenderContext": (".types", "RenderContext"),
    "TokenType": (".types", "TokenType"),
    "Token": (".types", "Token"),
}

#: ``from src.renderer import *`` 的导出面。
__all__ = sorted(_LAZY_EXPORTS)


def __getattr__(name: str):
    """PEP 562 模块级惰性属性解析（首次访问后缓存到模块命名空间）。"""
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(target[0], __name__)
    value = getattr(module, target[1])
    globals()[name] = value
    return value


def __dir__() -> list:
    return sorted(set(globals()) | set(_LAZY_EXPORTS))
