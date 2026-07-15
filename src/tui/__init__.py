"""TUI 统一框架 — 整合所有 TUI 相关功能。

分层架构（由底向上）：
  core/       — 核心基础工具层：animator, effects, color, style, state, ttl_cache, text_utils,
  │             gradient(渐变工具), palettes(预定义调色板), theme(主题系统),
  │             ansi_utils(ANSI工具), output_target(输出目标),
  │             formatter(文本格式化零依赖层), cost(费用计算纯函数),
  │             param_formatter(工具参数格式化)
  terminal/   — 终端 I/O 层：blessed适配, LockedTerminal, 窄屏检测, adapter(终端适配器),
  │             capabilities(终端能力检测: TrueColor/256色/UTF-8/Emoji)
  animation/  — 动画基础设施层：AnimatorContext, BreathPalette, composer, transitions
  events/     — 事件系统：DisplayEventBus, event_types, event_pool
  state/      — Agent 状态管理：AgentStateStore, AgentSlot, ToolRecord
  components/ — 组件库：TuiComponent基类, 消息组件, 通用组件(Box/Panel/Separator/Spinner/
  │             ProgressBar/Table/Markup), CostDisplayComponent(费用显示),
  │             SplashScreen(启动品牌屏)
  consumer/   — 渲染消费端：TuiRenderer, RenderState, dispatcher, engine, base_display,
  │             diff_renderer(差异渲染, 从 ui/diff_renderer 迁移)
  pipeline/   — 消息显示/编辑管线：message_display, message_editor
  widgets/    — 交互控件：bottom_bar, command_palette, completion, cursor_tracker,
  │             selector_base, help_panel(快捷键帮助浮层) 等
  parallel/   — 并行 Agent 显示管理：ParallelDisplay, FrameRenderer
  frame/      — 纯函数帧渲染器：FrameRenderer

已迁移模块（从 src/ui/）：
  - ui/diff_renderer → tui/consumer/diff_renderer
  - ui/formatters/param_formatter → tui/core/param_formatter
  - ui/components/cost_display → tui/core/cost + tui/components/_cost
  - ui/ansi → tui/core/ansi_utils
  - ui/colors → tui/core/gradient + tui/core/palettes
  - ui/theme → tui/core/theme
  - ui/output_target → tui/core/output_target
  - ui/terminal_adapter → tui/terminal/adapter
  - ui/base_display → tui/consumer/base_display

框架入口：
  - framework — TUI 框架统一入口，提供 Framework 单例、create_component、
    EffectRegistry、frame_from_context 等公开 API
"""
