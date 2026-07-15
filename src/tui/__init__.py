"""TUI 统一框架 — 整合所有 TUI 相关功能。

分层架构（由底向上）：
  core/       — 核心基础工具层（仅保留业务特定模块）：cost(费用计算), param_formatter(工具参数格式化),
  │             parallel_config / tool_icons / text_formatter / system_monitor / output_target
  │             其余零业务依赖模块（animator/color/style/gradient/palettes/ansi_utils/text_utils/
  │             formatter/ttl_cache/state/theme/theme_loader/time_format）已统一从 tui_framework.core 导入
  terminal/   — 终端 I/O 层（仅保留业务特定模块）：LockedTerminal, ports
  │             adapter/blessed/capabilities/narrow 已统一从 tui_framework.terminal 导入
  animation/  — 动画基础设施层：AnimatorContext / BreathPalette 从 core 重导出, composer, transitions
  events/     — 事件系统：DisplayEventBus, event_types, event_pool（保留独立实现，不可替换为 tui_framework.events）
  state/      — Agent 状态管理：AgentStateStore, AgentSlot, ToolRecord
  components/ — 组件库：TuiComponent基类, 消息组件, 通用组件(Box/Panel/Separator/Spinner/
  │             ProgressBar/Table/Markup), CostDisplayComponent(费用显示), TreeView（层级结构展示）,
  │             SplashScreen(启动品牌屏)
  consumer/   — 渲染消费端（inline 模式）：TuiRenderer, RenderState, dispatcher, engine, base_display,
  │             diff_renderer
  pipeline/   — 消息显示/编辑管线：message_display, message_editor
  widgets/    — 交互控件：bottom_bar(inline 模式), command_palette, completion, cursor_tracker,
  │             selector_base, help_panel(快捷键帮助浮层) 等
  parallel/   — 并行 Agent 显示管理：ParallelDisplay, FrameRenderer；基础模块已下沉至 core/，本层保留重导出
  frame/      — 纯函数帧渲染器：FrameRenderer

框架入口：
  - framework — TUI 框架统一入口，提供 Framework 单例、create_component、
    EffectRegistry、frame_from_context、get_animator 等公开 API

2026-07-16 重构摘要（TUI 框架统一重构）：
  - src/ui/ 废弃层已全量删除（15 个文件），引用迁移至 tui/core 和 tui_framework
  - src/tui/ 与 src/tui_framework/ 重复模块（17 个）已统一为从 tui_framework 导入的重导出存根
  - _BottomBar 从 DECSTBM 全屏模式重写为 inline \r\033[K 逐行模式，移除所有全屏 ANSI 序列
  - TuiEngine 渲染管线适配 inline 模式，移除 DECSTBM 相关光标定位调用
  - TuiRenderer 适配 IOutputTarget 接口，替代 OutputAdapter（Rich Console）依赖
  - 18 个组件文件 render_to_adapter → render_to_target
  - 效果增强：分隔线 aurora+shimmer+neon 三合一动效、SplashScreen 彩虹辉光、ProgressBar 脉冲列车、
    Spinner 多样式旋转、Panel 辉光边框、ToolOutput 语法高亮
  - 新代码应优先从 tui_framework 导入零业务依赖模块
"""
