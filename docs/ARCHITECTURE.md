# 架构分层规范

本项目采用**端口-适配器（六边形）/ 分层**架构。本文件是依赖方向的单一真源，
并由 `tests/test_architecture_layering.py` 静态守卫。

## 1. 分层

| 层 | 包 | 职责 |
|---|---|---|
| 基础层 | `src/config`、`src/paths`、`src/_compat*`、`src/constants` | 配置、路径、兼容、常量（零业务依赖） |
| 端口层 | `src/core/ports`、`src/core/events`（含 `display_types`）、`src/core/telemetry` | 纯抽象协议 + 核心事件类型（零外部实现依赖） |
| 领域层 | `src/core/*`（除 `adapters`/`commands`）、`src/core/internal/*` | 会话、Agent、工具调度、上下文、状态机 |
| 基础设施层 | `src/api`、`src/tools`、`src/mcp`、`src/skills`、`src/prompt_builder` | 模型调用、工具、MCP、技能、提词 |
| 适配器层 | `src/core/adapters` | 端口的具体实现，**允许**桥接表现层 |
| 表现层 | `src/tui`、`src/renderer` | 终端 UI、增量渲染 |
| 应用层 | `src/app_init`、`src/app_loop`、`src/application.py`、`src/clawbot` | 应用编排、模式策略、入口 |

## 2. 依赖方向规则

1. **领域层不得依赖表现层**：`src/core`（除 `src/core/adapters`）禁止
   `import src.tui` / `import src.renderer`（含相对导入与函数内延迟导入）。
   - UI 命令层（`core/commands`）经 `core/adapters/ui_runtime` 桥接工厂访问
     表现层。
   - 显示事件类型定义位于 `core/events/display_types.py`（核心层），
     `tui/events/event_types.py` 仅 re-export 兼容。
2. **适配器层允许桥接**：`src/core/adapters/*` 可依赖表现层/基础设施层，
   作为端口的默认实现与依赖倒置工厂。
3. **无循环依赖**：全项目 import 图（含延迟导入与 `TYPE_CHECKING`）无环。
   跨层反向调用一律经**注册式钩子**解耦，例如：
   - `api/stream/_usage_hook.py`（`core.context_manager` ↔ `api` 流式管线）
   - `mcp/_runtime.py`（`mcp.tool` ↔ `mcp.manager`）
   - `tui/ink/_render_api.py::register_session_cls`（`_render_api` ↔ `session`）
4. **循环打破优先提取中介模块**：共享纯函数/数据下沉到独立零依赖模块，例如
   `core/internal/shared/_message_text.py`、`tools/_tool_policy.py`、
   `app_loop/_agent_factory.py`。

## 3. 守卫测试

`tests/test_architecture_layering.py` 固化以下约束（架构回归即失败）：

- `test_core_domain_does_not_depend_on_presentation`：领域层无表现层依赖。
- `test_module_level_imports_have_no_cycles`：模块顶层 import 图无环。
- `test_full_ast_imports_have_no_cycles`：完整 AST import 图无环。

修改 import 结构后请运行：

```bash
python -m pytest tests/test_architecture_layering.py -q
```

## 4. 新增代码指引

- 领域层需要 UI 能力 → 经 `core/ports` 抽象，或经 `core/adapters` 桥接工厂。
- 事件类型 → 定义在 `core/events/display_types.py`。
- 跨层反向回调 → 使用注册式钩子模块（发送方调用、实现方注册），避免直接
  import 造成循环。
- 表现层实现端口 → 放在 `tui`/`renderer`，由组合根（`app_init`/`app_loop`）
  注入或在适配器中桥接。
