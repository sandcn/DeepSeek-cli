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
| `events` | `ctx.events` | 核心事件总线 + 显示事件总线 |
| `prompt` | `ctx.prompt` | 系统提词、子代理提词、空模式 |
| `policy` | `ctx.policy` | 工具可用性、路径白名单、沙盒 |
| `tools` | `ctx.tools` | 工具注册表 + 工具执行管线 |
| `tools_builtin` | — | 显式注册全部内置工具（每个注册都是可逆副作用） |
| `skills` | `ctx.skills` | 技能注册表 |
| `presets` | `ctx.presets` | 每会话能力组合（isolate 作用域） |
| `invariants` | `ctx.invariants` | 运行时自检（断言插件树关系） |
| `llm` | `ctx.llm` | 模型适配器路由 + 异步模型端口 |
| `sessions` | `ctx.sessions` | 会话创建/恢复/保存 |
| `agent_loop` | `ctx.agent_loop` | Agent 组装与循环 |
| `commands` | `ctx.commands` | 命令插件注册表 |
| `mcp` | `ctx.mcp` | 外部 MCP 工具接入 |
| `renderer` | `ctx.renderer` | 增量流式 Markdown 渲染 |
| `ui` | `ctx.ui` | 终端 UI 运行时桥接 |

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

- **entry-points**：安装的 Python 包若声明 `dsh.plugins` 组，启动时自动发现。

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
