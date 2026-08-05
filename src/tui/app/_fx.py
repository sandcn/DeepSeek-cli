"""app/_fx — 时间基动效助手（re-export 存根）。

实现已下沉至 ``src/tui/core/_fx.py``（2026-08-05 公共工具归位 core 层）；
本模块保持 re-export 存根——旧导入路径（``from src.tui.app import _fx`` /
``from src.tui.app._fx import SPINNER_FRAMES``）与测试 patch 路径
（``patch("src.tui.app._fx.time.monotonic")`` 等）兼容。

依赖约束：仅依赖 src/tui/core（color/_fx）与标准库 time——不依赖 app 组件。
"""

from __future__ import annotations

#: re-export time 模块：兼容测试 ``patch("src.tui.app._fx.time.monotonic")``
#: （patch 解析本模块命名空间的 ``time`` 属性；替换的是全局 time 模块对象，
#: core/_fx 实现随之生效）。
import time as time  # noqa: F401

from src.tui.core._fx import (
    _DEFAULT_FADE_DURATION,
    _DEFAULT_SPINNER_HZ,
    _default_fx_params,
    SPINNER_FRAMES,
    fade_color,
    spinner_frame,
    spinner_char,
)

__all__ = ["fade_color", "spinner_frame", "spinner_char", "SPINNER_FRAMES"]
