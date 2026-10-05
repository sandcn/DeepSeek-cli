# 插件架构（一切皆插件）

本项目采用 **「一切皆插件」** 的 Agent 运行时架构：

- **内核（Cordis-like）** 只做三件事：加载插件、卸载插件、解析依赖。它对模型、
  工具、甚至「Agent 是什么」都不带任何观点。
- **所有核心行为都是插件**：工具、技能、MCP、模型适配器、Agent 循环、会话、
  命令、事件、UI/渲染器、策略，全部坐在内核之上，通过稳定的 `ctx.<key>`
  服务相互发现，而非直接 import 具体实现。
- **产品与扩展的界线消失**：内置能力与第三方插件走同一套加载、依赖、生命周期
  机制，可以热挂载、卸载、重启。

## 1. 内核

代码位置：`src/kernel/`（零业务依赖）。

| 概念 | 说明 | 实现 |
|---|---|---|
| Kernel | 插件内核：服务容器 + Fiber 列表 + 事件总线 | `kernel.py` |
| Context | 插件上下文：服务容器 + 可逆副作用注册点 | `context.py` |
| Service | 插件向其他插件提供能力的基类 | `service.py` |
| Plugin | 插件描述（函数 / Service 子类 / 可调用对象） | `plugin.py` |
| Fiber | 插件生命周期作用域（状态机） | `fiber.py` |
| EventBus | 五种分发模式的事件总线 | `events.py` |

### Fiber 状态机

```
PENDING → LOADING → ACTIVE
               ↘ FAILED
ACTIVE → UNLOADING → DISPOSED
```

- `PENDING`  已声明，依赖未就绪；
- `ACTIVE`   运行中；
- `FAILED`   apply 抛出异常；
- `DISPOSED` 已完全卸载（非手动卸载的 Fiber 在依赖恢复后会自动重新加载）。

### 依赖驱动的加载与卸载

插件通过 `inject` 声明所需服务；所有依赖就绪前 Fiber 停在 `PENDING`。
某个服务消失（提供方被卸载）时，依赖它的插件自动卸载
（`ACTIVE → DISPOSED`）；服务恢复后自动重新加载。加载顺序由服务依赖表达，
无需手动编排启动序列（时空可组合性）。

### 可逆副作用

`ctx.provide()` / `ctx.on()` / `ctx.effect()` 注册的一切都绑定到当前 Fiber：

```python
@plugin("demo", inject=["config"])
def apply(ctx):
    ctx.provide("demo", DemoService(ctx))     # 服务，卸载时撤销
    ctx.on("some-event", handler)              # 事件监听，卸载时移除
    ctx.effect(lambda: resource.close)         # 自定义资源，卸载时释放（逆序）
```

卸载时清理回调按注册顺序**逆序**执行；需要严格顺序的清理放进同一个
`ctx.effect()` 返回的清理函数中。

### 事件分发模式

| 方法 | 语义 |
|---|---|
| `ctx.emit(event, *a)` | 顺序触发，不等待协程监听器，无返回值 |
| `ctx.waterfall(event, *a)` | 环绕中间件，监听器签名 `(*a, next)`，可包装或短路 |
| `ctx.parallel(event, *a)` | 并行执行并等待 |
| `ctx.serial(event, *a)` | 顺序执行，返回最后一个返回值 |
| `ctx.bail(event, *a)` | 顺序执行，遇首个非 None 返回值即停止 |

### 嵌套上下文与热挂载

```python
ctx.plugin(child_plugin)        # 子 Fiber：继承父上下文，独立生命周期
await fiber.dispose()           # 永久卸载：递归卸载子插件 + 撤销全部注册
await kernel.reload("tools")    # 完整卸载后重新加载
kernel.mount_file("plugins/x.py")  # 从文件热挂载
```

## 2. 配置组装层：Profile / Bundle / Patch

代码位置：`src/kernel/config_tree.py`、`src/kernel/manifest.py`。

- **Bundle**：一组插件条目，可 `includes` 其它 bundle；
- **Profile**：选择一组 bundle，并叠加一组 Patch；
- **Patch**：按插件 id（`<bundle>::<id>`）覆盖 config 或置 `disabled`。

内置清单位于 `src/plugins/manifest.py`，内置 profile：

| profile | 内容 |
|---|---|
| `cli` | 完整终端应用（默认） |
| `headless` | 无 UI 后端运行时 |
| `minimal` | 最小内核 |
| `full` | cli 的语义化别名 |

打印最终运行时配置：

```bash
python chat.py --dump-config
python chat.py dump-config --profile headless
python chat.py run --profile minimal --dump-config
```

外部清单目录（`plugins/`、`~/.chat_config/plugins`）中的 `*.yml` / `*.json`
会被自动发现并合并；其中的独立插件文件也会被自动热挂载。

## 3. 内置插件

代码位置：`src/plugins/`。

| 插件 | 服务 key | 内容 |
|---|---|---|
| `config` | `ctx.config` | 运行时配置、base_url、模型列表 |
| `events` | `ctx.events` | 核心事件总线 + 显示事件总线（服务独占，单例内核真源） |
| `output` | `ctx.output` | 默认输出端口（无锁/持锁写入，provider 可替换） |
| `cache` | `ctx.cache` | 通用缓存（可替换 `CachePort`） |
| `observability` | `ctx.observability` | 可观测性 provider（指标/追踪/遥测，可替换） |
| `prompt` | `ctx.prompt` | 系统提词、子代理提词、空模式 |
| `fs` | `ctx.fs` | 文件系统能力接缝（Definition+Provider+Consumer） |
| `subprocess` | `ctx.subprocess` | 子进程能力接缝 |
| `shell` | `ctx.shell` | Shell 能力接缝（默认经 subprocess 语义） |
| `terminals` | `ctx.terminals` | 持久终端能力接缝 |
| `jobs` | `ctx.jobs` | 后台任务能力接缝 |
| `sandbox` | `ctx.sandbox` | 沙盒能力接缝（路径/argv 校验与包装） |
| `policy` | `ctx.policy` | 工具可用性、路径白名单、沙盒 Provider |
| `notifications` | `ctx.notifications` | 桌面通知 provider（可替换/禁用） |
| `agents` | `ctx.agents` | 活跃 Agent 注册表 + `agent/*` 事件域 |
| `persistence` | `ctx.persistence` | 会话持久化 provider（可替换存储后端） |
| `checkpoint` | `ctx.checkpoint` | 断点存储 provider（可替换存储后端） |
| `session_log` | `ctx.session_log` | 仅追加会话日志（会话事实源） |
| `session_projections` | `ctx.session_projections` | 投影 seam（增量折叠已提交事件） |
| `tools` | `ctx.tools` | 工具注册表 + 工具执行管线 |
| `tools_builtin` | — | 显式注册全部内置工具（每个注册都是可逆副作用） |
| `context` | `ctx.context` | 上下文管理器创建 + 压缩策略注册（可替换） |
| `seams` | `ctx.seams` | 能力接缝汇总（自省与批量替换 Provider） |
| `skills` | `ctx.skills` | 技能注册表 |
| `presets` | `ctx.presets` | 每会话能力组合（isolate 作用域） |
| `invariants` | `ctx.invariants` | 运行时自检（断言插件树关系） |
| `llm` | `ctx.llm` | 模型适配器路由 + 异步模型端口 |
| `llm_provider_deepseek` / `llm_provider_anthropic` / `llm_provider_ollama` / `llm_provider_openai_compat` | — | 内置模型 provider（独立注册项，外部插件可覆盖/扩展） |
| `sessions` | `ctx.sessions` | 会话创建/恢复/保存 |
| `agent_loop` | `ctx.agent_loop` | Agent 组装与循环 |
| `commands` | `ctx.commands` | 命令插件注册表 |
| `mcp` | `ctx.mcp` | 外部 MCP 工具接入（服务独占 `McpManager`） |
| `clawbot` | `ctx.clawbot` | 微信远程控制模式装配 |
| `renderer` | `ctx.renderer` | 增量流式 Markdown 渲染 |
| `ui` | `ctx.ui` | 终端 UI 运行时桥接 + TUI 装配入口（独占宽度缓存 / SubAgent 面板控制器） |
| `app` | `ctx.app` | 应用组合根：可观测性 / 输出消费者 / 信号 / 错误处理器 / 子命令 / 模式 / clawbot 装配 |

组合根 `src/plugins/bootstrap.py::build_kernel()` 解析 Profile、挂载插件、
等待依赖稳定，并登记为进程级当前内核；`shutdown_kernel()` 逆序卸载。

### 3.1 应用层装配（组合根）

`src/app_init/main.py::main()` 是唯一的应用组合根：解析 Profile（默认 `cli`）
→ `build_kernel()` 构建内核插件树 → 运行时组件全部经内核服务解析，而非直接
import 具体实现：

| 运行时组件 | 解析入口 | 内核服务 |
|---|---|---|
| 事件化 Agent（TUI 对话） | `app_loop/_agent_factory.py::_make_event_agent` | `ctx.agent_loop` |
| 无 UI Agent（会话默认） | `core/session.py::ChatSession._create_default_agent` | `ctx.agent_loop` |
| ChatSession（交互/单次/clawbot/应用） | `app_loop/_session_factory.py::create_session` | `ctx.sessions` |
| 终端 UI（ChatUIConsumer） | `app_loop/_ui_factory.py::create_chat_ui` | `ctx.ui` |
| 工具注册表 | `core/agent.py` / `core/tool_executor_async.py` | `ctx.tools` |
| 模型端口 / 配置端口 / 提示词端口 | `core/agent.py` | `ctx.llm` / `ctx.config` / `ctx.prompt` |
| MCP 连接 | `main.py`（`kernel.resolve_service("mcp")`） | `ctx.mcp` |

领域层/应用层需要「当前内核提供的实现」时，经适配器
`src/core/adapters/kernel_runtime.py` 依赖倒置访问（`active_*` 访问器：
内核缺失时回退既有默认实现，保证单元测试与独立调用兼容）。

> **注册表同源**：`ctx.tools` 使用进程级默认注册表
> （`ToolRegistry.default()`），与 `ToolScheduler.default()` 及 MCP 动态工具
> 注册指向同一实例，避免「内核注册表 ≠ 全局默认注册表」导致 MCP 工具无法被
> 调度器 dispatch。

## 4. 编写一个插件

```python
from src.kernel import plugin, Service

class GreetingService(Service):
    provide = "greeting"
    inject = ("config",)

    def hello(self, name: str) -> str:
        return f"hello {name}"

@plugin("greeting", inject=["config"], provide=["greeting"])
def apply(ctx):
    ctx.provide("greeting", GreetingService(ctx))
```

把文件放入 `plugins/` 或 `~/.chat_config/plugins/`，或在清单中声明即可加载。

## 5. 空间可组合性：extend / isolate / intercept

除依赖驱动的加载/卸载（时间可组合性）外，内核还提供 Cordis 的空间可组合性：
同一份代码可以在不同子树里解析到不同服务实现/配置。

```python
child = ctx.extend()                 # 作用域子上下文：继承父级，可本地覆盖
child.provide("svc", MyImpl())       # 父级/内核不受影响

iso = ctx.isolate("llm")             # 隔离子上下文：llm 只解析本地，不穿透
iso.provide("llm", FakeLlm())

wrapped = ctx.intercept("svc", lambda v: LoggingProxy(v))  # 拦截解析结果
```

- 作用域上下文中的 `provide` 落在本地作用域；同一作用域重复提供同名服务抛
  `ServiceExists`（一个能力同一作用域只允许一个实现）。
- 根上下文与 Fiber 上下文保持既有全局栈语义（提供覆盖），向后兼容。
- 子插件 `ctx.plugin(child)` 挂在隔离/作用域上下文下时，继承该作用域的解析。

`ctx.presets` 即基于 `isolate` 实现「每会话能力组合」：`presets.scope(name)`
派生隔离作用域并挂上 preset，不同会话互不污染。

## 6. 工具是插件 + 工具执行管线

工具与策略分离（dsh 的 capability seam）：

- 内置工具由 `tools_builtin` 插件经 `ctx.tools.register` 显式注册；
- 外部插件用 `ctx.tools.register(ToolClass)` 或
  `ctx.tools.define(name, schema, handler)` 注册动态工具（与内置工具同构调度）；
- 清单可用 `src.plugins.tool_plugin`（config: `{tool: "pkg.mod.ToolClass"}`）声明单个工具；
- 工具执行是 waterfall 管线（注册即副作用）：

| 事件 | 签名 | 用途 |
|---|---|---|
| `tools/pre-execute` | `(call, next)` | allow/deny/ask 决策（返回 `{"allow": False, "reason": ...}` 拒绝） |
| `tools/execute` | `(call, next)` | 环绕执行（超时/重试/审计） |
| `tools/post-execute` | `(call, output, next)` | 改写工具结果 |

策略插件（`ctx.policy`）正是经 `tools/pre-execute` 挂进管线的：按 `agent_type`
的工具排除表与 plan 路径白名单裁决每次调用。

## 7. 组合与分发：Overlay / Profile 目录 / Plugin CLI

- **Overlay（`--patch`）**：在 Profile 解析结果之上 insert / replace / disable 插件：

  ```bash
  python chat.py --dump-config --patch my.cordis.patch.yml
  python chat.py run --patch my.cordis.patch.yml
  ```

  支持 dsh 风格的列表（`- insert: [...]` / `- {id, config}` / `- {id, disabled: true}`）
  与字典（`insert` / `replace` / `disable`）两种形态。

- **用户 Profile 目录**：`~/.chat_config/profiles/<profile>/` 内的清单文件会被
  合并进配置树；其中 `cordis.patch.{yml,yaml,json}` / `overlay.*` 作为该 profile
  的 Overlay 自动叠加。另有机器级 `~/.chat_config/cordis.patch.yml`。
- **Plugin CLI**：

  ```bash
  python chat.py plugin list               # 内置/外部/entry-point/已安装
  python chat.py plugin add ./my_plugin.py # 安装到 ~/.chat_config/plugins/
  python chat.py plugin remove my_plugin.py
  ```

- **entry-points**：安装的 Python 包若声明 `dsh.plugins` 组，启动时由组合根
  `build_kernel` 自动发现并挂载（单个加载失败被隔离，不影响其余插件）。

## 8. 运行时自省与自修改（cordis 工具）

cordis 工具族**全局禁用**：任何 agent（主 Agent 与全部 SubAgent 类型）都不能
加载。工具类仍保留在 `src/tools/cordis.py`，但工具发现
（`discover_builtin_tools`）会跳过 `tool_policy.GLOBAL_DISABLED_TOOLS` 中的工具，
它们不会进入注册表，也不会出现在任何 agent 的 schema 中。

| 工具 | 原作用（已禁用） |
|---|---|
| `cordis_inspect` | 列出内核已有服务与插件 Fiber 状态/依赖 |
| `cordis_define` | 把插件源码写入 `.chat/runtime_plugins/<name>.py` |
| `cordis_run` | 热挂载该插件文件到当前内核 |
| `cordis_stop` | 卸载指定插件（撤销其全部注册） |
| `cordis_undefine` | 卸载并删除插件文件 |

动态插件只存在于当前进程内存（不写 `~/.chat_config`、不改 profile、不跨重启）；
注册随 Fiber 生命周期撤销。安全边界与 bash 同级。工具禁用后，该机制不再由
模型经 agent 工具触发。

## 9. 运行时不变量

`ctx.invariants` 在独立 Fiber 里断言插件树自身的关系（服务合法性、Fiber 依赖
一致、工具注册表与调度器同源、agent_loop 依赖齐全、presets 内置项存在），
启动自检 + 可随时 `check()`。第三方插件可注册自己的不变量（注册即副作用）。

```bash
python chat.py --check-invariants      # 构建内核后运行自检并退出
```

`ctx.invariants` 现内置 `agents.messages_recorded` 不变量——断言每个活跃
Agent 的消息视图与其会话日志投影一致（见第 11 节「模型可见即已记录」）。

## 10. 事件域：会话事件 / Agent 事件 / 能力事件

对应 dsh 的三类事件域（定义于 `src/core/events/agent_types.py`），选对事件域
是大多数改动的第一个决定：

| 事件域 | 类型常量 | 语义 | 用法 |
|---|---|---|---|
| 会话事件（持久） | `SessionEventType` | 追加到会话日志的持久事实 | 需要在重载后仍存在（上下文、回放、fork、遥测） |
| Agent 事件（实时） | `AgentEventType` | 携带活跃 Agent 的扩展点 | 观察或拦截进行中的工作 |
| 能力事件（接缝） | `CapabilityEventType` | 向 `fs/*`、`tools/*`、`telemetry/*` 附加策略 | 策略与适配器，无需导入循环 |

会话事件包括 `turn/start`、`turn/end`、`step/start`、`step/end`、
`system/message`、`user/message`、`assistant/message`、`assistant/attempt`、
`tool/result`、`request/header`、`request/context`，以及结构变更
`session/insert|replace|delete|truncate|reset`。所有会话事件同时经
`session/event` 通道广播。

Agent 事件包括 `agent/created`、`agent/destroyed`、`agent/inbox`、
`agent/pre-step`、`agent/step-start|step-end`、`agent/request`、
`agent/assistant-stream`、`agent/turn-stopping`、`agent/status`、
`agent/validation`、`agent/continuation`。

## 11. 会话日志即唯一事实源

`src/core/session_log/`（dsh `core/session` 的对应实现）：

- **`SessionLog`**：仅追加的 `SessionEvent` 日志，唯一写入入口 `append()`；
- **`LoggedMessageList`**：由日志驱动的消息列表视图，实现完整
  `MutableSequence` 语义（append/pop/切片赋值/删除/清空），运行时
  `Agent.messages` / `SubAgent.messages` 即此视图，读写都落到日志；
- **`derive_messages()`**：从事件投影模型历史；fork / 恢复 / 回放 / 遥测
  都从同一份持久事实派生；
- **`LoggedMessageList.verify()`**：断言视图与日志投影一致——即
  **「模型可见即已记录」**，由 `agents.messages_recorded` 不变量在运行期
  持续校验。

```python
from src.core.session_log import SessionLog, LoggedMessageList

log = SessionLog(session_id="s1")
view = LoggedMessageList(log, initial=[{"role": "system", "content": "sys"}])
view.append({"role": "user", "content": "hi"})
assert view.verify()                       # 视图 == 日志投影
forked = log.fork(at=1)                     # 会话 fork：派生到指定事件位置
replayed = SessionLog.restore(log.snapshot())  # 恢复 / 回放
```

## 12. 投影 seam：`ctx.sessionProjections`

`ProjectionRegistry`（dsh `ctx.sessionProjections`）：已注册单元**增量折叠**
已提交事件，host 消费方通过 `state_of(name, events)` 读取单个类型化状态，
载体通过 `snapshot(events)` 批量取得裁剪后的客户端视图。内置
`turnBoundary` 投影折叠 `turn/*` 与 `step/*`，产出当前轮次边界。

```python
projections = kernel.resolve_service("session_projections")
projections.register("myCounter", lambda state, event: (state or 0) + 1, initial=lambda: 0)
state = projections.state_of("myCounter", log.events())
```

## 13. 主循环事件切面：turn / step

`src/core/internal/agent/_event_facets.py` 把主循环暴露为可拦截的事件切面，
`Pipeline.run_round_async` 依次驱动：

```
turn/start → agent/pre-step → [ step/start → agent/request → llm/stream
            → tools/pre-execute → tools/execute → tools/post-execute
            → agent/turn-stopping → step/end ]* → turn/end
```

| 切面 | 分发模式 | 监听器签名 | 用途 |
|---|---|---|---|
| `agent/pre-step` | waterfall | `(agent, decision, next)` | 改写或拒绝已领取输入（`reject`/`enter`） |
| `agent/request` | waterfall | `(agent, call, next)` | 改写 `messages`/`model`/`tools` |
| `llm/stream` | waterfall | `(agent, call, next)` | 环绕流式模型调用（超时/重试/审计） |
| `tools/pre-execute` | waterfall | `(call, next)` | allow/deny/ask 决策 |
| `tools/execute` | waterfall | `(call, next)` | 环绕执行 |
| `tools/post-execute` | waterfall | `(call, output, next)` | 改写结果 |
| `agent/turn-stopping` | serial | `(agent, state)` | 声明是否仍欠工作（`{"stop": bool}`） |

无内核时全部切面退化为 no-op，保证单元测试与独立调用兼容。

## 14. 能力接缝：三段式 seam

每项能力是 **Service Definition + Service Provider + Consumer**：

- **Definition**（`src/core/ports/`）：`FsPort` / `ShellPort` /
  `SubprocessPort` / `TerminalsPort` / `JobsPort` / `SandboxPort`；
- **Provider**（`src/core/adapters/capabilities.py` 默认实现）：
  `LocalFsProvider` / `LocalShellProvider` / ...；
- **Consumer**：`read_file` / `write_file` / `bash` 等工具经 `ctx.fs` /
  `ctx.shell` 使用能力。

```python
seams = kernel.resolve_service("seams")
seams.replace("fs", RemoteSandboxFs())      # 一次替换，读写/遍历/删除整体迁移
```

`file_ops.atomic_write_file` / `_sync_read_file` 与 bash 的进程创建均已经
接缝（无内核时回退本地实现）；每次操作广播 `fs/*` / `shell/spawn` 等能力
事件。

## 15. 作用域原语：`Scope` / `ScopeRegistry`

`src/kernel/scope.py`（dsh `core/scope` 的角色）按 key 划分注册空间：

```python
from src.kernel import ScopeRegistry

registry = ScopeRegistry(kernel.root)
scope = registry.open("agent-1", isolated=["llm"])   # llm 只解析本地
scope.provide("persona", "reviewer")
scope.on("tools/pre-execute", guard)
registry.close("agent-1")                            # 撤销全部注册
```

`ctx.agents` 为每个活跃 Agent 打开独立作用域（`agent.agent_scope`），
每个 Agent 的能力注册互不污染；`presets.scope()` 同样基于隔离作用域。

## 16. 可替换 Provider 一览

除三段式能力接缝（第 14 节）外，以下运行时能力同样以「provider 可替换」的
插件形态提供，替换入口与默认实现如下：

| 服务 | 替换入口 | 默认 provider |
|---|---|---|
| `ctx.llm` | `ctx.llm.register_provider(name, factory, prefixes=…, substrings=…, fallback=…)` | 内置 deepseek / anthropic / ollama / openai_compat（各自独立的 provider 插件） |
| `ctx.renderer` | `ctx.renderer.register_handler(factory)` / `register_filter(factory)` | 内置 TokenHandler 集合 + 三个内置过滤器 |
| `ctx.observability` | `ctx.observability.set_provider(port)` | `ObservabilityFacade` |
| `ctx.events` | 服务独占核心 / 显示总线与显示适配器 | `CoreEventBus` / `DisplayEventBus` / `DisplayEventBusAdapter` |
| `ctx.output` | `ctx.output` 持有的端口 | `DefaultOutputAdapter` |
| `ctx.cache` | `ctx.cache` 持有的缓存 | `LRUCache` |
| `ctx.ui` | `ctx.ui.width_cache` / `subagent_panel` / `assemble()` | `TerminalWidthCache` / `SubAgentPanelController` / `TuiAssembly` |
| `ctx.notifications` | `ctx.notifications.set_provider(provider)` | `src.notifications` 平台实现 |
| `ctx.persistence` | `ctx.persistence.set_provider(port)` | `JsonFilePersistence` |
| `ctx.checkpoint` | `ctx.checkpoint.set_provider(port)` | `JsonFileCheckpoint` |
| `ctx.context` | `ctx.context.register_strategy(name, factory)` / `set_strategy_builder(fn)` | `SummarizeStrategy` → `DropStrategy` |

provider / 扩展的注册都是挂在当前 Fiber 上的可逆副作用：插件卸载时自动撤销，
外部插件可覆盖内置实现，卸载后自动回退内置。

## 17. 细粒度插件化：工具 / 命令 / 渲染 / 中间件

「一切皆插件」落实到**单个能力**：内置工具、内置命令、渲染 handler/filter、
Agent 主循环中间件都是清单中的独立条目，可被 Profile/Bundle 声明、被
Patch/Overlay 按 id 禁用或替换。

| 能力 | 清单 bundle | 条目引用 | 组合根注入 |
|---|---|---|---|
| 内置工具 | `tools`（`tools::tool_*`） | `src.plugins.tool_plugin`（`config.tool` = `Func` 子类路径） | `tools_builtin` 的 `managed_tools` |
| 内置命令 | `commands`（`commands::cmd_*`） | `src.plugins.command_plugin`（`config.command` = `CommandPlugin` 子类路径） | `ctx.commands` 的 `managed_commands` |
| 渲染内置项 | `presentation`（`renderer_builtin`） | `src.plugins.renderer_builtin` | `config.disabled_handlers` / `disabled_filters` |
| Agent 中间件 | `runtime`（`agent_middleware`） | `src.plugins.agent_middleware` | `config.disabled` |
| 工具调度器 | `runtime`（`tool_scheduler`） | `src.plugins.tool_scheduler` | 提供 `ctx.tool_scheduler` |

- **工具**：默认 profile 为每个内置工具声明独立条目；`tools_builtin` 只兜底
  注册「清单未接管」的工具，并跳过 `managed_tools`（含被禁用的工具名），因此
  `--patch` 禁用单个工具不会被兜底重注册。
- **命令**：命令模块导入时只 `declare_command_plugin(...)`（声明，不注册）；
  `ctx.commands` 按 `managed_commands`（清单启用的命令名）注册，`command_plugin`
  条目再逐个确保注册（可逆副作用）。禁用命令即从清单移除其 `cmd_*` 条目。
- **渲染内置项**：`src/renderer/extensions.py` 持有内置 handler/filter 注册表，
  `RenderEngine` / `IncrementalRenderer` / `AnsiStreamRenderer` 只从注册表装配；
  `renderer_builtin` 插件可禁用/替换内置项。
- **Agent 中间件**：`src/core/middleware/registry.py` 持有内置中间件注册表，
  `Agent` 只从注册表装配；`agent_middleware` 插件可禁用内置项，插件也可经
  `register_middleware` 追加扩展。

### 17.1 单例收敛为内核服务

`ToolRegistry.default()`、`ToolScheduler.default()`、`LlmProviderRegistry.default_registry()`、
`SkillRegistry.default_registry()`、`get_plugin_registry()` 均**内核优先**：
内核挂载对应服务后返回其持有的实例（与 `ctx.tools` / `ctx.tool_scheduler` /
`ctx.llm` / `ctx.skills` / `ctx.commands` 同源），内核缺失或服务尚在构造中时
回退进程级单例，保证独立调用与单元测试兼容。

## 18. 进程级单例收敛为内核服务

除第 17.1 节的注册表单例外，总线 / 端口 / 观测 / 缓存 / 表现层等进程级单例
同样**内核优先**——内核挂载对应服务后，单例访问函数返回服务独占实例；内核
缺失（单元测试、独立调用）时才回退进程级单例。

| 单例访问函数 | 内核服务 | 服务独占实例 |
|---|---|---|
| `CoreEventBus.get_default_bus()` | `ctx.events` | `ctx.events.bus` |
| `DisplayEventBus.get_default()` | `ctx.events` | `ctx.events.display_bus` |
| `DisplayEventBusAdapter.get_default()` | `ctx.events` | `ctx.events.event_adapter()` |
| `DefaultOutputAdapter.get_default()` / `get_default_output_port()` | `ctx.output` | `ctx.output.port` |
| `get_default_cache()` | `ctx.cache` | `ctx.cache.cache` |
| `get_default_facade()` | `ctx.observability` | `ctx.observability.provider` |
| `get_default_collector()` / `get_default_tracer()` | `ctx.observability` | `ctx.observability.collector` / `.tracer` |
| `McpManager.default()` | `ctx.mcp` | `ctx.mcp.manager` |
| `TerminalWidthCache.get_default()` | `ctx.ui` | `ctx.ui.width_cache` |
| `SubAgentPanelController.get_default()` | `ctx.ui` | `ctx.ui.subagent_panel` |

因此生产路径不再落入游离的模块级全局状态：替换 / 卸载内核服务即整体改变
对应链路；`--profile minimal` 等未加载该服务的场景自动回退单例。

### 18.1 应用组合根：`ctx.app`

`main.py` 是最薄入口：解析参数 → `build_kernel()` → `ctx.app.run(args)` →
`app.shutdown()` → `shutdown_kernel()`。应用生命周期（可观测性启动、trace_id
初始化、输出消费者、信号处理、ChatUI 错误处理器、version / dump-config /
plugin / session / config / check-invariants 子命令分发、MCP 初始化、
clawbot / 交互 / 单次模式装配）由 `ctx.app` 插件承载，可按 Profile/Patch
禁用或替换；clawbot 装配另经 `ctx.clawbot` 服务提供。

