# DeepSeek-cli

全异步、高可扩展的 AI 聊天服务后端，支持多模型适配、增量流式 Markdown 渲染、工具调用系统、上下文压缩和终端+Web 双界面。

---

## 快速开始

### 1. 安装依赖

项目使用 **Python ≥ 3.9**，通过 `pyproject.toml` 管理依赖。使用 `pip` 安装：

```bash
# 安装核心依赖
pip install .

# 安装开发依赖（测试/代码检查等）
pip install ".[dev]"
```

**核心依赖列表**（自动安装）：

| 包 | 用途 |
|---|---|
| `aiohttp>=3.9` | 异步 HTTP 服务器/客户端 |
| `httpx>=0.27` | HTTP 请求库 |
| `rich>=10` | 终端富文本输出 |
| `prompt-toolkit>=3.0` | 终端交互式输入 |
| `Pygments>=2.16` | 代码语法高亮 |
| `Jinja2>=3.1` | 模板渲染 |
| `beautifulsoup4>=4.12` | HTML 解析 |
| `chardet>=3.0` | 字符编码检测 |
| `aiofiles>=23` | 异步文件操作 |

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

```bash
python chat.py
```

默认启动终端交互界面（CLI）。启动后自动加载配置，连接指定 API 服务。

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