"""renderer 包 — 精简版增量流式 Markdown 渲染器。

渲染路径：
  - **TUI 内容路径**：``src.renderer.ansi.AnsiStreamRenderer``（自绘 ANSI，
    零 Rich 依赖）；
  - **Rich 路径**：``IncrementalRenderer``（Parser → TokenPipeline →
    RenderEngine → OutputAdapter，见 ``_incremental.py``）。

★ 锁设计：无实例级锁。
  IncrementalRenderer 本身不持有任何线程锁，原因：
  - 每个渲染器实例由单一线程/任务专用，不存在并发竞争
  - OutputAdapter 内部使用全局 output_lock 保护所有 I/O 操作
  - 移除实例锁消除了「实例锁 → output_lock」的 ABBA 死锁风险

★ 启动性能（2026-10-07，PEP 562 惰性导出）：本 ``__init__`` 原先在导入期
eager 导入 ``rich``（console/style/text）以及 output/indicator/engine/pipeline
等 Rich 渲染链——**任何** ``import src.renderer.<submodule>``（TUI 的
``src.renderer.ansi`` / ``src.renderer.locks`` 等）都被迫加载整条 Rich 链
（实测 ~60-100ms）。现改为模块级 ``__getattr__`` 惰性解析：只有真正访问
``IncrementalRenderer`` 等 Rich 侧符号的调用方才付出该代价。

使用方式：
  renderer = IncrementalRenderer()
  renderer.write("Hello **world**")
  renderer.close()
"""

from __future__ import annotations

from importlib import import_module

#: 惰性导出表：公开名 → (模块名, 模块内属性名)。
#: 模块名以 ``.`` 开头为包内相对模块，否则为绝对模块（rich / src.terminal）。
_LAZY_EXPORTS = {
    # Rich 渲染器（``_incremental.py``）
    "IncrementalRenderer": ("._incremental", "IncrementalRenderer"),
    "_StyledOutputAdapter": ("._incremental", "_StyledOutputAdapter"),
    # 渲染链子模块公开符号（历史 re-export 面保持不变）
    "OutputAdapter": (".output", "OutputAdapter"),
    "StreamingIndicator": (".indicator", "StreamingIndicator"),
    "RecursiveDescentParser": (".recursive_parser", "RecursiveDescentParser"),
    "RenderContext": (".types", "RenderContext"),
    "TokenType": (".types", "TokenType"),
    "RenderEngine": (".engine", "RenderEngine"),
    "TokenPipeline": (".pipeline", "TokenPipeline"),
    "render_toc": ("._rendering", "render_toc"),
    "render_render_summary": ("._rendering", "render_render_summary"),
    # Rich 类型与终端辅助（原 ``__init__`` re-export）
    "Console": ("rich.console", "Console"),
    "Style": ("rich.style", "Style"),
    "Text": ("rich.text", "Text"),
    "get_safe_console_config": ("..terminal", "get_safe_console_config"),
}

#: ``from src.renderer import *`` 的导出面（与原模块级公开名一致——不含
#: 下划线前缀的内部符号；``_StyledOutputAdapter`` 仍可按名显式导入）。
__all__ = sorted(n for n in _LAZY_EXPORTS if not n.startswith("_"))


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
