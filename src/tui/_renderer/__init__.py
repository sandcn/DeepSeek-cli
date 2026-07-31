"""渲染器包 — 三层分离（TuiEngine / TuiRenderer / EventDispatcher）。

原始单文件 ``_renderer.py`` 已拆分为以下子模块：
  - ``_engine.py`` — TuiEngine 渲染引擎（render 线程 + 队列 + 四阶段循环）
  - ``_renderer.py`` — TuiRenderer 内容渲染器（dict 分发）
  - ``_dispatcher.py`` — EventDispatcher 事件→命令映射（filter_fn 注入）

向后兼容：所有旧 ``from src.tui._renderer import XXX`` 导入路径保持有效。
"""

from __future__ import annotations

from src.tui._renderer._engine import TuiEngine, _CONTENT_COMMANDS
from src.tui._renderer._renderer import TuiRenderer, _cmd_name, _emergency_write
from src.tui._renderer._dispatcher import EventDispatcher

__all__ = [
    "TuiEngine",
    "TuiRenderer",
    "EventDispatcher",
    "_cmd_name",
    "_emergency_write",
    "_CONTENT_COMMANDS",
]
