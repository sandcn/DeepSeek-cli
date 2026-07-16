"""TUI 统一框架 — 整合所有 TUI 相关功能。

分层架构（由底向上）：
  core/       — 核心基础工具层：animator, effects, color, style, state, ttl_cache, text_utils,
  │             gradient(渐变工具), palettes(预定义调色板), theme(主题系统),
  │             ansi_utils(ANSI工具), output_target(输出目标),
  │             formatter(文本格式化零依赖层), cost(费用计算纯函数),
  │             param_formatter(工具参数格式化), parallel_config / tool_icons /
  │             text_formatter（从 parallel 下沉）, TrueColor / ColorValue（颜色值对象）,
  │             theme_loader（YAML主题加载）
  terminal/   — 终端 I/O 层：blessed适配, LockedTerminal, 窄屏检测, adapter(终端适配器),
  │             capabilities(终端能力检测: TrueColor/256色/UTF-8/Emoji)
  animation/  — 动画基础设施层：AnimatorContext / BreathPalette 从 core 重导出, composer, transitions
  events/     — 事件系统：DisplayEventBus, event_types, event_pool
  state/      — Agent 状态管理：AgentStateStore, AgentSlot, ToolRecord
  components/ — 组件库：TuiComponent基类, 消息组件, 通用组件(Box/Panel/Separator/Spinner/
  │             ProgressBar/Table/Markup), CostDisplayComponent(费用显示), TreeView（层级结构展示）,
  │             SplashScreen(启动品牌屏)
  consumer/   — 渲染消费端：TuiRenderer, RenderState, dispatcher, engine, base_display,
  │             diff_renderer(差异渲染, 从 ui/diff_renderer 迁移)
  pipeline/   — 消息显示/编辑管线：message_display, message_editor
  widgets/    — 交互控件：bottom_bar, command_palette, completion, cursor_tracker,
  │             selector_base, help_panel(快捷键帮助浮层) 等
  parallel/   — 并行 Agent 显示管理：ParallelDisplay, FrameRenderer；基础模块已下沉至 core/，本层保留重导出
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
    EffectRegistry、frame_from_context、get_animator 等公开 API

2026-07-15 重构摘要：
  - UIDisplayAdapter 迁移：从已废弃的 src/ui/adapters.py 迁移至 src/webui/adapter.py，
    保持 webui 层自包含，不污染 tui 层
  - Framework.get_animator()：新增公开 API，返回 AnimatorContext 单例实例，
    组件可通过 Framework 统一获取动画时钟
  - EffectRegistry 组合效果：BottomBar 分隔线使用 EffectRegistry.compose(["aurora", "shimmer"])
    替代单色 sine_color，产生 aurora 极光 + shimmer 流光组合视觉效果
  - 组件动效升级：ErrorBlock / NotificationBlock 新增 FadeIn 入场过渡；
    SplashScreen 接入 build_rainbow_ansi 彩虹效果；
    ProgressBar 支持 pulse_mode 脉冲列车动效
"""
