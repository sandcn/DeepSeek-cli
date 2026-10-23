# TUI / Ink 框架架构

本文描述 `src/tui`（React Ink 风格自研 TUI）的分层、依赖规则与扩展点。
实现细节仍以代码 docstring 为准；本文只固定**结构约定**，供评审与新增代码对照。

## 1. 分层

```
L3  src/app_loop、src/app_init        应用主循环 / 组合根（经 ChatUIConsumer 使用 TUI）
L2  src/tui/app                        AppModel、视图（views）、视图注册表、组件树
L2  src/tui/{_assembly,_consumer,_ink_bridge,pipeline,subagent}
                                       装配、事件消费、兼容桥、消息管道
L1  src/tui/ink                        框架：element / fiber / hooks / reconciler /
                                       layout / paint / renderer / session / terminal / widgets
L0  src/_text_width、src/_ansi_text、src/_compat
    src/tui/_width、src/tui/_ansi_sequences、src/tui/_command_protocol
    src/tui/core/{style,color}         纯数据 / 纯函数 / 协议（零 UI 状态）
```

依赖只允许向下。L1 **不得** import L2 或 `src/{api,core,renderer,tools,config,kernel,plugins}`
与 `src/presentation_data`。

该规则由 `tests/tui/ink/test_layering_contract.py` 可执行地守护（含函数内惰性 import）。

## 2. 数据流

```
输入   InputIO(读字节) → InputDispatcher(解码/分发) → InkSession.update_input
       → hooks input router → 组件 use_input → AppModel 状态变更
命令   RenderCmd（_command_protocol）→ InkSession 优先队列 → APPLY 阶段
       → apply_cmd(model, cmd) → 模型/渲染缓存变更
渲染   render 线程 60Hz → _render_frame_impl（尺寸/ref low/兜底）
       → build_tree → Reconciler（fiber 复用/hooks/effects）
       → layout_tree（flexbox 测量）→ render_frame（绘制）→ InkRenderer（行级 diff 写屏）
```

单帧阶段见 `InkSession.RenderLoopPhase`：
`SIGWINCH → INPUT → FRAME_TICKS → PANELS → SYSTEM_STATS → DRAIN_COMMANDS → APPLY → RENDER`。

## 3. 依赖注入（框架不再知道应用）

`InkSession` 的构造参数即框架的**扩展点**；生产装配在
`src/tui/_assembly_steps.create_framework()` 注入，测试/独立 `render()` 会话走内置兜底。

| 注入项 | 作用 | 未注入时的兜底 |
|---|---|---|
| `width_cache` | 终端尺寸（TTL / 覆盖 / SIGWINCH 联动） | `ink.terminal.TerminalSizeCache` |
| `output_lock` | 与外部写入互斥的渲染锁 | `ink._locks.default_output_lock`（RLock） |
| `sigwinch_processor` / `sigwinch_unregister` | 信号消费 / 注销 | 无信号集成 |
| `blocking_runner` | suspend 回调里的协程桥 | `asyncio.run` |
| `frame_tick_registry` | 每帧驱动的周期任务（`run()`） | 不驱动 |
| `system_stats_probe` | CPU/MEM 采集（`start` / `get_cpu_and_mem`） | 不采集 |
| `command_coalescer` | 同帧相邻增量合并 | 逐条应用 |
| `view_refresh_provider` | 当前模态视图的定时刷新间隔 | 不做定时刷新 |
| `cursor_provider` | 输入区光标定位 | 保持光标隐藏 |
| `input_fiber_finder` | 输入区 fiber 定位 | `ink._fiber_utils.find_input_fiber`（按 `dataInputArea` 标记） |

表现层数据表（边框字符 / UI 默认值 / Spinner 预设 / Badge 度量）经
`ink._data.set_data_provider(presentation_data)` 注入；未注入时用内置兜底表
（`tests/tui/ink/test_layering_contract.py` 校验兜底表与真源同值）。

## 4. 视图约定

- 视图声明在 `src/tui/app/view_registry.py`（`ViewSpec`，点分字符串组件引用，
  可被清单禁用/覆盖）。新增视图 = 注册表加一条 + 组件实现 `use_modal_scope` /
  `use_input`。
- 列表型视图（左列表 + 右详情 + 搜索/帮助/状态行）应使用
  `_list_view.use_list_view()` 推导上下文，避免重复实现状态夹紧、分栏、视口预算与
  右栏 memo（已迁移：notify、mcp；其余视图按同一模式迁移）。
- 模态视图统一用 `_modal_view.use_modal_scope` / `is_modal_close_key` /
  `empty_modal_frame`；不可见时返回零高度占位（保持 fiber 结构稳定）。
- 视图组件的「未打开态」与「打开态」都必须可渲染：
  `tests/tui/app/test_view_registry_render.py` 遍历注册表断言。

## 5. 兼容层

以下模块是**纯 re-export**（迁移调用点后可删）：`src/tui/input.py`、
`src/tui/core/singleton.py`、`src/tui/events/{event_bus,event_types,publish}.py`、
`src/tui/_ink_bridge_compat.py`，以及 `src/tui/_const.py` 的命令协议部分
（真源 `src/tui/_command_protocol.py`）。

新增代码请直接 import 真源；不得在兼容层里加逻辑。

## 6. 变更约束

- 框架内新增 import 必须通过分层契约测试；
- `InkOptions` 默认值须与 `TuiConfig` 一致（同为测试守护）；
- 视图注册表的点分引用只允许指向可 import 的模块属性（元测试守护）；
- 覆盖率门槛随测试补齐分阶段上调（见 `pyproject.toml` 注释）。
