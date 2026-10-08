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

#### 方式一：配置文件 + 模型选择器（推荐）

**LLM 访问参数（API 密钥 / 接口地址 / 模型名 / 提供商）的唯一来源是模型档案**
（`model_profiles`）——首次运行未配置档案时会自动打开模型选择器引导配置
（`/models` 可随时打开；新增只需三步：① 选提供商 → ② 填模型名 → ③ 填 API 密钥）。

创建配置文件 `~/.chat_config/chatrc.json`：

```json
{
    "model_profiles": [
        {
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "api_key": "sk-你的API密钥",
            "base_url": "https://api.deepseek.com/v1/chat/completions",
            "name": "DeepSeek Flash"
        }
    ],
    "active_model_profile": 0,
    "reasoning_effort": "max",
    "temperature": 0.2,
    "max_context_chars": 3000000,
    "max_output_chars": 3000,
    "max_retries": 10,
    "retry_base_sec": 30,
    "max_session_messages": 0,
    "keep_recent_messages": 0,
    "theme": "dark",
    "max_context_tokens": 1000000,
    "summary_token_budget": 2000,
    "auto_force_compress_threshold": 400000,
    "enable_notifications": true,
    "notify_on_chat_completion": true,
    "image_upload_optimize": true,
    "image_upload_keep_recent": 4,
    "image_upload_max_dimension": 1568,
    "image_upload_quality": 80,
    "mcp_servers": [],
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

- `model_profiles`：模型档案列表（每个档案含 `provider` / `model` / `api_key` /
  `base_url` / `name`；`base_url` 留空则用提供商默认地址）。
- `active_model_profile`：当前生效档案下标（由模型选择器写入，`-1` = 未选择）。

配置文件位于 `~/.chat_config/chatrc.json`，首次运行时自动创建（使用默认值）。

#### 方式二：环境变量

仅流式输出节奏类参数支持环境变量覆盖（**LLM 访问参数已不再支持环境变量**——
API 密钥 / 接口地址 / 模型名 / 提供商只能通过模型档案配置）：

| 环境变量 | 说明 | 示例 |
|---|---|---|
| `CHAT_STAGGER_MIN_DELAY` | 流式输出最小延迟 | `export CHAT_STAGGER_MIN_DELAY="0.1"` |
| `CHAT_STAGGER_MAX_DELAY` | 流式输出最大延迟 | `export CHAT_STAGGER_MAX_DELAY="0.5"` |

> 已移除：`CHAT_API_KEY` / `CHAT_BASE_URL` / `CHAT_MODEL` / `CHAT_LOW_MODEL`
> 以及 RC 旧键 `api_key` / `base_url` / `model` / `provider` / `low_model` /
> `models`（加载配置时自动清理）。

#### 支持的多模型 Provider

| Provider | 适配器 | 说明 |
|---|---|---|
| `deepseek` | `DeepSeekAdapter` | DeepSeek 官方 API（默认），支持 deepseek-flash（V4.1 Flash，原生多模态视觉）、v4-pro、v4-flash / v4-flash-vision-exp（旧名，已路由到 V4.1 Flash）、reasoner、chat、coder 系列 |
| `custom` | `OpenAICompatAdapter` | 任意 OpenAI 兼容 API（OpenAI / 通义千问 / 本地 Ollama 的 OpenAI 兼容端点等），自动检测 reasoner 模型 |
| `anthropic` | `AnthropicAdapter` | Anthropic Claude 系列模型（API 格式自动转换） |
| `glm` | `OpenAICompatAdapter` | 智谱 GLM（open.bigmodel.cn） |
| `mimo` | `OpenAICompatAdapter` | 小米 MiMo（token-plan-cn） |

> 模型名以 `ollama` 开头时按 `OllamaAdapter` 路由（本地 Ollama 部署，默认
> `localhost:11434`）；该路由由模型名决定，不需要 `provider: ollama`。

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
python chat.py -m deepseek-v4-pro      # 按模型名匹配已有「模型档案」并设为当前
python chat.py --model deepseek-v4-pro
```

LLM 访问参数唯一来源 = 模型档案，因此 `-m` / `--model` 只能**选择已有档案**
（按档案的 `model` 或 `name` 匹配）——档案不存在时会列出可用档案并提示先运行
`/models` 新增。

#### 多模态视觉（deepseek-flash）

`deepseek-flash`（DeepSeek V4.1 Flash）是 DeepSeek 最新一代模型，原生支持
多模态视觉理解，图片按 token 计费。旧模型名 `deepseek-v4-flash` 与
`deepseek-v4-flash-vision-exp` 已下线，请求统一路由到 V4.1 Flash（按相同
单价计费，同样具备视觉能力）。接入方式：

```bash
# 先确保模型档案中有该模型（/models 界面新增，或 config set model_profiles），
# 再按名选择：
python chat.py -m deepseek-flash
```

该模型支持两种图片输入方式（图片仅支持出现在用户消息中）：

1. **用户消息直接传图**：在输入中携带本地图片路径、`![描述](图片路径或URL)` 或
   裸 http(s) 图片 URL，CLI 自动转换为图片 content blocks（本地图片自动
   base64 内联，URL 原样传递）：

   ```
   分析 /path/to/screenshot.png 里的报错信息
   看下 ![架构图](https://example.com/diagram.png)
   ```

   > **拖动文件输入路径**：把文件从文件管理器拖入终端窗口（输入框），终端会把
   > 路径文本注入为「粘贴」——CLI 自动识别并规范化为干净路径（去引号、还原
   > 反斜杠转义、解析 `file://` URI、Cygwin/MSYS 下把 Windows 路径转为 POSIX
   > 路径），多文件每行一个、含空格路径用双引号包裹；非路径文本（自然语言、
   > 代码）原样输入不受影响。图片路径因此同样可直接拖入传图。
   > 关闭：`python chat.py config set tui_drop_path_normalize false`。

2. **read_image 工具**：AI 代理可主动调用 `read_image` 工具读取本地图像
   （支持分块/灰度/旋转/翻转/缩放操作），多模态模型直接看到 base64 图片
   （图片按原始尺寸返回，不做自动缩放）。

> **上传前图片瘦身（默认开启）**：图片按原始尺寸进入会话历史，而每轮 API
> 请求都会重传**整个**历史（含全部历史图片的 base64），图片一多请求体便随
> 图片数量线性膨胀（实测 6 张 1080p 截图 ≈ 47MB/请求），表现为"图片多了每次
> 都很卡"。客户端在**发送副本**上自动做三件事：
> ① 折叠——仅保留最近 `image_upload_keep_recent` 张图片，更早的图片块替换为
> 文本占位；② 压缩——超过 `image_upload_max_dimension` 长边或体积阈值的图片
> 降采样并重编码为 JPEG；③ 编码缓存——同一图片不重复解码/编码。
> 该优化**不改动**会话存档、TUI 显示与 `read_image` 的「原始尺寸返回」契约。
>
> ```bash
> python chat.py config set image_upload_optimize false        # 关闭优化
> python chat.py config set image_upload_keep_recent 8         # 多保留几张（0=不折叠）
> python chat.py config set image_upload_max_dimension 1024    # 上传长边上限
> python chat.py config set image_upload_quality 80            # JPEG 质量 1~100
> ```
>
> **图片 token 统计**：图片按 token 计费，`/cost` 的输入 token 来自 API 真实
> usage（已含图片 token）；上下文使用率百分比（模式行 `main · N%`）与压缩
> 判断另按图像尺寸估算视觉 token（`ceil(w/patch) * ceil(h/patch)`，默认
> `patch=28`；长边按 `image_upload_max_dimension` 降采样、仅计
> `image_upload_keep_recent` 张），可用 `multimodal_image_token_patch`
> （分块边长）与 `multimodal_image_token_default`（无法读尺寸时每图占用）覆盖。

非多模态模型下，用户消息中的图片引用保持纯文本原样传递（模型不可见图片）。
如有多模态模型未被内置模式识别，可通过配置扩展：

```bash
python chat.py config set multimodal_models '["my-vision-model"]'
```

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
| `python chat.py -m deepseek-v4-pro` | 选择模型（按名匹配已有模型档案） |
| `python chat.py -v` | INFO 级别日志 |
| `python chat.py -vv` | DEBUG 级别日志 |
| `python chat.py session list` | 列出所有会话 |
| `python chat.py session delete abc123` | 删除会话 |
| `python chat.py session export abc123` | 导出会话 |
| `python chat.py config` | 显示全部配置（含敏感值脱敏） |
| `python chat.py config get model` | 查询单个配置 |
| `python chat.py config set model deepseek-v4-pro` | 设置配置并持久化 |
| `python chat.py config reset model` | 重置配置为默认值 |
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
| `Ctrl+N` | 循环切换对话模型（**只在模型档案中选择**：`/models` 新增的模型档案；未配置时提示先用 `/models` 新增） |
| `Ctrl+P` / `↑` | 浏览输入历史（上一条） |
| `↓` | 浏览输入历史（下一条） |
| `Ctrl+R` | 反向历史搜索（配置门控；默认重试上一轮） |
| `Ctrl+T` | 循环切换配色主题（dark/light/high-contrast/nord/dracula/gruvbox） |
| `Ctrl+L` | 清屏 |
| `Ctrl+D` | 退出程序（输入为空时） |
| `Ctrl+Z` | 撤销输入编辑（连续同类编辑合并为一步） |
| `Ctrl+Y` | 重做输入编辑 |
| `Ctrl+B` | 主 Agent 运行模式循环切换（空模式 → 简单模式 → 标准模式 → 空模式） |
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
| `/compact` | — | 压缩上下文历史：把最旧的平衡范围替换为一条结构化检查点（保留近期尾部），报告压缩条数与节省 token；压缩过程在模式行显示 `compact · N` |
| `/context [256k\|1m\|tokens]` | — | 显示当前上下文窗口、使用率与压缩预算（阈值/保留尾部/剪枝）；带参数写入模型上下文窗口，后续请求与自动压缩按新容量实时计算 |
| `/editmsg` | — | 编辑当前会话消息（同 Ctrl+O） |
| `/undo` | — | 撤销上一轮对话 |
| `/retry` | `/r` | 重新生成上一条回答 |
| `/edit` | — | 编辑并重新发送上一条输入 |
| `/model` | — | 切换模型（无参数时打开**模型选择器**，**只在模型档案中选择**——RC 顶层 `models` 字段已移除，不列内置 provider 模型；亦支持序号/名称直接切换） |
| `/models` | — | **模型选择器**（全屏界面：↑↓/jk 选择 · Enter 应用 · `a` 新增 · `c` 复制 · `e` 编辑 · `d` 删除 · `r` 刷新模型档案）。**新增只需三步**：① 选提供商（接口地址自动填好）→ ② 填模型名 → ③ 填 API 密钥（显示名留空则用模型名）；列表行同时显示模型名与接口主机，选中详情行展示完整模型名/提供商/接口地址/脱敏密钥；字段输入支持光标编辑（←/→ · Home/End · Delete · Ctrl+A/Ctrl+E · Ctrl+U）；`/` 搜索 · `n`/`N`/`p` 匹配 · `f` 过滤 · `?` 帮助（表单内亦可） · `y` 复制信息 · Esc 关闭 |
| `/reasoning [等级]` | — | 调整推理等级（low / medium / high / max，无参数时显示当前值） |
| `/temperature [数值]` | — | 调整大模型温度（0.0 ~ 2.0，无参数时显示当前值，保存到配置） |
| `/cost` | — | 查看 token 用量和费用 |
| `/config` | — | 显示/编辑程序配置（无参数打开**独立配置界面**：↑↓/jk 选择、Enter 编辑、Esc 关闭；枚举/布尔/模型走**选择界面**、list/dict 子 JSON 走**递归结构化编辑界面**（Enter 逐层下钻嵌套 · 标量编辑 · a 追加 · d 删除 · Esc 逐级返回、顶层保存）、数值/字符串走输入界面；亦支持 `show`/`list`/`get <键>`/`set <键> <值>`/`reset <键>`） |
| `/load <ID>` | — | 加载保存的对话 |
| `/sessions` | — | 列出所有保存的对话 |
| `/export [路径]` | — | 导出当前对话为 Markdown（含 SubAgent 聊天信息） |
| `/theme <名称>` | — | 切换配色主题（dark / light / high-contrast / nord / dracula / gruvbox） |
| `/changes` | — | 显示文件沙盒中被修改文件的差异（可加文件名过滤） |
| `exit` | — | 退出程序 |

> **SubAgent 聊天记录持久化**：每个 SubAgent 的完整内部对话（system 提示词 / 任务指令 /
> 助手回复 / 工具调用与结果）会在其运行结束时记录到父 Agent，并随会话自动保存到
> `.chat/msg_list/<id>.json` 的 `subagents` 字段；`/load` 加载会话时同步恢复，
> `/export` 导出 markdown 时一并包含。

---

## 工具系统（Tool System）

AI 代理在对话中可调用以下工具完成各类操作。共 **19 个内置工具**，涵盖文件操作、代码搜索、网络请求、用户交互等能力。

> 除内置工具外，还可通过 **MCP（Model Context Protocol）** 接入外部服务器提供的工具，
> 注册后与内置工具同构调用（详见下方 [MCP 外部工具接入](#mcp-外部工具接入model-context-protocol)）。

### 工具列表

| 工具名 | 缩写 | 分类 | 并行安全 | 功能说明 |
|--------|------|------|---------|---------|
| `read_file` | rf | IO | ✅ | 读取文件内容，支持指定行号范围、自动编码检测，可显示行号（默认关闭） |
| `write_file` | wf | IO | ✅ | 覆盖写入文件，自动创建父目录，原子写入 |
| `update_file` | uf | IO | ❌ | 精确替换文件中的文本（old_string → new_string），支持 use_regex 正则替换 |
| `search` | sr | 搜索 | ✅ | 在项目源码中搜索正则表达式，自动排除非源码目录 |
| `find` | fn | 搜索 | ✅ | 按通配符模式查找文件和目录，支持深度控制 |
| `ls` | ls | IO | ✅ | 列出目录内容，支持详细格式和隐藏文件显示 |
| `bash` | bs | 执行 | ❌ | 执行 shell 命令（安全沙盒保护，禁止替代专用工具） |
| `bash_opt` | bo | 执行 | ❌ | 按 task_id 操作后台 bash 任务：read（读取当前已产生的全部输出并清空缓冲，立即返回）/ wait（等待完成取输出）/ kill（杀死整个进程树：进程组 + 全部递归后代；杀完后校验进程是否真正退出、未死自动补杀，最多 3 轮尝试，残留进程在结果中报告）/ stdin（发送文本输入）/ keys（发送光标键盘消息，自动路由：目标进程有 GUI 窗口时作为窗口级键盘消息注入该窗口，否则写入终端 PTY/stdin，跨平台 ANSI/VT100；支持 `ctrl+c` 等修饰键组合、`ctrl_c`/`ctrl-c` 紧凑写法、`esc`/`del`/`pageup`/`return` 等别名、单个字符与 f1-f20）/ screenshot（把该命令进程树的窗口截图存为 PNG，需 path；可选 crop 只截取窗口内的像素区域，格式 `x,y,width,height`；可选 window 选择目标窗口、grid 叠加坐标参考线（省略=不叠加，0=自动步长）；命中已最小化/不可见的窗口时直接报错并提示先 restore，不产出占位小图）/ windows（列出该进程树的全部窗口及句柄/标题/几何/Z 序）/ window（控制窗口：activate/maximize/minimize/restore/close/move/resize/fit/always_on_top/not_on_top/get_geometry/save_geometry/restore_geometry）/ elements（列出窗口内控件：名称/类型/类名/矩形/可用状态，含窗口内坐标，可直接用于 click；element 可作过滤子串，支持 `'#N'`/`'text:子串'`/`'class:子串'`/`'type:edit'`）/ wait_window（等待窗口出现，按 window 选择器 + timeout 秒）/ clipboard（读写系统剪贴板：`clipboard_action=set/get/clear/append`）/ sequence（一次调用按序执行多个动作：`actions` 数组，步骤可为输入动作或 wait/screenshot/window，`on_error` 决定遇错停止或继续）/ move / click（左中右键、双击，需 button/count）/ drag（拖动，需 to_x/to_y，可选 from_x/from_y/duration/steps）/ scroll（滚轮，direction+amount）/ key（窗口级按键，如 `ctrl+s`，与 keys 共用键名规则；`phase` 可选 press/down/up 分别发送按下与弹起）/ type（向窗口逐字符输入文本，或用 `via='clipboard'` 走剪贴板粘贴、`paste_key` 指定粘贴键、`restore_clipboard=false` 保留剪贴板内容（恢复前会留出目标程序读取剪贴板的时间）；坐标/文本均可按控件名定位——输入 op 传 `element='确定'` 即用控件中心，type/key 会先点击该控件聚焦）；坐标以窗口截图左上角为原点（与 screenshot 产物一致），除像素整数外也支持 `'center'`/`'50%'`/`'center+20'`/`'left+20'`/`'bottom-30'` 等语义坐标；`method` 可选 auto/sendinput/message（仅 Windows 生效）；`wait_for='change'/'stable'` 可等界面变化或稳定（stable 会忽略光标闪烁级别的微小噪声，并在 `ignored_change` 说明），`diff=true` 回传注入前后截图差异 |
| `cp` | cp | IO | ✅ | 复制文件或目录，保留元数据，支持沙盒撤回 |
| `mv` | mv | IO | ✅ | 移动文件或目录，支持跨文件系统 |
| `rm` | rm | IO | ❌ | 删除文件或目录（删除前自动备份到沙盒） |
| `mkdir` | mk | IO | ✅ | 创建目录，支持递归创建父目录 |
| `read_image` | ri | IO | ✅ | 读取图像文件内容，支持分块读取与图像操作（灰度/旋转/翻转/缩放）；图片按原始尺寸返回给模型（不做自动缩放）；多模态 base64 图片（多模态模型如 deepseek-flash 直接看到图片）。发送前客户端会自动瘦身（折叠旧图+压缩大图+编码缓存），缓解多图场景每轮重传导致的请求卡顿 |
| `web_search` | ws | 网络 | ❌ | DeepSeek 官方原生联网搜索（Anthropic 兼容 Messages API + web_search_20250305），返回来源列表（标题/URL/摘要） |
| `web_fetch` | — | 网络 | ✅ | 获取指定 URL 的网页全文（自动提取正文，SSRF 防护，仅 http/https） |
| `user_select` | us | 交互 | ❌ | 向用户显示交互式选择界面（单选/多选/超时回退/非交互回退，选项可带说明，TUI 中高亮选项时说明显示在右侧；支持并发提问——多个问题可同一轮同时弹出、以 tab 形式一起回答） |
| `subagent` | sa | Agent | ❌ | 并行派发子 Agent 执行独立任务（支持类型：map/review/plan/execute）；直接后台执行，立即返回 `{"task_id": "sa-xxx"}` JSON，完成后结果自动插入对话（或由 subagent_opt 管理）。后台 subagent 仅主 Agent 可派发 |
| `subagent_opt` | so | Agent | ❌ | 按 task_id 操作后台 subagent 任务（subagent 直接后台启动）：read（读取当前状态与已产生的结果，立即返回）/ wait（等待完成取结果，timeout 秒，默认 300/0 无限）/ kill（取消后台 subagent 任务）/ wait_all（等待**所有**后台 subagent 任务完成，返回每个任务结果的 JSON 数组，无需 task_id）。仅主 Agent 可用 |
| `skill` | sk | 技能 | ✅ | 加载技能（skill）的完整指令（技能目录随系统提示词注入，任务与技能匹配或点名技能时调用） |

### 工具分类

| 分类 | 工具 | 说明 |
|------|------|------|
| **文件 IO** | read_file, write_file, update_file, ls, cp, mv, rm, mkdir, read_image | 读写文件、目录操作、文件管理、图像读取 |
| **代码搜索** | search, find | 正则搜索源码、通配符查找文件 |
| **命令执行** | bash, bash_opt | 安全沙盒中执行 shell 命令；按 task_id 操作后台 bash 任务（bash 后台任务注册在 bash 专用表 `_background_tasks`），含读取/等待/终止/终端输入、窗口截图（screenshot / windows / window，含置顶与几何记忆）、控件清单（elements）、等待窗口（wait_window）、剪贴板（clipboard）、动作序列（sequence）与 GUI 窗口输入注入（move/click/drag/scroll/key/type，支持按控件名定位、语义坐标、界面变化等待与前后截图比对） |
| **网络访问** | web_search, web_fetch | 网页搜索（DeepSeek 官方原生搜索）与网页全文获取 |
| **用户交互** | user_select | 交互式选择弹窗（单选/多选/超时回退；支持并发提问，多问题 tab 一起显示） |
| **Agent 调度** | subagent, subagent_opt | 并发派发原子 Agent 执行独立任务；按 task_id 操作后台 subagent 任务（subagent 后台任务注册在独立表 `_subagent_tasks`，与 bash 后台任务分表隔离） |
| **技能** | skill | 加载可用技能（skill）的完整指令 |

### 工具设计原则

- **纯异步** — 所有工具均基于 `asyncio`，不阻塞事件循环
- **沙盒安全** — 文件操作自动备份，支持撤回（undo）
- **元数据系统** — 每工具声明并行安全、网络依赖、超时估计等元数据，供调度层优化
- **双端适配** — 同时支持终端（`display()`）渲染路径

### 工具权限系统（v2.2.0+）

不同 SubAgent 类型对工具有不同的访问权限，通过 `Func.can_use()` 统一检查：

```python
@classmethod
def can_use(cls, tool_name: str, agent_type: str = "execute", path: str | None = None) -> tuple[bool, str | None]:
    """检查指定类型的 agent 能否使用某工具。"""
```

- **agent_type 注入** — SubAgent 在 `_handle_tool_calls()` 中自动注入 `func.agent_type = self.agent_type`
- **排除规则** — 定义在 `src/core/subagent.py` 的 `_TOOL_EXCLUSION_MAP`（详见下方 SubAgent 类型表）
- **路径白名单** — `FileToolBase._validate_path_and_size()` 对 plan Agent 实施路径限制，仅允许写入 `.chat/plan/` 目录，防止误写项目源码

---

## MCP 外部工具接入（Model Context Protocol）

除 19 个内置工具外，本 CLI 还支持接入**外部 MCP 服务器**（[Model Context Protocol](https://modelcontextprotocol.io/) 2025-06-18），
把第三方工具（文件系统、数据库、浏览器、自定义服务……）无缝变成模型可调用的工具。

### 配置

在 `~/.chat_config/chatrc.json` 顶层添加 `mcp_servers`（列表，默认 `[]`）：

```json
{
    "mcp_servers": [
        {
            "name": "filesystem",
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
            "agents": ["execute"],
            "timeout": 30
        },
        {
            "name": "remote",
            "transport": "http",
            "url": "https://example.com/mcp",
            "headers": {"Authorization": "Bearer xxx"}
        }
    ]
}
```

也可用 `/config` 界面或 CLI 编辑：

```bash
python chat.py config set mcp_servers '[{"name":"fs","transport":"stdio","command":"npx","args":["-y","@modelcontextprotocol/server-filesystem","/tmp"]}]'
```

### 字段说明

| 字段 | 适用 | 默认 | 说明 |
|---|---|---|---|
| `name` | 必填 | — | 服务器唯一名（工具名前缀 + 状态显示） |
| `transport` | 可选 | `stdio` | `stdio` / `http`（Streamable HTTP）/ `sse`（旧式 HTTP+SSE） |
| `command` | stdio 必填 | — | 可执行文件（如 `npx` / `uvx` / `python`） |
| `args` | stdio 可选 | `[]` | 命令行参数 |
| `env` | stdio 可选 | `{}` | 追加/覆盖子进程环境变量 |
| `cwd` | stdio 可选 | 当前目录 | 子进程工作目录 |
| `url` | http/sse 必填 | — | MCP 端点（仅 http/https） |
| `headers` | http/sse 可选 | `{}` | 附加请求头（如 `Authorization`） |
| `enabled` | 可选 | `true` | 是否启用（`false` 时跳过连接） |
| `agents` | 可选 | `["execute"]` | 允许使用该服务器工具的 SubAgent 类型（`map`/`review`/`plan`/`execute`） |
| `timeout` | 可选 | `30` | 单次请求超时（秒，上限 600） |
| `parallel_safe` | 可选 | `false` | 是否声明并行安全（影响工具 DAG 调度） |
| `inherit_env` | stdio 可选 | `false` | 是否把当前进程**全部**环境变量继承给子进程；默认只透传基础变量白名单（PATH/HOME/temp/LANG 等）+ `env`，避免凭据类环境变量外泄给第三方 server |
| `description` | 可选 | `""` | 人类可读说明（仅展示） |

### 行为

- **启动连接** — `chat.py` 启动时（交互 / 单次 / clawbot 模式统一入口）连接全部启用的服务器，
  完成 `initialize` 握手 → `tools/list` 发现工具 → 以 `mcp__<server>__<tool>` 名字注册进工具注册表；
  单个服务器连接失败只记 WARNING 并跳过，**不阻断应用启动**。
- **调用** — 模型调用 MCP 工具的方式与内置工具完全一致（工具卡、轨迹、token 统计、审计日志、
  沙盒记录等链路自动生效）；结果文本直接回传，图片结果在多模态模型下自动转成
  `image_url` content blocks（非多模态模型只回占位文本）。
- **权限** — MCP 工具默认**仅 `execute` 型 Agent 可用**（主 Agent + execute SubAgent）；
  `map` / `review` / `plan` 只读型 Agent 默认被排除（避免只读审查链路写入外部系统），
  可用 `agents` 字段按服务器显式放开（如 `["execute", "review"]`）。
- **提示词** — 已连接的服务器与工具清单会注入系统提示词（`## MCP 外部工具` 章节，无 MCP 配置时不注入）；
  该章节按目标 agent 类型过滤，只读型 SubAgent 不会看到自己被排除的 MCP 工具。
- **关闭** — 进程退出时关闭全部连接（terminate stdio 子进程 / DELETE HTTP 会话）、
  注销动态工具与权限策略。
- **零开销** — 未配置 `mcp_servers` 时不建立任何连接、不注册任何工具、不导入 mcp 子系统。

### 实现结构（`src/mcp/`）

| 模块 | 职责 |
|------|------|
| `protocol.py` | JSON-RPC 报文构造/解析、协议版本协商、方法名与头部常量 |
| `config.py` | `mcp_servers` 解析与校验（字段清洗、非法条目跳过、同名去重） |
| `transport.py` | 传输层：`StdioTransport`（子进程）/ `HttpTransport`（Streamable HTTP）/ `SseTransport`（旧式 HTTP+SSE） |
| `client.py` | 单服务器客户端：`initialize` 握手 + `tools/list`（cursor 分页）+ `tools/call`，结果归一化 |
| `tool.py` | MCP 工具 → 动态 `Func` 子类（名称清洗限长、schema 归一化、图片转 content blocks） |
| `manager.py` | 进程级单例：连接编排、工具注册、调用路由、权限策略、状态与提示词章节 |

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
| **plan** | 只读分析 + write_file/update_file/mkdir（仅限 `.chat/plan/` 目录） | 任务拆解、依赖分析、生成计划文件到 `.chat/plan/` |
| **map** | 只读（read_file/search/find/ls 等只读工具） | 项目探底、模块地图、调用链追踪、引用关系分析 |
| **review** | 只读 + web_search（无 bash/bash_opt 等任何 shell 执行工具） | Code Review、P0-P3 分级审查、跨文件一致性验证 |
| **execute** | 全工具（不含 user_select/subagent/subagent_opt/web_search） | 读/写/改代码、执行测试、通用任务 |

> **工具排除策略**（与 `src/core/subagent.py` 的 `_TOOL_EXCLUSION_MAP` 一致）：execute 排除 `subagent/subagent_opt/user_select/web_search`；map 排除 `bash/bash_opt/write_file/update_file/rm/mv/cp/mkdir/web_search/subagent/subagent_opt/user_select`；review 排除 `bash/bash_opt/write_file/update_file/rm/mv/cp/mkdir/subagent/subagent_opt/user_select`（纯只读审查：仅 read_file/search/find/ls/web_search，无任何 shell 执行能力）；plan 排除 `bash/bash_opt/rm/mv/cp/subagent/subagent_opt/user_select`，write_file/update_file/mkdir 仅限 `.chat/plan/` 目录。`subagent_opt` 与后台 subagent 均仅主 Agent 独有：SubAgent 工具白名单全类型排除 + 工具运行时 `isinstance(agent, SubAgent)` 双保险。SubAgent 在 `_handle_tool_calls()` 中注入 `agent_type` 到 Func 实例，`Func.can_use()` 进行统一检查。`FileToolBase._validate_path_and_size()` 额外实施 plan Agent 路径白名单校验。

### 并发调度策略

多个独立分析/审查任务同时触发时，同轮并发派发多个 SubAgent（如同时分析多个模块、同时审查多个文件），互不阻塞，缩短总执行时间。

---

## 目录结构

```
├── chat.py                # 入口脚本（asyncio.run(main())）
├── pyproject.toml         # 项目配置与依赖
├── prompts/               # 系统提示词（7 个文件）
│   ├── prompts_export_main.md    # 主 Agent 系统提示词（标准模式）
│   ├── prompts_export_main_simple.md  # 主 Agent 系统提示词（简单模式）
│   ├── prompts_export_main_empty.md  # 主 Agent 系统提示词（精简/空版本）
│   ├── prompts_export_map.md     # map SubAgent 探底提示词
│   ├── prompts_export_plan.md    # plan SubAgent 计划提示词
│   ├── prompts_export_execute.md  # execute SubAgent 提示词
│   ├── prompts_export_review.md  # review SubAgent 审查提示词

├── tests/                 # 测试（按模块划分，覆盖各功能域）
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
│   │   ├── multimodal.py       # 多模态模型判定 + 图片 content blocks 构造（read_image 依赖）
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
│   │   ├── compaction/        # dsh 同款上下文压缩（config/region/checkpoint/pruner/summarizer/engine）
│   │   ├── compression.py     # 上下文压缩（策略模式，回退链）
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
│   │   ├── commands/          # 命令系统（base / _ui_adapter / plugins/）
│   │   ├── exceptions.py      # 异常定义
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
│   ├── tools/              # 工具调用系统（19 个内置工具）
│   │   ├── base.py            # Func 基类 + 元数据系统（含 can_use 工具可用性检查 / agent_type）
│   │   ├── file_base.py       # FileToolBase 文件操作基类（含 plan agent 路径白名单）
│   │   ├── registry.py        # 工具注册表（自动发现 + 调度 + 元数据索引）
│   │   ├── read_file.py / write_file.py / update_file.py / read_image.py
│   │   ├── search.py / find.py / ls.py
│   │   ├── bash.py / cp.py / mv.py / rm.py / mkdir.py / skill_tool.py
│   │   ├── web_search.py / web_fetch.py / user_select.py / subagent.py / subagent_opt.py
│   │   ├── file_ops.py        # 文件操作原子工具（原子写入、路径安全校验、沙盒记录）
│   │   ├── _constants.py      # 共享常量（排除目录、安全路径、编码等）
│   │   ├── encoding.py        # 编码检测工具函数
│   │   ├── utils.py           # 工具通用辅助函数
│   │   ├── search_providers.py  # DeepSeek 官方原生搜索提供者（web_search 依赖）
│   │   └── page_fetcher.py    # 网页内容抓取（web_fetch 依赖）
│   │
│   ├── prompt_builder/     # 系统提示词构建
│   ├── mcp/                # MCP 外部工具接入（stdio / Streamable HTTP / 旧式 SSE）
│   ├── notifications/      # 桌面通知（Termux/Linux/Windows）
│   └── observability/      # 可观测性门面（聚合指标/追踪/遥测日志）
```

---

## 插件内核（一切皆插件）

`chat.py` 的启动是**最薄组合根**：解析参数 → 构建内核插件树 → `ctx.app.run` → 卸载内核。
其余一切——工具、工具元数据、工具运行期常量、事件类型（核心事件类型/显示事件类型/
会话·Agent·能力三事件域）、命名样式、命令、渲染 handler/filter/target、
Agent 类型与中间件、流式处理器、LLM 模型 provider、
通知后端、上下文策略、MCP 传输、工具引擎、事件消费者、UI 视图、Web 提供者、主题、
技能来源、会话投影、全局禁用工具策略、Preset、提示词运行模式/来源/系统提词片段、
ClawBot 远程指令、
CLI 顶层子命令、TUI 键位绑定与特殊键处理器、工具表现（类别/图标/Agent 类型标签）、
语法高亮语言、表现层数据表（Emoji/上下标/HTML 配色/列表符号/轨迹样式/模式文本/
工具显示名/告示块样式/Spinner 帧/配置项说明与选项/轨迹种类顺序与块映射/角色图标/
边框样式表/运行期默认值——计费单价、指标百分位、UI 默认参数、语义色槽位、渐变与
呼吸动效参数、Shell/终端检测表、HTTP 错误提示、Badge 对比色度量、Kitty 键盘协议表、
嵌套列表符号、Diff 样式表、轨迹样式表、模型名称匹配模式表）、
内核 host 组件、补全提供者、状态栏段、运行时数据服务与能力接缝、
运行时不变量检查——都是清单（Profile/Bundle/Patch）中的
**独立插件条目**，可被 `~/.chat_config/cordis.patch.yml`、`--patch` 或用户 Profile
目录按 id 覆盖配置、禁用或替换。

### 内核模型（`src/kernel/`）

| 概念 | 说明 |
|---|---|
| `Kernel` | 无特权内核：服务容器 + Fiber 生命周期 + 依赖驱动加载（`settle`） |
| `Context` (`ctx`) | 服务容器与可逆副作用注册点；`provide`/`consume`/`on`/`effect`/`plugin`；空间可组合性 `extend`/`isolate`/`intercept` |
| `Service` | 插件向其它插件提供能力的基类（占据稳定的 `ctx.<key>`） |
| `Fiber` | 插件生命周期状态机（PENDING→LOADING→ACTIVE / FAILED / DISPOSED），支持 `restart`/`disable`/`enable` |
| `EventBus` | 五种分发模式：`emit` / `waterfall` / `parallel` / `serial` / `bail` |
| `Scope` | 按 key 划分的作用域注册原语（同一进程多 Agent/会话互不污染） |
| `ConfigTree` | Profile / Bundle / Patch 三层组装；`Overlay` 提供 `--patch` / 用户补丁叠加 |
| `loader` | 目录扫描、模块提取、`dsh.plugins` entry-points 发现（源码直编，热重载确定性） |
| `diagnostics` | 依赖诊断（`dependency_report` / `why_blocked` / `service_providers` / `kernel_stats`） |
| `watch` | 插件文件热重载监听（mtime 轮询 → 自动 `reload_file`） |

### 运行时管理（`ctx.kernel_admin`）

| 能力 | 说明 |
|---|---|
| 启用 / 禁用 | `await admin.disable(name)` / `await admin.enable(name)`（可逆，撤销全部注册并提供重新启用） |
| 重载 | `await admin.reload(name)` / `await admin.reload_file(path)` |
| 热重载监听 | `admin.watch_file(path)` + `await admin.start_watching(interval)` |
| 依赖诊断 | `admin.plugins()` / `admin.why_blocked(name)` / `admin.stats()` / `admin.diagnose()` |

组合根支持 `--dump-config` 打印最终插件树、`--check-invariants` 运行时不变量自检；
`python chat.py plugin list/add/remove` 管理外部插件（`~/.chat_config/plugins`），
`/plugin` 界面总览当前内核已加载的插件（运行时 Fiber）。

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

显示层事件系统，定义 **25 种 `DisplayEvent`** 类型（生命周期/工具调用/Agent 状态/模型阶段/流式内容/附加状态/通用输出/用户交互/上下文压缩），基于 `CoreEventBus` 底层发布机制实现。其中 `CompactionChangedEvent` 承载压缩开始/完成/失败状态——TUI 聚合主 Agent 与全部 SubAgent 后在模式行行首显示 `compact · N`，并在完成/失败时输出通知。`DisplayEventBus` 对 `DisplayEvent` 子类提供类型安全包装，与核心事件（字符串类型）并行独立运作，确保终端共享相同的事件语义。

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
- **流式 Markdown 语法增强（2026-10-07）** ✅ — TUI 流式渲染路径（`src/renderer/ansi/`，零 Rich）补齐并新增语法：**数学公式** LaTeX→Unicode 终端排版（行内紧凑 + 块级二维——分数堆叠、根式上划线、矩阵/cases 多行对齐、大算符/极限/希腊字母/关系符/箭头/函数/重音/着色/框选；`_math_latex`）；**Mermaid** 11 类图 ASCII 图形渲染（流程图/时序图/类图/状态图/甘特/饼图/ER/Git/思维导图/时间线/旅程，`_mermaid_render`，与 Rich 路径共享字符级解析 `_mermaid_parse`）；**HTML** 块级与行内元素渲染（内联格式保留、实体解码、注释隐藏、`<br>`/`<details>` 单行折叠块修复）；**任务列表取消态** `[~]`；`LINE_BREAK`/旧式 `BLOCKQUOTE` token 补齐；解析层新增 Fenced 告示 `!!! type "title"` / `??? type`（引用风格与两渲染路径 + 流式预览一致）；性能：数学/图表渲染结果**有界缓存**（源码未变零布局）+ 预览行数上限与省略提示（数学 60 行 / Mermaid 120 行保留类型行），超长纯文本段落/表格/代码块既有行级增量缓存保持
- **流式 Markdown 语法扩展（第四批 · 2026-10-07）** ✅ — 在第二/三批之上继续扩展语法与渲染质量，并保持流式性能：**行内脚注** `^[脚注文本]`（Pandoc inline footnote——正文渲染为序号 `[n]`，内容进文末脚注列表，与定义式脚注 `[^id]:` 共用编号序列；以「归一化内容」为键幂等注册，内容相同合并编号、流式预览重复渲染不重复追加；TUI 与 Rich 两路径同步）；**代码块行号扩展** `linenostart=` / `linenostep=`（起始值与步长，`{linenos}{linenostart=10}{linenostep=5}` 等写法；行号列宽按最大显示行号计算，TUI 与 Rich 两路径一致）；**连续多组属性** `{1,3}{linenos}{title="x"}` 与**裸属性** `hl_lines="1,3" linenos` 完整解析（修复仅取首组、后续组落入代码内容成为首行的问题；`python print(1)` 式代码首行仍严格保留）；**Diff/patch 语义高亮**（TUI 路径补齐与 Rich 路径 `render_diff_line` 同源语义：`+` 亮绿深绿底 / `-` 亮红深红底 / `@@` 青粗 / 文件头灰粗）；**高亮行整行背景**（`hl_lines` 在 `▸` 前缀之外叠加琥珀背景）；**链接 OSC 8 可点击**（markdown 链接 / 自动链接 / 邮箱的 `Run.link` 贯通 wrap / truncate / 表格单元格 / TUI 输出层，现代终端可直接点击；宽度计算不计入）；**分隔线整宽渐隐**（固定 40 列 → 终端宽度整宽 + 两端渐隐）；**嵌套层次配色**（引用前缀与无序列表符号按嵌套深度分级着色）；**渲染修复**：段落含 `|`（如 `a || b`）时表格候选行越序导致内容顺序错乱、段落与表格顺序、引用块内含 `|` 文本误判、数学多行水平拼接（分数/根式）非基线行未按列宽补齐导致错位、词法分析器失败结果重复重试（改为缓存 None 并降为 debug 日志）。测试 `tests/test_ansi_markdown_syntax_v4.py`（40 例）固化。
- **流式 Markdown 语法扩展（第二批 · 2026-10-07）** ✅ — TUI 流式渲染路径（`src/renderer/ansi/`，零 Rich）与 Rich 路径同步新增/增强语法：**Front Matter** 元信息块（YAML `---` / TOML `+++` / JSON `{ }`——文档头识别、键值卡片渲染、未闭合回退为分隔线、流式预览；文档开头 `---` 后跟空行不误判；解析真源收敛到共享纯逻辑模块 `_front_matter`）；**缩进代码块**（4 空格 / Tab，CommonMark——修复此前被字母快速通道当段落吞掉、语法完全失效的问题，现识别为代码块并高亮，且不中断进行中的段落）；**参考式图片** `![alt][ref]`（查定义表展开 URL / 标题 / 尺寸，未解析保留占位提示）与**快捷引用链接** `[ref]` / `[ref][]`（未命中定义时原样回退，不误改普通方括号文本）；**更多 HTML 标签**（`<figure>/<figcaption>`、`<dl>/<dt>/<dd>`、`<ul>/<li>` 语义化行渲染，`<table>` HTML 表格 → 框线表格并支持 `<caption>` 表注，`<ruby>` 注音 `漢(かん)`，`<a href>` 行内链接，`<video>/<audio>/<iframe>` 媒体占位）；**引用块内块级元素**（列表 / 代码块 / 表格 / 嵌套引用）统一补 `│` 前缀（解析层标记 `bq_depth` + 渲染层统一前缀注入，覆盖跨行块与 flush 路径；引用内表格要求出现分隔行才成表格，避免 `> a | b` 正文被误判）；**内容丢失修复**（无语言围栏首行被当语言吞掉；`<details>` 正文以完整 Markdown 语义递归渲染；定义列表多定义缩进续行）；**表格表注** `: 说明` / `Table: 说明` 识别与渲染，**单列表格** `| a |` / `|---|` 分隔行识别修复（原判定要求 ≥2 个分隔单元，单列表格被降级为段落）；Rich 路径同步（新增 `FrontMatterHandler` / `TableCaptionHandler`，注册为独立插件条目 `renderer_handler_front_matter` / `renderer_handler_table_caption`）。测试 `tests/test_ansi_markdown_syntax_v2.py`（53 例）+ `tests/test_front_matter_parse.py`（10 例）固化。
- **增量渲染（除 resize 全量外均增量）** ✅ — 行级 diff + committed 前缀身份复用 + 位移锚点：头部动画（标题栏呼吸）不再引发 committed 可见区全量重写，流式增长每帧重写范围 O(可见区) → O(头部差异+位移区)；第十二轮强化：已提交内容修改（工具卡状态图标 ●→✔ / 标题更新）经 `_replace_committed_line` 使前缀缓存失效并新建 Line 对象 → 关闭后必现刷新；开放块行 key 用块内绝对行号 → 流式追加不重建已渲染行；subagent 卡片元素按引用 use_memo 缓存；补全弹窗/搜索激活时推进呼吸动画（空闲不渲染）；PriorityQueue 腾位 heapify / ANSI CSI 终止符（真彩冒号+终端键）三处正则收敛 / 换行缓存长度快照 / str 依赖按值比较 / 崩溃恢复计数复位 / 刷盘失败退避等 20 项渲染正确性与健壮性修复（BUG-30~62）
- **光标坐标追踪** ✅ — 新增 `CursorTracker` 全局光标坐标追踪系统，集成到 ContentRenderer / RenderEngine / _BottomBar / _CompletionPopup，消除坐标推算累积误差
- **React Ink 组件框架** ✅ — `src/tui/ink/`：调和器 + flexbox 布局 + hooks + 帧差异渲染，覆盖 useState/useReducer/useRef/useEffect/useLayoutEffect（独立时序）/useMemo/useCallback/useContext/useId/useSyncExternalStore/useInput/useFocus/forwardRef/useImperativeHandle/memo/ErrorBoundary/useMeasure/usePrevious；TEXT shorthand 样式/transform/wrap/dimColor/align；BOX flexBasis/borderStyle 变体（single/double/round/bold/classic/dashed/singleDouble/doubleSingle）/alignItems/justifyContent/gap；框架级缺陷修复：useImperativeHandle hook 槽位稳定、useSyncExternalStore 订阅重订、memo×context 短路恢复、生成器子级展开
- **React Ink v6 全特性补齐（A~G）** ✅ — 对照官方 v6 API 补齐剩余特性（44 例固化）：**文本样式** strikethrough（`\x1b[9m`）/inverse（`\x1b[7m`）；**布局** flexDirection="row-reverse"/"column-reverse"（视觉顺序反转）、flexWrap="wrap-reverse"（行序反转）、alignItems/alignSelf="baseline"（终端近似底部对齐）与 "auto"（跟随父）、alignContent（flex-start/end/center/stretch/space-between/around/evenly 行分布）、columnGap/rowGap（gap 独立控制）、position="static"（忽略定位偏移）、overflow/overflowX/overflowY="hidden"（绘制裁剪：垂直行裁剪 + 水平列切片）、aspectRatio（宽/高缺省维度推导）；**边框** borderStyle 自定义对象（{topLeft,top,topRight,left,bottomLeft,bottom,bottomRight,right} 左右独立）、borderTopColor/RightColor/BottomColor/LeftColor、borderDimColor 系列、borderBackgroundColor 系列、borderTop/Right/Bottom/Left（bool 显隐）；**Box 背景** backgroundColor（区域填充 + 子 Text 未指定时继承）；**Hooks** usePaste（粘贴独立通道，阻断 useInput）/useBoxMetrics(ref)（width/height/left/top/hasMeasured）/useWindowSize（columns/rows，resize 自动重渲染）/useFocusManager（enableFocus/disableFocus/focusNext/focusPrevious/focus(id)/activeId，Tab 自动切换）/useFocus({id,autoFocus,isActive})/useCursor（setCursorPosition）/useIsScreenReaderEnabled/useAnimation（帧号+时间戳）/useApp 扩展（waitUntilRenderFlush/suspendTerminal）；**生命周期** render() 轻量入口（waitUntilExit/unmount/cleanup/rerender/clear）；**输入与组件** useInput 兼容 React Ink `(input, key)` 双参签名（key 含 pageUp/pageDown 等完整字段，PageUp/PageDown 键解析）、Static items 数组模式 + style prop、Transform (line, index) 逐行签名、wrap="hard" 字符级硬拆
- **标准控件/布局重构（阶段2）** ✅ — app 组件树全部改用语义化标准布局容器：App 消息区/底部区 Column、TopHeader Row、StatusBar/ChatView Column（ToolStatusHeader 已从组件树移除——工具状态由工具卡片顶边框 ● 展示，死代码收尾时删除模块）、_ParseLine/_StreamingLine 空状态统一空 TEXT（避免 BOX↔TEXT fiber 销毁重建）；控件库内部同步收敛：SelectInput/TextInput/MultiSelect/Table/Divider/Grid 用 Row/Column 门面（输出等价）；渲染错误修复 E1（显式 width 超 avail 钳制——行宽不变量）、E2（宽字符第二列覆盖不再静默丢失，`_merge_line` 与 input-area 统一合并路径）、E8（SelectInput/MultiSelect items 动态缩小越界防护）、E9（MultiSelect 不可哈希 value 兜底）、E10（TextInput 光标列对齐）；性能优化 P-H2/P-H3/P-H7/P-H9/P-H10/P-H14（布局/收集/截断/调和快路径，1000 行历史帧渲染 < 1ms）
- **user_select 弹窗 React Ink 化** ✅ — `user_select` 工具交互从「命令补全弹窗（show_completions + CompletionState）+ 手动 raw I/O（select/read_byte/cbreak）」迁移为独立 React Ink 组件 `UserSelectPopup`（`src/tui/app/user_select.py`）：弹窗在 App 组件树底部区渲染（StatusBar 上方，不可见零高度），`use_input` + `use_state` 处理 ↑↓/Enter/Esc/空格（不再直接读 stdin、不再 stop/start EscapeMonitor、不再操作补全弹窗私有字段）；结果经 `model.user_select.done/action/result` 回传工具协程；`_run_interactive` 不再 suspend render 线程（InputDispatcher 保持路由 use_input）；App 以 `key=seq` 强制重挂载（连续多次打开不残留旧选中）；组件 hooks 无条件注册且 `is_active=visible`（弹窗关闭后输入放行旧路径，修复输入被吞回归）；`InputDispatcher` ESC 内联分支先询问 input router（修复前 Esc 直接走中断从未进 router——`useInput` 钩子收不到 escape 事件，弹窗按 Esc 无法取消；修复后 router 消费则跳过中断路径，无 router 时 Esc 语义零变化）；单选高亮 ▶/多选 ●○ 勾选/分栏说明（右栏当前选中项说明，复用 input_area 列宽计算）；**模态底部视图通用机制（2026-08-17）**：user_select 从底部区常规成员**独立为「模态底部视图」**——弹窗打开时**底部框（状态栏/输入区）不显示、弹窗在原来底部框位置独立显示**，做成通用化 = **`use_modal` hook**（`src/tui/ink/_hooks_input.py`——与 `use_fullscreen` 同一 `FullscreenHook` 节点类型，模态输入接管语义泛化：激活时 router 全部 use_input 未消费的事件吞掉，不落入输入缓冲）+ **`AppModel.bottom_view`** 状态 + **`app.BOTTOM_VIEWS`** 底部视图注册表（App 按 id 只渲染底部区对应视图；key 约定支持 `(组件, key_fn)` 元组——UserSelectPopup 用 `model.user_select.seq` 递增序号强制重挂载；状态栏/输入区不渲染 → 输入光标自动隐藏；弹窗高度预算 `h-11 → h-3`（不再预留状态栏/输入区空间）；输入文本不再清空——弹窗关闭后原输入恢复显示不丢失）；user_select 工具 / `CommandUiAdapter.run_bottom_bar_selection` 协议：打开设置 `bottom_view="user_select"`、清理恢复 ""（与 UserSelectState 同生命周期）；**/editmsg 消息选择独立协议（2026-08-18 用户需求：editmsg 与 user_select 不能用同一份代码）**——独立状态 `EditMsgSelectState`（`model.editmsg_select`）+ 独立组件 `EditMsgSelectPopup`（`src/tui/app/editmsg_select.py`）+ 独立底部视图 `bottom_view="editmsg"`，不复用 UserSelectPopup / user_select 状态；**editmsg 每条消息只显示一行**（`_user_msg_summary` 单行摘要，多行折叠为一行，超宽截断）；`reset_display`（Ctrl+L 清屏）同时退出底部视图；新增底部视图只需注册表加条目 + 设置 bottom_view，底部区渲染/输入接管/光标隐藏自动生效；测试固化（tests/ 下 editmsg/user_select 相关测试）；**user_select 并发 + tab 切换（2026-08-19 用户需求，参考 Claude Code AskUserQuestion）**——`parallel_safe=True`（ToolDAG `_add_user_select_constraints` 移除 user_select 间串行化，同层可并发）+ `model.user_selects` 并发队列（真源，`model.user_select` 保留兼容字段）+ `UserSelectPopup` 多问题 tab 界面（顶部 tab 栏 `[×] 已答 / [ ] 未答`、当前焦点高亮、Tab/Shift+Tab/←/→ 切换焦点；当前问题标题 + SelectInput/MultiSelect 选项 + 提示行）；**Enter 确认后自动切到下一个未选择的问题**（跳过已完成；Esc 取消同样切换；全部回答后自动切到 Submit 页）；**已经回答的可以重新答**（2026-08-19 用户需求）——Enter 仅 `mark_answered` 标记 answered（提交前可切回该 tab 重新导航选择 + Enter 覆盖旧答案，`UserSelectState.mark_answered` 与提交终态 `try_set_final` 分离）；**增加 Submit tab 页面给玩家确认是否提交**（2026-08-19 用户需求）——最后一个 tab 为「提交」页：汇总显示全部问题答案（已答 `[×] 标题 → ✓ 答案` / 未答 `[ ] 标题 → 未回答`），Enter 统一提交（已答按各自 action/result 写终态、未答取 default_options，统一置 done → 各工具协程同时返回）、Esc 返回修改；已完成 tab 标记 [×] 保留显示，全部提交后最后一个协程统一清空队列 + 关闭 bottom_view（单问题非并发行为与旧版完全一致：不渲染 tab 栏/Submit 页，Enter 直接提交）；测试 `tests/test_user_select_concurrent.py` 23 例固化（parallel_safe/DAG 同层/并发队列协议/[×] 保留/整体关闭/tab 切换/自动切换/重答/Submit 页提交）
- **轨迹视图（DSH 风格，Ctrl+H 开关）** ✅ — 2026-08-19：TUI 实现 DSH Web「轨迹（Trajectory）」功能——**Ctrl+H** 打开/关闭（0x08 字节从 backspace 改判为 ctrl_key '\x08'；Backspace 键仍为 0x7f DEL，现代终端字节可区分；未注入轨迹回调时回退 backspace 语义零回归；CSI u 增强键盘协议 `\x1b[104;5u`/`\x1b[8;5u` 同路径）；**打开时整屏只显示轨迹界面**（消息区/顶部标题栏/状态栏/输入区全部不渲染——「其他 TUI 不显示，只显示这个界面」，台账/检查器占满整个终端高度；Esc/Ctrl+H 关闭恢复完整聊天界面；**2026-08-17 迁移到「模态全屏视图通用机制」**：打开期间**模态独占键盘输入**——字符/Enter/Backspace 等未消费按键被 input router 吞掉、**不落入输入缓冲**（杜绝「看不见的输入」误编辑/误提交；关闭后输入区恢复正常输入）；机制通用化 = **`use_fullscreen` hook**（`src/tui/ink/_hooks_input.py`——激活时 router 全部 use_input 未消费的事件返回 True，InputDispatcher 跳过旧路径）+ **`AppModel.fullscreen`** 状态 + **`app.FULLSCREEN_VIEWS`** 视图注册表（App 按 id 整屏渲染）+ **`_make_fullscreen_toggle_cb(model, session, view_id)`** 通用开关工厂（任意全屏视图绑定快捷键复用；`trace_open` 为 `fullscreen=="trace"` 兼容别名 property；光标隐藏通用化为 fullscreen 非空即隐藏——新增全屏视图只需注册表加条目 + 设置 fullscreen，整屏渲染/输入接管/光标隐藏自动生效）：**React Ink 左右布局**（`src/tui/app/trace_view.py`）——左栏「台账」（轮次分隔 `── 轮次 N ──` + 记录行 `#N 种类图标 摘要 右对齐耗时`，选中行 ▶ + 整行背景高亮，虚拟窗口按终端高度自适应）+ 右栏「检查器」（#N 种类 · 状态 ●/✔/✖ · 耗时 · token 输入/输出 · 内容行按栏宽换行 + 视口截断 + 省略提示，每行唯一 key）；**数据源 = agent 消息列表**（装配经 `_register_session_handlers` 把 `session.messages` 注入 `AppModel.message_source`——轨迹从真实会话消息组装业务记录（system/user/assistant+tool_calls/tool 返回），**不是 TUI 渲染过的聊天块**；未注入时回退块路径）——**system 消息 → system 记录显示系统提词**（每条一条，摘要=首行、检查器读全文，对齐 DSH SYSTEM 记录）、user 消息 = 新轮次、assistant 消息按内容拆分思考💭/回答💬/工具调用⚡、**工具调用 + tool 返回按 `tool_call_id` 合并成一条**（台账行同时显示调用 `⚡ bash ls -la` 与返回首行预览 `· 总用量 4462…`，详情 = 调用行 + 返回行，无匹配返回的孤儿 tool 消息独立显示）；**# 0 工具列表**（2026-08-17 用户需求：台账固定首条 `# 0 🧰 工具列表`——右侧检查器显示 agent 全部工具，**一行一个**（显示原名），数据源 = `ToolRegistry` 注册表（注册顺序 = 自动发现顺序），注册表异常/为空时静默降级不显示；用户确认范围：**主轨迹与 subagent 轨迹均显示**）；`use_memo` 指纹缓存（消息列表身份/长度/尾消息内容变化才重建）+ 详情惰性提取；导航 ↑↓/PgUp/PgDn/Home/End/g/G（g=首/G=末），**-1 尾部跟随**（打开定位最新记录、流式追加自动跟进，导航后写回具体索引），Esc/Ctrl+H 关闭；**工具调用参数/返回值用树控件显示**（2026-08-17 用户需求：轨迹 Trace 的工具调用修改——选中 tool 记录时检查器内容 = **`▸ 参数` 小节 + 参数树**（`tool_args` 原始 arguments JSON 树形展开：dict 键值叶子 / list 下标 `[i]` / 嵌套 `key (N 项)` / 空容器 `{}`/`[]` / JSON 字面量 `null`/`true`/`false`）+ **分割线**（`──` 深灰满宽）+ **`▸ 返回值` 小节 + 返回值树**（`tool_result` 原始返回文本：JSON 树形展开、非 JSON 纯文本每行一个叶子节点；对齐 ink Tree 控件渲染——缩进 + `▾` 展开指示符，只读展示不抢台账导航焦点；head-first 截断 + 「… 后 N 行省略」后置；无树数据（手动构造/异常）回退纯文本 lines 零回归；模块级缓存跨流式重建命中）；**框架修复 P3-21**（`_cursor.find_input_fiber` 长 sibling 链 O(2^N) 指数压栈 → 压栈去重 O(N+Σ链长)——轨迹检查器 23 个兄弟 TEXT 触发，渲染线程卡死数秒，200 链/环结构回归测试固化）；**工具实参显示完整**（2026-08-19 用户需求：轨迹 Trace 的工具的实参要显示完整——树行由截断改为**换行显示完整**（`wrap_runs_by_width` hard 字符级硬拆，续行 hanging indent 对齐值起始列）：长实参（bash command / update_file old_string 全文等）折行后全部可见、每行宽 ≤ 栏宽（行级 diff 宽度不变量保持）、键/值分色跨行保持（BEAUTY-36），检查器滚动窗口（vim j/k/g/G）可浏览全部内容；**树控件空格展开/收缩**（2026-08-19 用户需求：树控件按空格可以展开和收缩，默认展开所有——检查器焦点空格切换光标所在节点：折叠节点子级行不进入可见列表（对齐 ink Tree `_collect_visible`）、指示符 `▾`↔`▸`；节点路径 key（`args/0`/`res/0` 前缀隔离参数/返回值树、`0/0` 深层）写入与内容行对齐的 `keys` 列表；折叠集合存 `model.trace_tree_collapsed`/`trace_tools_tree_collapsed`（工具列表详情视图 schema 树同样支持），切换记录/工具/进入子代理/关闭视图时复位（默认全展开）；**vim 风格搜索**（2026-08-19 用户需求：输入 "/" 和内容回车查找——"/" 进入搜索输入模式（底部显示 `/${query}` + 光标，回车后输入行消失；字符累积/退格/Esc 取消输入保留已执行搜索）、回车执行**正则搜索当前焦点面板**（台账搜记录全文 / 检查器搜内容行，re.search 子串匹配、非法正则静默无匹配）、**所有匹配行背景高亮**（vim hlsearch 风格 `_S_SEARCH_BG` 236）+ 当前匹配行亮蓝（`_S_SEARCH_CUR_BG` 25）、**n 下一个 / N、p 上一个**（p 为用户原话 prev 兼容别名）环绕切换并定位（台账→选中记录并滚动 / 检查器→光标行并滚动、焦点切到匹配所在侧）、`/` 预填上次 pattern（vim 语义）；搜索状态存 model（`trace_search_*` 六字段），切换记录（检查器搜索失效）/折叠树/进入子代理/工具列表/关闭视图/清屏时清除；测试 `tests/test_trace_tool_args_full.py`（实参完整 13 例）+ `tests/test_trace_tree_collapse.py`（树折叠 20 例）+ `tests/test_trace_search.py`（搜索 24 例）固化）；测试固化（tests/ 下 `test_trace_vim_pane.py` 等 6 个 test_trace_*.py——解析/分发/消息构建/块回退/渲染/注入链/工具列表/工具树显示/搜索/折叠/端到端）
- **TUI 四界面增强第二批（2026-10-07）** ✅ — 用户需求「改进 轨迹 Trace / editmsg / plugin / config：更好的显示、操作、更多功能」全部落地：
  - **轨迹 Trace**：记录标记/书签 `m{a-z}` 设置、`'{a-z}` 跳转（台账行显示 `'a`）+ 搜索历史（`/` 输入模式内 ↑↓ 回溯，去重有界）+ 时间列（`T` 循环 关/绝对/相对；仅墙上时钟时间戳记录显示）+ 按种类过滤（`t` 循环 `全部→各 kind→全部`）+ 检查器行号显示（`#`）与**当前内容行复制**（检查器焦点 `y`；台账焦点仍复制整条记录）+ 记录详情**内联展开/折叠**（`o` 在台账行下方就地插入详情预览行，展开行不可选）；
  - **editmsg**：消息行元信息增强（序号 · 轮次 `tN` · `字符数/行数` · 消息时间字段）+ 选中消息**全文预览区**（列表下方，按可用高度截断）+ 弹窗内搜索过滤（`/` 字符累积 / 退格 / Enter 保留过滤 / Esc 清除；过滤视图 ↔ 原始消息索引换算，提交仍回传原始索引）+ 导航增强（PgUp/PgDn 翻页、Home/End 首末；标题 `(n/total) · 过滤 x/y`）；
  - **plugin**：搜索过滤（`/` + `n`/`N`/`p` 匹配导航 + `f` 过滤模式）+ 统计概览（头部：总数 · 按状态 · 错误 · 缺依赖）+ 帮助面板（`?`，数据表 `plugin_keymap`）+ 复制插件信息（`y`，OSC52）+ 详情字段警示渲染（错误字段红 / 缺失依赖黄 / 异常状态分色）+ 左栏警示标记（`⚠错误`/`⚠缺依赖`）；
  - **config**：搜索过滤（`/` + `n`/`N`/`p` + `f`）+ 帮助面板（`?`，数据表 `config_keymap`）+ 恢复默认值（`r`，可撤销）+ 撤销上次编辑（`u`，编辑与恢复默认均入栈，上限 20）+ 复制配置值（`y` → `path = value`）+ 头部显示配置文件路径来源；
  - **架构**：ListView 新增 `isSelectable` 扩展点（声明更多不可选行类型，缺省行为不变）、通用键位面板模块 `src/tui/app/_keymap_pane.py`（轨迹/插件/配置三视图帮助面板共享渲染，`trace_help` 改薄封装）、`plugin_view`/`config_view` 视图模型增强（`collect_plugin_stats`/`format_plugin_stats`/`plugin_search_text`/`format_plugin_entry_text`/`_field_levels`）、`_state_types` 与 `AppModel` 新字段（标记/搜索历史/种类过滤/时间列/行号/内联展开、plugin/config 搜索与帮助态、撤销栈、来源路径；`reset_display` 同步复位）；
  - **测试**：`tests/test_trace_enhancements_v2.py`（33 例）、`tests/test_editmsg_enhancements.py`（23 例）、`tests/test_plugin_view_enhancements.py`（19 例）、`tests/test_config_view_enhancements.py`（28 例）固化；全量 `pytest tests` 通过（5585 passed, 1 skipped）。
- **TUI 四界面增强第三批（2026-10-07）** ✅ — 用户需求「改进 轨迹 Trace / editmsg / plugin / config：更好的显示、操作、更多功能」第三批（显示可视化 · 操作 · 更多功能）全部落地：
  - **轨迹 Trace**：台账行尾**耗时条形图**（`█`/`░` 迷你占比条，按全表最大耗时归一化；`trace_ledger._time_bar_fill`/`_time_bar_max`，`time_bar`/`time_bar_bg` 样式入 `trace_style` 表）；**轮次折叠**（`za`/`zc`/`zo` 当前轮次、`zC`/`zO` 全部；折叠轮次记录行隐藏为 `── 轮次 N · 折叠 K 条 ──` 头行（`_TraceTurnCollapsedRow` 不可选），`{`/`}` 上/下轮次跳转；`_collapse_turns` 纯函数 + `_nearest_turn` 定位）；**记录对比**（`C` 选两条记录 → 右栏并排对照面板：字段差异高亮 + 参数/返回值并排预览；`trace_compare.py`）；**范围导出**（`x` 循环 全部/当前视图/仅失败/仅工具，状态行标注，`w`/`W` 按范围导出；`_export_records_for_scope`）；
  - **editmsg**：预览区 **markdown 渲染**（复用聊天区渲染管线 `apply._render_markdown_lines`；`_preview_rows` 按内容哈希缓存）+ **预览滚动**（`[`/`]`，`es.preview_scroll`，切换消息复位）+ 列表**搜索命中子串高亮**（`_highlight_runs`，精确到子串非整行背景）；消息选择仍只显示**用户消息**（保持编辑语义）；
  - **plugin**：**依赖关系视图**（`r` 开关——右栏显示选中插件的依赖 / 被依赖 / 提供服务关系，Enter 跳转到相关插件；`plugin_relation.py`）+ **服务交叉引用**（`view_model._compute_dependents` 全局填充 `dependents`）+ **状态 / 分类过滤**（`S`/`K` 循环；`_filter_allowed` 组合过滤 搜索 ∩ 状态 ∩ 分类）+ **清单导出**（`w`/`W` → `plugin_export.py`，Markdown/JSON）；
  - **config**：**编辑 diff 预览**（编辑行下方 `旧: O → 新: N`，不同值高亮；输入 / 子输入模式）+ **分组折叠与导航**（按 `path` 首段分组，`za`/`zc`/`zo`/`zC`/`zO` 折叠展开、`[`/`]` 组间跳转；`_config_rows` 分组头为不可选行，行↔条目双向映射）+ **导出 / 导入**（`e` 导出 JSON、`i` 导入 JSON 路径输入，`config_export.py`；敏感项导出排除）+ **撤销历史面板**（`U`——列出最近修改，Enter 回退到该历史点；`_undo_entries`/`_undo_to`）；
  - **架构**：AnsiLine→StyledRun 转换提取为共享模块 `src/tui/app/_ansi_convert.py`（轨迹检查器与 editmsg 预览单一真源，消除重复实现）；
  - **测试**：`tests/test_trace_enhancements_v3.py`（26 例）、`tests/test_editmsg_enhancements_v2.py`（17 例）、`tests/test_plugin_view_enhancements_v2.py`（26 例）、`tests/test_config_view_enhancements_v2.py`（30 例）固化；全量 `pytest tests` 通过（5684 passed, 1 skipped）。
- **轨迹视图增强（显示信息 · 操作 · 更多功能，2026-10-07）** ✅ — 用户需求「改进 ▍轨迹 Trace：显示信息、操作、更多功能」九项全部落地：**头部统计条**（条数/轮次/工具 done|总数/运行中/失败/总耗时/token ↑↓/当前位置 n/total；过滤时标注「过滤 x/y 条」、面板打开时标注面板名；数据源 `trace_stats.collect_trace_stats` 单一真源 + `format_summary` 生成，随栏宽截断、行尾 `─` 填充保持）；**检查器元信息增强**（`_meta_parts`：耗时/状态/工具名/调用 ID/起始时间（epoch 基准）/token 明细（输入·输出·缓存·实时↓）/参数与返回行数/内容行数；`_inspector_fixed_rows` 与之同源——视口预算与渲染恒一致）；**台账行增强**（`tN` 轮次标记 + 失败记录摘要红色高亮（`_S_ERROR`）+ `↳` 子代理标记；`_row_turn_map` O(1) 查表）；**键位操作增强**（`e`/`E` 上/下条失败记录、`]`/`[` 上/下一个工具调用、数字+`g`/`G` 跳到记录号 #N、`zR`/`zM` 全展开/全折叠树、`Ctrl+D`/`Ctrl+U` 半屏翻页；`_find_record_pos` 单向查找 + `_record_pos_by_number` + `_collapse_all`；事件处理按 `model.trace_selected` **实时**解析选中位置——同帧连续按键不陈旧）；**搜索增强**（底部状态行 `/pattern  n/m [Aa] [过滤]` 匹配计数；`f` 过滤模式——台账只显示匹配记录，`_filter_view` 构造子集 + `view_map` 原始↔视图索引映射贯穿选择/下钻/搜索定位；`v` 大小写敏感开关（默认忽略大小写，切换后重跑当前搜索；`_trace_search_matches` 增 `case_sensitive`））；**视图内帮助面板**（`?` 开关、`?`/`q`/`Esc` 关闭、j/k/PgUp/PgDn/g/G 滚动；内容源 = 新增表现层数据表 `trace_keymap`（manifest 同步注册）经 `trace_help.help_panel_rows` 渲染）；**导出轨迹**（`w` Markdown / `W` JSON → `trace_export.write_export` 写工作目录 `trace-export-<时间戳>.<ext>`；序列化纯函数与写盘分离，路径/字符数回报到状态行）；**统计概览面板**（`i` 开关——`trace_stats.stats_panel_rows`：概览计数/耗时/token/成功率 + 各工具耗时排行（条形图 + 次数 + 失败标记）+ 记录种类分布（占比条））；**复制记录内容**（`y` → `_screen.set_clipboard` OSC52 序列写系统剪贴板，超长截断，成功/失败回报状态行）；**底部状态行**（`_status_line_text`：匹配计数 + 操作反馈（导出路径/复制字符数/无匹配/无更多错误等））；新增 `trace_stats.py` / `trace_help.py` / `trace_export.py` 三个可独立测试模块；新增 model 字段 `trace_count_buffer`/`trace_pending_prefix`/`trace_help_open`/`trace_stats_open`/`trace_status_message`/`trace_search_case`/`trace_search_filter`（`reset_display` 同步复位）；`TraceRecord` 新增 `tool_name`（工具名真源，构建链路四处填充）；测试固化 `tests/test_trace_enhancements.py`（42 例：统计/帮助/导出/过滤/键位/台账/meta/model 字段/端到端渲染/剪贴板）
- **标准控件/布局重构（阶段3）** ✅ — 新增标准控件 RadioList（单选列表：◉/○ 指示符 + 键盘导航 + limit 窗口）/CodeBlock（代码块：边框 + 语言标签标题栏 + 行号 + 宽字符安全截断，行宽不变量）/InlineSpinner（行内时间基 spinner 字符控件）/Gradient（逐字符渐变文本：lerp_color 色标插值，TopHeader 渐变单一真源收敛）；app 层 `_StreamingLine` 手写 spinner 单 TEXT → Row + InlineSpinner + TEXT 标准控件表达（渲染输出等价）；控件与布局门面经 `widgets/__init__.py`/`ink/__init__.py` 统一导出（21 例固化）
- **TUI 全量标准 React Ink 组件化（阶段4，无例外）** ✅ — 所有 TUI 布局/组件按标准 React Ink 表达重构，无例外（11 例固化）：
  - **committed-chat → StaticLines 标准组件**（`src/tui/ink/widgets/staticlines.py`）——聊天历史静态行批量渲染从 app 层私有 host 迁移为标准组件（组件库导出 `h(StaticLines, {"lines": ...})`），保留帧前缀缓存/增量发射性能机制（无变化帧 O(1)）；`render_frame`/`layout` 的 committed 前缀消费统一识别 static-lines；旧 host 标签保留为兼容别名；
  - **input-area → InputArea + CompletionPopup 标准组件**（`src/tui/app/input_area.py`）——输入区自定义 host（直接画布绘制）迁移为函数组件 `InputArea`（返回 Column 组件树：`CompletionPopup` 弹窗 + 上/下分隔线 TEXT + 历史搜索 TEXT + 输入行 TEXT），`dataInputArea` 标记容器 + props 透传；`session._position_cursor` 经 dataInputArea 容器定位 + 换行布局缓存写回（`fiber._input_layout_cache`——同 text/max_input 帧零重复换行计算）；`use_memo` 原子值 deps（id/len 指纹 + 时间桶）缓存 Element 列表——修复嵌套 tuple deps 恒 miss（is 引用比较）后无变化帧 **~0.9ms → ~0.62ms**；占位符渐显状态经 `use_ref` 组件级持久（修复组件化后渐显每 0.1s 桶重置 bug）；
  - **subagent 卡片去 ANSI 中间层**（`src/tui/_subagent_render.py`）——子代理卡片渲染从「ANSI 字符串行拼接」迁移为「ink Line 行（StyledRun）」：`render_frame`/`build_agent_lines`/`format_tool_record` 返回 `Line`，样式统一用 `Style(fg=色号)`（与 StatusBar/ToolCard/UserSelect 同源），`subagent_panel.SubAgentCard`（`_lines_to_children` 转换点）直接复用 `Line.runs` 转 TEXT 标准组件（不再 `ansi_to_runs` 解析）；`Line` 增补值比较 `__eq__`（控制器变更检测）；`_get_tool_color` 返回 `Style`（色号与旧 ANSI 一致）；
  - **兼容层彻底移除（无例外）** — 旧 host 标签 `committed-chat`/`input-area` 注册已移除（生产/测试全部用 StaticLines/InputArea 标准组件）；`chat_view.register()`/`input_area.register()` 空操作移除；`input_area` 遗留 host 绘制函数（`_measure`/`_paint`/`_build_separator_line`/`_merge`/`_compute_input_rows`/`_wrap_input_text`）与 `ToolStatusHeader` 死代码模块（`tool_header.py`，工具状态已由工具卡片顶边框 ● 展示）已删除（无例外）；`_const.py` 的 `_COLOR_*`/`_C_*` ANSI 颜色常量与 `_screen.py`/`_subagent_panel.py` re-export 已删除（生产渲染统一 `Style(fg=色号)`，色号从 `_SEMANTIC_COLOR` 槽位表解析）；`reconciler._mark_deleted` 递归标记子树全部 fiber deleted（修复函数组件 key 变化时外部缓存——session 输入区 fiber / committed 前缀缓存——失效检测失败）；
  - **性能**：重构后全场景无变化帧 < 1.5ms（20 条历史 + 20 项弹窗 + 中文输入 1.40ms；1050 行历史 0.66ms；流式增长 0.65ms——30Hz 预算 33ms 仅占 <5%）；全量 `tests/` 用例通过。
- **TUI 全量 React Ink 深化控件化 + 架构守卫（阶段5，2026-08-16）** ✅ — 用户需求「所有 TUI 都要用 React Ink 控件跟布局实现所有」收尾：
  - **渲染辅助层统一 ink 输出模型**：`pipeline/message_display`（非 ChatUI 兜底直写）渲染行迁移为 `ink.output.Line`（`_display_line`）——兜底路径与界面渲染共用同一输出模型；`_diff_renderer`（diff 文本生成）行构建统一迁移为 ink 输出模型（`Line`/`StyledRun`，样式统一 `tui.core.Style`）——`_inline_highlight` 返回 StyledRun 列表、`_render_chunk`/`_flush_pairs`/`_render_diff_summary` 经 `Line` 构建 + `_write_diff_line`（接受 Line，兼容 str 旧调用）渲染，不再手工 `Style.apply` 拼接 ANSI；**输出与旧实现逐字节一致**（7 类典型 diff 场景基线比对 + 字节基线测试固化）；`Line.render()` ANSI 渲染缓存复用（同行跨次零重建）；**`events.consumers.OutputConsumer`（事件回退直写路径）同步迁移 ink 输出模型**——`_LEVEL_COLORS`/`_RESET` ANSI 色串直拼 → `_LEVEL_STYLES`（`core.style.Style`，色号取与旧 16 色视觉等价的 256 色语义色）+ `Line.of(text, style).render()`（旧常量保留为 deprecated 兼容 re-export，生产路径零引用）；
  - **架构守卫测试**（AST 静态分析 9 例，防回归）：**R5 渲染模块必须依赖 ink**——tui 模块凡含 `h(`/`use_*` hook 调用者必须运行时依赖 `src.tui.ink`（禁止脱离组件树手工渲染）；**R6 界面渲染层禁止直写终端**——`tui.app.*` 组件树与 `tui.ink.widgets.*` 标准控件库不得出现 `sys.stdout`/`sys.__stdout__` 写入或 `print()`（终端 I/O 由 `_screen`/`ink.session` 等基础设施承担）；**R7 h 字符串 host 合规**——`h("<字符串>")` 必须是内置 host（box/text/static/spacer/app/fragment）或 `register_host` 注册的 host（如 static-lines）；**R8 事件输出消费者统一 ink 输出模型**——`OutputConsumer._write` 生产路径不得引用旧 `_LEVEL_COLORS`/`_RESET`（须经 `_LEVEL_STYLES` + `Line.render()`，回退直写与界面渲染共用输出模型）；
  - **新增测试**：ink 输出模型（8 例：兜底行为 + 写失败跳过）+ diff 渲染（11 例：StyledRun 行内高亮 / Line 输入截断 / str 兼容 / 字节基线 / 语法高亮路径）+ OutputConsumer（18 例：Style 渲染 / raw 原样 / 未知 level 回退 / 旧常量兼容 re-export / 生产路径零引用 / ANSI 闭合）。
- **TUI 全面控件化（阶段6，2026-08-16 方案B）** ✅ — 用户需求「所有 TUI 都要用 React Ink 控件跟布局实现」深化：界面组件树从「基础 TEXT/Column/Row + 手写 Line 行」进一步迁移为**标准控件库（widgets）表达**，视觉/交互/性能零回归：
  - **TopHeader → Gradient 控件**（`header.py`）——渐变标题经 `h(Gradient, {"styled": ...})` 渲染（styled 注入模式：宽屏 use_memo 缓存引用 / 窄屏截断后注入，与 `_gradient_runs` 视觉等价；`Gradient` 新增 `styled` prop）；
  - **StatusBar → Divider 控件**（`status_bar.py`）——分隔线经 `h(Divider, {"width", "char": "━", "style": sep_style})` 渲染（纯填充分隔线，与 sep_line 语义等价）；`Divider` 新增 `trailing` 右侧内容支持（左侧填充 + 右侧内容，行宽恒 = width——InputArea CPU/MEM/时间戳分隔线场景）；
  - **TraceView → ListView 控件**（`trace_view.py`）——台账左栏经 `h(ListView, ...)` 表达：受控光标（`cursor` prop，跟随/导航写回 `model.trace_selected`）、虚拟滚动（`height` 视口）、导航（↑↓/PgUp/PgDn/Home/End/g/G）、None 分隔行自动跳过、`renderItem(item, index, isSelected)` 三参选中态注入；`ListView` 扩展：受控 cursor / onNavigate / page/g / None 跳过 / enter 放行（无 onSelect 时）；
  - **UserSelectPopup → SelectInput/MultiSelect 控件**（`user_select.py`）——弹窗选项列表经标准控件表达（导航 ↑↓/j/k/g/G 由控件消费、Enter/Esc/空格协议经 onSelect/onSubmit/onCancel 回调承载、`renderItem` 保留单选 ▶/整行背景、多选 ●/○ 勾选、分栏说明视觉；★ 2026-08-18：/editmsg 多行 option_lines 已随「editmsg 独立协议」移除——UserSelectPopup 仅服务 user_select 工具，单行纯文本选项）；`SelectInput`/`MultiSelect` 扩展：vim 导航 j/k/g/G、onCancel（Esc）、onHighlight（选中变化）、renderItem、consumeAll（弹窗模式阻断输入框、Ctrl+C 放行）、无 onSelect 时 enter 放行；
  - **ToolCard → Panel 控件**（`toolcard.py`）——工具卡经 `h(Panel, {"border": 0, ...})` 表达（无边框模式：直接渲染内部 Column，「无边框裸行 + │ 引导线」Claude Code 极简视觉保持——2026-08-06 用户需求）；`Panel` 新增 `border=0/"none"/None/False` 无边框模式；
  - **CompletionPopup → SelectInput 控件**（`input_area.py`）——补全候选项经 `h(SelectInput, ...)` 表达：导航（↑↓/j/k）消费并写回 `completion.selected`（onHighlight）、`limit` = 锁定高度可见行数 + 底部补白（高度锁定防闪烁语义保持）、`renderItem` 复用候选项视觉（▶ 高亮 + match 前缀高亮 + 命令描述灰显）、Enter/Esc 放行（补全确认/关闭由 InputDispatcher 旧路径接管）；分栏说明模式（历史 user_select 场景，生产已迁移）回退 `_build_popup_lines` 旧路径；
  - **架构守卫扩展**（12 例）：**R9 界面组件禁止字符串 host**——`tui.app.*` 的 `h()` 第一参禁止字符串（必须用命名控件/布局门面，防绕过控件层）；**R10 界面控件化组件审计**——方案B 迁移清单（header→Gradient / status_bar→Divider / trace_view→ListView / user_select→SelectInput+MultiSelect / toolcard→Panel / input_area→SelectInput）AST 静态防回归；
  - **新增测试**：`test_gradient_styled.py`（styled 注入 6 例）+ `test_select_input_extended.py`（SelectInput/MultiSelect 扩展 12 例）+ `test_listview_extended.py`（ListView 扩展 9 例）+ `test_divider_extended.py`（Divider trailing 5 例）+ `test_completion_popup_widget.py`（CompletionPopup 控件化 6 例）+ `test_status_bar_divider_widget.py`（StatusBar Divider 3 例）；更新 test_header/test_trace_view（控件穿透/受控光标）等既有测试；
  - **性能/视觉保持**：Line 行数据（`_build_lines`/`tool_card_lines`/`_subagent_render`/`_build_status_runs`）作为 TEXT styled props 保留（快照缓存/引用稳定/diff 身份短路性能模型不动）；弹窗静态色/高度锁定/无边框工具卡等既有视觉决策全部保持。
- **行宽不变量（渲染错误修复）** ✅ — E-ROW-OVERFLOW（row 内容自然宽超容器时按 flexShrink 权重收缩子节点，默认 flexShrink=1 React Ink 标准语义，收缩后重新测量约束内部内容）、E-FILL-OVERFLOW（fill=False 容器被钳制时内部子节点按容器实际宽度重测）、E-OVERFLOW-GUARD（render_frame 行级截断防线——行宽恒 <= 文档宽，行级 diff 模型核心不变量）、E-COMMITTED-OVERFLOW（committed-chat 前缀复用路径的行宽守卫——reflow_committed 未执行/失败时 committed_lines 按旧宽度 wrap 产生超宽行，前缀复用不经 E-OVERFLOW-GUARD 直接进帧；修复：chat_view._paint 缓存重建时 O(n) 检查行宽标记 all_ok（非每帧，缓存命中零开销），render_frame 对 all_ok=False 前缀截断超宽行，正常行保持身份短路）；2000+ 模糊用例零超宽（嵌套 row/ZStack/边框/宽字符/绝对定位组合）
- **高级布局能力** ✅ — 百分比尺寸（width/height/min/max="50%" 相对可用尺寸解析）；flexWrap="wrap" 换行流式布局（行间距 = gap，超宽项截断）；position="absolute" 绝对定位（left/top/right/bottom 锚点、显式/百分比尺寸、left+right/top+bottom 拉伸、最近 position="relative" 祖先为基准、脱离正常流不占空间、两阶段布局——正常流测量 + 绝对定位第二遍放置）；布局容器组件（`src/tui/ink/widgets/layout.py`）：Row/Column/Center/Stack/HStack/VStack/Grid（CSS Grid 风格，列等宽 flexGrow）/ZStack（层叠，子节点绝对定位叠放）
- **控件库（widgets）** ✅ — `src/tui/ink/widgets/`：交互控件 SelectInput（单选列表）/TextInput（受控文本输入，含 placeholder/mask/光标）/MultiSelect（多选，space 切换）/ConfirmInput（y/n 确认）/Toggle（开关，space/enter 切换）/Checkbox（复选，`[x]`/`[ ]` 样式，受控/内部双模式）/Tree（树形，展开折叠 + 键盘导航）/ListView（虚拟滚动列表，只渲染视口内行——大列表 O(视口)）/Menu（垂直菜单——分组标题/禁用项/快捷键右对齐/循环导航）/SearchInput（搜索输入——实时过滤 + 结果列表选择 + limit 窗口）/Tabs（标签页——左右键切换 + 内容渲染）；展示控件 Spinner（时间基动画）/ProgressBar（进度条）/Table（对齐表格，支持表头/边框变体）/Badge（背景色块徽章，前景自动对比）/Divider（分隔线，可选标题）/Panel（带标题边框面板，BOX border 标准布局）/Breadcrumbs（面包屑导航——分隔符/active 高亮/maxItems 折叠）；布局门面 Box/Text（React Ink `<Box>`/`<Text>` 生态命名，与 host 等价）/Flex（显式 flexbox）/Spacer（flexGrow 撑开占位）；焦点管理 FocusGroup/Key（Tab/Shift+Tab 在多个可聚焦区域间切换，focus prop 注入互斥）；基于 use_input + use_state，同批连续按键状态经 ref 镜像正确累积（闭包陈旧修复），focus=False 不参与输入路由
- **渲染性能优化（宽度缓存 + 测量缓存）** ✅ — `StyledRun`（frozen 不可变）构造期一次性计算显示宽度（`__post_init__`），`Line.width` 惰性缓存 + `append` 增量维护，`_runs_natural_width` 复用 run 缓存宽度——热路径（diff/截断/画布转换/measure）免重复 `wcswidth_simple`；`_measure_cache`（PERF-14）按 `(ftype, props 引用, avail_w, fill)` 缓存 TEXT 测量结果——同 props 引用无变化帧布局零重建（1000 TEXT 无变化帧 78ms → 51ms，layout_tree 30ms → 6ms，-80%）；`_find_committed_chat` 未挂载快速路径（PERF-15）——无 committed-chat 的组件树每帧零 DFS；reconciler 叶子空子跳过（PERF-16）；绝对定位第二遍快速路径（PERF-17）——无 `position="absolute"` 节点的组件树（绝大多数）跳过第二遍整树遍历（1000+ 节点树省 ~10%）；`_normalize_children` 快速路径（PERF-18）——空/单 Element children 免列表分配 + 遍历（`h(TEXT, {...})` 无子级热路径）；reconciler 遍历迭代化（PERF-19）——`_traverse_functions`/`_attach_host_refs`/`_collect_input_hooks` 递归 → 显式栈（大组件树每帧数千节点省递归调用开销）；叶子内置 host 快路径（PERF-21）——TEXT/SPACER 等叶子跳过 context 清空/provider 检查/子调和（1000+ 叶子树每帧省数千次调用）；wrap 纯 ASCII 批量快路径（PERF-22）——单 run 可打印 ASCII（无空格/换行/控制字符）按 max_width 直接字符串切片（C 级，免 100k 字符逐字符展开 tuple + `wcswidth_simple` 调用），100k 字符 wrap 0.42s → 0.055s（~8x，超长行 wrap 性能边界从偶发超时转为稳定通过）；1000 行历史帧渲染 0.98ms → 0.53ms（~2x）；真实 TUI 场景（20 条消息 + 长回答，71 行 committed）无变化帧 ~2.5ms、流式增长帧 ~2.5ms（端到端预算测试固化）；渲染健壮性测试固化；**PERF-24（2026-08-05 渲染管线深度优化）** — `Line.render()` ANSI 渲染缓存（`_r` 字段：同 Line 对象跨帧复用零重建，`append` 修改 runs 时失效——全项目唯一修改点审计确认；实测 200 行 × 200 帧 diff 渲染 ~1.18s → ~0.1s 量级）；`Element.key`/`Fiber.key` 惰性缓存（调和热路径每帧访问，首次计算后 O(1)——Fiber props 变化经 `_set_props` 失效）；`_begin_work` 免 `list(children)` 复制（`_reconcile_children` 只读遍历 tuple）；ChatView `model.blocks[committed_count:]`切片 → 索引循环（免每帧切片分配）；InputArea `_input_snap_key` `props.get` 去重（history_search 局部变量一次提取）；完整渲染管线常规场景 **~0.84ms/帧**、2400 行大历史 **~0.99ms/帧**（含 diff+输出+光标，30Hz 预算 33ms 占 <3%）；性能回归测试固化；**PERF-25（reconciler 合并元数据遍历）** — `render()` 后置阶段原三趟独立全树遍历（`_attach_host_refs` ref 填充 / `_traverse_functions` effects 收集 / 
- **渲染性能优化（props 引用级缓存，阶段3）** ✅ — reconciler `_set_props` **内容相等时保持 props 引用稳定**（值比较；不可比较对象兜底更新引用）——修复前每帧 h() 重建 props dict → `_measure_cache` 引用级命中（`mc[1] is fiber.props`）恒 miss（实测 0%），无变化帧/流式帧对全部 host fiber 重做 props 解析；修复后 props 值不变帧引用稳定 → 命中率提升，无变化帧 ~3.9ms → ~0.8ms（200 条消息 6600 行历史，4-5x）；`_measure_cache` 结构补 styled 长度快照（styled 原地修改检测，与 `_wrap_cache` BUG-35 同契约）——TEXT 分支缓存命中先校验 `len(styled)`，兼容 `test_wrap_cache_invalidated_on_styled_list_mutation` 测试契约
- **恒定 30Hz 渲染 + 多会话隔离（架构修复）** ✅ — (1) 渲染线程**恒定 30Hz**：任何状态（空闲/无命令/无动画）都按 `render_interval`（1/30s）重建整帧，帧率不可被配置或参数改变（`TuiConfig.__post_init__` 强制 `render_interval` 恒为 1/30；`render(maxFps=...)` 不再改变帧率）；(2) hooks 会话状态由模块级全局变量收敛为可实例化 `HookContext`（每 `Reconciler`/`InkSession`/`render()` 一个，渲染期激活；`hooks._xxx` 读写经 PEP 562 代理到当前上下文）——同一进程可并存多个渲染会话而互不覆盖重渲染回调/router 注入/app control；context 注册表为进程级共享（`create_context` 与会话无关），逐 fiber context 缓存在 provider 值变化与父链变化时精确失效；(3) `Fiber` 全部渲染/布局扩展状态（`_measure_cache`/`_wrapped_lines`/`_committed_prefix`/`_parent_avail_h`/`_focus_id` 等）显式声明（消除动态挂载的隐式属性）；(4) 渲染期无副作用——`TraceView`/`TraceToolsView` 的光标/滚动/选中归一化改经 `use_effect` 提交，状态栏快照缓存改挂 `WeakKeyDictionary`（不再写 model 属性）；(5) 渲染互斥（`_render_lock`）串行化渲染线程常规帧与外部同步渲染，`flush_input_router` 改为**主动同步渲染一帧**（消除「猜帧数」时序窗口）
- **渲染正确性与健壮性修复（2026-10 架构审查）** ✅ — memo 组件从「删除」恢复时整棵子树 `deleted` 复位（`_revive_reused`/`_revive_subtree`——修复前 memo 短路保留的子树残留 `deleted=True`，effects/refs/`use_input` 全部丢失）；单帧重写行数超 `_MAX_REWRITE_ROWS` 降级为**受控全量重写**（clear + 单次全量写入，防病态大重写冻结 UI）；committed 内容区起始行由会话从 committed host 的 `layout_box.y` **显式注入**（不再硬编码 `_CONTENT_LINE_OFFSET`）；帧差异区间收集复用稳定前缀跳过（`_diff_runs`）；`use_input` 兼容包装缓存以 handler 对象为键（消除 `id()` 复用风险）；`StaticLines` 前缀缓存键用 lines 对象身份（非 `id()`）；`AppModel` 未声明字段访问记 warning（拼写错误可观测）；关键路径异常日志由 debug 提升为 warning（paint/effect/ref 回调失败不再静默）；`_measure` 的 TEXT 分支提取为 `_measure_text`、`InkRenderer` 的帧写入提取为 `_apply_frame_writes`（巨型方法拆分）
- **渲染错误修复（健壮性）** ✅ — OverflowError 捕获：`int(float('inf'))` 在布局/控件层 25+ 处不再崩溃（`_resolve_length`/`_resolve_height`/`_flex_grow`/`_flex_shrink`/`_resolve_padding`/border/margin/gap/flexBasis/`_abs_int`/百分比路径/各控件 width/height/limit/indent）；控件 items 不可迭代防御：ListView/SelectInput/MultiSelect/Menu 对 None/标量/字典 items 渲染安全（`_normalize_items` + ListView/Menu 防御回退空列表——Menu 修复前空 items 渲染期钳制越界抛 IndexError）；TextInput value 非 str 归一化；bytes 子级解码为文本（修复前 `str(b'x')` 渲染出 `b'x'` repr）；全组件模糊 500+ trial 零异常零超宽（含 inf/nan/畸形值/极端窄屏/增量漂移 8000 帧可见区合法）；渲染器模糊不变量测试（随机帧序列 + 迷你终端重放：屏幕内完整文档可见/高于屏幕末尾行可见/宽字符不丢失/resize 后重渲染，180+ 序列）与布局模糊不变量测试（随机组件树 + 极端值：行宽不变量 + 不崩溃，120+ 树）固化
- **渲染错误修复（阶段3，BUG-74/75/76）** ✅ — BUG-74：committed-chat 前缀缓存键缺布局宽度 `box.w`——终端宽度变化（reflow 前/失败）时 id(lines)/行数/y 均未变 → 缓存错误命中 → 旧宽度超宽行直接进入帧（E-COMMITTED-OVERFLOW 防线被缓存绕过）；修复：缓存键补 `box.w`，宽度变化强制重建并重新检查 all_ok；BUG-75：WRITE_LINE/NOTIFICATION/ERROR 文本含 `\n` 时未按行拆分——换行符嵌进单条 AnsiLine，frame 行内嵌字面换行符渲染成多条终端行，破坏行级 diff/光标定位（与 `build_assistant_line` 拆行语义不一致）；修复：三处均按 `\n` 拆行（空段保留空行）；BUG-76：物理缓冲漂移时缩短/增长后残留行未清除——`_rewrite_drifted`/`_grow_drifted` 对 doc 无对应内容的物理行用 `old_line is not None` 判断清除，但物理行旧内容不在 prev doc（`old_idx >= prev_h`，残留自更早帧）时 `old_line=None` → 误判为空 → 缩短后旧行残留在可见区（如 18→15 行缩短后 'zbzbzb' 残留）；修复：`old_idx >= prev_h` 时保守清除；渲染器纯帧序列模糊 400 seeds × 150 帧零残留；真实 App 树 + MiniTerm 重放 90 seeds × 300+ 帧可见区合法性零错误（回归测试固化）
- **富交互组件** ✅ — 在终端中嵌入可交互元素（选择列表、确认弹窗、进度条、开关、树、虚拟列表、焦点组），减少纯文本输出的信息密度（`src/tui/ink/widgets/` 已实现）
- **语法高亮增强** — 支持更多编程语言的代码块高亮，优化长代码段的折叠/展开机制
- **多面板布局** — 对话区/工具调用日志/系统状态分屏显示，便于调试与观察 Agent 行为
- **主题系统扩展** ✅ — 支持自定义配色方案，适配亮色/暗色终端环境（已内置 dark/light/high-contrast 三种基础主题 + nord/dracula/gruvbox 三种扩展主题；内置主题清单化、可按 Patch 禁用/覆盖）
- **动效与呼吸效果** ✅ — 标题栏✦/工具卡边框/状态栏分隔线/模型名/解析行 spinner/推理头/错误标记/补全弹窗/流式占位符/工具计数箭头/失败警示等 10+ 处时间基动效（time_glow 0.1s 桶缓存）；2026-08-05 新增 BEAUTY-18~24：user_select 弹窗标题/选中高亮/提示行/说明列呼吸（**已于 2026-08-05 静态化**——弹窗呼吸使弹窗行每帧随 time_glow 重写，Termux 等终端每帧刷新/错乱；现改静态色且不驱动动画循环，仅交互按键时重绘）、状态栏耗时/token/速度/CPU/MEM 呼吸、补全弹窗说明列/命令描述呼吸、工具 detail 呼吸、subagent 卡统计呼吸；2026-08-05 第二轮 BEAUTY-25~34：空状态欢迎行 ✦ 活跃期呼吸（空闲静态单例零重建）、工具卡标题图标运行中呼吸、思考块角色头 live spinner 化（💭→⠋⠙⠹…，关闭回退静态）、状态栏 thinking 阶段标签弱呼吸（…思考）、user_select 弹窗标题模式图标（单选 ▶ / 多选 ☑）、解析进度行 spinner 金色呼吸（178↔190）、标题栏版本号活跃期呼吸、live content 流式末尾指示 spinner、通知/子代理角色头 live 呼吸、subagent 组卡省略提示呼吸- **React Ink 对齐补齐（v6/v7 API 收官）** ✅ — 2026-10-01 对照官方 React Ink v6.8 / v7.1 API 全量审计并补齐缺口：**`renderToString(node, {columns})`**（同步渲染为字符串，不写 stdout/不建终端监听；终端相关 hooks 返回安全默认，layout/passive effect 触发的 state 更新经有界重渲染反映到输出）；**`measureElement`** 返回值补 `x`/`y`（对齐官方 `{x,y,width,height}`）；**`kittyFlags`/`kittyModifiers`/`resolveFlags`**（`src/tui/ink/kitty.py`，附 `encode_modifiers`/`resolve_kitty_options`/`enable_sequence`/`disable_sequence`）；**`render()` options 补齐** `maxFps`（覆盖 render_interval）/`isScreenReaderEnabled`（`useIsScreenReaderEnabled` 返回注入值）/`kittyKeyboard`（启用时写 `CSI > flags u`、unmount 写 `CSI < u`）/`onRender`（每帧回调 metrics）；**`Instance.waitUntilRenderFlush`**；**`useApp().exit(errorOrResult)`** 官方语义（`exit()` 无值 / `exit(value)` 令 `waitUntilExit()` 以该值 resolve / `exit(error)` reject；返回值对象身份跨渲染稳定 + 参数转发）；**`aria-*` 属性支持**（`src/tui/ink/accessibility.py`：`get_accessibility`/`screen_reader_text`，提取 `aria-label`/`aria-hidden`/`aria-role`/`aria-state`）
- **kitty 键盘协议（CSI-u 增强）** ✅ — 2026-10-01：`KeyEvent` 新增 `kitty_bits`/`event_type` 字段；`_read_csi_sequence` 按 `':'` 子参数分组解析（`groups`，兼容 `\x1b[<code>:<shifted>:<base>;<mod>:<event>u` 完整形式；标准形式与旧行为逐字节等价）；解析出口统一写入 kitty 元信息（**取自分组原始修饰值而非事件 `modifier`**——映射分支会重写 modifier，如 Ctrl+A→home modifier=0，用事件字段会丢超键/锁定键位）；`useInput` 的 `key` 补齐 `super`/`hyper`/`capsLock`/`numLock`（kitty 位掩码）与 `eventType`（press/repeat/release），`meta` 兼容 kitty meta 位（32）
- **`useAnimation` 官方语义 + 共享动画驱动** ✅ — 2026-10-01：由「简化版 `{frame,timestamp}`」重写为官方 `{frame, time, delta, reset}` + `{interval(ms,默认100), isActive}`；`src/tui/ink/_animation.py` 提供**共享驱动**（session `_render_frame` 每帧 `advance_animation()` 递增 tick 并通知订阅者 → 多个动画组件合并为一轮渲染，React Ink v7 语义）；`isActive=False` 归零返回，重新激活归零重启
- **视口/滚动控件（独立可选层）** ✅ — 2026-10-01：新增 `Viewport`（`src/tui/ink/widgets/viewport.py`）——固定高度窗口 + 内容偏移（受控 `offset` / 非受控内部 state）+ 滚动条（thumb/track 按内容比例）+ 键盘导航（↑↓/j/k/PgUp/PgDn/Home/End/g/G，经 `use_input`）；作为独立容器，不影响既有「内容流入 scrollback」流动模型
- **多面板分屏布局** ✅ — 2026-10-01：新增 `MultiPanel`（`src/tui/ink/widgets/multipanel.py`）——水平/垂直分屏、`panels[{title,content,size,...}]`、按比例 `flexGrow`、活动面板边框高亮 + Tab/Shift+Tab/方向键切换（受控 `activeIndex`/`onActiveChange` 或非受控）；对应 README 未完成项「多面板布局」
- **CodeBlock 折叠/展开 + 语法高亮增强** ✅ — 2026-10-01：`CodeBlock` 新增纯 props 折叠（`maxLines`/`expandable`/`expanded`，折叠提示行「还有 N 行，Enter 展开」；**保持无 hook、可直接调用**）+ 内置**多语言语法高亮**（`src/tui/ink/widgets/_syntax.py`：python/js/ts/go/rust/java/c/cpp/ruby/shell/sql/yaml/json/css/html + 别名归一化，关键字/字符串/注释/数字着色）；交互式折叠由新组件 `CollapsibleCodeBlock`（`use_state` + `use_input`，Enter/空格切换）承载
- **性能优化（wrap 多 run ASCII 快路径 / props 比较快路径）** ✅ — 2026-10-01：`wrap_runs_by_width` 新增**多 run 纯 ASCII 快路径**（所有 run 均非空可打印 ASCII：每字符宽 1、空格断点经 `str.rfind`、按 span + `bisect` 二分定位切片产出，免通用路径逐字符 tuple 展开；10 万字符 / 2500 run 实测 **~55ms → ~6ms（~9x）**）；`Reconciler._set_props` 新增**长度不等快路径**（props 长度不同直接判不等，免大 props 深比较）；含与通用算法逐例等价性回归测试（200 随机用例）
- **React Ink 框架独立测试套件** ✅ — 2026-10-01：新增 `tests/test_tui/ink/`（68+ 例）——`renderToString`（隔离/layout effect/不写 stdout）、kitty（常量/解析/key 映射）、`useAnimation`（语义/驱动/重置）、`measureElement`（x/y/畸形防御/组件内）、`accessibility`、`Viewport`（窗口/偏移/滚动条/键盘）、`MultiPanel`（分屏/高亮/Tab）、`CodeBlock`（折叠/高亮）、`render()` options（maxFps/kitty/screen reader/onRender/exit 值语义）、wrap 快路径等价性
- **review 修复（一次性审查发现的缺陷）** ✅ — 2026-10-01：`memo(Comp)` 对常规单参组件必抛 `TypeError`（包装函数恒双参调用）→ 按 ref 是否存在单参/双参调用；`render({isScreenReaderEnabled})` 全局开关退出后不还原（跨会话泄漏）→ 保存/还原（unmount/cleanup/启动失败路径）；`useAnimation` tick 通知经 `_schedule` 落 force 通道会打破 30Hz 节流（无节流忙循环）→ advance 不再通知订阅者（全程 30Hz 已保证动画推进），新增独立 `notify_animation_listeners`；`Viewport`/`MultiPanel` 的 `height` 与 BOX 边框语义冲突（内容越框/底框被覆盖）→ `height` 明确为「可见内容行数/面板内容行数」，经 `_outer_height` 换算 BOX 总高（+边框+内边距），内容列预算扣除边框；`_event_key` 的 `ctrl`/`shift` 补 kitty 位掩码（CSI-u Ctrl+字母映射后 modifier 被置 0 丢标志）；掩码事件 `_MaskedCharEvent` 透传 `kitty_bits`/`event_type`；`_syntax` 去掉死变量 + 未知语言不再回退 `#` 注释；`Transform` 的 `accessibilityLabel` 判定移出条件式 hook 调用；CodeBlock 边框变体收敛到 `_paint_border._BORDER_CHARS`（补 dashed/singleDouble/doubleSingle）；控件回调统一走 `_widget_common._call`（带日志）；清理死代码（`_traverse_functions.include_self` 分支、session 未用字段、`hooks.__all__` 中的可变状态变量）；补 `element.__all__` 的 `FRAGMENT`；`_pop_starved_state_cmd` 弹出后 `not_full.notify_all()`
- **模型选择器（2026-10-09）** ✅ — 用户需求「增加模型选择器，可以增加、选择、编辑模型（url/key/name 等）」+「没有内部模型，新增时先让用户选提供商、填模型名，url 自动给定，用户只要给 key」：新增全屏视图 `ModelView`（`src/tui/app/model_view.py`，`model.fullscreen == "model"`，`/models` 命令或 `/model` 无参数打开，经清单独立条目 `ui_view_model` 注册、`ui_view` 插件装配）——列表 = **模型档案**（`model_profiles`，**模型列表唯一来源**；RC 顶层 `models` 字段已随之移除，内置 provider 模型只作元数据不进入列表，空态提示「暂无模型档案（未配置 model_profiles）· 按 a 新增」）；**选择**（↑↓/jk + Enter 应用——写 `active_model_profile` 切换当前生效档案，LLM 参数全部从档案解析）、**新增**（`a`：① 选**提供商**（选择界面，deepseek/anthropic/glm/mimo/custom）→ ② 填**模型名** → ③ 填 **API 密钥**，**接口地址按提供商自动填好**，显示名留空则用模型名）、**编辑**（`e`）、**删除**（`d` 确认）；`/` 搜索 + `n`/`N`/`p` + `f` 过滤、`?` 帮助面板（数据表 `model_keymap`）、`y` 复制（OSC52）、字段输入（字符 / 退格 / Ctrl+U / Enter / Esc）。数据源为 RC 顶层新键 `model_profiles` + `active_model_profile`（`CONFIG_KEYS.MODEL_PROFILES` / `ACTIVE_MODEL_PROFILE`；纯逻辑模块 `src/config/model_profiles.py` 负责字段元数据 / 校验 / 持久化 / 应用 / 提供商候选）；`/model <Tab>` 补全并入档案模型名、`/config` 界面可编辑 `model_profiles`；**模型列表唯一来源 = 模型档案**（`configured_models` 返回档案模型名，去重保序；RC 顶层 `models` 字段已删除，加载时自动清理历史遗留键）——`Ctrl+N`、`/model`（无参数选择器 / 序号 / 名称）与 `/model <Tab>` 补全**只在模型档案中选择**（不再列出内置 provider 模型；未配置时提示「请先 /models 新增」）；`/config` 的 MODEL 候选、`config_port.get_models()` 与 `plugins/config.models()` 同步收敛到档案；测试 `tests/test_model_view.py`（65 例）固化
- **LLM 访问入口收敛（2026-10-09）** ✅ — 用户需求「删除 CHAT_API_KEY 等环境变量和老配置，只能通过新加的东西访问 llm」+「如果第一次没有配置 models 就要显示让用户配置」：**LLM 访问参数（API 密钥 / 接口地址 / 模型名 / 提供商）唯一来源 = 模型档案**（`model_profiles` + `active_model_profile`）——删除环境变量 `CHAT_API_KEY` / `CHAT_MODEL` / `CHAT_BASE_URL` / `CHAT_LOW_MODEL` 与 RC 旧键 `api_key` / `base_url` / `model` / `provider` / `low_model` / `models`（加载配置时自动清理并落盘；`config.API_KEY` / `BASE_URL` / `MODEL` 改为经 `src/config/model_profiles.py` 的 `current_api_key` / `current_base_url` / `current_model` 从当前档案解析，`TOKEN_PRICES` 缺省回退当前档案 provider 的内置价表）；`CONFIG_KEYS` 移除 `MODEL`（`/config` 不再出现 model/provider/base_url/api_key 条目）、新增 `ACTIVE_MODEL_PROFILE`；`ConfigPort.get_low_model()` / `plugins.config.low_model()` 移除，子 Agent 的「低优先级模型」改为取模型档案中当前模型之外的首个档案（`src/tools/subagent.py`）；`-m/--model` 改为按模型名/显示名**匹配已有档案并设为当前**（未匹配列出可用档案并提示 `/models` 新增）；`web_search` 的密钥同样取自当前档案；**首次启动未配置任何档案时自动显示配置引导并打开模型选择器**（`InteractiveLoop._maybe_prompt_model_setup`——聊天区醒目提示「① 选提供商 ② 填模型名 ③ 填 API 密钥」+ 打开选择器，Esc 可稍后配置；单次模式 `-p` 则提示并退出），新增档案保存后自动设为当前生效；`config.loader ↔ config.model_profiles ↔ config.schema` 保持零循环依赖（架构测试校验）；测试 `tests/test_model_onboarding.py`（8 例）+ `tests/test_config_schema.py` / `tests/test_model_view.py` / `tests/test_special_keys.py` 等同步更新
- **TUI 体验增强（2026-10-07）** ✅ — 欢迎屏卡片化（圆角边框 + 分支/上下文等环境信息，窄屏自动回退无边框；splash 与空态同卡）、状态栏信息增强（**段级分隔**——工具计数改为 `⚙ n/m` / `✔ m` / `✔ n/m ✖ f`，并修复段内多 run 被 ` · ` 拆开的既有缺陷；同日新增的 `provider`/`theme`/`context` 段已按用户需求删除，状态栏仅保留模型名/工具计数/耗时/消息数/token/速度）、工具卡标题元信息（完成后显示 `· 耗时 · N 行`，失败追加红色 `· 失败`；运行中保持极简）、输入区体验（模式行图标 ◇/▸/▣、占位提示轮播扩充至 6 条）、操作优化（`Ctrl+Z` 撤销 / `Ctrl+Y` 重做输入编辑，连续同类编辑按 0.8s 窗口合并为一个撤销单元）、帮助视图增强（`/` 搜索 + `n`/`N` 跳转 + 匹配高亮、Enter/空格 折叠/展开分组、头部搜索态提示）、新增 nord / dracula / gruvbox 三套配色主题（内置主题清单化、可按 Patch 禁用/覆盖）
- **TUI 性能专项（2026-10-07）** ✅ — 五个方向实测优化：
  - **启动/初始化**：`src.core` / `src.core.internal` / `src.api.escape_monitor` / `src.renderer` 四个包 `__init__` 改 **PEP 562 惰性导出**——导入轻量子模块不再连锁加载 agent/tools/commands/Rich 渲染链（`import src.renderer.ansi` 不再拉入 `rich`）；冷启动导入实测 **~930ms → ~424ms（-54%）**。同时消除「`base_agent` → `sandbox_manager` → `internal.shared` → `internal.agent` → `subagent` → `base_agent`」的隐性导入顺序契约（原靠 `agent_builder` 先加载侥幸避开循环导入，任意入口导入现均安全）。
  - **渲染帧管线**：`registry.get_host` 读路径**去锁**（GIL 下 `dict.get` 原子；注册/注销写路径仍持锁）+ `_layout_measure`/`components` 的 host 查询提升为**模块级导入**（原每帧上百次函数内 `from .registry import get_host` 模块查找）；完整组件树单帧 **4.8ms → 2.4ms（-50%）**。
  - **输入与补全**：路径补全改**一次 `os.scandir`** 建「名 → 是否目录」映射（原排序 key 对每个匹配逐项 `os.path.isdir`，Cygwin 上每项一次 stat）；400 项目录实测 **262ms → 12ms（~22x）**，匹配数 ≤ 32 的小目录仍走逐项路径（避免为空结果白扫整目录）。
  - **流式 Markdown 预览**：`ansi.table._wrap_runs` 增「整段宽度 ≤ 列宽」**单行快路径**（表格单元格绝大多数形态免逐字符测宽）、`TablePreviewCache._reuse_data` 纯追加改 **C 级切片等值比较**；**活动行「无行内触发字符」增量判定**（`_note_paragraph_format_chars` 按 `startswith` 增量维护段落最后一个触发字符位置，纯文本窗口直接等价构造单 Run 行，免每帧对 4096 字符窗口重复 `isdisjoint` 扫描；与 `render_inline` 快路径同源同产出）；长纯文本段落流式预览 **176ms → 36ms（-80%）**，表格流式预览 **126ms → 65ms**，混合文档 **357ms → 137ms**。
  - **内存**：`StyledRun`（`src.tui.ink.output`）/ `Run` / `Style`（`src.renderer.ansi`）/ `Style`（`src.tui.core`）加 `slots=True`（无实例 `__dict__`）；提交历史每行常驻内存 **540B → 460B（-15%）**。
  - 回归测试：新增 `tests/test_tui_perf_improvements.py`（120 例：惰性导出/导入顺序无关性/表格快路径等价/补全扫描/内存 slots/端到端帧渲染预算）；另修复 `tests/test_completion_shrink_cursor.py` 中依赖「已配置模型」的环境相关硬编码帧高断言（改以放大态帧高为基准，语义更强的等高契约）。

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
