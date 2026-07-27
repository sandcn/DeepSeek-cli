"""TUI 统一框架 — 整合所有 TUI 相关功能。

分层架构（由底向上）：

  框架层（通用，可独立复用）：
  ─────────────────────────────
  core/       — 核心基础工具层：effects, color, style, ttl_cache, text_utils,
  │             gradient(渐变工具), theme(主题系统), ansi_utils(ANSI工具),
  │             output_target(输出目标), formatter(文本格式化零依赖层),
  │             cost(费用计算纯函数), param_formatter(工具参数格式化),
  │             parallel_config / tool_icons / text_formatter,
  │             TrueColor / ColorValue（颜色值对象）, theme_loader（YAML主题加载）
  │
  terminal/   — 终端 I/O 层：blessed适配, LockedTerminal, 窄屏检测, adapter(终端适配器),
  │             capabilities(终端能力检测: TrueColor/256色/UTF-8/Emoji)
  │
  animation/  — 动画基础设施层：AnimatorContext(动画时钟管理器), BreathPalette(呼吸调色板),
  │             transitions(过渡效果:FadeIn/FadeOut/Slide/Typewriter)
  │
  events/     — 事件总线层：DisplayEventBus(显示层事件总线), event_types(21种事件类型),
  │             adapters(事件适配器), consumers(事件消费者)
  │
  widget_base/ — 控件基类：Widget(控件抽象), WidgetTree(控件树)
  │
  render_buffer/ — 渲染缓冲区：RenderBuffer(二维字符网格，支持叠加合成)
  │
  layout/     — 声明式布局：Vertical/Horizontal/Padding/Border/Grid/Center
  │
  components/ — 通用框架组件（可独立复用）:
  │             Box/RoundedBox/DoubleBox, Separator, Spinner, ProgressBar,
  │             SplashScreen
  │
  framework   — 框架入口：Framework 单例、create_component/create_widget、
  │             get_animator/get_framework/frame_from_context

  应用层（聊天域特有）：
  ──────────────────────
  state/      — 统一状态管理：UISessionState(会话状态), InputState(输入状态),
  │             StreamingState(流式状态), TUIStateTree(聚合容器),
  │             AgentStateStore(多Agent状态),
  │             render_state.py(RenderState框架通用基类+ChatRenderState聊天域子类),
  │             ChatRenderState, consumer_registry(消费者注册表)
  │
  components/ — 聊天域组件（业务相关）:
  │             ThinkingBlock, AnswerBlock, UserMsgBlock, ToolOutputBlock,
  │             ToolSummaryBlock, ErrorBlock, NotificationBlock, WriteLineBlock
  │
  engine/     — 渲染引擎层：TuiEngine(render线程+命令队列), TuiRenderer(命令分发),
  │             EventDispatcher(事件→命令映射), RenderCommand(命令枚举),
  │             commands.py(FrameworkCommand框架通用命令),
  │             renderer_base.py(FrameworkRenderer框架通用渲染器基类)
  │
  consumer/   — 消费者 API 层：ChatUIConsumer(生命周期协调), 工厂装配,
  │             协议定义(RenderEngine/BottomBarProtocol), 补全处理器,
  │             错误处理, base_display,
  │             chat_config.py(ChatConfig聊天域配置),
  │             chat_commands.py(ChatCommand聊天域命令枚举)
  │
  pipeline/   — 消息显示/编辑管线：message_display, message_editor
  │
  widgets/    — 交互控件：bottom_bar(底部固定栏/状态行/输入区/补全弹窗),
  │             completion, cursor_tracker,
  │             help_panel(快捷键帮助浮层), status_bar
  │
  parallel_display.py — 并行 Agent 显示管理：ParallelDisplay
  │
  frame/      — 纯函数帧渲染器：FrameRenderer(AgentSlot→终端行)


框架入口：
  - framework — TUI 框架统一入口，提供 Framework 单例、create_component、
    EffectRegistry、frame_from_context、get_animator 等公开 API
  - consumer — ChatUIConsumer、get_active_chat_ui、RenderCommand 等聊天域 API

2026-07-22 框架整理：
  - 统一公共 API 表面：所有框架层符号集中在 __init__.py 导出
  - 区分框架层 vs 应用层：通用控件提升到顶层，聊天域组件保留在 consumer/
  - Framework 强化为统一入口：新增配置管理、事件总线、RenderBuffer 工厂等方法
  - 修复跨层耦合：core/internal/agent/ 通过 DisplayTarget 协议访问显示层

2026-07-17 框架优化：
  - 清理 consumer/ 下 8 个向后兼容重导出存根，consumer/__init__.py 直接引用 engine/ 和 state/
  - 清理 parallel/ 下 3 个向后兼容重导出存根（_config, _text_formatter, _tool_icons）
  - 清理 core/ 下 3 个向后兼容重导出存根（animator, palettes, state），core/__init__.py 直接引用 animation/ 和 state/
  - 合并 bus/ → events/：bus/ 目录删除，events/ 成为事件总线的真实实现
  - ui/ 清理：console.py 迁移到 tui/core/rich_console.py，ui/ 目录删除（保留 __init__.py 作为 deprecation 标记）
  - 所有外部引用从 ui.xxx 更新为 tui.xxx 或 core.constants

2026-07-17 框架重构：
  - config — TuiConfig 统一配置 dataclass，集中管理所有可调参数
  - Framework 强化：移除 @deprecated 废弃标记，添加 start()/stop()/is_running
    生命周期管理，集成 ComponentRegistry（get_component_registry()），
    添加配置入口（get_config()）
  - ComponentRegistry 激活：TuiRenderer.render() 改用 resolve() 分发，
    替代本地 _RENDER_DISPATCH 字典
  - 崩溃自动恢复：render 线程异常退出后自动重建（最多 3 次）
  - 组件引用修复：_answer/_error/_notification 改用模块级 get_animator()
  - testing 增强：新增 MockConsumer 和 MockTerminal 测试辅助工具
"""

from __future__ import annotations

# ═══════════════════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════════════════
from .config import TuiConfig

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
from .widget_base import Widget, WidgetTree
from .render_buffer import RenderBuffer

# ═══════════════════════════════════════════════════════════
# 引擎层 — 渲染引擎与命令系统
# ═══════════════════════════════════════════════════════════
from .engine import (
    FrameworkRenderer,
    FrameworkCommand,
)

# ═══════════════════════════════════════════════════════════
# 消费层 — 聊天域配置与命令
# ═══════════════════════════════════════════════════════════
from .consumer.chat_config import ChatConfig
from .consumer.chat_commands import ChatCommand

# ═══════════════════════════════════════════════════════════
# 状态层 — 渲染状态基类与子类
# ═══════════════════════════════════════════════════════════
from .state.render_state import RenderState, ChatRenderState

# ═══════════════════════════════════════════════════════════
# 布局控件
# ═══════════════════════════════════════════════════════════
from .layout import (
    Vertical,
    Horizontal,
    Padding,
    Border,
    Grid,
    Center,
)

# ═══════════════════════════════════════════════════════════
# 通用框架组件 — 延迟导入（避免反向依赖 src.config → tui）
# ═══════════════════════════════════════════════════════════
from ._lazy import LazyLoader

_components_mod = LazyLoader("src.tui.components")

# ═══════════════════════════════════════════════════════════
# 动画 — 直接从 _base 子模块导入（无循环依赖）
# ═══════════════════════════════════════════════════════════
from .core.text_utils import apply_fade_in

# ═══════════════════════════════════════════════════════════
# 测试工具
# ═══════════════════════════════════════════════════════════
from .testing import MockConsumer, MockTerminal

# ── 组件延迟导入映射 ────────────────────────────────────
# 以下符号通过 __getattr__ 从懒加载模块获取，
# 避免 eager import 触发循环依赖链
# (src.tui.components._cost → src.tui.core.cost → src.config → src.tui)

_COMPONENT_SYMBOLS: set[str] = {
    "Box",
    "BoxStyle",
    "RoundedBox",
    "DoubleBox",
    "Separator",
    "Spinner",
    "ProgressBar",
    "SplashScreen",
}


def __getattr__(name: str):
    """模块级 __getattr__ — 延迟解析组件符号。

    当 ``from src.tui import Box`` 等语句访问组件符号时，
    Python 调用此函数从 LazyLoader 中获取实际对象。
    """
    if name in _COMPONENT_SYMBOLS:
        return getattr(_components_mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    """支持 dir() 列出所有导出符号。"""
    return sorted(__all__)


__all__ = [
    # 配置
    "TuiConfig",
    # 框架入口
    "Framework",
    "create_component",
    "create_widget",
    "get_animator",
    "get_framework",
    "frame_from_context",
    # 核心抽象
    "Widget",
    "WidgetTree",
    "RenderBuffer",
    # 引擎层
    "FrameworkRenderer",
    "FrameworkCommand",
    # 消费层
    "ChatConfig",
    "ChatCommand",
    # 状态层
    "RenderState",
    "ChatRenderState",
    # 布局
    "Vertical",
    "Horizontal",
    "Padding",
    "Border",
    "Grid",
    "Center",
    # 通用组件（框架层）
    "Box",
    "BoxStyle",
    "RoundedBox",
    "DoubleBox",
    "Separator",
    "Spinner",
    "ProgressBar",
    "SplashScreen",
    # 动画
    "apply_fade_in",
    # 测试
    "MockConsumer",
    "MockTerminal",
]
