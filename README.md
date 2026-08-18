# DeepSeek-cli `v2.2.0`

全异步、高可扩展的 AI 聊天服务后端，支持多模型适配、增量流式 Markdown 渲染、工具调用系统、上下文压缩和终端交互界面。

---

## 快速开始

### 1. 安装依赖

项目使用 **Python ≥ 3.9**。

#### 方式一：一键安装（推荐）

```bash
# 安装全部核心依赖
pip install httpx rich Pygments Jinja2 beautifulsoup4 chardet aiofiles qrcode

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
| `httpx` | HTTP 请求库 | `pip install httpx` |
| `rich` | 终端富文本输出 | `pip install rich` |
| `Pygments` | 代码语法高亮 | `pip install Pygments` |
| `Jinja2` | 模板渲染 | `pip install Jinja2` |
| `beautifulsoup4` | HTML 解析 | `pip install beautifulsoup4` |
| `chardet` | 字符编码检测 | `pip install chardet` |
| `aiofiles` | 异步文件操作 | `pip install aiofiles` |
| `qrcode` | 终端二维码生成（微信 ClawBot 登录） | `pip install qrcode` |

---

### 2. 配置

#### 方式一：配置文件（推荐）

创建配置文件 `~/.chat_config/chatrc.json`：

```json
{
    "provider": "deepseek",
    "api_key": "sk-你的API密钥",
    "model": "deepseek-v4-flash",
    "reasoning_effort": "max",
    "temperature": 0.2,
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

#### 单次问答模式

```bash
python chat.py -p "你好，请介绍一下自己"
```

输入一句话，大模型回答完成后立即退出，适合脚本调用。

#### 从保存的会话恢复

```bash
python chat.py --load <会话ID>
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
| `python chat.py session list` | 列出所有会话 |
| `python chat.py session delete abc123` | 删除会话 |
| `python chat.py session export abc123` | 导出会话 |
| `python chat.py --version` | 显示版本信息 |
| `python chat.py clawbot` | 微信 ClawBot 远程控制（扫码登录） |
| `python chat.py clawbot --re-login` | 强制重新扫码登录 |

---

### 3.5 微信 ClawBot 远程控制（clawbot）

通过微信官方 ClawBot 插件协议（iLink Bot API）实现**远程发命令 + 结果显示**：

```bash
python chat.py clawbot              # 启动（复用缓存凭证或扫码登录）
python chat.py clawbot --re-login   # 强制重新扫码登录
```

**登录**：终端会直接渲染微信官方登录二维码（手机扫码即可，无需打开文件），扫码确认后自动进入监听模式。

**远程发命令**（在微信里给 ClawBot 发消息）：

| 微信消息 | 功能 |
|---|---|
| 普通文本 | AI 对话（DeepSeek 会话引擎，可自动调用文件/Shell 等工具） |
| `/shell <命令>` | 远程执行 Shell 命令并回显结果 |
| `/clear` | 清空当前会话上下文 |
| `/new` | 开始新会话 |
| `/status` | 显示模型、会话与连接状态 |
| `/time` | 显示连接剩余时间 |
| `/model <名称>` | 切换模型 |
| `/help` | 显示帮助 |

**安全配对**：首次发消息的用户需回复终端打印的配对码完成授权，之后该用户的所有命令都被处理；已授权用户持久化在 `~/.chat_config/clawbot_allowed.json`。

**其他说明**：
- 每个微信用户有独立会话（LRU 上限 20 个），结果分段回显到微信
- 输入状态指示（"正在输入"）自动发送/取消
- iLink 连接有效期 24 小时，到期前自动提醒并支持扫码重连

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
| `Ctrl+P` / `↑` | 浏览输入历史（上一条） |
| `↓` | 浏览输入历史（下一条） |
| `Ctrl+R` | 反向历史搜索（配置门控；默认重试上一轮） |
| `Ctrl+T` | 循环切换配色主题（dark/light/high-contrast） |
| `Ctrl+L` | 清屏 |
| `Ctrl+D` | 退出程序（输入为空时） |
| `Ctrl+B` | 主 Agent 空模式切换 |
| `Ctrl+C`（首次） | 中断当前 AI 回复 |
| `Ctrl+C`（再次） | 强制退出程序 |
| `Tab` | 自动补全（命令名、会话 ID 等） |
| `Shift+Tab` | 补全反向循环 |
| `PgUp` / `PgDn` | 补全弹窗翻页 |
| `Ctrl+A` / `Home` | 光标移到行首 |
| `Ctrl+E` / `End` | 光标移到行尾 |
| `Ctrl+F` / `→` | 光标右移一字符 |
| `Ctrl+B`（编辑） | 光标左移一字符（`←` 键） |
| `Ctrl+←` / `→` | 词跳转（等价 `Alt+B` / `Alt+F`） |
| `Ctrl+W` / `Alt+Backspace` | 删除光标前一个词 |
| `Alt+D` | 删除光标后一个词 |
| `Ctrl+U` | 删除光标到行首 |
| `Ctrl+K` | 删除光标到行尾 |
| `↑` / `↓`（补全可见） | 移动补全高亮 |

---

### 5. 斜杠命令（终端交互模式下）

在对话输入框中以 `/` 开头输入命令：

| 命令 | 别名 | 功能 |
|------|------|------|
| `/help` | — | 显示所有可用命令 |
| `/clear` | — | 清空对话（保留系统提词） |
| `/loop <N> <提词>` | — | 循环执行 N 次指定提词（每轮第1次用用户提词，第2次用固定提词"继续完成所有"） |
| `/pin` | — | 标记重要消息（压缩时保留） |
| `/editmsg` | — | 编辑当前会话消息（同 Ctrl+O） |
| `/undo` | — | 撤销上一轮对话 |
| `/retry` | `/r` | 重新生成上一条回答 |
| `/edit` | — | 编辑并重新发送上一条输入 |
| `/model` | — | 切换模型（无参数时交互选择，支持序号/名称） |
| `/reasoning [等级]` | — | 调整推理等级（low / medium / high / max，无参数时显示当前值） |
| `/temperature [数值]` | — | 调整大模型温度（0.0 ~ 2.0，无参数时显示当前值，保存到配置） |
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

AI 代理在对话中可调用以下工具完成各类操作。共 **17 个内置工具**，涵盖文件操作、代码搜索、网络请求、用户交互等能力。

### 工具列表

| 工具名 | 缩写 | 分类 | 并行安全 | 功能说明 |
|--------|------|------|---------|---------|
| `read_file` | rf | IO | ✅ | 读取文件内容，支持指定行号范围、自动编码检测 |
| `write_file` | wf | IO | ✅ | 覆盖写入文件，自动创建父目录，原子写入 |
| `update_file` | uf | IO | ❌ | 精确替换文件中的文本（old_string → new_string），支持 use_regex 正则替换 |
| `search` | sr | 搜索 | ✅ | 在项目源码中搜索正则表达式，自动排除非源码目录 |
| `find` | fn | 搜索 | ✅ | 按通配符模式查找文件和目录，支持深度控制 |
| `ls` | ls | IO | ✅ | 列出目录内容，支持详细格式和隐藏文件显示 |
| `bash` | bs | 执行 | ❌ | 执行 shell 命令（安全沙盒保护，禁止替代专用工具） |
| `bash_opt` | bo | 执行 | ❌ | 按 task_id 操作后台 bash 任务：read（读取当前已产生的全部输出并清空缓冲，立即返回）/ wait（等待完成取输出）/ kill（杀死进程树）/ stdin（发送文本输入）/ keys（发送光标键盘消息，跨平台 ANSI/VT100） |
| `cp` | cp | IO | ✅ | 复制文件或目录，保留元数据，支持沙盒撤回 |
| `mv` | mv | IO | ✅ | 移动文件或目录，支持跨文件系统 |
| `rm` | rm | IO | ❌ | 删除文件或目录（删除前自动备份到沙盒） |
| `mk` | mk | IO | ✅ | 创建目录，支持递归创建父目录 |
| `web_search` | ws | 网络 | ❌ | DeepSeek 官方原生联网搜索（Anthropic 兼容 Messages API + web_search_20250305），返回来源列表（标题/URL/摘要） |
| `web_fetch` | — | 网络 | ✅ | 获取指定 URL 的网页全文（自动提取正文，SSRF 防护，仅 http/https） |
| `user_select` | us | 交互 | ❌ | 向用户显示交互式选择界面（单选/多选/超时回退/非交互回退，选项可带说明，TUI 中高亮选项时说明显示在右侧） |
| `subagent` | sa | Agent | ❌ | 并行派发子 Agent 执行独立任务（支持类型：map/review/plan/execute）；默认后台执行，立即返回 `{"task_id": "sa-xxx"}` JSON，完成后结果自动插入对话（或由 subagent_opt 管理）；background=false 时前台阻塞执行并直接返回结果。后台 subagent 仅主 Agent 可派发 |
| `subagent_opt` | so | Agent | ❌ | 按 task_id 操作后台 subagent 任务（subagent 默认后台启动）：read（读取当前状态与已产生的结果，立即返回）/ wait（等待完成取结果，timeout 秒，默认 300/0 无限）/ kill（取消后台 subagent 任务）。仅主 Agent 可用 |

### 工具分类

| 分类 | 工具 | 说明 |
|------|------|------|
| **文件 IO** | read_file, write_file, update_file, ls, cp, mv, rm, mk | 读写文件、目录操作、文件管理 |
| **代码搜索** | search, find | 正则搜索源码、通配符查找文件 |
| **命令执行** | bash, bash_opt | 安全沙盒中执行 shell 命令；按 task_id 操作后台 bash 任务（bash 后台任务注册在 bash 专用表 `_background_tasks`） |
| **网络访问** | web_search, web_fetch | 网页搜索（DeepSeek 官方原生搜索）与网页全文获取 |
| **用户交互** | user_select | 交互式选择弹窗（单选/多选/超时回退） |
| **Agent 调度** | subagent, subagent_opt | 并发派发原子 Agent 执行独立任务；按 task_id 操作后台 subagent 任务（subagent 后台任务注册在独立表 `_subagent_tasks`，与 bash 后台任务分表隔离） |

### 工具设计原则

- **纯异步** — 所有工具均基于 `asyncio`，不阻塞事件循环
- **沙盒安全** — 文件操作自动备份，支持撤回（undo）
- **元数据系统** — 每工具声明并行安全、网络依赖、超时估计等元数据，供调度层优化
- **双端适配** — 同时支持终端（`display()`）渲染路径

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

本项目的核心是 **Main-Sub Agent 架构**，通过 `subagent` 委派任务给不同类型的子 Agent。

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
| **execute** | 全工具（不含 user_select/subagent/subagent_opt） | 读/写/改代码、执行测试、通用任务 |

> **工具排除策略**：execute 排除 `subagent/subagent_opt/user_select`；map 排除 `bash/write_file/update_file/rm/mv/cp/mk/web_search/subagent/subagent_opt/user_select`；review 排除 `bash/write_file/update_file/rm/mv/cp/mk/subagent/subagent_opt/user_select`（保留 web_search）；plan 排除 `bash/rm/mv/cp/mk/subagent/subagent_opt/user_select`，write_file/update_file 仅限 `.chat/plan/` 目录。`subagent_opt` 与后台 subagent（`subagent background=true`）均仅主 Agent 独有：SubAgent 工具白名单全类型排除 + 工具运行时 `isinstance(agent, SubAgent)` 双保险。SubAgent 在 `_handle_tool_calls()` 中注入 `agent_type` 到 Func 实例，`Func.can_use()` 进行统一检查。`FileToolBase._validate_path_and_size()` 额外实施 plan Agent 路径白名单校验。

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
│   │   ├── _assembly.py / _assembly_steps.py / _base_display.py / _completion.py / _completion_engine.py
│   │   ├── _config.py / _const.py / _consumer.py / _diff_renderer.py / _dispatcher.py / _format.py
│   │   ├── _input.py / _input_io.py / _input_parser.py / _input_buffer.py / _input_dispatcher.py
│   │   ├── _input_layout.py / _input_metrics.py / _input_orchestrator.py / _ink_bridge.py / _lifecycle.py
│   │   ├── _screen.py / _snapshot.py / _stdout_tracker.py / _subagent_panel.py / _subagent_render.py
│   │   ├── _subagent_state.py / _tool_icons.py / _width.py / input.py / _history_disk.py / _system_monitor.py
│   │   ├── app/               # AppModel + apply_cmd + 组件树（input_area/status_bar/toolcard/...）
│   │   ├── consumer/          # ChatUIConsumer 事件消费者 + 渲染入口
│   │   ├── core/              # 核心工具（color/style/singleton/_fx/_theme）
│   │   ├── events/            # UI 事件总线 + DisplayEvent 类型定义
│   │   ├── ink/               # React Ink 风格组件框架（调和器/flexbox/hooks/渲染器）
│   │   ├── pipeline/          # 消息编辑/显示管道
│   │   ├── state/             # 消费/注册表状态管理
│   │   └── subagent/          # SubAgent 面板子域聚合门面
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
│   ├── tools/              # 工具调用系统（17 个内置工具）
│   │   ├── base.py            # Func 基类 + 元数据系统（含 can_use 工具可用性检查 / agent_type）
│   │   ├── file_base.py       # FileToolBase 文件操作基类（含 plan agent 路径白名单）
│   │   ├── registry.py        # 工具注册表（自动发现 + 调度 + 元数据索引）
│   │   ├── read_file.py / write_file.py / update_file.py
│   │   ├── search.py / find.py / ls.py
│   │   ├── bash.py / cp.py / mv.py / rm.py / mk.py
│   │   ├── web_search.py / web_fetch.py / user_select.py / subagent.py / subagent_opt.py
│   │   ├── file_ops.py        # 文件操作原子工具（原子写入、路径安全校验、沙盒记录）
│   │   ├── _constants.py      # 共享常量（排除目录、安全路径、编码等）
│   │   ├── encoding.py        # 编码检测工具函数
│   │   ├── utils.py           # 工具通用辅助函数
│   │   ├── search_providers.py  # DeepSeek 官方原生搜索提供者（web_search 依赖）
│   │   └── page_fetcher.py    # 网页内容抓取（web_fetch 依赖）
│   │
│   ├── prompt_builder/     # 系统提示词构建
│   ├── notifications/      # 桌面通知（Termux/Linux/Windows）
│   └── observability/      # 可观测性门面（聚合指标/追踪/遥测日志）
```

---

## 六边形架构（Ports & Adapters）

核心层通过 **8 个端口接口** 访问基础设施，实现依赖倒置——核心层不直接依赖 `api`、`tui`、`chat_msgs` 等具体实现模块，基础设施层通过适配器模式实现这些端口。

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

显示层事件系统，定义 **24 种 `DisplayEvent`** 类型（生命周期/工具调用/Agent 状态/模型阶段/流式内容/附加状态/通用输出/用户交互），基于 `CoreEventBus` 底层发布机制实现。`DisplayEventBus` 对 `DisplayEvent` 子类提供类型安全包装，与核心事件（字符串类型）并行独立运作，确保终端共享相同的事件语义。

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
- **React Ink v6 全特性补齐（A~G）** ✅ — 对照官方 v6 API 补齐剩余特性（`tests/test_tui/ink/test_react_ink_complete.py` 44 例固化）：**文本样式** strikethrough（`\x1b[9m`）/inverse（`\x1b[7m`）；**布局** flexDirection="row-reverse"/"column-reverse"（视觉顺序反转）、flexWrap="wrap-reverse"（行序反转）、alignItems/alignSelf="baseline"（终端近似底部对齐）与 "auto"（跟随父）、alignContent（flex-start/end/center/stretch/space-between/around/evenly 行分布）、columnGap/rowGap（gap 独立控制）、position="static"（忽略定位偏移）、overflow/overflowX/overflowY="hidden"（绘制裁剪：垂直行裁剪 + 水平列切片）、aspectRatio（宽/高缺省维度推导）；**边框** borderStyle 自定义对象（{topLeft,top,topRight,left,bottomLeft,bottom,bottomRight,right} 左右独立）、borderTopColor/RightColor/BottomColor/LeftColor、borderDimColor 系列、borderBackgroundColor 系列、borderTop/Right/Bottom/Left（bool 显隐）；**Box 背景** backgroundColor（区域填充 + 子 Text 未指定时继承）；**Hooks** usePaste（粘贴独立通道，阻断 useInput）/useBoxMetrics(ref)（width/height/left/top/hasMeasured）/useWindowSize（columns/rows，resize 自动重渲染）/useFocusManager（enableFocus/disableFocus/focusNext/focusPrevious/focus(id)/activeId，Tab 自动切换）/useFocus({id,autoFocus,isActive})/useCursor（setCursorPosition）/useIsScreenReaderEnabled/useAnimation（帧号+时间戳）/useApp 扩展（waitUntilRenderFlush/suspendTerminal）；**生命周期** render() 轻量入口（waitUntilExit/unmount/cleanup/rerender/clear）；**输入与组件** useInput 兼容 React Ink `(input, key)` 双参签名（key 含 pageUp/pageDown 等完整字段，PageUp/PageDown 键解析）、Static items 数组模式 + style prop、Transform (line, index) 逐行签名、wrap="hard" 字符级硬拆
- **标准控件/布局重构（阶段2）** ✅ — app 组件树全部改用语义化标准布局容器：App 消息区/底部区 Column、TopHeader Row、StatusBar/ChatView Column（ToolStatusHeader 已从组件树移除——工具状态由工具卡片顶边框 ● 展示，死代码收尾时删除模块）、_ParseLine/_StreamingLine 空状态统一空 TEXT（避免 BOX↔TEXT fiber 销毁重建）；控件库内部同步收敛：SelectInput/TextInput/MultiSelect/Table/Divider/Grid 用 Row/Column 门面（输出等价）；渲染错误修复 E1（显式 width 超 avail 钳制——行宽不变量）、E2（宽字符第二列覆盖不再静默丢失，`_merge_line` 与 input-area 统一合并路径）、E8（SelectInput/MultiSelect items 动态缩小越界防护）、E9（MultiSelect 不可哈希 value 兜底）、E10（TextInput 光标列对齐）；性能优化 P-H2/P-H3/P-H7/P-H9/P-H10/P-H14（布局/收集/截断/调和快路径，1000 行历史帧渲染 < 1ms）
- **user_select 弹窗 React Ink 化** ✅ — `user_select` 工具交互从「命令补全弹窗（show_completions + CompletionState）+ 手动 raw I/O（select/read_byte/cbreak）」迁移为独立 React Ink 组件 `UserSelectPopup`（`src/tui/app/user_select.py`）：弹窗在 App 组件树底部区渲染（StatusBar 上方，不可见零高度），`use_input` + `use_state` 处理 ↑↓/Enter/Esc/空格（不再直接读 stdin、不再 stop/start EscapeMonitor、不再操作补全弹窗私有字段）；结果经 `model.user_select.done/action/result` 回传工具协程；`_run_interactive` 不再 suspend render 线程（InputDispatcher 保持路由 use_input）；App 以 `key=seq` 强制重挂载（连续多次打开不残留旧选中）；组件 hooks 无条件注册且 `is_active=visible`（弹窗关闭后输入放行旧路径，修复输入被吞回归）；`InputDispatcher` ESC 内联分支先询问 input router（修复前 Esc 直接走中断从未进 router——`useInput` 钩子收不到 escape 事件，弹窗按 Esc 无法取消；修复后 router 消费则跳过中断路径，无 router 时 Esc 语义零变化）；单选高亮 ▶/多选 ●○ 勾选/分栏说明（右栏当前选中项说明，复用 input_area 列宽计算）；**模态底部视图通用机制（2026-08-17）**：user_select 从底部区常规成员**独立为「模态底部视图」**——弹窗打开时**底部框（状态栏/输入区）不显示、弹窗在原来底部框位置独立显示**，做成通用化 = **`use_modal` hook**（`src/tui/ink/_hooks_input.py`——与 `use_fullscreen` 同一 `FullscreenHook` 节点类型，模态输入接管语义泛化：激活时 router 全部 use_input 未消费的事件吞掉，不落入输入缓冲）+ **`AppModel.bottom_view`** 状态 + **`app.BOTTOM_VIEWS`** 底部视图注册表（App 按 id 只渲染底部区对应视图；key 约定支持 `(组件, key_fn)` 元组——UserSelectPopup 用 `model.user_select.seq` 递增序号强制重挂载；状态栏/输入区不渲染 → 输入光标自动隐藏；弹窗高度预算 `h-11 → h-3`（不再预留状态栏/输入区空间）；输入文本不再清空——弹窗关闭后原输入恢复显示不丢失）；user_select 工具 / `CommandUiAdapter.run_bottom_bar_selection` 协议：打开设置 `bottom_view="user_select"`、清理恢复 ""（与 UserSelectState 同生命周期）；**/editmsg 消息选择独立协议（2026-08-18 用户需求：editmsg 与 user_select 不能用同一份代码）**——独立状态 `EditMsgSelectState`（`model.editmsg_select`）+ 独立组件 `EditMsgSelectPopup`（`src/tui/app/editmsg_select.py`）+ 独立底部视图 `bottom_view="editmsg"`，不复用 UserSelectPopup / user_select 状态；**editmsg 每条消息只显示一行**（`_user_msg_summary` 单行摘要，多行折叠为一行，超宽截断）；`reset_display`（Ctrl+L 清屏）同时退出底部视图；新增底部视图只需注册表加条目 + 设置 bottom_view，底部区渲染/输入接管/光标隐藏自动生效；测试 `tests/test_tui/test_bottom_view.py` + `tests/test_tui/test_editmsg_select.py` + `tests/test_tui/test_user_select_no_duplicate.py` + `tests/test_tui/test_fullscreen_view.py` 固化
- **轨迹视图（DSH 风格，Ctrl+H 开关）** ✅ — 2026-08-19：TUI 实现 DSH Web「轨迹（Trajectory）」功能——**Ctrl+H** 打开/关闭（0x08 字节从 backspace 改判为 ctrl_key '\x08'；Backspace 键仍为 0x7f DEL，现代终端字节可区分；未注入轨迹回调时回退 backspace 语义零回归；CSI u 增强键盘协议 `\x1b[104;5u`/`\x1b[8;5u` 同路径）；**打开时整屏只显示轨迹界面**（消息区/顶部标题栏/状态栏/输入区全部不渲染——「其他 TUI 不显示，只显示这个界面」，台账/检查器占满整个终端高度；Esc/Ctrl+H 关闭恢复完整聊天界面；**2026-08-17 迁移到「模态全屏视图通用机制」**：打开期间**模态独占键盘输入**——字符/Enter/Backspace 等未消费按键被 input router 吞掉、**不落入输入缓冲**（杜绝「看不见的输入」误编辑/误提交；关闭后输入区恢复正常输入）；机制通用化 = **`use_fullscreen` hook**（`src/tui/ink/_hooks_input.py`——激活时 router 全部 use_input 未消费的事件返回 True，InputDispatcher 跳过旧路径）+ **`AppModel.fullscreen`** 状态 + **`app.FULLSCREEN_VIEWS`** 视图注册表（App 按 id 整屏渲染）+ **`_make_fullscreen_toggle_cb(model, session, view_id)`** 通用开关工厂（任意全屏视图绑定快捷键复用；`trace_open` 为 `fullscreen=="trace"` 兼容别名 property；光标隐藏通用化为 fullscreen 非空即隐藏——新增全屏视图只需注册表加条目 + 设置 fullscreen，整屏渲染/输入接管/光标隐藏自动生效）：**React Ink 左右布局**（`src/tui/app/trace_view.py`）——左栏「台账」（轮次分隔 `── 轮次 N ──` + 记录行 `#N 种类图标 摘要 右对齐耗时`，选中行 ▶ + 整行背景高亮，虚拟窗口按终端高度自适应）+ 右栏「检查器」（#N 种类 · 状态 ●/✔/✖ · 耗时 · token 输入/输出 · 内容行按栏宽换行 + 视口截断 + 省略提示，每行唯一 key）；**数据源 = agent 消息列表**（装配经 `_register_session_handlers` 把 `session.messages` 注入 `AppModel.message_source`——轨迹从真实会话消息组装业务记录（system/user/assistant+tool_calls/tool 返回），**不是 TUI 渲染过的聊天块**；未注入时回退块路径）——**system 消息 → system 记录显示系统提词**（每条一条，摘要=首行、检查器读全文，对齐 DSH SYSTEM 记录）、user 消息 = 新轮次、assistant 消息按内容拆分思考💭/回答💬/工具调用⚡、**工具调用 + tool 返回按 `tool_call_id` 合并成一条**（台账行同时显示调用 `⚡ bash ls -la` 与返回首行预览 `· 总用量 4462…`，详情 = 调用行 + 返回行，无匹配返回的孤儿 tool 消息独立显示）；**# 0 工具列表**（2026-08-17 用户需求：台账固定首条 `# 0 🧰 工具列表`——右侧检查器显示 agent 全部工具，**一行一个**（显示原名），数据源 = `ToolRegistry` 注册表（注册顺序 = 自动发现顺序），注册表异常/为空时静默降级不显示；用户确认范围：**主轨迹与 subagent 轨迹均显示**）；`use_memo` 指纹缓存（消息列表身份/长度/尾消息内容变化才重建）+ 详情惰性提取；导航 ↑↓/PgUp/PgDn/Home/End/g/G（g=首/G=末），**-1 尾部跟随**（打开定位最新记录、流式追加自动跟进，导航后写回具体索引），Esc/Ctrl+H 关闭；**工具调用参数/返回值用树控件显示**（2026-08-17 用户需求：轨迹 Trace 的工具调用修改——选中 tool 记录时检查器内容 = **`▸ 参数` 小节 + 参数树**（`tool_args` 原始 arguments JSON 树形展开：dict 键值叶子 / list 下标 `[i]` / 嵌套 `key (N 项)` / 空容器 `{}`/`[]` / JSON 字面量 `null`/`true`/`false`）+ **分割线**（`──` 深灰满宽）+ **`▸ 返回值` 小节 + 返回值树**（`tool_result` 原始返回文本：JSON 树形展开、非 JSON 纯文本每行一个叶子节点；对齐 ink Tree 控件渲染——缩进 + `▾` 展开指示符，只读展示不抢台账导航焦点；head-first 截断 + 「… 后 N 行省略」后置；无树数据（手动构造/异常）回退纯文本 lines 零回归；模块级缓存跨流式重建命中）；**框架修复 P3-21**（`_cursor.find_input_fiber` 长 sibling 链 O(2^N) 指数压栈 → 压栈去重 O(N+Σ链长)——轨迹检查器 23 个兄弟 TEXT 触发，渲染线程卡死数秒，200 链/环结构回归测试固化）；测试 `tests/test_tui/test_trace_view.py` 142 例（解析/分发/消息构建/块回退/渲染/注入链/工具列表/工具树显示/端到端 pyte 固化）
- **标准控件/布局重构（阶段3）** ✅ — 新增标准控件 RadioList（单选列表：◉/○ 指示符 + 键盘导航 + limit 窗口）/CodeBlock（代码块：边框 + 语言标签标题栏 + 行号 + 宽字符安全截断，行宽不变量）/InlineSpinner（行内时间基 spinner 字符控件）/Gradient（逐字符渐变文本：lerp_color 色标插值，TopHeader 渐变单一真源收敛）；app 层 `_StreamingLine` 手写 spinner 单 TEXT → Row + InlineSpinner + TEXT 标准控件表达（渲染输出等价）；控件与布局门面经 `widgets/__init__.py`/`ink/__init__.py` 统一导出（`tests/test_tui/ink/test_widgets_radio_codeblock.py` 21 例固化）
- **TUI 全量标准 React Ink 组件化（阶段4，无例外）** ✅ — 所有 TUI 布局/组件按标准 React Ink 表达重构，无例外（`tests/test_tui/ink/test_react_ink_refactor.py` 11 例固化）：
  - **committed-chat → StaticLines 标准组件**（`src/tui/ink/widgets/staticlines.py`）——聊天历史静态行批量渲染从 app 层私有 host 迁移为标准组件（组件库导出 `h(StaticLines, {"lines": ...})`），保留帧前缀缓存/增量发射性能机制（无变化帧 O(1)）；`render_frame`/`layout` 的 committed 前缀消费统一识别 static-lines；旧 host 标签保留为兼容别名；
  - **input-area → InputArea + CompletionPopup 标准组件**（`src/tui/app/input_area.py`）——输入区自定义 host（直接画布绘制）迁移为函数组件 `InputArea`（返回 Column 组件树：`CompletionPopup` 弹窗 + 上/下分隔线 TEXT + 历史搜索 TEXT + 输入行 TEXT），`dataInputArea` 标记容器 + props 透传；`session._position_cursor` 经 dataInputArea 容器定位 + 换行布局缓存写回（`fiber._input_layout_cache`——同 text/max_input 帧零重复换行计算）；`use_memo` 原子值 deps（id/len 指纹 + 时间桶）缓存 Element 列表——修复嵌套 tuple deps 恒 miss（is 引用比较）后无变化帧 **~0.9ms → ~0.62ms**；占位符渐显状态经 `use_ref` 组件级持久（修复组件化后渐显每 0.1s 桶重置 bug）；
  - **subagent 卡片去 ANSI 中间层**（`src/tui/_subagent_render.py`）——子代理卡片渲染从「ANSI 字符串行拼接」迁移为「ink Line 行（StyledRun）」：`render_frame`/`build_agent_lines`/`format_tool_record` 返回 `Line`，样式统一用 `Style(fg=色号)`（与 StatusBar/ToolCard/UserSelect 同源），`subagent_panel.SubAgentCard`（`_lines_to_children` 转换点）直接复用 `Line.runs` 转 TEXT 标准组件（不再 `ansi_to_runs` 解析）；`Line` 增补值比较 `__eq__`（控制器变更检测）；`_get_tool_color` 返回 `Style`（色号与旧 ANSI 一致）；
  - **兼容层彻底移除（无例外）** — 旧 host 标签 `committed-chat`/`input-area` 注册已移除（生产/测试全部用 StaticLines/InputArea 标准组件）；`chat_view.register()`/`input_area.register()` 空操作移除；`input_area` 遗留 host 绘制函数（`_measure`/`_paint`/`_build_separator_line`/`_merge`/`_compute_input_rows`/`_wrap_input_text`）与 `ToolStatusHeader` 死代码模块（`tool_header.py`，工具状态已由工具卡片顶边框 ● 展示）已删除（无例外）；`_const.py` 的 `_COLOR_*`/`_C_*` ANSI 颜色常量与 `_screen.py`/`_subagent_panel.py` re-export 已删除（生产渲染统一 `Style(fg=色号)`，色号从 `_SEMANTIC_COLOR` 槽位表解析）；`reconciler._mark_deleted` 递归标记子树全部 fiber deleted（修复函数组件 key 变化时外部缓存——session 输入区 fiber / committed 前缀缓存——失效检测失败）；
  - **性能**：重构后全场景无变化帧 < 1.5ms（20 条历史 + 20 项弹窗 + 中文输入 1.40ms；1050 行历史 0.66ms；流式增长 0.65ms——10Hz 预算 100ms 仅占 <1.5%）；全量 `tests/` 2643 例通过。
- **TUI 全量 React Ink 深化控件化 + 架构守卫（阶段5，2026-08-16）** ✅ — 用户需求「所有 TUI 都要用 React Ink 控件跟布局实现所有」收尾：
  - **渲染辅助层统一 ink 输出模型**：`pipeline/message_display`（非 ChatUI 兜底直写）渲染行迁移为 `ink.output.Line`（`_display_line`）——兜底路径与界面渲染共用同一输出模型；`_diff_renderer`（diff 文本生成）行构建统一迁移为 ink 输出模型（`Line`/`StyledRun`，样式统一 `tui.core.Style`）——`_inline_highlight` 返回 StyledRun 列表、`_render_chunk`/`_flush_pairs`/`_render_diff_summary` 经 `Line` 构建 + `_write_diff_line`（接受 Line，兼容 str 旧调用）渲染，不再手工 `Style.apply` 拼接 ANSI；**输出与旧实现逐字节一致**（7 类典型 diff 场景基线比对 + 字节基线测试固化）；`Line.render()` ANSI 渲染缓存复用（同行跨次零重建）；**`events.consumers.OutputConsumer`（事件回退直写路径）同步迁移 ink 输出模型**——`_LEVEL_COLORS`/`_RESET` ANSI 色串直拼 → `_LEVEL_STYLES`（`core.style.Style`，色号取与旧 16 色视觉等价的 256 色语义色）+ `Line.of(text, style).render()`（旧常量保留为 deprecated 兼容 re-export，生产路径零引用）；
  - **架构守卫测试**（`tests/test_tui/test_ink_guard.py`，AST 静态分析 9 例，防回归）：**R5 渲染模块必须依赖 ink**——tui 模块凡含 `h(`/`use_*` hook 调用者必须运行时依赖 `src.tui.ink`（禁止脱离组件树手工渲染）；**R6 界面渲染层禁止直写终端**——`tui.app.*` 组件树与 `tui.ink.widgets.*` 标准控件库不得出现 `sys.stdout`/`sys.__stdout__` 写入或 `print()`（终端 I/O 由 `_screen`/`ink.session` 等基础设施承担）；**R7 h 字符串 host 合规**——`h("<字符串>")` 必须是内置 host（box/text/static/spacer/app/fragment）或 `register_host` 注册的 host（如 static-lines）；**R8 事件输出消费者统一 ink 输出模型**——`OutputConsumer._write` 生产路径不得引用旧 `_LEVEL_COLORS`/`_RESET`（须经 `_LEVEL_STYLES` + `Line.render()`，回退直写与界面渲染共用输出模型）；
  - **新增测试**：`tests/test_tui/test_message_display_ink.py`（8 例：ink 输出模型 + 兜底行为 + 写失败跳过）+ `tests/test_tui/test_diff_renderer_ink.py`（11 例：StyledRun 行内高亮 / Line 输入截断 / str 兼容 / 字节基线 / 语法高亮路径）+ `tests/test_tui/test_output_consumer_ink.py`（18 例：Style 渲染 / raw 原样 / 未知 level 回退 / 旧常量兼容 re-export / 生产路径零引用 / ANSI 闭合）。
- **TUI 全面控件化（阶段6，2026-08-16 方案B）** ✅ — 用户需求「所有 TUI 都要用 React Ink 控件跟布局实现」深化：界面组件树从「基础 TEXT/Column/Row + 手写 Line 行」进一步迁移为**标准控件库（widgets）表达**，视觉/交互/性能零回归：
  - **TopHeader → Gradient 控件**（`header.py`）——渐变标题经 `h(Gradient, {"styled": ...})` 渲染（styled 注入模式：宽屏 use_memo 缓存引用 / 窄屏截断后注入，与 `_gradient_runs` 视觉等价；`Gradient` 新增 `styled` prop）；
  - **StatusBar → Divider 控件**（`status_bar.py`）——分隔线经 `h(Divider, {"width", "char": "━", "style": sep_style})` 渲染（纯填充分隔线，与 sep_line 语义等价）；`Divider` 新增 `trailing` 右侧内容支持（左侧填充 + 右侧内容，行宽恒 = width——InputArea CPU/MEM/时间戳分隔线场景）；
  - **TraceView → ListView 控件**（`trace_view.py`）——台账左栏经 `h(ListView, ...)` 表达：受控光标（`cursor` prop，跟随/导航写回 `model.trace_selected`）、虚拟滚动（`height` 视口）、导航（↑↓/PgUp/PgDn/Home/End/g/G）、None 分隔行自动跳过、`renderItem(item, index, isSelected)` 三参选中态注入；`ListView` 扩展：受控 cursor / onNavigate / page/g / None 跳过 / enter 放行（无 onSelect 时）；
  - **UserSelectPopup → SelectInput/MultiSelect 控件**（`user_select.py`）——弹窗选项列表经标准控件表达（导航 ↑↓/j/k/g/G 由控件消费、Enter/Esc/空格协议经 onSelect/onSubmit/onCancel 回调承载、`renderItem` 保留单选 ▶/整行背景、多选 ●/○ 勾选、分栏说明视觉；★ 2026-08-18：/editmsg 多行 option_lines 已随「editmsg 独立协议」移除——UserSelectPopup 仅服务 user_select 工具，单行纯文本选项）；`SelectInput`/`MultiSelect` 扩展：vim 导航 j/k/g/G、onCancel（Esc）、onHighlight（选中变化）、renderItem、consumeAll（弹窗模式阻断输入框、Ctrl+C 放行）、无 onSelect 时 enter 放行；
  - **ToolCard → Panel 控件**（`toolcard.py`）——工具卡经 `h(Panel, {"border": 0, ...})` 表达（无边框模式：直接渲染内部 Column，「无边框裸行 + │ 引导线」Claude Code 极简视觉保持——2026-08-06 用户需求）；`Panel` 新增 `border=0/"none"/None/False` 无边框模式；
  - **CompletionPopup → SelectInput 控件**（`input_area.py`）——补全候选项经 `h(SelectInput, ...)` 表达：导航（↑↓/j/k）消费并写回 `completion.selected`（onHighlight）、`limit` = 锁定高度可见行数 + 底部补白（高度锁定防闪烁语义保持）、`renderItem` 复用候选项视觉（▶ 高亮 + match 前缀高亮 + 命令描述灰显）、Enter/Esc 放行（补全确认/关闭由 InputDispatcher 旧路径接管）；分栏说明模式（历史 user_select 场景，生产已迁移）回退 `_build_popup_lines` 旧路径；
  - **架构守卫扩展**（`tests/test_tui/test_ink_guard.py`，12 例）：**R9 界面组件禁止字符串 host**——`tui.app.*` 的 `h()` 第一参禁止字符串（必须用命名控件/布局门面，防绕过控件层）；**R10 界面控件化组件审计**——方案B 迁移清单（header→Gradient / status_bar→Divider / trace_view→ListView / user_select→SelectInput+MultiSelect / toolcard→Panel / input_area→SelectInput）AST 静态防回归；
  - **新增测试**：`test_gradient_styled.py`（styled 注入 6 例）+ `test_select_input_extended.py`（SelectInput/MultiSelect 扩展 12 例）+ `test_listview_extended.py`（ListView 扩展 9 例）+ `test_divider_extended.py`（Divider trailing 5 例）+ `test_completion_popup_widget.py`（CompletionPopup 控件化 6 例）+ `test_status_bar_divider_widget.py`（StatusBar Divider 3 例）；更新 test_header/test_trace_view（控件穿透/受控光标）等既有测试；
  - **性能/视觉保持**：Line 行数据（`_build_lines`/`tool_card_lines`/`_subagent_render`/`_build_status_runs`）作为 TEXT styled props 保留（快照缓存/引用稳定/diff 身份短路性能模型不动）；弹窗静态色/高度锁定/无边框工具卡等既有视觉决策全部保持。
- **行宽不变量（渲染错误修复）** ✅ — E-ROW-OVERFLOW（row 内容自然宽超容器时按 flexShrink 权重收缩子节点，默认 flexShrink=1 React Ink 标准语义，收缩后重新测量约束内部内容）、E-FILL-OVERFLOW（fill=False 容器被钳制时内部子节点按容器实际宽度重测）、E-OVERFLOW-GUARD（render_frame 行级截断防线——行宽恒 <= 文档宽，行级 diff 模型核心不变量）、E-COMMITTED-OVERFLOW（committed-chat 前缀复用路径的行宽守卫——reflow_committed 未执行/失败时 committed_lines 按旧宽度 wrap 产生超宽行，前缀复用不经 E-OVERFLOW-GUARD 直接进帧；修复：chat_view._paint 缓存重建时 O(n) 检查行宽标记 all_ok（非每帧，缓存命中零开销），render_frame 对 all_ok=False 前缀截断超宽行，正常行保持身份短路）；2000+ 模糊用例零超宽（嵌套 row/ZStack/边框/宽字符/绝对定位组合）
- **高级布局能力** ✅ — 百分比尺寸（width/height/min/max="50%" 相对可用尺寸解析）；flexWrap="wrap" 换行流式布局（行间距 = gap，超宽项截断）；position="absolute" 绝对定位（left/top/right/bottom 锚点、显式/百分比尺寸、left+right/top+bottom 拉伸、最近 position="relative" 祖先为基准、脱离正常流不占空间、两阶段布局——正常流测量 + 绝对定位第二遍放置）；布局容器组件（`src/tui/ink/widgets/layout.py`）：Row/Column/Center/Stack/HStack/VStack/Grid（CSS Grid 风格，列等宽 flexGrow）/ZStack（层叠，子节点绝对定位叠放）
- **控件库（widgets）** ✅ — `src/tui/ink/widgets/`：交互控件 SelectInput（单选列表）/TextInput（受控文本输入，含 placeholder/mask/光标）/MultiSelect（多选，space 切换）/ConfirmInput（y/n 确认）/Toggle（开关，space/enter 切换）/Checkbox（复选，`[x]`/`[ ]` 样式，受控/内部双模式）/Tree（树形，展开折叠 + 键盘导航）/ListView（虚拟滚动列表，只渲染视口内行——大列表 O(视口)）/Menu（垂直菜单——分组标题/禁用项/快捷键右对齐/循环导航）/SearchInput（搜索输入——实时过滤 + 结果列表选择 + limit 窗口）/Tabs（标签页——左右键切换 + 内容渲染）；展示控件 Spinner（时间基动画）/ProgressBar（进度条）/Table（对齐表格，支持表头/边框变体）/Badge（背景色块徽章，前景自动对比）/Divider（分隔线，可选标题）/Panel（带标题边框面板，BOX border 标准布局）/Breadcrumbs（面包屑导航——分隔符/active 高亮/maxItems 折叠）；布局门面 Box/Text（React Ink `<Box>`/`<Text>` 生态命名，与 host 等价）/Flex（显式 flexbox）/Spacer（flexGrow 撑开占位）；焦点管理 FocusGroup/Key（Tab/Shift+Tab 在多个可聚焦区域间切换，focus prop 注入互斥）；基于 use_input + use_state，同批连续按键状态经 ref 镜像正确累积（闭包陈旧修复），focus=False 不参与输入路由
- **渲染性能优化（宽度缓存 + 测量缓存）** ✅ — `StyledRun`（frozen 不可变）构造期一次性计算显示宽度（`__post_init__`），`Line.width` 惰性缓存 + `append` 增量维护，`_runs_natural_width` 复用 run 缓存宽度——热路径（diff/截断/画布转换/measure）免重复 `wcswidth_simple`；`_measure_cache`（PERF-14）按 `(ftype, props 引用, avail_w, fill)` 缓存 TEXT 测量结果——同 props 引用无变化帧布局零重建（1000 TEXT 无变化帧 78ms → 51ms，layout_tree 30ms → 6ms，-80%）；`_find_committed_chat` 未挂载快速路径（PERF-15）——无 committed-chat 的组件树每帧零 DFS；reconciler 叶子空子跳过（PERF-16）；绝对定位第二遍快速路径（PERF-17）——无 `position="absolute"` 节点的组件树（绝大多数）跳过第二遍整树遍历（1000+ 节点树省 ~10%）；`_normalize_children` 快速路径（PERF-18）——空/单 Element children 免列表分配 + 遍历（`h(TEXT, {...})` 无子级热路径）；reconciler 遍历迭代化（PERF-19）——`_traverse_functions`/`_attach_host_refs`/`_collect_input_hooks` 递归 → 显式栈（大组件树每帧数千节点省递归调用开销）；叶子内置 host 快路径（PERF-21）——TEXT/SPACER 等叶子跳过 context 清空/provider 检查/子调和（1000+ 叶子树每帧省数千次调用）；wrap 纯 ASCII 批量快路径（PERF-22）——单 run 可打印 ASCII（无空格/换行/控制字符）按 max_width 直接字符串切片（C 级，免 100k 字符逐字符展开 tuple + `wcswidth_simple` 调用），100k 字符 wrap 0.42s → 0.055s（~8x，`test_renderer_perf.py::test_wrap_large_line_smoke` 性能边界从偶发超时转为稳定通过）；1000 行历史帧渲染 0.98ms → 0.53ms（~2x）；真实 TUI 场景（20 条消息 + 长回答，71 行 committed）无变化帧 ~2.5ms、流式增长帧 ~2.5ms（端到端预算测试 `tests/test_tui/ink/test_tui_end_to_end.py`）；渲染健壮性测试见 `tests/test_tui/ink/test_render_robustness.py`；**PERF-24（2026-08-05 渲染管线深度优化）** — `Line.render()` ANSI 渲染缓存（`_r` 字段：同 Line 对象跨帧复用零重建，`append` 修改 runs 时失效——全项目唯一修改点审计确认；实测 200 行 × 200 帧 diff 渲染 ~1.18s → ~0.1s 量级）；`Element.key`/`Fiber.key` 惰性缓存（调和热路径每帧访问，首次计算后 O(1)——Fiber props 变化经 `_set_props` 失效）；`_begin_work` 免 `list(children)` 复制（`_reconcile_children` 只读遍历 tuple）；ChatView `model.blocks[committed_count:]`切片 → 索引循环（免每帧切片分配）；InputArea `_input_snap_key` `props.get` 去重（history_search 局部变量一次提取）；完整渲染管线常规场景 **~0.84ms/帧**、2400 行大历史 **~0.99ms/帧**（含 diff+输出+光标，10Hz 预算 100ms 占 <1%）；性能回归测试 `test_renderer_perf.py::TestLineRenderCache` / `TestRenderPipelineSmoke` 固化；**PERF-25（reconciler 合并元数据遍历）** — `render()` 后置阶段原三趟独立全树遍历（`_attach_host_refs` ref 填充 / `_traverse_functions` effects 收集 / 
- **渲染性能优化（props 引用级缓存，阶段3）** ✅ — reconciler `_set_props` **内容相等时保持 props 引用稳定**（值比较；不可比较对象兜底更新引用）——修复前每帧 h() 重建 props dict → `_measure_cache` 引用级命中（`mc[1] is fiber.props`）恒 miss（实测 0%），无变化帧/流式帧对全部 host fiber 重做 props 解析；修复后 props 值不变帧引用稳定 → 命中率提升，无变化帧 ~3.9ms → ~0.8ms（200 条消息 6600 行历史，4-5x）；`_measure_cache` 结构补 styled 长度快照（styled 原地修改检测，与 `_wrap_cache` BUG-35 同契约）——TEXT 分支缓存命中先校验 `len(styled)`，兼容 `test_wrap_cache_invalidated_on_styled_list_mutation` 测试契约
- **渲染错误修复（健壮性）** ✅ — OverflowError 捕获：`int(float('inf'))` 在布局/控件层 25+ 处不再崩溃（`_resolve_length`/`_resolve_height`/`_flex_grow`/`_flex_shrink`/`_resolve_padding`/border/margin/gap/flexBasis/`_abs_int`/百分比路径/各控件 width/height/limit/indent）；控件 items 不可迭代防御：ListView/SelectInput/MultiSelect/Menu 对 None/标量/字典 items 渲染安全（`_normalize_items` + ListView/Menu 防御回退空列表——Menu 修复前空 items 渲染期钳制越界抛 IndexError）；TextInput value 非 str 归一化；bytes 子级解码为文本（修复前 `str(b'x')` 渲染出 `b'x'` repr）；全组件模糊 500+ trial 零异常零超宽（含 inf/nan/畸形值/极端窄屏/增量漂移 8000 帧可见区合法）；渲染器模糊不变量测试 `tests/test_tui/ink/test_render_fuzz_invariants.py`（随机帧序列 + 迷你终端重放：屏幕内完整文档可见/高于屏幕末尾行可见/宽字符不丢失/resize 后重渲染，180+ 序列）与布局模糊不变量测试 `tests/test_tui/ink/test_layout_fuzz_invariants.py`（随机组件树 + 极端值：行宽不变量 + 不崩溃，120+ 树）固化
- **渲染错误修复（阶段3，BUG-74/75/76）** ✅ — BUG-74：committed-chat 前缀缓存键缺布局宽度 `box.w`——终端宽度变化（reflow 前/失败）时 id(lines)/行数/y 均未变 → 缓存错误命中 → 旧宽度超宽行直接进入帧（E-COMMITTED-OVERFLOW 防线被缓存绕过）；修复：缓存键补 `box.w`，宽度变化强制重建并重新检查 all_ok；BUG-75：WRITE_LINE/NOTIFICATION/ERROR 文本含 `\n` 时未按行拆分——换行符嵌进单条 AnsiLine，frame 行内嵌字面换行符渲染成多条终端行，破坏行级 diff/光标定位（与 `build_assistant_line` 拆行语义不一致）；修复：三处均按 `\n` 拆行（空段保留空行）；BUG-76：物理缓冲漂移时缩短/增长后残留行未清除——`_rewrite_drifted`/`_grow_drifted` 对 doc 无对应内容的物理行用 `old_line is not None` 判断清除，但物理行旧内容不在 prev doc（`old_idx >= prev_h`，残留自更早帧）时 `old_line=None` → 误判为空 → 缩短后旧行残留在可见区（如 18→15 行缩短后 'zbzbzb' 残留）；修复：`old_idx >= prev_h` 时保守清除；渲染器纯帧序列模糊 400 seeds × 150 帧零残留；真实 App 树 + MiniTerm 重放 90 seeds × 300+ 帧可见区合法性零错误（回归测试 `tests/test_tui/ink/test_render_bugfix_batch2.py` 6 例 + `test_renderer_drift_chaos.py::TestBug76ResidualClear` 固化）
- **富交互组件** ✅ — 在终端中嵌入可交互元素（选择列表、确认弹窗、进度条、开关、树、虚拟列表、焦点组），减少纯文本输出的信息密度（`src/tui/ink/widgets/` 已实现）
- **语法高亮增强** — 支持更多编程语言的代码块高亮，优化长代码段的折叠/展开机制
- **多面板布局** — 对话区/工具调用日志/系统状态分屏显示，便于调试与观察 Agent 行为
- **主题系统扩展** ✅ — 支持自定义配色方案，适配亮色/暗色终端环境（已内置 dark/light/high-contrast 三种主题）
- **动效与呼吸效果** ✅ — 标题栏✦/工具卡边框/状态栏分隔线/模型名/解析行 spinner/推理头/错误标记/补全弹窗/流式占位符/工具计数箭头/失败警示等 10+ 处时间基动效（time_glow 0.1s 桶缓存）；2026-08-05 新增 BEAUTY-18~24：user_select 弹窗标题/选中高亮/提示行/说明列呼吸（**已于 2026-08-05 静态化**——弹窗呼吸使弹窗行每帧随 time_glow 重写，Termux 等终端每帧刷新/错乱；现改静态色且不驱动动画循环，仅交互按键时重绘）、状态栏耗时/token/速度/CPU/MEM 呼吸、补全弹窗说明列/命令描述呼吸、工具 detail 呼吸、subagent 卡统计呼吸；2026-08-05 第二轮 BEAUTY-25~34：空状态欢迎行 ✦ 活跃期呼吸（空闲静态单例零重建）、工具卡标题图标运行中呼吸、思考块角色头 live spinner 化（💭→⠋⠙⠹…，关闭回退静态）、状态栏 thinking 阶段标签弱呼吸（…思考）、user_select 弹窗标题模式图标（单选 ▶ / 多选 ☑）、解析进度行 spinner 金色呼吸（178↔190）、标题栏版本号活跃期呼吸、live content 流式末尾指示 spinner、通知/子代理角色头 live 呼吸、subagent 组卡省略提示呼吸（渲染性能：Line.append ASCII 批量宽度快路径）

---

### 2. ✅ 🧠 Plan Agent 架构 — 已完成

独立的 **Plan Agent** 层已实现并投入使用。任何文件修改或新需求前，必须先通过 `subagent(type="plan")` 委派 plan SubAgent 生成结构化计划文件（`.chat/plan/`），主 Agent 读取后逐条执行，形成「规划 → 探底 → 推理 → 执行 → 审查 → 验证」六阶段流水线。详见上方 🔄 [Agent 工作流程](#agent-工作流程)。

---

### 3. ⚡ 更高的 Agent 并行度

从「串行 Agent 链」演进为「高并发 Agent 网格」，最大化利用 I/O 等待时间：

**短期目标（当前 → v3.0）**：

| 改进项 | 现状 | 目标 |
|--------|------|------|
| SubAgent 并发派发 | ✅ 同轮多次 `subagent`（ParallelExecutor 并行已实现） | 支持批量派发 + 动态扩缩容 Worker 池 |
| 文件读取并发 | ✅ 同轮多个 `read_file` 自动并行 | 增加读取优先级队列（关键路径先读） |
| 审查并行 | ✅ 多文件同轮并发 review（同轮并发 subagent 已实现） | 支持审查结果增量合并，减少重复审查 |
| 工具调用并行 | 单步工具串行执行 | 支持独立的工具调用 DAG（无依赖的工具并行执行） |

**中期目标（v3.0 → v4.0）**：

- **Agent 工作池** — 构建可复用的 Agent  Worker 池，按任务类型（map / review / edit / test）分类管理，减少每次派发的冷启动开销
- **流水线并行** — 前序 Agent 的输出流式送入后续 Agent，无需等待完整输出，边生成边消费（如 map 分析结果流式输入 review Agent）
- **资源感知调度** — 根据当前系统负载（CPU / 内存 / I/O）动态调整并发数，避免资源耗尽
- **跨对话并行** — 多个对话会话之间共享 Agent 工作池，全局协调并发上限

**长期目标（v4.0+）**：

- **分布式 Agent 执行** — 将 SubAgent 派发到远程计算节点执行，支持大规模并行代码分析和批量修改
- **自适应并行策略** — 基于历史任务执行时间自动学习并行度配置，为新任务推荐最优并发参数

---

## 技能系统（Skills）

参照 DeepSeek Harness 的 dsh-skill 设计实现的可复用指令技能系统。技能是一组可复用的任务专用指令（Markdown + YAML frontmatter），模型可在执行任务前按需加载。

### 存放位置（仅 `./.skills`）

技能只存放在项目的 `./.skills` 目录（自动定位到 git 根，子目录运行同样生效）：

```
<项目根>/
├── .skills/
│   ├── code-review/
│   │   └── SKILL.md          # 目录包技能（可携带相对资源）
│   ├── summarize.md          # 扁平技能
│   └── installed/            # GitHub 安装的技能（/skill install）
│       └── owner__repo/
│           └── <技能>/
└── .git/
```

技能文件格式（与 Claude Skills / DSH 相同）：

```markdown
---
name: code-review          # 必填，kebab-case
description: 代码审查指南   # 必填，一句话描述（模型目录用）
whenToUse: 用户要求审查代码时  # 可选，路由提示
disable-model-invocation: false  # 可选，禁止模型调用
user-invocable: true            # 可选，允许 /name 手势调用
metadata:                        # 可选，任意附加元数据
  author: someone
---
# 技能正文（Markdown 指令）
```

### 调用方式

1. **模型自动加载（所有 Agent 可用）** — 技能目录（名称 + 描述摘要）随系统提示词注入，位置在环境信息之后，**构建时只注入一次**（不随对话轮次重复注入）；主 Agent 与 map/review/plan/execute SubAgent 的系统提示词均会注入，每个 Agent 都能使用技能。模型判断任务匹配后调用 `skill` 工具加载完整指令（返回 `<skill_content>` 块）。技能变更后（`/skill install/update/remove/refresh`）系统提示词自动重建，技能章节随之更新。
2. **无条件自动加载（`skills.auto_load`）** — 配置的技能正文直接注入系统提示词（「已自动加载的技能」小节，`<skill_content>` 块），模型无需调用工具即可遵循。适合高频必用技能，注意正文常驻上下文：

```json
{
  "skills": {
    "enabled": true,
    "auto_load": ["pdf", "docx"],
    "catalog_description_max_length": 500
  }
}
```

3. **用户 `/name` 手势** — 消息中以词边界输入 `/code-review` 即直接加载该技能（仅 `user-invocable` 技能），正文以 `<skill_content>` user 消息注入会话。路径（`/usr/bin`）、分数（`5/8`）、URL 不会误匹配。
4. **`/skill` 命令** — 管理技能：`/skill list`（含 installed）/ `/skill info <名>` / `/skill refresh`（技能变更后自动重建系统提示词）。

### 从 GitHub 安装技能

```bash
/skill install owner/repo                 # 默认分支
/skill install owner/repo@main            # 指定分支/标签
/skill install https://github.com/a/b/tree/dev
/skill update owner/repo                  # 更新（沿用原 ref）
/skill remove owner/repo                  # 卸载（或 owner__repo / 技能名）
/skill list installed                     # 查看已安装
```

安装实现：通过 codeload 下载 tarball（httpx 流式，30MB 上限）→ 安全解压（拒绝路径穿越/符号链接/设备文件，60MB 解压上限）→ 识别技能根（`skills/` 目录 > 根目录 `SKILL.md` > 技能集合）→ 校验至少一个合法技能 → 原子替换到 `./.skills/installed/<owner>__<repo>/`，并记录 `.skill-source.json` 元数据（owner/repo/ref/commit/时间）。同名技能项目级（rank 100）优先于已安装（rank 200）。

> ⚠️ 技能内容按受信任本地内容处理（原样注入），仅从可信仓库安装。

### 优先级与配置

- 同名技能：项目 `.skills`（rank 100）> GitHub 安装（rank 200）> 运行时注册（rank 250）。
- 配置（`~/.chat_config/chatrc.json`）：

```json
{
  "skills": {
    "enabled": true,
    "catalog_description_max_length": 500
  }
}
```

### 实现结构（`src/skills/`）

| 模块 | 职责 |
|------|------|
| `models.py` | 数据结构、kebab-case 校验、调用策略（InvocationPolicy） |
| `frontmatter.py` | YAML frontmatter 解析（零依赖内置解析器，PyYAML 存在时优先） |
| `discovery.py` | 技能根扫描（目录包 / 扁平 Markdown / 单技能根） |
| `registry.py` | 注册表：多根合并、rank 裁决、mtime 缓存、运行时注册 |
| `render.py` | `<skill_content>` 规范渲染（工具结果与手势注入同形） |
| `prompt_section.py` | 系统提示词技能章节（环境信息之后注入一次，主 Agent 与 SubAgent 均注入） |
| `gestures.py` | `/name` 手势扫描与正文注入 |
| `github.py` | GitHub 安装/更新/卸载（spec 解析 + tarball 安全解压） |
| `src/tools/skill_tool.py` | `skill` 工具（模型加载入口，自动发现注册） |
| `src/core/commands/plugins/skill_plugin.py` | `/skill` 命令（变更后重建系统提示词） |
| `src/prompt_builder/builder.py` | `_build_prompt(include_skills=True)` 在环境信息后追加技能章节 |
