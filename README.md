# DeepSeek-cli

全异步、高可扩展的 AI 聊天服务后端，支持多模型适配、增量流式 Markdown 渲染、工具调用系统、上下文压缩和终端+Web 双界面。

---

## 快速开始

### 1. 安装依赖

项目使用 **Python ≥ 3.9**。

#### 方式一：一键安装（推荐）

```bash
# 安装全部核心依赖
pip install aiohttp httpx rich prompt-toolkit Pygments Jinja2 beautifulsoup4 chardet aiofiles

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
| `prompt-toolkit` | 终端交互式输入 | `pip install prompt-toolkit` |
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
    "max_retries": 3,
    "retry_base_sec": 1,
    "theme": "dark",
    "max_context_tokens": 60000,
    "summary_token_budget": 2000,
    "auto_force_compress_threshold": 60000,
    "tool_output_truncate": 500,
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

| Provider | 说明 |
|---|---|
| `deepseek` | DeepSeek 官方 API（默认） |
| `custom` | 自定义 OpenAI 兼容 API |

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
| `web_search` | ws | 网络 | ❌ | 搜索引擎搜索 + 网页全文抓取（百度/必应） |
| `user_select` | us | 交互 | ❌ | 向用户显示交互式选择界面（单选/多选） |
| `dispatch_agent` | da | Agent | ❌ | 并行派发子 Agent 执行独立任务 |

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

---

## Agent 工作流程

本项目的核心是 **Main-Sub Agent 架构**，通过 `dispatch_agent` 委派任务给不同类型的子 Agent。

```
┌─────────────────────────────────────────────────────────────┐
│                         Main Agent                          │
│                   主控 Agent，负责任务调度                     │
│                                                             │
│  ① 列计划 ─→ ② 探底分析 ─→ ③ 修改执行 ─→ ④ 审查 ─→ ⑤ 验证  │
└───────┬────────────┬────────────┬────────────┬──────────────┘
        │            │            │            │
        │ dispatch   │ dispatch   │ dispatch   │
        ▼            ▼            ▼            ▼
┌────────────┐ ┌────────────┐ ┌────────────┐ ┌──────────────┐
│ map        │ │ ordinary   │ │ review     │ │ ordinary     │
│ SubAgent   │ │ SubAgent   │ │ SubAgent   │ │ SubAgent     │
│            │ │            │ │            │ │              │
│ 只读分析型  │ │ 通用型     │ │ 代码审查型  │ │ 测试与运行   │
│ │           │ │            │ │            │ │              │
│ • 项目探底  │ │ • 读文件   │ │ • P0-P3    │ │ • 执行测试   │
│ • 模块地图  │ │ • 写文件   │ │   分级审查  │ │ • 运行验证   │
│ • 调用链    │ │ • 修改代码 │ │ • 循环审查  │ │              │
│ • 引用关系  │ │ • 创建文件 │ │ • 阻断策略  │ │              │
│            │ │ • 其他任务  │ │            │ │              │
└────────────┘ └────────────┘ └────────────┘ └──────────────┘
```

### 工作流说明

```
1. 列计划 ──→  做事前强制列计划，评估影响范围和风险
     │
2. 探底 ──→  委派 map SubAgent 获取模块地图 + 调用链分析
     │         （只读分析，不修改代码）
     │
3. 修改 ──→  基于探底结果执行代码修改
     │         多个独立目标可并发派发 ordinary SubAgent
     │
4. 审查 ──→  委派 review SubAgent 逐文件审查
     │         P0/P1/P2 阻断修复，P3 纳入记录
     │         最多三轮循环审查
     │
5. 验证 ──→  语法检查 → 新增测试 → 运行测试 → 运行验证
     │
6. 完成 ──→  输出变更总结，更新跨对话记忆
```

### SubAgent 类型

| 类型 | 能力 | 用途 |
|---|---|---|
| **map** | 只读（read_file/search/find/ls） | 项目探底、模块地图、调用链追踪、引用关系分析 |
| **review** | 只读 + web_search | Code Review、P0-P3 分级审查、跨文件一致性验证 |
| **ordinary** | 全工具（不含 user_select/dispatch_agent） | 读/写/改代码、执行测试、通用任务 |

### 并发调度策略

多个独立分析/审查任务同时触发时，同轮并发派发多个 SubAgent（如同时分析多个模块、同时审查多个文件），互不阻塞，缩短总执行时间。

---

## 目录结构

```
├── src/                 # 核心源码
│   ├── api/             # API 适配层
│   ├── config/          # 配置加载与校验
│   ├── core/            # 核心业务逻辑
│   ├── notifications/   # 通知系统
│   ├── observability/   # 可观测性
│   ├── prompt_builder/  # prompt 构建
│   ├── tools/           # 工具调用系统
│   ├── ui/              # 终端 UI
│   └── webui/           # Web 界面
├── tests/               # 测试
├── prompts/             # 系统提词
├── docs/                # 文档
├── chat.py              # 入口
└── pyproject.toml       # 项目配置与依赖
```

---

## 后续计划

### 1. 🎨 增加并优化 TUI 渲染

重构终端用户界面渲染层，提升视觉体验与交互流畅度：

- **流式渲染性能优化** — 降低增量 Markdown 渲染延迟，消除大 Token 输出时的界面卡顿
- **富交互组件** — 在终端中嵌入可交互元素（选择列表、确认弹窗、进度条），减少纯文本输出的信息密度
- **语法高亮增强** — 支持更多编程语言的代码块高亮，优化长代码段的折叠/展开机制
- **多面板布局** — 对话区/工具调用日志/系统状态分屏显示，便于调试与观察 Agent 行为
- **主题系统扩展** — 支持自定义配色方案，适配亮色/暗色终端环境
- **Web UI 同步增强** — 终端与 Web 界面的渲染逻辑复用，保证两种模式下显示一致性

---

### 2. 🧠 Plan Agent 架构

引入独立的 **Plan Agent** 层，将任务规划从执行中解耦，形成「规划 → 执行 → 审查」三阶段流水线：

```
┌──────────────────────────────────────────────────────────┐
│                     Orchestrator                         │
│              全局调度器，维护任务 DAG                      │
└──────────┬──────────────┬──────────────┬─────────────────┘
           │              │              │
           ▼              ▼              ▼
┌─────────────────┐ ┌──────────────┐ ┌──────────────────┐
│   Plan Agent    │ │  Exec Agent  │ │  Review Agent    │
│                 │ │              │ │                  │
│ • 任务拆解      │ │ • 探底模块   │ │ • 代码审查       │
│ • 依赖分析      │ │ • 修改代码   │ │ • 测试验证       │
│ • 资源估算      │ │ • 创建文件   │ │ • 质量门禁       │
│ • 风险识别      │ │ • 运行测试   │ │                  │
│ • 调度决策      │ │              │ │                  │
│ • 动态重规划    │ │              │ │                  │
└─────────────────┘ └──────────────┘ └──────────────────┘
```

**核心能力**：

| 能力 | 说明 |
|------|------|
| **任务拆解** | 将复杂需求自动拆分为可独立执行的子任务，输出任务 DAG |
| **依赖分析** | 识别子任务间的依赖关系，确定最优执行顺序（拓扑排序） |
| **资源估算** | 评估每个子任务的上下文消耗、执行时间和风险等级 |
| **动态重规划** | 根据执行结果（成功/失败/新信息）动态调整剩余任务计划 |
| **并行调度** | 识别无依赖子任务，自动决策并行度与资源分配 |
| **失败恢复** | 子任务失败时尝试替代方案或缩小范围，避免全盘回退 |

**与当前架构的演进关系**：当前 Main Agent 自行完成「列计划」步骤；Plan Agent 将规划能力独立为可调用的专用 Agent，支持嵌套规划（子任务内可递归派生子 Plan Agent），适用于多轮复杂任务场景。

---

### 3. ⚡ 更高的 Agent 并行度

从「串行 Agent 链」演进为「高并发 Agent 网格」，最大化利用 I/O 等待时间：

**短期目标（当前 → v3.0）**：

| 改进项 | 现状 | 目标 |
|--------|------|------|
| SubAgent 并发派发 | 同轮多次 `dispatch_agent` | 支持批量派发 + 动态扩缩容 Worker 池 |
| 文件读取并发 | 同轮多个 `read_file` 自动并行 | 增加读取优先级队列（关键路径先读） |
| 审查并行 | 多文件同轮并发 review | 支持审查结果增量合并，减少重复审查 |
| 工具调用并行 | 单步工具串行执行 | 支持独立的工具调用 DAG（无依赖的工具并行执行） |

**中期目标（v3.0 → v4.0）**：

- **Agent 工作池** — 构建可复用的 Agent  Worker 池，按任务类型（map / review / edit / test）分类管理，减少每次派发的冷启动开销
- **流水线并行** — 前序 Agent 的输出流式送入后续 Agent，无需等待完整输出，边生成边消费（如 map 分析结果流式输入 review Agent）
- **资源感知调度** — 根据当前系统负载（CPU / 内存 / I/O）动态调整并发数，避免资源耗尽
- **跨对话并行** — 多个对话会话之间共享 Agent 工作池，全局协调并发上限

**长期目标（v4.0+）**：

- **分布式 Agent 执行** — 将 SubAgent 派发到远程计算节点执行，支持大规模并行代码分析和批量修改
- **自适应并行策略** — 基于历史任务执行时间自动学习并行度配置，为新任务推荐最优并发参数
