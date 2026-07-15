"""TUI 统一框架 — 整合所有 TUI 相关功能。

分层架构（由底向上）：
  core/       — 核心基础工具层：animator, effects, color, style, state, ttl_cache, text_utils,
  │             gradient(渐变工具), palettes(预定义调色板), theme(主题系统),
  │             ansi_utils(ANSI工具), output_target(输出目标)
  terminal/   — 终端 I/O 层：blessed适配, LockedTerminal, 窄屏检测, adapter(终端适配器)
  animation/  — 动画基础设施层：AnimatorContext, BreathPalette, composer(新增), transitions(新增)
  events/     — 事件系统：DisplayEventBus, event_types, event_pool
  state/      — Agent 状态管理：AgentStateStore, AgentSlot, ToolRecord
  components/ — 组件库：TuiComponent基类, 消息组件, 通用组件(Box/Panel/Separator/Spinner/ProgressBar/Table/Markup)(新增)
  pipeline/   — 消息显示/编辑管线：message_display, message_editor
  widgets/    — 交互控件：bottom_bar, command_palette, completion, cursor_tracker, selector_base 等
  consumer/   — 渲染消费端：TuiRenderer, RenderState, dispatcher, engine, base_display(显示抽象基类)
  parallel/   — 并行 Agent 显示管理：ParallelDisplay, FrameRenderer
  frame/      — 纯函数帧渲染器：FrameRenderer
"""
