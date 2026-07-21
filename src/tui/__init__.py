"""TUI 统一框架 — 整合所有 TUI 相关功能。

分层架构（由底向上）：

  core/       — 核心基础工具层：effects, color, style, ttl_cache, text_utils,
  │             gradient(渐变工具), theme(主题系统), ansi_utils(ANSI工具),
  │             output_target(输出目标), formatter(文本格式化零依赖层),
  │             cost(费用计算纯函数), param_formatter(工具参数格式化),
  │             parallel_config / tool_icons / text_formatter,
  │             TrueColor / ColorValue（颜色值对象）, theme_loader（YAML主题加载）
  │             system_monitor(系统监控)
  │
  terminal/   — 终端 I/O 层：blessed适配, LockedTerminal, 窄屏检测, adapter(终端适配器),
  │             capabilities(终端能力检测: TrueColor/256色/UTF-8/Emoji)
  │
  animation/  — 动画基础设施层：AnimatorContext(动画时钟管理器), BreathPalette(呼吸调色板),
  │             composer(动画合成器), transitions(过渡效果:FadeIn/FadeOut/Slide/Typewriter)
  │
  events/     — 事件总线层：DisplayEventBus(显示层事件总线), event_types(21种事件类型),
  │             adapters(事件适配器), event_pool(事件对象池), consumers(事件消费者)
  │
  state/      — 统一状态管理：UISessionState(会话状态), InputState(输入状态),
  │             StreamingState(流式状态), TUIStateTree(聚合容器),
  │             AgentStateStore(多Agent状态), _RenderState(渲染器生命周期),
  │             consumer_registry(消费者注册表)
  │
  components/ — 组件库：TuiComponent基类, 消息组件(Thinking/Answer/UserMsg/ToolOutput/
  │             ToolSummary/Error/Notification/WriteLine), 通用组件(Box/Panel/
  │             Separator/Spinner/ProgressBar/Table/Markup/TreeView),
  │             CostDisplayComponent, SplashScreen(启动品牌屏)
  │
  engine/     — 渲染引擎层：TuiEngine(render线程+命令队列), TuiRenderer(命令分发),
  │             EventDispatcher(事件→命令映射), RenderCommand(命令枚举)
  │
  consumer/   — 消费者 API 层：ChatUIConsumer(生命周期协调), 工厂装配,
  │             协议定义(RenderEngine/BottomBarProtocol), 补全处理器,
  │             错误处理, base_display
  │
  pipeline/   — 消息显示/编辑管线：message_display, message_editor
  │
  widgets/    — 交互控件：bottom_bar(底部固定栏/状态行/输入区/补全弹窗),
  │             command_palette, completion, cursor_tracker,
  │             selector_base, help_panel(快捷键帮助浮层), status_bar
  │
  parallel_display.py — 并行 Agent 显示管理：ParallelDisplay
  │
  frame/      — 纯函数帧渲染器：FrameRenderer(AgentSlot→终端行)


框架入口：
  - framework — TUI 框架统一入口，提供 Framework 单例、create_component、
    EffectRegistry、frame_from_context、get_animator 等公开 API

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

from .config import TuiConfig
from .testing import MockConsumer, MockTerminal
from .layout import Grid, Center
from .components._base import apply_fade_in

__all__ = [
    "TuiConfig",
    "MockConsumer",
    "MockTerminal",
    "Grid",
    "Center",
    "apply_fade_in",
]
