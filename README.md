# DeepSeek-cli `v2.2.0`

全异步、高可扩展的 AI 聊天服务后端，支持多模型适配、增量流式 Markdown 渲染、工具调用系统、上下文压缩和终端+Web 双界面。

---

## 快速开始

### 1. 安装依赖

项目使用 **Python ≥ 3.9**。

#### 方式一：一键安装（推荐）

```bash
# 安装全部核心依赖
pip install aiohttp httpx rich Pygments Jinja2 beautifulsoup4 chardet aiofiles blessed

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
| `blessed` | 终端底层控制（光标移动、屏幕管理、键盘事件） | `pip install blessed` |

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
    "max_retries": 3,
    "retry_base_sec": 1,
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
| `/loop <N> <提词>` | — | 循环执行 N 次指定提词（每轮前自动清空对话） |
| `/compress` | — | 手动压缩上下文（减少 token 消耗） |
| `/pin` | — | 标记重要消息（压缩时保留） |
| `/editmsg` | — | 编辑当前会话消息（同 Ctrl+O） |
| `/undo` | — | 撤销上一轮对话 |
| `/retry` | `/r` | 重新生成上一条回答 |
| `/edit` | — | 编辑并重新发送上一条输入 |
| `/model` | — | 切换模型（无参数时交互选择，支持序号/名称） |
| `/system` | — | 查看或追加系统提示词 |
| `/cost` | — | 查看 token 用量和费用 |
| `/init` | — | 生成项目摘要文件 init.md |
| `/load <ID>` | — | 加载保存的对话 |
| `/sessions` | — | 列出所有保存的对话 |
| `/theme <名称>` | — | 切换配色主题（dark / light / high-contrast） |
| `/changes` | — | 显示文件沙盒中被修改文件的差异（可加文件名过滤） |
| `exit` | — | 退出程序 |

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
| `user_select` | us | 交互 | ❌ | 向用户显示交互式选择界面（单选/多选/超时回退/非交互回退） |
| `dispatch_agent` | da | Agent | ❌ | 并行派发子 Agent 执行独立任务（支持类型：ordinary/map/review/plan/read_memory/write_memory） |

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
def can_use(cls, tool_name: str, agent_type: str = "ordinary") -> tuple[bool, str | None]:
    """检查指定类型的 agent 能否使用某工具。"""
```

- **agent_type 注入** — SubAgent 在 `_handle_tool_calls()` 中自动注入 `func.agent_type = self.agent_type`
- **排除规则** — 定义在 `src/core/subagent.py` 的 `_TOOL_EXCLUSION_MAP`（详见下方 SubAgent 类型表）
- **路径白名单** — `FileToolBase._validate_path_and_size()` 对 plan/write_memory Agent 实施路径限制，分别仅允许写入 `.chat/plan/` 和 `.chat/memory/` 目录，防止误写项目源码

---

## 光标坐标追踪系统（CursorTracker）

新增于 v2.2.0，全局统一的终端光标坐标追踪基础设施，消除分散在各渲染组件中的坐标推算累积误差。

### 核心 API

| 方法 | 功能 |
|------|------|
| `move_to(row, col)` | 写 ANSI CUP 序列 + 更新内部坐标 |
| `move_xy(col, row)` | 0-based → 1-based 转换入口（blessed 风格） |
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
│                   主控 Agent，负责任务调度（7 步工作流）                 │
│                                                                  │
│  ① 规划 ─→ ② 探底分析 ─→ ③ 记忆检索 ─→ ④ 修改执行 ─→ ⑤ 审查 ─→ ⑥ 验证 ─→ ⑦ 记忆更新（完成）│
└───────┬───────┬───────┬───────┬───────┬───────┘
        │       │       │       │       │       │
        │dispatch│dispatch│dispatch│dispatch│dispatch│dispatch
        ▼       ▼       ▼       ▼       ▼       ▼
┌─────────────┐ ┌────────────┐ ┌────────────┐ ┌──────────────┐ ┌─────────────────┐ ┌──────────────────┐
│ plan        │ │ map        │ │ review     │ │ ordinary     │ │ read_memory     │ │ write_memory     │
│ SubAgent    │ │ SubAgent   │ │ SubAgent   │ │ SubAgent     │ │ SubAgent        │ │ SubAgent         │
│             │ │            │ │            │ │              │ │                 │ │                  │
│ 计划型      │ │ 只读分析型  │ │ 代码审查型  │ │ 通用型       │ │ 只读记忆型      │ │ 读写记忆型       │
│             │ │            │ │            │ │              │ │                 │ │                  │
│ • 任务拆解  │ │ • 项目探底 │ │ • P0-P3    │ │ • 读/写文件  │ │ • 检索记忆索引  │ │ • 新增/更新记忆  │
│ • 依赖分析  │ │ • 模块地图 │ │   分级审查  │ │ • 修改代码   │ │ • 读取记忆条目  │ │ • 合并记忆条目   │
│ • 资源估算  │ │ • 调用链   │ │ • 循环审查  │ │ • 创建文件   │ │ • 关键词搜索    │ │ • 写入 .chat/    │
│ • 风险识别  │ │ • 引用关系 │ │ • 阻断策略  │ │ • 测试运行   │ │                 │ │   memory/        │
│ • 动态重规划│ │            │ │            │ │ • 执行验证   │ │                 │ │                  │
└─────────────┘ └────────────┘ └────────────┘ └──────────────┘ └─────────────────┘ └──────────────────┘
```

### 工作流说明

```
1. 规划 ──→  委派 plan SubAgent 制定结构化计划（任务拆解、依赖分析、风险评估）
     │
2. 探底 ──→  委派 map SubAgent 获取模块地图 + 调用链分析
     │         （只读分析，不修改代码）
     │
3. 记忆 ──→  委派 read_memory SubAgent 检索相关记忆
     │         关键词搜索 .chat/memory/ 目录
     │
4. 修改 ──→  基于探底结果执行代码修改
     │         多个独立目标可并发派发 ordinary SubAgent
     │
5. 审查 ──→  委派 review SubAgent 逐文件审查
     │         P0/P1/P2 阻断修复，P3 纳入记录
     │         最多三轮循环审查
     │
6. 验证 ──→  语法检查 → 新增测试 → 运行测试 → 运行验证
     │
7. 完成 ──→  输出变更总结，委派 write_memory SubAgent 更新跨对话记忆
```

### SubAgent 类型

各类型 SubAgent 通过 `_TOOL_EXCLUSION_MAP`（定义在 `src/core/subagent.py`）控制工具可用性。

| 类型 | 可用工具 | 用途 |
|---|---|---|
| **plan** | 只读分析 + write_file/update_file（仅限 `.chat/plan/` 目录） | 任务拆解、依赖分析、生成计划文件到 `.chat/plan/` |
| **map** | 只读（read_file/search/find/ls） | 项目探底、模块地图、调用链追踪、引用关系分析 |
| **review** | 只读 + web_search | Code Review、P0-P3 分级审查、跨文件一致性验证 |
| **ordinary** | 全工具（不含 user_select/dispatch_agent） | 读/写/改代码、执行测试、通用任务 |
| **read_memory** | 只读（read_file/search/find/ls） | 检索 `.chat/memory/` 目录下的记忆文件 |
| **write_memory** | 只读 + write_file/update_file/mk（仅限 `.chat/memory/` 目录） | 维护记忆文件（新增/更新/合并条目） |

> **工具排除策略**：ordinary 排除 `dispatch_agent/user_select`；map 排除 `bash/write_file/update_file/rm/mv/cp/mk/web_search/dispatch_agent/user_select`；review 排除 `bash/write_file/update_file/rm/mv/cp/mk/dispatch_agent/user_select`（保留 web_search）；plan 排除 `bash/rm/mv/cp/mk/dispatch_agent/user_select`，write_file/update_file 仅限 `.chat/plan/` 目录；read_memory 与 map 策略一致（排除所有写入类+web_search+dispatch_agent+user_select）；write_memory 排除 `bash/rm/mv/cp/web_search/dispatch_agent/user_select`，write_file/update_file/mk 仅限 `.chat/memory/` 目录。SubAgent 在 `_handle_tool_calls()` 中注入 `agent_type` 到 Func 实例，`Func.can_use()` 进行统一检查。`FileToolBase._validate_path_and_size()` 额外实施 plan/write_memory Agent 路径白名单校验。

### 并发调度策略

多个独立分析/审查任务同时触发时，同轮并发派发多个 SubAgent（如同时分析多个模块、同时审查多个文件），互不阻塞，缩短总执行时间。

---

## 目录结构

```
├── chat.py                # 入口脚本（asyncio.run(main())）
├── pyproject.toml         # 项目配置与依赖
├── prompts/               # 系统提示词（main/sub/map/plan/review + memory 指南）
├── tests/                 # 测试（107 个测试文件）
├── .chat/                 # 运行时数据目录
│   ├── memory/            # 跨对话记忆系统（索引 + 详情）
│   └── plan/              # Plan Agent 计划文件
│
├── src/                   # 核心源码
│   ├── app.py             # 入口 re-export
│   ├── app_init.py        # 应用初始化（参数解析、模式选择）
│   ├── app_loop.py        # 交互式/单次模式主循环
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
│   │   └── renderer/          # 增量流式 Markdown 渲染引擎（AST + VNode + Diff/Patch，~99 文件）
│   │
│   ├── config/            # 配置系统
│   │   ├── loader.py          # 配置加载/持久化（~/.chat_config/chatrc.json）
│   │   ├── defaults.py        # 默认配置 + Provider 定义
│   │   └── schema.py          # 配置校验
│   │
│   ├── core/               # 核心业务逻辑
│   │   ├── agent.py           # 对话代理（Pipeline 中间件管道）
│   │   ├── base_agent.py      # Agent 基类（消息管理、沙盒上下文）
│   │   ├── session.py         # ChatSession 会话（状态机驱动）
│   │   ├── state_machine.py   # 会话状态机（INIT→IDLE→RUNNING→COMPLETED/INTERRUPTED）
│   │   ├── subagent.py        # SubAgent 子代理（含 _TOOL_EXCLUSION_MAP 工具排除策略 + agent_type 注入）
│   │   ├── _subagent_spawner.py # SubAgentSpawner — 创建/渲染/事件发布
│   │   ├── _tool_callbacks.py   # ToolCallbackChain — 工具生命周期回调（before/after/run）
│   │   ├── _command_core.py     # 命令核心 — 调度基础设施、注册表、帮助文本
│   │   ├── _capture_manager.py  # stdout 捕获管理器（工具输出捕获）
│   │   ├── message_queue.py     # MessageQueue — 异步消息队列（asyncio.Queue）
│   │   ├── message_edit.py      # 消息编辑功能
│   │   ├── file_change_record.py # 文件变更记录
│   │   ├── pipeline.py        # 中间件处理管道（Pipeline.run_round_async）
│   │   ├── compression.py     # 上下文压缩（策略模式）
│   │   ├── context_manager.py # 上下文管理器 + 消息上限控制
│   │   ├── context_selector.py / context_summarizer.py
│   │   ├── sandbox_manager.py # 文件沙盒管理器
│   │   ├── parallel_executor.py # ParallelExecutor — 并行 SubAgent 调度（批量模式）
│   │   ├── tool_executor_async.py # AsyncToolExecutor — 异步工具执行器
│   │   ├── cache.py             # 增量统计缓存
│   │   ├── constants.py          # 主题常量
│   │   ├── commands.py           # 命令系统入口
│   │   ├── commands/              # 命令插件子模块
│   │   ├── commands_config.py / commands_data.py / commands_session.py
│   │   ├── events/            # 核心事件总线 + 事件类型
│   │   ├── middleware/        # Pipeline 中间件（审计/中断/状态机/可观测性/工具适配器）
│   │   ├── ports/             # 六边形架构端口定义（13 个端口）
│   │   └── telemetry/         # 可观测性（指标/追踪/上下文传播）
│   │
│   ├── chat_ui/            # 终端聊天渲染引擎
│   │   ├── _consumer.py       # 事件消费者（队列 → 增量渲染），持有 CursorTracker 实例注入所有子系统
│   │   ├── _engine.py         # 增量渲染引擎（render 线程 10Hz），集成 CursorTracker 坐标同步
│   │   ├── _dispatcher.py     # 事件分发（11 种事件类型 → 渲染命令）
│   │   ├── _renderers.py      # 渲染器集合（14 种 _do_* 方法，集成 CursorTracker 行数追踪）
│   │   ├── _render_state.py   # 渲染状态管理
│   │   ├── _completion.py     # Tab 命令/会话 ID 自动补全
│   │   ├── _const.py          # 渲染相关常量定义
│   │   ├── _protocols.py      # 渲染协议定义
│   │   ├── _state.py          # 渲染状态追踪
│   │   ├── _utils.py          # 渲染工具函数
│   │   └── _error_handler.py  # 日志→上屏（日志显示在底部栏上方）
│   │
│   ├── tools/              # 工具调用系统（14 个内置工具）
│   │   ├── base.py            # Func 基类 + 元数据系统（含 can_use 工具可用性检查 / agent_type）
│   │   ├── file_base.py       # FileToolBase 文件操作基类（含 plan/write_memory agent 路径白名单）
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
│   ├── ui/                 # 终端 UI
│   │   ├── display.py         # 显示系统（含 ANSI 输出、ParallelDisplay）
│   │   ├── theme.py           # 主题（dark/light/high-contrast）
│   │   ├── _cursor_tracker.py # ★ 全局光标坐标追踪系统（1-based row/col，save/restore 检查点）
│   │   ├── _bottom_bar.py     # 底部固定输入栏（3 行），集成 CursorTracker 坐标同步
│   │   ├── _bottom_bar_completion.py # 底部补全弹窗，集成 CursorTracker
│   │   ├── _bottom_bar_status.py     # 底部栏状态行格式化
│   │   ├── _bottom_bar_theme.py      # 底部栏主题颜色常量
│   │   ├── _bottom_bar_selection.py  # 底部栏交互选择
│   │   ├── _bottom_cursor.py         # 光标视觉位置计算
│   │   ├── _completion.py     # Tab 命令/路径自动补全
│   │   ├── _lock.py           # 输出锁机制（render 线程独占）
│   │   ├── _stdout_tracker.py # stdout 行追踪器
│   │   ├── _blessed.py        # blessed 终端封装
│   │   ├── ansi.py / colors.py # ANSI / 颜色常量
│   │   ├── console.py         # Rich 控制台
│   │   ├── base_display.py    # BaseDisplay 基类
│   │   ├── adapters.py        # UI 适配器
│   │   ├── diff_renderer.py   # Diff 渲染
│   │   ├── msg_list.py        # 消息列表编辑
│   │   ├── narrow.py          # 窄屏检测
│   │   ├── output_target.py   # 输出目标
│   │   ├── terminal_adapter.py # 终端适配器
│   │   ├── tui/               # TUI 组件（消息显示/状态栏/选择器）
│   │   ├── parallel/          # 并行 SubAgent 面板显示
│   │   ├── events/            # UI 事件总线（15 种事件类型）
│   │   ├── common/            # 公共基础设施（AgentStateStore）
│   │   ├── components/        # UI 组件（cost_display）
│   │   ├── formatters/        # 参数格式化
│   │   ├── renderer/          # 帧渲染器
│   │   └── state/             # 显示状态管理
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
│   └── observability/      # 可观测性门面
```

---

## 后续计划

### 1. 🎨 增加并优化 TUI 渲染

重构终端用户界面渲染层，提升视觉体验与交互流畅度：

- **流式渲染性能优化** ✅ — 降低增量 Markdown 渲染延迟，消除大 Token 输出时的界面卡顿（`chat_ui/` 增量流式渲染引擎已实现）
- **光标坐标追踪** ✅ — 新增 `CursorTracker` 全局光标坐标追踪系统，集成到 ContentRenderer / RenderEngine / _BottomBar / _CompletionPopup，消除坐标推算累积误差
- **富交互组件** — 在终端中嵌入可交互元素（选择列表、确认弹窗、进度条），减少纯文本输出的信息密度
- **语法高亮增强** — 支持更多编程语言的代码块高亮，优化长代码段的折叠/展开机制
- **多面板布局** — 对话区/工具调用日志/系统状态分屏显示，便于调试与观察 Agent 行为
- **主题系统扩展** ✅ — 支持自定义配色方案，适配亮色/暗色终端环境（已内置 dark/light/high-contrast 三种主题）
- **Web UI 同步增强** — 终端与 Web 界面的渲染逻辑复用，保证两种模式下显示一致性

---

### 2. ✅ 🧠 Plan Agent 架构 — 已完成

独立的 **Plan Agent** 层已实现并投入使用。任何文件修改或新需求前，必须先通过 `dispatch_agent(type="plan")` 委派 plan SubAgent 生成结构化计划文件（`.chat/plan/`），主 Agent 读取后逐条执行，形成「规划 → 探底 → 记忆检索 → 执行 → 审查 → 验证 → 记忆更新」七阶段流水线。详见上方 🔄 [Agent 工作流程](#agent-工作流程)。

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
