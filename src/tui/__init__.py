"""TUI 精简框架 — 零第三方依赖 TUI 系统。

重构说明（2026-07-29）：
  - 删除旧 terminal/animation/components/core/frame/pipeline/layout/widgets/engine/consumer 等 100+ 文件
  - 用 ~24 个顶层模块替代，零第三方依赖（blessed/wcwidth 移除）
  - rich 仅限内容渲染（OutputAdapter），TUI 框架本身不依赖 rich
  - 所有 ANSI 序列手写，终端尺寸通过 fcntl.ioctl + os.get_terminal_size 获取

模块架构：
  _config.py                — TuiConfig 配置 dataclass
  _const.py                 — RenderCommand / FrameworkCommand / ChatCommand 枚举
  _screen.py                — 纯 ANSI 终端屏幕管理（尺寸/光标/滚动/颜色/SIGWINCH）
  _input.py                 — Input 统一输入管理（stdin 读取/解析/缓冲/历史/补全）
  _input_parser.py          — InputParser ANSI 解析策略（Input 组合持有委托）
  _dispatcher.py            — EventDispatcher（DisplayEvent → RenderCommand 过滤+入队）
  _consumer.py              — ChatUIConsumer 兼容实现
  _completion.py            — _CmplHandler 补全处理器
  _completion_engine.py     — CompletionEngine 终端补全引擎（/命令/路径/参数补全，
                              供 EscapeMonitor Tab 回调使用；与 _completion.py 平行存在）
  _assembly.py              — TuiAssembly 子系统装配工厂
  _base_display.py          — 显示抽象基类（被 webui 引用）
  _diff_renderer.py         — 差异渲染（纯函数，被 core/tools/webui 引用）
  _input_orchestrator.py    — TuiInputOrchestrator 输入等待编排器
  _lifecycle.py             — TuiLifecycle 生命周期管理（start/stop/suspend/resume）
  _output_target.py         — 输出目标协议存根（IOutputTarget）
  _snapshot.py              — Token 速度快照惰性加载共享模块
  _stdout_tracker.py        — _StdoutLineTracker stdout 行追踪（环形缓冲）
  _subagent_panel.py        — SubAgent 面板控制器（EventBus 事件渲染）
  _tool_icons.py            — 工具图标 & Agent 类型标签
  input.py                  — Input 统一输入门面（委托实现至 ._input）

新结构目录（非旧残留）：
  consumer/                 — ChatUIConsumer 事件消费者 + 渲染入口
  core/                     — 核心工具（color/style/singleton）
  events/                   — UI 事件总线 + DisplayEvent 类型定义
  pipeline/                 — 消息编辑/显示管道（message_display/message_editor）
  state/                    — 消费/注册表状态管理（consumer_registry）
  ink/                      — React Ink 风格组件框架（调和器 + flexbox 布局 + 非全屏渲染）
  app/                      — 应用组件与模型（AppModel + apply_cmd + 组件树）

Layer 层次（由底向上）：
  _config → _const → _screen → _input → _dispatcher → ink/app → _consumer
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
# 输入系统
# ═══════════════════════════════════════════════════════════
from ._input import Input, KeyEvent

# ═══════════════════════════════════════════════════════════
# 消费者 API
# ═══════════════════════════════════════════════════════════
from ._consumer import ChatUIConsumer
from .state.consumer_registry import get_active_chat_ui

# ═══════════════════════════════════════════════════════════
# 应用模型（替代 RenderState/ChatRenderState — render_state.py 已并入 AppModel）
# ═══════════════════════════════════════════════════════════
from .app.model import AppModel

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
        "create_widget", "get_animator", "get_framework", "frame_from_context",
        "create_component",
        "Vertical", "Horizontal", "Padding", "Border", "Grid", "Center",
        "apply_fade_in",
        "MockConsumer", "MockTerminal",
    }
    if name in _OBSOLETE_SYMBOLS:
        raise ImportError(
            f"{name!r} 已在 TUI 重构中移除。"
            f" 请参考 src/tui/* 新模块。"
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
    # 输入系统
    "Input",
    "KeyEvent",
    # 消费者
    "ChatUIConsumer",
    "get_active_chat_ui",
    # 应用模型
    "AppModel",
    # 聊天域配置
    "ChatConfig",
    # 差异渲染
    "render_diff_to_ansi",
    "show_file_diff",
    # 显示抽象
    "BaseDisplay",
]
