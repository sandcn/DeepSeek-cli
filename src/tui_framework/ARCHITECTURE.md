# TUI Framework 架构文档

> 自动生成于 2026-07-16 · 框架版本 0.1.0

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
- **模块**：14 个文件（含 4 个 effects 子模块）
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

本框架从 `src/tui/` 提取零业务依赖模块：

| src/tui/ 模块 | tui_framework 对应 | 关系 |
|--------------|-------------------|------|
| `src/tui/core/effects.py` | `tui_framework.core/effects.py` (+4子模块) | 提取+拆分 |
| `src/tui/core/animator.py` | `tui_framework/core/animator.py` | 提取 |
| `src/tui/core/color.py` | `tui_framework/core/color.py` | 提取 |
| `src/tui/core/style.py` | `tui_framework/core/style.py` | 提取 |
| `src/tui/core/gradient.py` | `tui_framework/core/gradient.py` | 提取 |
| `src/tui/core/palettes.py` | `tui_framework/core/palettes.py` | 提取 |
| `src/tui/core/ansi_utils.py` | `tui_framework/core/ansi_utils.py` | 提取 |
| `src/tui/core/text_utils.py` | `tui_framework/core/text_utils.py` | 提取 |
| `src/tui/core/formatter.py` | `tui_framework/core/formatter.py` | 提取 |
| `src/tui/core/ttl_cache.py` | `tui_framework/core/ttl_cache.py` | 提取 |
| `src/tui/core/state.py` | `tui_framework/core/state.py` | 提取 |
| `src/tui/core/theme.py` | `tui_framework/core/theme.py` | 提取 |
| `src/tui/core/theme_loader.py` | `tui_framework/core/theme_loader.py` | 提取 |
| `src/tui/core/time_format.py` | `tui_framework/core/time_format.py` | 提取 |
| `src/tui/terminal/adapter.py` | `tui_framework/terminal/adapter.py` | 提取 |
| `src/tui/terminal/blessed.py` | `tui_framework/terminal/blessed.py` | 提取 |
| `src/tui/terminal/capabilities.py` | `tui_framework/terminal/capabilities.py` | 提取 |
| `src/tui/terminal/narrow.py` | `tui_framework/terminal/narrow.py` | 提取 |
| `src/tui/terminal/output_target.py` | `tui_framework/terminal/output_target.py` | 提取+增强 |
| `src/tui/terminal/terminal.py` | `tui_framework/terminal/terminal.py` | 提取 |
| `src/tui/events/event_bus.py` | `tui_framework/events/event_bus.py` | 提取+解耦 |
| `src/tui/events/event_types.py` | `tui_framework/events/event_types.py` | 提取+扩展 |
| `src/tui/events/event_pool.py` | `tui_framework/events/event_pool.py` | 提取 |
| `src/tui/animation/composer.py` | `tui_framework/animation/composer.py` | 提取 |
| `src/tui/animation/transitions.py` | `tui_framework/animation/transitions.py` | 提取 |
| `src/tui/framework.py` | `tui_framework/framework.py` | 提取 |

**迁移原则**：src/tui/ 保留完整代码不变，通过 Adapter 模式渐进迁移。新代码优先从 tui_framework 导入。

## 技术债清理记录

| 项目 | 状态 | 说明 |
|------|------|------|
| effects.py 体量过大（~1200行） | ✅ 已清理 | 拆分为 _wave/_sparkle/_train/_compose 四个子模块 |
| build_glow_ansi 双重导出 | ✅ 已清理 | text_utils 版本添加 DeprecationWarning，统一使用 effects 版本 |
| state.py 使用 src._compat.dataclass | ✅ 已清理 | 已改用标准库 dataclasses |
| 错误处理缺失 | ✅ 已完善 | Widget 生命周期方法全量 try/except + DEBUG 日志 |
| 日志与可观测性 | ✅ 已增强 | Widget 生命周期 DEBUG 日志 + AnimatorContext 每 1000 帧 INFO 日志 |

## 版本信息

- 框架版本: 0.1.0
- 最后更新: 2026-07-16
- 维护者: TUI Framework Team
