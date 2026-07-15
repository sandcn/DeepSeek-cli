# TUI Framework 架构文档

> 更新于 2026-07-16 · 框架版本 0.1.1（inline 模式重构后）

## 总览

tui_framework 是 DeepSeek-cli 的零业务依赖 TUI 框架，独立于业务代码（`src/tui/`）存在。
经过 2026-07-16 重构后，项目 TUI 渲染从 **DECSTBM 全屏模式** 切换为 **inline 非全屏逐行覆盖模式**。

### 关键变更（2026-07-16 重构）

| 变更项 | 变更前 | 变更后 |
|--------|--------|--------|
| 渲染模式 | DECSTBM 全屏滚动区域 | `\r\033[K` inline 逐行覆盖 |
| 底部栏光标 | SCOSC/DECRC 保存/恢复 | CUP 绝对定位 |
| 输出目标 | Rich Console OutputAdapter | InlineOutputTarget (IOutputTarget) |
| src/ui/ 废弃层 | 15 个文件 | 已全量删除 |
| src/tui/ 重复模块 | 17 个重复（vs tui_framework） | 重导出存根，统一从 tui_framework 导入 |
| 底部栏 bar.py | ~1050 行 | ~834 行（inline 模式） |

## 分层架构

```
┌─────────────────────────────────────────────────┐
│  layout/          布局系统（VBox/HBox/Flex）      │ ← 最上层：组合控件
├─────────────────────────────────────────────────┤
│  widgets/         控件库（Widget/Input/Button…）  │
├─────────────────────────────────────────────────┤
│  animation/       动画系统（Composer/Transitions） │
├─────────────────────────────────────────────────┤
│  events/          事件系统（EventBus/Types/Reader）│
├─────────────────────────────────────────────────┤
│  terminal/        终端抽象（Adapter/OutputTarget） │
├─────────────────────────────────────────────────┤
│  core/            内核（Color/Style/Effects…）     │ ← 最底层：零业务依赖
└─────────────────────────────────────────────────┘
```

## 依赖方向（强制单向）

```
core → terminal → events → animation → widgets → layout
  ↑       ↑          ↑          ↑           ↑        ↑
  最底层  依赖core   依赖term   依赖events  依赖anim  依赖widgets
```

**规则**：
- core 层不依赖任何上层模块（terminal/events/animation/widgets/layout）
- terminal 层仅依赖 core
- events 层依赖 core + terminal
- animation 层依赖 core
- widgets 层依赖 core + events
- layout 层依赖 widgets + core

## 各层职责

### core/
- **职责**：纯计算工具，零业务依赖
- **模块**：18 个文件（含 4 个 effects 子模块）
  - `_wave.py` — 正弦波/呼吸/波动效果
  - `_sparkle.py` — 闪烁/脉冲/高亮效果
  - `_train.py` — 列车/扫光/极光等流动效果
  - `_compose.py` — EffectRegistry 合成器 + 霓虹/打字机等便捷效果
  - `effects.py` — 统一重导出入口（< 100 行）
  - `animator.py` — AnimatorContext 全局帧号推进器
  - `color.py` — 256 色调色板系统
  - `style.py` — 样式数据类
  - `gradient.py` — 渐变生成器
  - `palettes.py` — 预定义调色板常量
  - `ansi_utils.py` — ANSI 转义序列处理
  - `text_utils.py` — 纯文本工具函数
  - `formatter.py` — 文本格式化
  - `ttl_cache.py` — TTL 缓存
  - `state.py` — 统一状态容器
  - `theme.py` — 层次化 Theme 类
  - `theme_loader.py` — 用户主题加载
  - `time_format.py` — 时间格式化

### terminal/
- **职责**：终端 I/O 抽象
- **模块**：6 个文件
  - `adapter.py` — TerminalAdapter + query_terminal_size()
  - `blessed.py` — BlessedTerminal 单例
  - `capabilities.py` — 终端能力检测
  - `narrow.py` — 窄屏检测与降级
  - `output_target.py` — IOutputTarget Protocol + InlineOutputTarget/TerminalTarget/BufferTarget/NullTarget
  - `terminal.py` — LockedTerminal 线程安全终端写入

### events/
- **职责**：事件系统（发布/订阅、输入解析）
- **模块**：4 个文件
  - `event_types.py` — DisplayEvent + KeyPressEvent/MouseEvent/ResizeEvent/FocusEvent
  - `event_bus.py` — EventBus（queue.Queue + threading.RLock）
  - `event_pool.py` — 事件对象复用池
  - `input_reader.py` — InputReader（Blessed 输入封装）

### animation/
- **职责**：动画/动效系统
- **模块**：3 个文件
  - `composer.py` — AnimationComposer 动效组合器
  - `transitions.py` — 过渡效果与缓动函数
  - `declarative.py` — @effect 声明式动效装饰器

### widgets/
- **职责**：控件库
- **模块**：8 个文件
  - `base.py` — TuiComponent + Widget 基类
  - `input.py` — Input 文本输入控件
  - `button.py` — Button 按钮控件
  - `select.py` — Select 下拉选择控件
  - `checkbox.py` — Checkbox 复选框控件
  - `menu.py` — Menu 菜单控件
  - `dialog.py` — Dialog 对话框控件
  - `animated.py` — AnimatedWidget 声明式动效基类

### layout/
- **职责**：布局容器系统
- **模块**：4 个文件
  - `container.py` — LayoutContainer 抽象基类
  - `vbox.py` — VBox 垂直布局
  - `hbox.py` — HBox 水平布局
  - `flex.py` — Flex 弹性布局

### 顶层
- `__init__.py` — 包入口，定义版本号与分层说明
- `framework.py` — Framework 单例（统一入口）
- `application.py` — Application Protocol（TUI 应用最小契约）

## 循环依赖检查

通过导入顺序分析确认：**无循环依赖**。

所有模块间导入均为单向：
- core → 无内部跨模块依赖（_sparkle → _wave 单向）
- terminal → core
- events → core + terminal
- animation → core
- widgets → core + events
- layout → widgets + core
- framework → core + widgets + layout
- application → widgets + terminal

## 与 src/tui/ 的关系

### 模块分工（重构后）

| 层 | tui_framework（框架层） | src/tui/（业务层） |
|----|------------------------|-------------------|
| core | 零业务依赖模块（18个） | 业务特定模块（7个）：cost, param_formatter, parallel_config, tool_icons, text_formatter, system_monitor, output_target |
| terminal | 终端抽象（6个） | 业务终端（2个）：terminal.py(LockedTerminal), ports.py |
| events | 事件系统（4个） | 事件系统（独立实现，3个） |
| animation | 动画系统（3个） | 重导出存根（2个） |
| widgets | 控件基类（8个） | 业务控件：bottom_bar(9), completion, command_palette, cursor_tracker, help_panel 等 |
| consumer | — | 渲染消费端（17个）：engine, renderer, factory, consumer, adapter, dispatcher 等 |
| components | — | 消息组件（18个）：AnswerBlock, ThinkingBlock, ToolOutputBlock 等 |
| pipeline | — | 消息管线（2个）：message_display, message_editor |
| frame | — | 帧渲染器（1个） |
| parallel | — | 并行显示管理（4个） |
| state | — | Agent 状态管理 |

### 重复模块处理

`src/tui/` 中与 `tui_framework` 重复的模块已处理为以下三种状态之一：

| 处理方式 | 模块列表 | 说明 |
|---------|---------|------|
| 重导出存根 | animator, color, style, gradient, palettes, ansi_utils, text_utils, formatter, ttl_cache, state, theme, theme_loader, time_format（core/）, adapter, blessed, capabilities, narrow（terminal/） | `from tui_framework.xxx import *` 保持向后兼容 |
| 保持独立 | effects.py, terminal.py | effects.py 有业务扩展；terminal.py 含 LockedTerminal |
| 已删除 | src/ui/ 全部 15 个文件 | 废弃层，引用已迁移至 src/tui/core 和 tui_framework |

**导入原则**：新代码优先从 `tui_framework` 导入零业务依赖模块。`src/tui/` 中的重导出存根保留用于向后兼容。

## inline 模式详解

### 核心原理

替代 DECSTBM 全屏滚动区域的渲染方案：
- 终端保持正常全屏滚动模式
- 底部栏使用 CUP 绝对定位在终端底部渲染
- 内容区域直接 `\n` 追加到终端，无需帧覆盖
- 仅使用三种基本 ANSI 序列：`\r\033[K`（清行）、`\033[{r};{c}H`（CUP）、`\033[A`（上移）

### 架构对比

| 维度 | 旧 DECSTBM 模式 | 新 inline 模式 |
|------|----------------|---------------|
| 内容区渲染 | `render_frame` 帧覆盖（SCOSC/DECRC 保存/恢复） | 直接 `write_line()` 追加 |
| 底部栏渲染 | DECSTBM 滚动区域 + SU/SD 滚动 | CUP 定位 + `\r\033[K` 逐行覆盖 |
| 光标管理 | `ensure_cursor_upper`/`ensure_cursor_in_lower` | `force_redraw()` 统一处理 |
| 输出目标 | OutputAdapter (Rich Console) | InlineOutputTarget (IOutputTarget) |
| stdout 追踪 | `_StdoutLineTracker` 包装 | 无需追踪 |
| 测试兼容性 | 依赖真实终端 ANSI | BufferTarget 可替换测试 |

### 关键组件

```
TuiEngine._phase_redraw_bottom()  (10Hz)
    └── _BottomBar.force_redraw()
            ├── _build_all_lines()          → 构建全量行
            │   ├── _build_sep_with_system_stats()  → 分隔线
            │   ├── subagent 面板行
            │   ├── _format_status()        → 状态行
            │   └── _build_input_area_lines() → 输入区域
            │       ├── 补全弹窗
            │       ├── 上分割线（CPU/MEM）
            │       ├── 输入文本行（呼吸占位符）
            │       └── 下分割线（时间戳）
            ├── CUP 跳转到起始行
            ├── 逐行 \r\033[K + 内容 + \n
            └── 清除多余行 + flush
```

## 技术债清理记录

| 项目 | 状态 | 说明 |
|------|------|------|
| effects.py 体量过大（~1200行） | ✅ 已清理 | 拆分为 _wave/_sparkle/_train/_compose 四个子模块 |
| build_glow_ansi 双重导出 | ✅ 已清理 | text_utils 版本添加 DeprecationWarning，统一使用 effects 版本 |
| state.py 使用 src._compat.dataclass | ✅ 已清理 | 已改用标准库 dataclasses |
| 错误处理缺失 | ✅ 已完善 | Widget 生命周期方法全量 try/except + DEBUG 日志 |
| 日志与可观测性 | ✅ 已增强 | Widget 生命周期 DEBUG 日志 + AnimatorContext 每 1000 帧 INFO 日志 |
| DECSTBM 全屏模式 | ✅ 已重构 | 全部替换为 inline `\r\033[K` 模式 |
| src/ui/ 废弃层 | ✅ 已删除 | 15 个文件全量删除，引用迁移至 src/tui/core 和 tui_framework |
| src/tui/ vs tui_framework 重复模块 | ✅ 已统一 | 17 个重复模块改为重导出存根 |
| bar.py 体量过大（~1050行） | ⚠️ 已缩减 | 降至 ~834 行（inline 模式），进一步拆分标记为后续优化 |
| _build_input_area_lines 过长 | ⚠️ 待优化 | ~120 行，可提取到 draw.py 或拆分为子方法 |

## 版本信息

- 框架版本: 0.1.1
- 最后更新: 2026-07-16（inline 模式重构后）
- 维护者: TUI Framework Team
