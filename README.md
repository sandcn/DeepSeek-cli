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
| `python chat.py --webui` | Web UI 模式（默认 0.0.0.0:8080） |
| `python chat.py webui --host 127.0.0.1 --port 3000` | 自定义 Web UI 地址端口 |
| `python chat.py session list` | 列出所有会话 |
| `python chat.py session delete abc123` | 删除会话 |
| `python chat.py session export abc123` | 导出会话 |
| `python chat.py --version` | 显示版本信息 |

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

## LICENSE

本项目基于 [LICENSE](LICENSE) 许可协议开源。