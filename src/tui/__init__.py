"""TUI 精简框架 — 零第三方依赖 TUI 系统。

重构说明（2026-07-29）：
  - 删除旧 terminal/animation/components/core/frame/pipeline/layout/widgets/engine/consumer 等 100+ 文件
  - 用 ~12 个新模块替代，零第三方依赖（blessed/wcwidth 移除）
  - rich 仅限内容渲染（OutputAdapter），TUI 框架本身不依赖 rich
  - 所有 ANSI 序列手写，终端尺寸通过 fcntl.ioctl + os.get_terminal_size 获取

模块架构：
  _config.py      — TuiConfig 配置 dataclass
  _const.py       — RenderCommand / FrameworkCommand / ChatCommand 枚举
  _screen.py      — 纯 ANSI 终端屏幕管理（尺寸/光标/滚动/颜色/SIGWINCH）
  _buffer.py      — RenderBuffer 二维字符渲染缓冲区
  _input.py       — Input 统一输入管理（stdin 读取/解析/缓冲/历史/补全）
  _bottom_bar.py  — DECSTBM 分屏底部固定栏
  _renderer.py    — TuiEngine + TuiRenderer + EventDispatcher（统一渲染器）
  _consumer.py    — ChatUIConsumer 兼容实现
  _completion.py  — _CmplHandler 补全处理器
  _locks.py       — 输出锁（render_lock / io_lock / diff_active）
  framework.py    — Framework 单例框架入口（保留不变）

Layer 层次（由底向上）：
  _config → _const → _screen → _buffer → _input → _bottom_bar → _renderer → _consumer → Framework
"""

from __future__ import annotations

# ═══════════════════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════════════════
from ._config import TuiConfig

# ═══════════════════════════════════════════════════════════
# 命令枚举
# ═══════════════════════════════════════════════════════════
from ._const import RenderCommand, FrameworkCommand, ChatCommand

# ═══════════════════════════════════════════════════════════
# 框架入口
# ═══════════════════════════════════════════════════════════
from .framework import (
    Framework,
    create_component,
    create_widget,
    get_animator,
    get_framework,
    frame_from_context,
)

# ═══════════════════════════════════════════════════════════
# 核心抽象
# ═══════════════════════════════════════════════════════════
from ._buffer import RenderBuffer

# ═══════════════════════════════════════════════════════════
# 输入系统
# ═══════════════════════════════════════════════════════════
from ._input import Input, KeyEvent

# ═══════════════════════════════════════════════════════════
# 消费者 API
# ═══════════════════════════════════════════════════════════
from ._consumer import ChatUIConsumer
from .state.consumer_registry import get_active_chat_ui

# ═══════════════════════════════════════════════════════════
# 渲染状态
# ═══════════════════════════════════════════════════════════
from .state.render_state import RenderState, ChatRenderState

# ═══════════════════════════════════════════════════════════
# 聊天域配置
# ═══════════════════════════════════════════════════════════
from .consumer.chat_config import ChatConfig

# ═══════════════════════════════════════════════════════════
# 差异渲染（纯函数，被 core/tools/webui 引用）
# ═══════════════════════════════════════════════════════════
from ._diff_renderer import render_diff_to_ansi, show_file_diff

# ═══════════════════════════════════════════════════════════
# 显示抽象基类（被 webui 引用）
# ═══════════════════════════════════════════════════════════
from ._base_display import BaseDisplay


def __getattr__(name: str):
    """模块级 __getattr__ — 对已删除的旧组件符号提供明确的 ImportError 提示。"""
    _OBSOLETE_SYMBOLS = {
        "Box", "BoxStyle", "RoundedBox", "DoubleBox", "Separator",
        "Spinner", "ProgressBar", "SplashScreen",
        "Widget", "WidgetTree",
        "Vertical", "Horizontal", "Padding", "Border", "Grid", "Center",
        "apply_fade_in",
        "MockConsumer", "MockTerminal",
    }
    if name in _OBSOLETE_SYMBOLS:
        raise ImportError(
            f"{name!r} 已在 TUI 重构中移除。"
            f" 请参考 src/tui/* 新模块或使用 RenderBuffer 替代 Widget/RenderBuffer 体系。"
        )
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    """支持 dir() 列出所有导出符号。"""
    return sorted(__all__)


__all__ = [
    # 配置
    "TuiConfig",
    # 命令枚举
    "RenderCommand",
    "FrameworkCommand",
    "ChatCommand",
    # 框架入口
    "Framework",
    "create_component",
    "create_widget",
    "get_animator",
    "get_framework",
    "frame_from_context",
    # 核心抽象
    "RenderBuffer",
    # 输入系统
    "Input",
    "KeyEvent",
    # 消费者
    "ChatUIConsumer",
    "get_active_chat_ui",
    # 渲染状态
    "RenderState",
    "ChatRenderState",
    # 聊天域配置
    "ChatConfig",
    # 差异渲染
    "render_diff_to_ansi",
    "show_file_diff",
    # 显示抽象
    "BaseDisplay",
]
