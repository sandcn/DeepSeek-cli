# DeepSeek-cli `v2.2.0`

全异步、高可扩展的 AI 聊天服务后端，支持多模型适配、增量流式 Markdown 渲染、工具调用系统、上下文压缩和终端+Web 双界面。

---

## 快速开始

### 1. 安装依赖

项目使用 **Python ≥ 3.9**。

#### 方式一：一键安装（推荐）

```bash
# 安装全部核心依赖
pip install aiohttp httpx rich Pygments Jinja2 beautifulsoup4 chardet aiofiles

# 安装开发依赖（测试/代码检查等）
pip install pytest pytest-asyncio pytest-xdist pytest-cov ruff mypy
```

#### 方式二：通过项目安装（自动读取 pyproject.toml）

```bash
# 安装核心依赖
pip install .

# 安装开发依赖（测试/代码检查等）
pip install ".[dev]"
```

**依赖库说明**：

| 包 | 用途 | 安装命令 |
|---|---|---|
| `aiohttp` | 异步 HTTP 服务器/客户端 | `pip install aiohttp` |
| `httpx` | HTTP 请求库 | `pip install httpx` |
| `rich` | 终端富文本输出 | `pip install rich` |
| `Pygments` | 代码语法高亮 | `pip install Pygments` |
| `Jinja2` | 模板渲染 | `pip install Jinja2` |
| `beautifulsoup4` | HTML 解析 | `pip install beautifulsoup4` |
| `chardet` | 字符编码检测 | `pip install chardet` |
| `aiofiles` | 异步文件操作 | `pip install aiofiles` |

---

### 2. 配置

#### 方式一：配置文件（推荐）

创建配置文件 `~/.chat_config/chatrc.json`：

```json
{
    "provider": "deepseek",
    "api_key": "sk-你的API密钥",
    "model": "deepseek-v4-flash",
    "base_url": "https://api.deepseek.com/v1/chat/completions",
    "max_context_chars": 60000,
    "max_output_chars": 3000,
    "max_retries": 10,
    "retry_base_sec": 30,
    "max_session_messages": 0,
    "keep_recent_messages": 0,
    "theme": "dark",
    "max_context_tokens": 60000,
    "summary_token_budget": 2000,
    "auto_force_compress_threshold": 60000,
    "enable_notifications": true,
    "notify_on_chat_completion": true,
    "performance": {
        "http_client": {
            "connect_timeout": 30,
            "read_timeout": 120,
            "write_timeout": 120,
            "max_connections": 100,
            "max_connections_per_host": 20,
            "keep_alive_timeout": 15,
            "enable_pool": true,
            "enable_http2": true
        }
    }
}
```

配置文件位于 `~/.chat_config/chatrc.json`，首次运行时自动创建（使用默认值）。

#### 方式二：环境变量

部分配置项支持通过环境变量覆盖：

| 环境变量 | 说明 | 示例 |
|---|---|---|
| `CHAT_API_KEY` | API 密钥 | `export CHAT_API_KEY="sk-xxx"` |
| `CHAT_BASE_URL` | API 基础地址 | `export CHAT_BASE_URL="https://api.deepseek.com/v1/chat/completions"` |
| `CHAT_MODEL` | 模型名称 | `export CHAT_MODEL="deepseek-v4-flash"` |
| `CHAT_STAGGER_MIN_DELAY` | 流式输出最小延迟 | `export CHAT_STAGGER_MIN_DELAY="0.1"` |
| `CHAT_STAGGER_MAX_DELAY` | 流式输出最大延迟 | `export CHAT_STAGGER_MAX_DELAY="0.5"` |

环境变量优先级高于配置文件。

#### 支持的多模型 Provider

| Provider | 适配器 | 说明 |
|---|---|---|
| `deepseek` | `DeepSeekAdapter` | DeepSeek 官方 API（默认），支持 v4-pro、v4-flash、reasoner、chat、coder 系列 |
| `custom` | `OpenAICompatAdapter` | 任意 OpenAI 兼容 API（OpenAI / GLM / 通义千问等），自动检测 reasoner 模型 |
| `anthropic` | `AnthropicAdapter` | Anthropic Claude 系列模型（API 格式自动转换） |
| `ollama` | `OllamaAdapter` | 本地 Ollama 部署模型（默认 `localhost:11434`） |

---

### 3. 启动

#### 交互式对话（默认）

```bash
python chat.py
```

启动终端交互界面，进入多轮对话。

#### Web UI 模式

```bash
python chat.py --webui
# 或等价的子命令
python chat.py webui
```

启动浏览器界面（默认 http://0.0.0.0:8080），支持自定义监听地址和端口：

```bash
python chat.py webui --host 127.0.0.1 --port 3000
```

#### 单次问答模式

```bash
python chat.py -p "你好，请介绍一下自己"
```

输入一句话，大模型回答完成后立即退出，适合脚本调用。

#### 从保存的会话恢复

```bash
python chat.py --load <会话ID>
python chat.py webui --load <会话ID>   # Web UI 模式恢复
```

#### 指定模型

```bash
python chat.py -m deepseek-v4-pro
python chat.py --model deepseek-v4-pro
```

通过 `-m` / `--model` 临时覆盖配置文件中的模型，不影响配置文件。

#### 详细日志模式

```bash
python chat.py -v           # INFO 级别日志
python chat.py -vv          # DEBUG 级别日志
```

#### 会话管理

```bash
python chat.py session list                   # 列出所有保存的会话
python chat.py session delete <会话ID>        # 删除指定会话
python chat.py session export <会话ID>        # 导出会话（打印到 stdout）
python chat.py session export <会话ID> -o chat.json  # 导出到文件
```

#### 查看版本

```bash
python chat.py --version
python chat.py version
```

#### 完整命令一览

| 命令 | 说明 |
|---|---|
| `python chat.py` | 交互式对话（默认） |
| `python chat.py -p "你好"` | 单次问答模式 |
| `python chat.py --load abc123` | 从会话恢复 |
| `python chat.py -m deepseek-v4-pro` | 指定模型 |
| `python chat.py -v` | INFO 级别日志 |
| `python chat.py -vv` | DEBUG 级别日志 |
| `python chat.py --webui` | Web UI 模式（默认 0.0.0.0:8080） |
| `python chat.py webui --host 127.0.0.1 --port 3000` | 自定义 Web UI 地址端口 |
| `python chat.py session list` | 列出所有会话 |
| `python chat.py session delete abc123` | 删除会话 |
| `python chat.py session export abc123` | 导出会话 |
| `python chat.py --version` | 显示版本信息 |

---

### 4. 快捷操作（终端交互模式下）

> 以下快捷键仅在终端交互式对话（`python chat.py`）中生效。

| 快捷键 | 功能 |
|--------|------|
| `Enter` | 发送消息 |
| `Esc`（双击） | 清空当前输入框内容 |
| `Ctrl+G` | 使用 vim 编辑器编辑当前输入内容（支持 $EDITOR 环境变量） |
| `Ctrl+O` | 编辑当前会话中的已有消息（触发 `/editmsg` 命令） |
| `Ctrl+N` | 循环切换对话模型 |
| `Ctrl+C`（首次） | 中断当前 AI 回复 |
| `Ctrl+C`（再次） | 强制退出程序 |
| `↑` / `↓` | 浏览输入历史 |
| `Tab` | 自动补全（命令名、会话 ID 等） |

---

### 5. 斜杠命令（终端交互模式下）

在对话输入框中以 `/` 开头输入命令：

| 命令 | 别名 | 功能 |
|------|------|------|
| `/help` | — | 显示所有可用命令 |
| `/clear` | — | 清空对话（保留系统提词） |
| `/loop <N> <提词>` | — | 循环执行 N 次指定提词（每轮第1次用用户提词，第2次用固定提词"继续完成所有"） |
| `/compress` | — | 手动压缩上下文（减少 token 消耗） |
| `/pin` | — | 标记重要消息（压缩时保留） |
| `/editmsg` | — | 编辑当前会话消息（同 Ctrl+O） |
| `/undo` | — | 撤销上一轮对话 |
| `/retry` | `/r` | 重新生成上一条回答 |
| `/edit` | — | 编辑并重新发送上一条输入 |
| `/model` | — | 切换模型（无参数时交互选择，支持序号/名称） |
| `/system` | — | 查看或追加系统提示词 |
| `/cost` | — | 查看 token 用量和费用 |
| `/load <ID>` | — | 加载保存的对话 |
| `/sessions` | — | 列出所有保存的对话 |
| `/export [路径]` | — | 导出当前对话为 Markdown（含 SubAgent 聊天信息） |
| `/theme <名称>` | — | 切换配色主题（dark / light / high-contrast） |
| `/changes` | — | 显示文件沙盒中被修改文件的差异（可加文件名过滤） |
| `exit` | — | 退出程序 |

> **SubAgent 聊天记录持久化**：每个 SubAgent 的完整内部对话（system 提示词 / 任务指令 /
> 助手回复 / 工具调用与结果）会在其运行结束时记录到父 Agent，并随会话自动保存到
> `.chat/msg_list/<id>.json` 的 `subagents` 字段；`/load` 加载会话时同步恢复，
> `/export` 导出 markdown 时一并包含。

---

## 工具系统（Tool System）

AI 代理在对话中可调用以下工具完成各类操作。共 **14 个内置工具**，涵盖文件操作、代码搜索、网络请求、用户交互等能力。

### 工具列表

| 工具名 | 缩写 | 分类 | 并行安全 | 功能说明 |
|--------|------|------|---------|---------|
| `read_file` | rf | IO | ✅ | 读取文件内容，支持指定行号范围、自动编码检测 |
| `write_file` | wf | IO | ✅ | 覆盖写入文件，自动创建父目录，原子写入 |
| `update_file` | uf | IO | ❌ | 精确替换文件中的文本（old_string → new_string） |
| `search` | sr | 搜索 | ✅ | 在项目源码中搜索正则表达式，自动排除非源码目录 |
| `find` | fn | 搜索 | ✅ | 按通配符模式查找文件和目录，支持深度控制 |
| `ls` | ls | IO | ✅ | 列出目录内容，支持详细格式和隐藏文件显示 |
| `bash` | bs | 执行 | ❌ | 执行 shell 命令（安全沙盒保护，禁止替代专用工具） |
| `cp` | cp | IO | ✅ | 复制文件或目录，保留元数据，支持沙盒撤回 |
| `mv` | mv | IO | ✅ | 移动文件或目录，支持跨文件系统 |
| `rm` | rm | IO | ❌ | 删除文件或目录（删除前自动备份到沙盒） |
| `mk` | mk | IO | ✅ | 创建目录，支持递归创建父目录 |
| `web_search` | ws | 网络 | ❌ | 搜索引擎搜索 + 网页全文抓取（百度/必应/GitHub） |
| `user_select` | us | 交互 | ❌ | 向用户显示交互式选择界面（单选/多选/超时回退/非交互回退，选项可带说明，TUI 中高亮选项时说明显示在右侧） |
| `dispatch_agent` | da | Agent | ❌ | 并行派发子 Agent 执行独立任务（支持类型：map/review/plan/execute） |

### 工具分类

| 分类 | 工具 | 说明 |
|------|------|------|
| **文件 IO** | read_file, write_file, update_file, ls, cp, mv, rm, mk | 读写文件、目录操作、文件管理 |
| **代码搜索** | search, find | 正则搜索源码、通配符查找文件 |
| **命令执行** | bash | 安全沙盒中执行 shell 命令 |
| **网络访问** | web_search | 搜索引擎查询和网页内容获取 |
| **用户交互** | user_select | 交互式选择弹窗（单选/多选/超时回退） |
| **Agent 调度** | dispatch_agent | 并发派发原子 Agent 执行独立任务 |

### 工具设计原则

- **纯异步** — 所有工具均基于 `asyncio`，不阻塞事件循环
- **沙盒安全** — 文件操作自动备份，支持撤回（undo）
- **元数据系统** — 每工具声明并行安全、网络依赖、超时估计等元数据，供调度层优化
- **双端适配** — 同时支持终端（`display()`）和 Web UI（`web_display()`）两种渲染路径

### 工具权限系统（v2.2.0+）

不同 SubAgent 类型对工具有不同的访问权限，通过 `Func.can_use()` 统一检查：

```python
@classmethod
def can_use(cls, tool_name: str, agent_type: str = "execute") -> tuple[bool, str | None]:
    """检查指定类型的 agent 能否使用某工具。"""
```

- **agent_type 注入** — SubAgent 在 `_handle_tool_calls()` 中自动注入 `func.agent_type = self.agent_type`
- **排除规则** — 定义在 `src/core/subagent.py` 的 `_TOOL_EXCLUSION_MAP`（详见下方 SubAgent 类型表）
- **路径白名单** — `FileToolBase._validate_path_and_size()` 对 plan Agent 实施路径限制，仅允许写入 `.chat/plan/` 目录，防止误写项目源码

---

## 光标坐标追踪系统（CursorTracker）

新增于 v2.2.0，全局统一的终端光标坐标追踪基础设施，消除分散在各渲染组件中的坐标推算累积误差。

### 核心 API

| 方法 | 功能 |
|------|------|
| `move_to(row, col)` | 写 ANSI CUP 序列 + 更新内部坐标 |
| `move_xy(col, row)` | 0-based → 1-based 转换入口 |
| `set(row, col)` | 仅更新内部状态，不写终端 |
| `record_newlines(n)` | 追加 `n` 行后自动更新行号 + 列号复位 |
| `record_move_down(n)` | 下移 `n` 行（滚动场景） |
| `save()` / `restore(pos)` | 检查点模式（返回/恢复 CursorPosition 快照），支持渲染前后坐标范围对比 |
| `pos → CursorPosition` | 获取当前坐标快照 |

### 集成架构

```
ChatUIConsumer
  └── CursorTracker（唯一实例，构造注入到所有子系统）
        ├── ContentRenderer   → _do_content / _do_tool_output 等 14 种渲染后调用 record_newlines()
        ├── RenderEngine       → _phase_render 记录渲染坐标范围，position_cursor 同步最终光标
        ├── _BottomBar         → force_redraw / sync_bottom_lines / ensure_cursor_* 中 set 光标位置
        └── _CompletionPopup   → render / render_cycle_update 中 set 弹窗行坐标
```

### 设计决策

- **单线程使用** — 仅在 render 线程中操作，无需锁
- **1-based 坐标** — 与终端 ANSI CUP 序列一致
- **轻量无依赖** — 仅标准库，零外部依赖
- **检查点模式** — `save/restore` 支持嵌套渲染场景的坐标回退

---

## Agent 工作流程

本项目的核心是 **Main-Sub Agent 架构**，通过 `dispatch_agent` 委派任务给不同类型的子 Agent。

```
┌──────────────────────────────────────────────────────────────────┐
│                         Main Agent                               │
│                   主控 Agent，负责任务调度（6 步工作流）                 │
│                                                                  │
│  ① 规划 ─→ ② 探底分析 ─→ ③ 修改执行 ─→ ④ 审查 ─→ ⑤ 验证（完成）│
└───────┬───────┬───────┬───────┬───────┘
        │       │       │       │
        │dispatch│dispatch│dispatch│dispatch
        ▼       ▼       ▼       ▼
┌─────────────┐ ┌────────────┐ ┌────────────┐ ┌──────────────┐
│ plan        │ │ map        │ │ review     │ │ execute      │
│ SubAgent    │ │ SubAgent   │ │ SubAgent   │ │ SubAgent     │
│             │ │            │ │            │ │              │
│ 计划型      │ │ 只读分析型  │ │ 代码审查型  │ │ 执行型       │
│             │ │            │ │            │ │              │
│ • 任务拆解  │ │ • 项目探底 │ │ • P0-P3    │ │ • 读/写文件  │
│ • 依赖分析  │ │ • 模块地图 │ │   分级审查  │ │ • 修改代码   │
│ • 资源估算  │ │ • 调用链   │ │ • 循环审查  │ │ • 创建文件   │
│ • 风险识别  │ │ • 引用关系 │ │ • 阻断策略  │ │ • 测试运行   │
│ • 动态重规划│ │            │ │            │ │ • 执行验证   │
└─────────────┘ └────────────┘ └────────────┘ └──────────────┘
```

### 工作流说明

```
1. 规划 ──→  委派 plan SubAgent 制定结构化计划（任务拆解、依赖分析、风险评估）
     │
2. 探底 ──→  委派 map SubAgent 获取模块地图 + 调用链分析
     │         （只读分析，不修改代码）
     │
3. 修改 ──→  基于探底结果执行代码修改
     │         多个独立目标可并发派发 execute SubAgent
     │
4. 审查 ──→  委派 review SubAgent 逐文件审查
     │         P0/P1/P2 阻断修复，P3 纳入记录
     │         最多三轮循环审查
     │
5. 验证 ──→  语法检查 → 构建/编译 → 加测试 → 运行测试 → 运行验证
```

### SubAgent 类型

各类型 SubAgent 通过 `_TOOL_EXCLUSION_MAP`（定义在 `src/core/subagent.py`）控制工具可用性。

| 类型 | 可用工具 | 用途 |
|---|---|---|
| **plan** | 只读分析 + write_file/update_file（仅限 `.chat/plan/` 目录） | 任务拆解、依赖分析、生成计划文件到 `.chat/plan/` |
| **map** | 只读（read_file/search/find/ls） | 项目探底、模块地图、调用链追踪、引用关系分析 |
| **review** | 只读 + web_search | Code Review、P0-P3 分级审查、跨文件一致性验证 |
| **execute** | 全工具（不含 user_select/dispatch_agent） | 读/写/改代码、执行测试、通用任务 |

> **工具排除策略**：execute 排除 `dispatch_agent/user_select`；map 排除 `bash/write_file/update_file/rm/mv/cp/mk/web_search/dispatch_agent/user_select`；review 排除 `bash/write_file/update_file/rm/mv/cp/mk/dispatch_agent/user_select`（保留 web_search）；plan 排除 `bash/rm/mv/cp/mk/dispatch_agent/user_select`，write_file/update_file 仅限 `.chat/plan/` 目录。SubAgent 在 `_handle_tool_calls()` 中注入 `agent_type` 到 Func 实例，`Func.can_use()` 进行统一检查。`FileToolBase._validate_path_and_size()` 额外实施 plan Agent 路径白名单校验。

### 并发调度策略

多个独立分析/审查任务同时触发时，同轮并发派发多个 SubAgent（如同时分析多个模块、同时审查多个文件），互不阻塞，缩短总执行时间。

---

## 目录结构

```
├── chat.py                # 入口脚本（asyncio.run(main())）
├── pyproject.toml         # 项目配置与依赖
├── prompts/               # 系统提示词（5 个文件）
│   ├── prompts_export_main.md    # 主 Agent 系统提示词
│   ├── prompts_export_map.md     # map SubAgent 探底提示词
│   ├── prompts_export_plan.md    # plan SubAgent 计划提示词
│   ├── prompts_export_execute.md  # execute SubAgent 提示词
│   ├── prompts_export_review.md  # review SubAgent 审查提示词

├── tests/                 # 测试（128 个测试文件）
├── .chat/                 # 运行时数据目录（首次运行自动创建）
│   ├── memory/            # 跨对话记忆系统（索引 + 详情）
│   ├── plan/              # Plan Agent 计划文件
│   └── msg_list/          # 会话消息存储（JSON 格式）
│
├── src/                   # 核心源码
│   ├── app.py             # 入口 re-export
│   ├── app_init/          # 应用初始化（参数解析、模式选择）
│   ├── app_loop/          # 交互式/单次模式主循环
│   ├── application.py     # 应用层编排（Application、AppMode）
│   ├── chat_msgs.py       # 对话消息存/取/列/导出
│   ├── checkpoint.py      # 任务断点保存与恢复
│   ├── paths.py           # 路径常量
│   ├── terminal.py        # 终端颜色配置（始终启用颜色）
│   ├── _compat.py         # Python 版本兼容（dataclass/aclosing/get_event_loop）
│   │
│   ├── api/               # API 适配层
│   │   ├── client_async.py    # httpx 异步 HTTP 客户端
│   │   ├── model_async.py     # 模型调用入口 + 重试
│   │   ├── interrupt_async.py # 全局中断信号
│   │   ├── stream/            # 流式输出处理（含推理/工具调用/速度）
│   │   ├── stream_parse.py    # 流式工具调用解析
│   │   ├── tokens.py          # Token 启发式估算
│   │   ├── stats.py           # 会话级 Token 统计
│   │   ├── json_repair.py     # JSON 格式自动修复
│   │   ├── protocols.py       # LLM 协议定义
│   │   ├── telemetry.py        # API 层可观测性
│   │   ├── escape_monitor.py   # 键盘输入监听
│   │   ├── events.py           # API 事件定义
│   │   ├── _model_loops.py / _stats_core.py / _stream_lifecycle.py / _token_speed.py / _tool_parse_utils.py  # 内部辅助模块
│   │   ├── adapters/          # 多模型适配器（DeepSeek/OpenAI/Anthropic/Ollama）
│   │   └── _adapter_manager.py   # 适配器管理
│   │
│   ├── config/            # 配置系统
│   │   ├── loader.py          # 配置加载/持久化（~/.chat_config/chatrc.json）
│   │   ├── defaults.py        # 默认配置 + Provider 定义
│   │   └── schema.py          # 配置校验
│   │
│   ├── core/               # 核心业务逻辑
│   │   ├── agent.py           # Agent 对话代理（Pipeline 驱动）
│   │   ├── base_agent.py      # Agent 基类（消息管理、沙盒上下文）
│   │   ├── agent_di.py        # Agent 依赖注入工厂
│   │   ├── agent_builder.py   # Agent 构建器
│   │   ├── session.py         # ChatSession 纯领域会话对象（状态机驱动）
│   │   ├── state_machine.py   # 会话状态机（INIT→IDLE→RUNNING→COMPLETED/INTERRUPTED）
│   │   ├── subagent.py        # SubAgent 子代理（含 _TOOL_EXCLUSION_MAP 工具权限策略）
│   │   ├── pipeline.py        # Pipeline 中间件管道（Model-Execute 循环编排）
│   │   ├── compression.py     # 上下文压缩（策略模式）
│   │   ├── context_manager.py # 上下文管理器 + 消息上限控制
│   │   ├── context_selector.py / context_summarizer.py
│   │   ├── message_queue.py   # MessageQueue 异步消息队列
│   │   ├── message_edit.py    # 消息编辑功能
│   │   ├── file_change_record.py # 文件变更记录
│   │   ├── sandbox_manager.py # 文件沙盒管理器
│   │   ├── parallel_executor.py # ParallelExecutor 并行 SubAgent 调度
│   │   ├── tool_executor_async.py # AsyncToolExecutor 异步工具执行器
│   │   ├── tool_dag.py        # 工具 DAG 调度
│   │   ├── cache.py           # 增量统计缓存
│   │   ├── constants.py       # 主题常量
│   │   ├── commands.py        # 命令系统入口
│   │   ├── commands/          # 命令插件子模块（含 base / _ui_adapter / plugins/）
│   │   ├── exceptions.py      # 异常定义
│   │   ├── hooks.py           # Hook 系统
│   │   ├── internal/          # 内部实现子模块
│   │   │   ├── agent/         # Agent 内部（spawner / callbacks / capture）
│   │   │   ├── shared/        # 共享工具（sandbox_history / stats_cache）
│   │   │   ├── session/       # 会话内部（persistence / messages）
│   │   │   └── commands/      # 命令内部（_command_core / _config_cmd / _data_cmd / _session_cmd）
│   │   ├── events/            # 核心事件总线 + 事件类型
│   │   ├── middleware/        # Pipeline 中间件（审计/中断/状态机/可观测性/工具适配器）
│   │   ├── ports/             # 六边形架构端口定义（8 个端口）
│   │   └── telemetry/         # 可观测性（指标/追踪/上下文传播）
│   │
│   ├── tui/                # 终端 UI 聊天渲染引擎（替代 chat_ui/）
│   │   ├── _animator.py / _assembly.py / _base_display.py / _buffer.py / _completion.py / _completion_engine.py
│   │   ├── _config.py / _const.py / _consumer.py / _cost.py / _cursor_tracker.py / _diff_renderer.py
│   │   ├── _input.py / _input_parser.py / _input_orchestrator.py / _lifecycle.py / _output.py / _output_target.py
│   │   ├── _screen.py / _snapshot.py / _stdout_tracker.py / _subagent_panel.py / _tool_icons.py / input.py
│   │   ├── _bottom_bar/        # DECSTBM 分屏底部固定栏（_bar/_layout/_layout_utils/_monitor/_popup/_render/_status）
│   │   ├── _renderer/          # TuiEngine + TuiRenderer + EventDispatcher（_dispatcher/_engine/_renderer）
│   │   ├── consumer/           # ChatUIConsumer 事件消费者 + 渲染入口
│   │   ├── core/               # 核心工具（color/style/singleton）
│   │   ├── events/             # UI 事件总线 + DisplayEvent 类型定义
│   │   ├── pipeline/           # 消息编辑/显示管道
│   │   └── state/              # 渲染/消费/输入/会话状态管理
│   │
│   ├── renderer/           # 增量流式 Markdown 渲染引擎
│   │   ├── engine.py          # RenderEngine 渲染引擎
│   │   ├── pipeline.py        # TokenPipeline 过滤器链
│   │   ├── recursive_parser.py # 递归下降解析器
│   │   ├── types.py           # Token/TokenType/RenderContext 类型
│   │   ├── states.py          # 渲染状态
│   │   ├── factory.py         # 渲染器工厂
│   │   ├── protocols.py       # 渲染协议
│   │   ├── output.py          # OutputAdapter 输出适配器
│   │   ├── indicator.py       # 流式指示器
│   │   ├── ast/               # AST 构建→扁平化→优化→渲染
│   │   ├── handlers/          # 块级元素处理器（code/table/mermaid/math/admonition 等）
│   │   ├── targets/           # 渲染目标抽象（RenderTarget / CompositeRenderTarget）
│   │   │   ├── __init__.py
│   │   │   └── base.py
│   │   │
│   │   ├── pipeline_filters/  # 流式优化过滤器
│   │   ├── math_symbols/      # 数学符号定义
│   │   ├── _rendering/        # 内部渲染辅助
│   │   └── _utils/            # 内部工具函数
│   │
│   ├── tools/              # 工具调用系统（14 个内置工具）
│   │   ├── base.py            # Func 基类 + 元数据系统（含 can_use 工具可用性检查 / agent_type）
│   │   ├── file_base.py       # FileToolBase 文件操作基类（含 plan agent 路径白名单）
│   │   ├── registry.py        # 工具注册表（自动发现 + 调度 + 元数据索引）
│   │   ├── read_file.py / write_file.py / update_file.py
│   │   ├── search.py / find.py / ls.py
│   │   ├── bash.py / cp.py / mv.py / rm.py / mk.py
│   │   ├── web_search.py / user_select.py / dispatch_agent.py
│   │   ├── file_ops.py        # 文件操作原子工具（原子写入、路径安全校验、沙盒记录）
│   │   ├── _constants.py      # 共享常量（排除目录、安全路径、编码等）
│   │   ├── encoding.py        # 编码检测工具函数
│   │   ├── utils.py           # 工具通用辅助函数
│   │   ├── page_fetcher.py    # 网页内容抓取（web_search 内部依赖）
│   │   └── parsers/           # 搜索引擎结果解析器（baidu / bing / generic / github）
│   │
│   ├── webui/              # Web 界面
│   │   ├── server.py          # aiohttp HTTP 服务器 + WebSocket
│   │   ├── bridge.py          # WebEventBridge（EventBus → WebSocket）
│   │   ├── session.py         # WEBChatSession（Web 会话管理）
│   │   ├── display.py         # WebDisplay 适配器
│   │   ├── types.py           # WebSocket 消息类型定义
│   │   ├── output_target.py   # 输出目标适配
│   │   ├── cleanup.py         # 资源清理逻辑
│   │   ├── msg_index.py       # 消息索引管理
│   │   ├── _base_sender.py    # WebSocket 发送基类
│   │   ├── _pending_selects.py # user_select 待决追踪
│   │   ├── _termux.py         # Termux 浏览器适配
│   │   ├── routing/           # WebSocket 消息路由
│   │   ├── ws_handler/        # WebSocket 处理器
│   │   └── static/            # 前端 SPA（HTML/CSS/JS）
│   │
│   ├── prompt_builder/     # 系统提示词构建
│   ├── notifications/      # 桌面通知（Termux/Linux/Windows）
│   └── observability/      # 可观测性门面（聚合指标/追踪/遥测日志）
```

---

## 六边形架构（Ports & Adapters）

核心层通过 **8 个端口接口** 访问基础设施，实现依赖倒置——核心层不直接依赖 `api`、`tui`、`webui`、`chat_msgs` 等具体实现模块，基础设施层通过适配器模式实现这些端口。

| 端口 | 文件 | 说明 |
|------|------|------|
| `ConfigPort` | `ports/config.py` | 配置管理（读取/写入/默认值） |
| `AsyncModelPort` | `ports/model.py` | 异步模型调用（LLM API）+ ModelResult |
| `PersistencePort` | `ports/persistence.py` | 会话持久化（JSON 文件存储） |
| `CheckpointPort` | `ports/persistence.py` | 任务断点保存与恢复 |
| `EventPort` | `ports/events.py` | 事件总线发布/订阅 |
| `InterruptPort` | `ports/interrupt.py` | 中断信号检查 |
| `ObservabilityPort` | `ports/observability.py` | 可观测性（指标/追踪） |
| `ModelResult` | `ports/model.py` | 模型调用结果数据类（input/output tokens / tool_calls） |

**设计原则**：所有端口均为 Protocol 或抽象基类，核心层仅依赖端口接口，不感知具体实现。测试时可通过 Mock 适配器替换基础设施，实现核心逻辑的独立单元测试。

---

## 事件系统

### 核心事件总线（`src/core/events/`）

通用事件发布/订阅系统，支持通配符订阅和优先级排序。定义 **16 种事件类型**：

| 事件常量 | 事件类型字符串 | 说明 |
|----------|---------------|------|
| `MODEL_CALL_STARTED` | `model.call.started` | 模型调用开始 |
| `MODEL_CALL_COMPLETED` | `model.call.completed` | 模型调用完成 |
| `MODEL_CALL_FAILED` | `model.call.failed` | 模型调用失败 |
| `MODEL_STREAM_CHUNK` | `model.stream.chunk` | 流式内容块 |
| `TOOL_CALL_STARTED` | `tool.call.started` | 工具调用开始 |
| `TOOL_CALL_COMPLETED` | `tool.call.completed` | 工具调用完成 |
| `TOOL_CALL_FAILED` | `tool.call.failed` | 工具调用失败 |
| `SESSION_STARTED` | `session.started` | 会话开始 |
| `SESSION_COMPLETED` | `session.completed` | 会话完成 |
| `SESSION_INTERRUPTED` | `session.interrupted` | 会话中断 |
| `SESSION_SAVED` | `session.saved` | 会话保存 |
| `CONTEXT_COMPRESSED` | `context.compressed` | 上下文压缩完成 |
| `CONTEXT_COMPRESS_FAILED` | `context.compress.failed` | 上下文压缩失败 |
| `CONFIG_CHANGED` | `config.changed` | 配置变更 |
| `APP_BOOTSTRAP` | `app.bootstrap` | 应用启动 |
| `APP_SHUTDOWN` | `app.shutdown` | 应用关闭 |

**特性**：通配符订阅（如 `model.*` 匹配所有模型事件）、优先级排序（`EventPriority` 枚举，LOWEST→HIGHEST）、不可变事件数据类（`frozen dataclass`）。

### UI 事件总线（`src/tui/events/`）

显示层事件系统，定义 **24 种 `DisplayEvent`** 类型（生命周期/工具调用/Agent 状态/模型阶段/流式内容/附加状态/通用输出/用户交互），基于 `CoreEventBus` 底层发布机制实现。`DisplayEventBus` 对 `DisplayEvent` 子类提供类型安全包装，与核心事件（字符串类型）并行独立运作，确保终端和 Web UI 双端共享相同的事件语义。

---

## Pipeline 中间件管道

Pipeline 将 Agent 对话循环编排为可插拔中间件链。中间件按注册顺序依次执行，每个钩子可拦截/增强/跳过特定阶段。

### 中间件列表（5 个）

| 中间件 | 文件 | 功能 |
|--------|------|------|
| `_InterruptCheckMiddleware` | `middleware/interrupt.py` | 模型调用前检查中断信号 |
| `_AsyncObservabilityMiddleware` | `middleware/observability.py` | 指标采集 + 调用链追踪 |
| `_AuditLogMiddleware` | `middleware/audit.py` | 审计日志记录 |
| `StateMachineMiddleware` | `middleware/state_machine.py` | 状态机自动状态转换 |
| `_ToolRegistryAdapter` | `middleware/adapters.py` | 工具注册表端口适配器（继承 ToolRegistryPort） |

### 生命周期钩子（6 个）

| 钩子 | 触发时机 |
|------|----------|
| `before_model_call` | 模型调用之前 |
| `after_model_call` | 模型调用之后 |
| `before_tool_execution` | 工具执行之前 |
| `after_tool_execution` | 工具执行之后 |
| `on_round_complete` | 一轮对话完成 |
| `on_exception` | 异常发生时 |

---

## 版本控制

- **分支**: `main`
- **当前版本**: `v2.2.0`
- **仓库**: Git 管理，`.gitignore` 排除 `__pycache__/`、`*.pyc`、虚拟环境及运行时数据

---

## 后续计划

### 1. 🎨 增加并优化 TUI 渲染

重构终端用户界面渲染层，提升视觉体验与交互流畅度：

- **流式渲染性能优化** ✅ — 降低增量 Markdown 渲染延迟，消除大 Token 输出时的界面卡顿（`src/tui/` 增量流式渲染引擎已实现）
- **增量渲染（除 resize 全量外均增量）** ✅ — 行级 diff + committed 前缀身份复用 + 位移锚点：头部动画（标题栏呼吸）不再引发 committed 可见区全量重写，流式增长每帧重写范围 O(可见区) → O(头部差异+位移区)；第十二轮强化：已提交内容修改（工具卡状态图标 ●→✔ / 标题更新）经 `_replace_committed_line` 使前缀缓存失效并新建 Line 对象 → 关闭后必现刷新；开放块行 key 用块内绝对行号 → 流式追加不重建已渲染行；subagent 卡片元素按引用 use_memo 缓存；补全弹窗/搜索激活时推进呼吸动画（空闲不渲染）；PriorityQueue 腾位 heapify / ANSI CSI 终止符（真彩冒号+终端键）三处正则收敛 / 换行缓存长度快照 / str 依赖按值比较 / 崩溃恢复计数复位 / 刷盘失败退避等 20 项渲染正确性与健壮性修复（BUG-30~62）
- **光标坐标追踪** ✅ — 新增 `CursorTracker` 全局光标坐标追踪系统，集成到 ContentRenderer / RenderEngine / _BottomBar / _CompletionPopup，消除坐标推算累积误差
- **React Ink 组件框架** ✅ — `src/tui/ink/`：调和器 + flexbox 布局 + hooks + 帧差异渲染，覆盖 useState/useReducer/useRef/useEffect/useLayoutEffect（独立时序）/useMemo/useCallback/useContext/useId/useSyncExternalStore/useInput/useFocus/forwardRef/useImperativeHandle/memo/ErrorBoundary/useMeasure/usePrevious；TEXT shorthand 样式/transform/wrap/dimColor/align；BOX flexBasis/borderStyle 变体（single/double/round/bold/classic/dashed/singleDouble/doubleSingle）/alignItems/justifyContent/gap；框架级缺陷修复：useImperativeHandle hook 槽位稳定、useSyncExternalStore 订阅重订、memo×context 短路恢复、生成器子级展开
- **标准控件/布局重构（阶段2）** ✅ — app 组件树全部改用语义化标准布局容器：App 消息区/底部区 Column、TopHeader Row、StatusBar/ChatView/ToolStatusHeader Column、_ParseLine/_StreamingLine 空状态统一空 TEXT（避免 BOX↔TEXT fiber 销毁重建）；控件库内部同步收敛：SelectInput/TextInput/MultiSelect/Table/Divider/Grid 用 Row/Column 门面（输出等价）；渲染错误修复 E1（显式 width 超 avail 钳制——行宽不变量）、E2（宽字符第二列覆盖不再静默丢失，`_merge_line` 与 input-area 统一合并路径）、E8（SelectInput/MultiSelect items 动态缩小越界防护）、E9（MultiSelect 不可哈希 value 兜底）、E10（TextInput 光标列对齐）；性能优化 P-H2/P-H3/P-H7/P-H9/P-H10/P-H14（布局/收集/截断/调和快路径，1000 行历史帧渲染 < 1ms）
- **行宽不变量（渲染错误修复）** ✅ — E-ROW-OVERFLOW（row 内容自然宽超容器时按 flexShrink 权重收缩子节点，默认 flexShrink=1 React Ink 标准语义，收缩后重新测量约束内部内容）、E-FILL-OVERFLOW（fill=False 容器被钳制时内部子节点按容器实际宽度重测）、E-OVERFLOW-GUARD（render_frame 行级截断防线——行宽恒 <= 文档宽，行级 diff 模型核心不变量）；2000+ 模糊用例零超宽（嵌套 row/ZStack/边框/宽字符/绝对定位组合）
- **高级布局能力** ✅ — 百分比尺寸（width/height/min/max="50%" 相对可用尺寸解析）；flexWrap="wrap" 换行流式布局（行间距 = gap，超宽项截断）；position="absolute" 绝对定位（left/top/right/bottom 锚点、显式/百分比尺寸、left+right/top+bottom 拉伸、最近 position="relative" 祖先为基准、脱离正常流不占空间、两阶段布局——正常流测量 + 绝对定位第二遍放置）；布局容器组件（`src/tui/ink/widgets/layout.py`）：Row/Column/Center/Stack/HStack/VStack/Grid（CSS Grid 风格，列等宽 flexGrow）/ZStack（层叠，子节点绝对定位叠放）
- **控件库（widgets）** ✅ — `src/tui/ink/widgets/`：交互控件 SelectInput（单选列表）/TextInput（受控文本输入，含 placeholder/mask/光标）/MultiSelect（多选，space 切换）/ConfirmInput（y/n 确认）；展示控件 Spinner（时间基动画）/ProgressBar（进度条）/Table（对齐表格，支持表头/边框变体）/Badge（背景色块徽章，前景自动对比）/Divider（分隔线，可选标题）；基于 use_input + use_state，同批连续按键状态经 ref 镜像正确累积（闭包陈旧修复），focus=False 不参与输入路由
- **富交互组件** ✅ — 在终端中嵌入可交互元素（选择列表、确认弹窗、进度条），减少纯文本输出的信息密度（`src/tui/ink/widgets/` 已实现）
- **语法高亮增强** — 支持更多编程语言的代码块高亮，优化长代码段的折叠/展开机制
- **多面板布局** — 对话区/工具调用日志/系统状态分屏显示，便于调试与观察 Agent 行为
- **主题系统扩展** ✅ — 支持自定义配色方案，适配亮色/暗色终端环境（已内置 dark/light/high-contrast 三种主题）
- **动效与呼吸效果** ✅ — 标题栏✦/工具卡边框/状态栏分隔线/模型名/解析行 spinner/推理头/错误标记/补全弹窗/流式占位符/工具计数箭头/失败警示等 10+ 处时间基动效（time_glow 0.1s 桶缓存）
- **Web UI 同步增强** — 终端与 Web 界面的渲染逻辑复用，保证两种模式下显示一致性

---

### 2. ✅ 🧠 Plan Agent 架构 — 已完成

独立的 **Plan Agent** 层已实现并投入使用。任何文件修改或新需求前，必须先通过 `dispatch_agent(type="plan")` 委派 plan SubAgent 生成结构化计划文件（`.chat/plan/`），主 Agent 读取后逐条执行，形成「规划 → 探底 → 推理 → 执行 → 审查 → 验证」六阶段流水线。详见上方 🔄 [Agent 工作流程](#agent-工作流程)。

---

### 3. ⚡ 更高的 Agent 并行度

从「串行 Agent 链」演进为「高并发 Agent 网格」，最大化利用 I/O 等待时间：

**短期目标（当前 → v3.0）**：

| 改进项 | 现状 | 目标 |
|--------|------|------|
| SubAgent 并发派发 | ✅ 同轮多次 `dispatch_agent`（ParallelExecutor 并行已实现） | 支持批量派发 + 动态扩缩容 Worker 池 |
| 文件读取并发 | ✅ 同轮多个 `read_file` 自动并行 | 增加读取优先级队列（关键路径先读） |
| 审查并行 | ✅ 多文件同轮并发 review（同轮并发 dispatch_agent 已实现） | 支持审查结果增量合并，减少重复审查 |
| 工具调用并行 | 单步工具串行执行 | 支持独立的工具调用 DAG（无依赖的工具并行执行） |

**中期目标（v3.0 → v4.0）**：

- **Agent 工作池** — 构建可复用的 Agent  Worker 池，按任务类型（map / review / edit / test）分类管理，减少每次派发的冷启动开销
- **流水线并行** — 前序 Agent 的输出流式送入后续 Agent，无需等待完整输出，边生成边消费（如 map 分析结果流式输入 review Agent）
- **资源感知调度** — 根据当前系统负载（CPU / 内存 / I/O）动态调整并发数，避免资源耗尽
- **跨对话并行** — 多个对话会话之间共享 Agent 工作池，全局协调并发上限

**长期目标（v4.0+）**：

- **分布式 Agent 执行** — 将 SubAgent 派发到远程计算节点执行，支持大规模并行代码分析和批量修改
- **自适应并行策略** — 基于历史任务执行时间自动学习并行度配置，为新任务推荐最优并发参数
