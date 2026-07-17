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
  bus/        — 事件总线层：DisplayEventBus(显示层事件总线), event_types(21种事件类型),
  │             adapters(事件适配器), event_pool(事件对象池)
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
  parallel/   — 并行 Agent 显示管理：ParallelDisplay
  │
  frame/      — 纯函数帧渲染器：FrameRenderer(AgentSlot→终端行)


框架入口：
  - framework — TUI 框架统一入口，提供 Framework 单例、create_component、
    EffectRegistry、frame_from_context、get_animator 等公开 API

2026-07-17 框架重构摘要：
  - engine/ 目录新建：consumer/engine, renderer, dispatcher, const, utils, lock
    迁入 engine/，consumer/ 保留向后兼容重导出存根
  - bus/ 目录新建：events/event_bus, event_types, adapters, consumers, event_pool
    迁入 bus/，events/ 保留向后兼容重导出存根
  - state/ 统一收敛：core/state.py 拆分为 session_state/input_state/streaming_state/
    tui_state_tree；consumer/render_state 迁入 state/render_state；
    consumer/state 迁入 state/consumer_registry
  - animation/ 增强：core/animator + core/palettes 迁入 animation/，
    core/ 保留向后兼容重导出存根
  - 所有旧导入路径保留兼容存根，测试无需修改
"""
