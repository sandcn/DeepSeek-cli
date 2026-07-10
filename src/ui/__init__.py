"""ui — 显示层适配器与基础设施

职责范围：
- BaseDisplay 抽象与实现（终端 TUI / Web UI 显示适配器）
- DisplayEventBus 实现（事件总线基础设施，委托 CoreEventBus）
- 底层显示组件（bottom_bar、diff_renderer、msg_list、theme、colors）
- 输出目标抽象（IOutputTarget Protocol + 实现）
- TUI 组件（status_bar、message_editor、command_palette、session_switcher）
- 并行 Agent 显示引擎（ParallelDisplay）
- 事件适配器（DisplayEventAdapter、EventBusDisplayProxy）

依赖关系：
- 被 chat_ui/ 和 core/adapters/ 引用
- 不依赖 core/ 模块（事件类型除外）

与 chat_ui/ 的边界：
- ui = 显示适配器 + 基础设施（提供底层能力）
- chat_ui/ = 消费者 + 渲染引擎（使用 ui/ 基础设施实现渲染）
"""

from __future__ import annotations
