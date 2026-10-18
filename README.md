# DeepSeek-cli `v2.2.0`

全异步、高可扩展的 AI 聊天服务后端，支持多模型适配、增量流式 Markdown 渲染、工具调用系统、上下文压缩和终端交互界面。

---

## 快速开始

### 1. 安装依赖

项目使用 **Python ≥ 3.9**。

#### 方式一：一键安装（推荐）

```bash
# 安装全部核心依赖
pip install httpx Pygments beautifulsoup4 chardet aiofiles qrcode \
            Pillow psutil PyYAML typing_extensions

# 可选加速（推荐）
pip install orjson h2

# 安装开发依赖（测试 / 代码检查 / 提交钩子）
pip install pytest pytest-asyncio pytest-xdist pytest-cov ruff mypy pre-commit orjson h2
```

#### 方式二：通过项目安装（自动读取 pyproject.toml，推荐）

```bash
# 安装核心依赖
pip install .

# 核心依赖 + 高性能 JSON / HTTP2
pip install ".[perf,http2]"

# 安装开发依赖（测试/代码检查等）
pip install ".[dev]"
```

**依赖库说明**：

| 包 | 用途 | 必需 |
|---|---|---|
| `httpx` | HTTP 请求库 | ✅ |
| `Pygments` | 代码语法高亮 | ✅ |
| `beautifulsoup4` | HTML 抓取解析（`web_fetch`） | ✅ |
| `chardet` | 字符编码检测（`read`） | ✅ |
| `aiofiles` | 异步文件操作 | ✅ |
| `qrcode` | 终端二维码生成（微信 ClawBot 登录） | ✅ |
| `Pillow` | 图像解码/归一化（`read_image`、图片上传） | ✅ |
| `psutil` | 状态栏 CPU/内存、Shell 探测（缺失时回退平台实现） | ✅ |
| `PyYAML` | 插件清单 / 技能 frontmatter（缺失时回退内置 YAML 子集解析） | ✅ |
| `typing_extensions` | Python < 3.11 的 `dataclass_transform` 支持 | ✅（<3.11） |
| `orjson` | 高性能 JSON 编解码（缺失时回退标准库 `json`） | 可选 |
| `h2` | HTTP/2 支持（配置 `enable_http2` 生效前提） | 可选 |

> 可选依赖缺失时程序仍可运行，只是对应能力降级（由 `pip install ".[perf,http2]"` 启用）。

---

### 2. 配置

#### 方式一：配置文件 + 模型选择器（推荐）

**LLM 访问参数（API 密钥 / 接口地址 / 模型名 / 提供商）的唯一来源是模型档案**
（`model_profiles`）——首次运行未配置档案时会自动打开模型选择器引导配置
（`/models` 可随时打开；新增只需三步：① 选提供商 → ② 选/填模型名（可从该
提供商内置模型列表 ↑↓ 选择，也可输入自定义名）→ ③ 填 API 密钥）。

创建配置文件 `~/.chat_config/chatrc.json`：

```json
{
    "model_profiles": [
        {
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "api_key": "sk-你的API密钥",
            "base_url": "https://api.deepseek.com/v1/chat/completions",
            "name": "DeepSeek Flash",
            "temperature": "0.3",
            "reasoning_effort": "high",
            "multimodal": "true"
        }
    ],
    "active_model_profile": 0,
    "reasoning_effort": "max",
    "temperature": 0.2,
    "max_context_chars": 3000000,
    "max_output_chars": 1000000,
    "max_retries": 10,
    "retry_base_sec": 30,
    "empty_response_max_retries": 0,
    "max_session_messages": 0,
    "keep_recent_messages": 0,
    "theme": "dark",
    "max_context_tokens": 1000000,
    "summary_token_budget": 2000,
    "auto_force_compress_threshold": 600000,
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
  `base_url` / `name`，以及可选的 `temperature`（0.0~2.0）与
  `reasoning_effort`（low / medium / high / max）；`base_url` 留空则用提供商
  默认地址；生成参数留空则沿用全局 `temperature` / `reasoning_effort` 默认值）。
  `multimodal`（`"true"` / `"false"`，默认 `"false"`）声明该模型是否支持
  多模态读图：`true` 时才会向模型加载 `read_image` 工具，`false` 时该工具不
  进入工具集、执行也会被拒绝（在 `/models` 界面「多模态读图」字段设置）。
- `active_model_profile`：当前生效档案下标（由模型选择器写入，`-1` = 未选择）。
- `empty_response_max_retries`：模型返回**完全空**响应（无正文/无推理/无工具
  调用）时的自动重发上限。`0`（默认）= 无限重试，直到拿到有效回答或用户
  `ESC` 中断——空响应不是「调用失败」而是「没拿到回答」，请求健康、尚未渲染
  任何内容、重发幂等安全，因此**不占用 `max_retries` 瞬时错误预算**，也
  **不输出错误**（连续多次未恢复时给一条弱提示）；等待按 2→4→8→15 秒封顶。
  `>0` = 有界重试（内部调用方如压缩摘要、SubAgent 恒为有界，快速失败）。

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
| `deepseek` | `DeepSeekAdapter` | DeepSeek 官方 API（默认）。内置模型：`deepseek-flash`（V4.1 Flash，最新一代、原生多模态视觉）、`deepseek-v4-pro`（V4-Pro 旗舰）；旧名 `deepseek-v4-flash` / `deepseek-v4-flash-vision-exp` 已下线并由 V4.1 Flash 承接（保留以兼容旧配置）。`deepseek-chat` / `deepseek-reasoner` 已退役 |
| `custom` | `OpenAICompatAdapter` | 任意 OpenAI 兼容 API（OpenAI / 通义千问 / 本地 Ollama 的 OpenAI 兼容端点等），自动检测 reasoner 模型 |
| `anthropic` | `AnthropicAdapter` | Anthropic Claude 系列模型（API 格式自动转换）。内置模型：`claude-opus-5-5`（默认，官方推荐）、`claude-sonnet-5-5`、`claude-haiku-5-5`、`claude-fable-5-1`、`claude-opus-5`、`claude-sonnet-5`、`claude-haiku-4-5-20251001`、`claude-opus-4-8`、`claude-sonnet-4-6` |
| `glm` | `OpenAICompatAdapter` | 智谱 GLM（open.bigmodel.cn）。内置模型：`glm-5.3`（默认，旗舰）、`glm-5.3-flash` / `glm-5.3-flashx`（原生多模态）、`glm-5.2`、`glm-5.1`、`glm-5`、`glm-4.7` |
| `mimo` | `OpenAICompatAdapter` | 小米 MiMo（token-plan-cn）。内置模型：`mimo-v2.6-pro`（默认）/ `mimo-v2.6-flash` / `mimo-v2.6-pro-ultraspeed`（全模态理解）；`mimo-v2.5-pro` / `mimo-v2.5` 将于 2026-10-21 下线（保留候选便于迁移） |

> 上表「内置模型」即 `/models` 新增档案时「模型名」字段的候选来源（随各提供商
> 官方最新模型更新，候选行标注「默认」与计费单价；`deepseek` / `anthropic` 为
> 美元 / 百万 tokens，`glm` / `mimo` 以人民币计价故不内置单价）；候选之外仍可
> 直接键入任意自定义模型名。


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

2. **read_image 工具**（按模型开关加载）：模型档案的「多模态读图」
   （`multimodal`）为 `true` 时才向该模型加载 `read_image` 工具；为 `false`
   时工具不进入工具集、执行也会被拒绝。开启后 AI 代理可主动调用
   `read_image` 读取本地图像（支持分块/灰度/旋转/翻转/缩放操作），多模态
   模型直接看到 base64 图片（图片按原始尺寸返回，不做自动缩放）。
   在 `/models` 编辑模型档案的「多模态读图」字段即可切换（默认 false）。

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
| `Esc` | 清空当前输入框内容（空闲时单次按下；AI 生成中为中断当前回复） |
| `Ctrl+G` | 使用 vim 编辑器编辑当前输入内容（支持 $EDITOR 环境变量） |
| `Ctrl+O` | 编辑当前会话中的已有消息（触发 `/editmsg` 命令） |
| `Ctrl+N` | 循环切换对话模型（**只在模型档案中选择**：`/models` 新增的模型档案；未配置时提示先用 `/models` 新增） |
| `Ctrl+P` / `↑` | 浏览输入历史（上一条） |
| `↓` | 浏览输入历史（下一条） |
| `Ctrl+R` | 反向历史搜索（配置门控；默认重试上一轮） |
| `Ctrl+T` | 循环切换配色主题（dark/light/high-contrast/nord/dracula/gruvbox/tokyo-night/catppuccin） |
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
| `F1` / `Ctrl+/` | 打开/关闭帮助速查视图（命令行上 Enter 直接执行、Tab 填入输入框） |
| `F2` | 打开/关闭**命令面板**（模糊检索全部斜杠命令：输入即筛选，↑↓/PgUp/PgDn 选择，Enter 执行，Tab 填入输入框，Esc/F2 关闭；最近使用命令置顶） |
| `F11` | 打开/关闭文件变更审查器（沙盒 diff / 回滚 / 历史时间线 / 统计；打开后按一次 F11 或 Esc 即关闭） |
| `F12` | 打开/关闭会话日志 / 投影浏览器 |
| `Ctrl+H` | 打开/关闭轨迹视图（DSH 风格台账 + 检查器） |

> **命令面板（F2）**：无需记住命令名——输入任意关键词即可按名称 / 别名 /
> 描述 / 分组做子序列模糊匹配；右侧实时显示命令详情、别名、同组命令与操作
> 方式；`Enter` 立即执行，`Tab` 只填入输入框（便于补充 `/load <ID>` 等参数）。
> 最近执行过的命令会在空查询时置顶（★ 标记），面板在 AI 流式输出期间也能
> 立即打开。状态栏同时展示工作区信息（`⎇ 分支  当前目录`，可在插件清单中
> 按 `status_segment_workspace` 禁用/替换）。

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
| `/models` | — | **模型选择器**（全屏界面：↑↓/jk 选择 · Enter 应用 · `a` 新增 · `c` 复制 · `e` 编辑 · `d` 删除 · `r` 刷新模型档案）。**新增只需三步**：① 选提供商（接口地址自动填好）→ ② **选/填模型名**（进入「模型名」字段后 `↑`/`↓` 从**该提供商内置模型列表**选择——候选行标注「默认」与计费单价，亦可直接键入自定义模型名）→ ③ 填 API 密钥（显示名留空则用模型名）；表单还提供 **温度**（0.0~2.0，留空沿用全局默认）、**思考级别**（low/medium/high/max，留空沿用全局默认）与 **多模态读图**（true/false：true 才加载 `read_image` 工具；默认 false），保存后按模型生效（全局 `/temperature`、`/reasoning` 命令已由本界面取代）；列表行同时显示模型名与接口主机，选中详情行展示完整模型名/提供商/接口地址/脱敏密钥/温度/思考级别/多模态读图；字段输入支持光标编辑（←/→ · Home/End · Delete · Ctrl+A/Ctrl+E · Ctrl+U）；`/` 搜索 · `n`/`N`/`p` 匹配 · `f` 过滤 · `?` 帮助（表单内亦可） · `y` 复制信息 · Esc 关闭 |
| `/cost` | — | 查看 token 用量和费用 |
| `/config` | — | 显示/编辑程序配置（无参数打开**独立配置界面**：↑↓/jk 选择、Enter 编辑、Esc 关闭；枚举/布尔/模型走**选择界面**、list/dict 子 JSON 走**递归结构化编辑界面**（Enter 逐层下钻嵌套 · 标量编辑 · a 追加 · d 删除 · Esc 逐级返回、顶层保存）、数值/字符串走输入界面；亦支持 `show`/`list`/`get <键>`/`set <键> <值>`/`reset <键>`） |
| `/load <ID>` | — | 加载保存的对话 |
| `/sessions` | — | 列出所有保存的对话 |
| `/export [路径]` | — | 导出当前对话为 Markdown（含 SubAgent 聊天信息） |
| `/theme <名称>` | — | 切换配色主题（dark / light / high-contrast / nord / dracula / gruvbox / tokyo-night / catppuccin） |
| `/changes` | — | **文件变更审查器**（全屏界面，**F11** 快捷打开/关闭：打开后按一次 **F11 或 Esc** 即关闭）：左栏文件列表按 `t` 切目录树、`m` 切消息维度、`s` 轮换排序（路径/次数/最近/索引）、`T` 循环类型过滤；右栏按 `d` 在差异 / **历史时间线**（该文件逐次修改 + diff）间切换、`i` 统计面板、`r` 回滚预览；`x` 回滚该文件 · `u` 撤销回滚 · `X` 回滚全部 · `R` 回滚到消息索引 · `y` 复制路径 · `c` 复制差异 · `w`/`W` 导出变更报告（md/json）· `/` 搜索 · `f` 过滤 · `?` 帮助 · Esc/F11 关闭（可加文件名过滤） |
| `/sandbox` | — | **文件沙盒概览**（全屏界面，Esc/F11 关闭）：统计区块（概览 / 变更类型 / 工具分布 / 缓存与回滚）+ `r` 刷新 · `c` 清空沙盒（再按确认）· `h` **消息维度历史**（`/sandbox history`：按消息浏览变更并 `R` 回滚到消息）· `l` **变更记录流水**（`/sandbox records`：逐条变更 + 差异 + `x` 回滚）· Esc/F11 关闭 |
| `/logs` | — | **会话日志 / 投影浏览器**（全屏界面，**F12** 快捷打开）：左栏事件流（`#seq 图标 类型 时间 摘要`）+ 右栏按 `v` 循环四模式——事件详情（data 展开）/ 消息投影（`derive_messages` 模型历史）/ 投影状态（`turnBoundary` 等投影单元 snapshot）/ 一致性校验（`SessionLog.verify`「模型可见即已记录」）；`F` 跟随最新（随会话日志增长**实时刷新**）/ `r` 刷新 / `/` 搜索 · `n`/`N`/`p` 定位 · `f` 过滤 · `?` 帮助 · Esc 关闭 |
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
| `read` | rd | IO | ✅ | 读取文件内容，支持指定行号范围、自动编码检测，可显示行号（默认关闭） |
| `write` | wr | IO | ✅ | 覆盖写入文件，自动创建父目录，原子写入 |
| `edit` | ed | IO | ❌ | 精确替换文件中的文本（old_string → new_string），支持 use_regex 正则替换 |
| `grep` | gp | 搜索 | ✅ | 在项目源码中搜索正则表达式，自动排除非源码目录 |
| `glob` | gl | 搜索 | ✅ | 按通配符模式查找文件和目录，支持深度控制 |
| `ls` | ls | IO | ✅ | 列出目录内容，支持详细格式和隐藏文件显示 |
| `bash` | bs | 执行 | ❌ | 执行 shell 命令（安全沙盒保护，禁止替代专用工具） |
| `bash_opt` | bo | 执行 | ❌ | 按 task_id 操作后台 bash 任务：read（读取当前已产生的全部输出并清空缓冲，立即返回；只读观察、不接管任务，任务结果仍会在轮次结束时正常回灌；缓冲有内存上限，单次返回超长时只给尾部最新内容并标注 `truncated`/`dropped_chars`）/ wait（等待完成取输出）/ kill（杀死整个进程树：进程组 + 全部递归后代；杀完后校验进程是否真正退出、未死自动补杀，最多 3 轮尝试，残留进程在结果中报告）/ stdin（发送文本输入）/ keys（发送光标键盘消息，自动路由：目标进程有 GUI 窗口时作为窗口级键盘消息注入该窗口，否则写入终端 PTY/stdin，跨平台 ANSI/VT100；支持 `ctrl+c` 等修饰键组合、`ctrl_c`/`ctrl-c` 紧凑写法、`esc`/`del`/`pageup`/`return` 等别名、单个字符与 f1-f20）/ screenshot（把该命令进程树的窗口，或整个屏幕，截图存为 PNG，需 path；可选 crop 只截取窗口内的像素区域，格式 `x,y,width,height`；可选 window 选择目标窗口、grid 叠加坐标参考线（省略=不叠加，0=自动步长）；`screen=true`/`'primary'`/序号 做整屏 / 多显示器截取（不需要进程句柄）；`element`+`margin` 只截某控件区域（可外扩/收缩）；命中已最小化/不可见的窗口时直接报错并提示先 restore，不产出占位小图）/ windows（列出该进程树的全部窗口及句柄/标题/几何/Z 序）/ window（控制窗口：activate/maximize/minimize/restore/close/move/resize/fit/always_on_top/not_on_top/get_geometry/save_geometry/restore_geometry）/ elements（列出窗口内控件：名称/类型/类名/矩形/可用状态，含窗口内坐标，可直接用于 click；Windows 优先走 UI Automation（Chrome/Electron/Qt/WPF/UWP 等自绘界面也能枚举），不可用时回退经典 Win32 子窗口；element 可作过滤子串，支持 `'#N'`/`'text:子串'`/`'id:子串'`（自动化 ID）/`'class:子串'`/`'type:edit'` 与中文标签）/ locate（在窗口截图里定位：给 `template=模板图片路径` 做模板匹配（支持 `min_scale`/`max_scale`/`scale_steps` 多尺度），或给 `query=文字` 做 OCR（Windows 系统自带 OCR / 其它平台 tesseract），返回与输入 op 同源的坐标可直接点击；`crop` 限定搜索区域，适合没有可枚举控件的界面）/ wait_window（等待窗口出现，按 window 选择器 + timeout 秒）/ clipboard（读写系统剪贴板：`clipboard_action=set/get/clear/append`）/ sequence（一次调用按序执行多个动作：`actions` 数组，步骤可为输入动作或 wait/screenshot/window，`on_error` 决定遇错停止或继续）/ move（绝对 x/y，或相对当前光标的 dx/dy 像素偏移）/ hover（移动到目标点停留 `dwell` 秒，触发 tooltip / 悬浮菜单）/ click（左中右键、双击，需 button/count；`hold` 长按、`interval` 多击间隔）/ drag（拖动，需 to_x/to_y，可选 from_x/from_y/duration/steps）/ scroll（滚轮，direction+amount）/ key（窗口级按键，如 `ctrl+s`，与 keys 共用键名规则；`phase` 可选 press/down/up 分别发送按下与弹起）/ type（向窗口逐字符输入文本，或用 `via='clipboard'` 走剪贴板粘贴、`paste_key` 指定粘贴键、`restore_clipboard=false` 保留剪贴板内容（恢复前会留出目标程序读取剪贴板的时间）；坐标/文本均可按控件名定位——输入 op 传 `element='确定'` 即用控件中心，type/key 会先点击该控件聚焦（该聚焦点击是真实左键点击，会在结果里以 focus_click 回报））；release（释放按下的键与鼠标按钮：`keys`/`buttons` 可只释放指定目标，留空释放全部——清理 key/click 的 `phase='down'` 留下的悬空状态）；坐标以窗口截图左上角为原点（与 screenshot 产物一致），除像素整数外也支持 `'center'`/`'50%'`/`'center+20'`/`'left+20'`/`'bottom-30'` 等语义坐标；`method` 可选 auto/sendinput/message（仅 Windows 生效）；`wait_for='change'/'stable'` 可等界面变化或稳定（stable 会忽略光标闪烁级别的微小噪声，并在 `ignored_change` 说明），`diff=true` 回传注入前后截图差异 |
| `cp` | cp | IO | ✅ | 复制文件或目录，保留元数据，支持沙盒撤回 |
| `mv` | mv | IO | ✅ | 移动文件或目录，支持跨文件系统 |
| `rm` | rm | IO | ❌ | 删除文件或目录（删除前自动备份到沙盒） |
| `mkdir` | mk | IO | ✅ | 创建目录，支持递归创建父目录 |
| `read_image` | ri | IO | ✅ | 读取图像文件内容，支持分块读取与图像操作（灰度/旋转/翻转/缩放）；图片按原始尺寸返回给模型（不做自动缩放）；**仅当模型档案「多模态读图」=true 时加载**（`/models` 设置，默认 false；false 时工具不进入工具集且执行被拒绝）；多模态 base64 图片。发送前客户端会自动瘦身（折叠旧图+压缩大图+编码缓存），缓解多图场景每轮重传导致的请求卡顿 |
| `web_search` | ws | 网络 | ❌ | DeepSeek 官方原生联网搜索（Anthropic 兼容 Messages API + web_search_20250305），返回来源列表（标题/URL/摘要） |
| `web_fetch` | — | 网络 | ✅ | 获取指定 URL 的网页全文（自动提取正文，SSRF 防护，仅 http/https） |
| `user_select` | us | 交互 | ❌ | 向用户显示交互式选择界面（单选/多选/超时回退/非交互回退，选项可带说明，TUI 中高亮选项时说明显示在右侧；支持并发提问——多个问题可同一轮同时弹出、以 tab 形式一起回答） |
| `subagent` | sa | Agent | ❌ | 并行派发子 Agent 执行独立任务（支持类型：map/review/plan/execute）；直接后台执行，立即返回 `{"task_id": "sa-xxx"}` JSON，完成后结果自动插入对话（或由 subagent_opt 管理）。后台 subagent 仅主 Agent 可派发 |
| `subagent_opt` | so | Agent | ❌ | 按 task_id 操作后台 subagent 任务（subagent 直接后台启动）：read（读取当前状态与已产生的结果，立即返回）/ wait（等待完成取结果，timeout 秒，默认 300/0 无限）/ kill（取消后台 subagent 任务）/ wait_all（等待**所有**后台 subagent 任务完成，返回每个任务结果的 JSON 数组，无需 task_id）。仅主 Agent 可用 |
| `skill` | sk | 技能 | ✅ | 加载技能（skill）的完整指令（技能目录随系统提示词注入，任务与技能匹配或点名技能时调用） |

### 工具分类

| 分类 | 工具 | 说明 |
|------|------|------|
| **文件 IO** | read, write, edit, ls, cp, mv, rm, mkdir, read_image | 读写文件、目录操作、文件管理、图像读取 |
| **代码搜索** | grep, glob | 正则搜索源码、通配符查找文件 |
| **命令执行** | bash, bash_opt | 安全沙盒中执行 shell 命令；按 task_id 操作后台 bash 任务（bash 后台任务注册在 bash 专用表 `_background_tasks`），含读取/等待/终止/终端输入、窗口截图（screenshot / windows / window，含整屏与多显示器截取、按控件区域截图、置顶与几何记忆）、控件清单（elements，Windows 优先 UI Automation）、图像/文字定位（locate）、等待窗口（wait_window）、剪贴板（clipboard）、动作序列（sequence）与 GUI 窗口输入注入（move/hover/click/drag/scroll/key/type，支持相对移动与长按、按控件名/自动化 ID 定位、语义坐标、界面变化等待与前后截图比对） |
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
| **plan** | 只读分析 + write/edit/mkdir（仅限 `.chat/plan/` 目录） | 任务拆解、依赖分析、生成计划文件到 `.chat/plan/` |
| **map** | 只读（read/grep/glob/ls 等只读工具） | 项目探底、模块地图、调用链追踪、引用关系分析 |
| **review** | 只读 + web_search（无 bash/bash_opt 等任何 shell 执行工具） | Code Review、P0-P3 分级审查、跨文件一致性验证 |
| **execute** | 全工具（不含 user_select/subagent/subagent_opt/web_search） | 读/写/改代码、执行测试、通用任务 |

> **工具排除策略**（与 `src/core/subagent.py` 的 `_TOOL_EXCLUSION_MAP` 一致）：execute 排除 `subagent/subagent_opt/user_select/web_search`；map 排除 `bash/bash_opt/write/edit/rm/mv/cp/mkdir/web_search/subagent/subagent_opt/user_select`；review 排除 `bash/bash_opt/write/edit/rm/mv/cp/mkdir/subagent/subagent_opt/user_select`（纯只读审查：仅 read/grep/glob/ls/web_search，无任何 shell 执行能力）；plan 排除 `bash/bash_opt/rm/mv/cp/subagent/subagent_opt/user_select`，write/edit/mkdir 仅限 `.chat/plan/` 目录。`subagent_opt` 与后台 subagent 均仅主 Agent 独有：SubAgent 工具白名单全类型排除 + 工具运行时 `isinstance(agent, SubAgent)` 双保险。SubAgent 在 `_handle_tool_calls()` 中注入 `agent_type` 到 Func 实例，`Func.can_use()` 进行统一检查。`FileToolBase._validate_path_and_size()` 额外实施 plan Agent 路径白名单校验。

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

├── tests/                 # 测试（按被测模块分组）
│   ├── renderer/          # Markdown/ANSI 解析与渲染、流式渲染
│   ├── tui/               # 终端界面（含 ink/ 框架用例）
│   ├── tools/             # 工具与平台后端（bash/window/screenshot/文件/Web）
│   ├── api/               # HTTP 适配器、多模态、流式生命周期
│   ├── core/              # 核心域（Agent/会话/压缩/上下文/沙盒/工具调度）
│   ├── kernel/            # 内核、清单、插件条目、架构守卫
│   ├── integrations/      # 外部集成（MCP / ClawBot / Skills）
│   ├── _paths.py          # 与文件层级无关的仓库根定位（新增用例统一使用）
│   └── conftest.py        # 全局夹具（事件循环兜底 / 覆盖率模式跳过时间预算用例）
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
│   │   ├── _sync_bridge.py / _stats_core.py / _stream_lifecycle.py / _token_speed.py / _tool_parse_utils.py  # 内部辅助模块
│   │   ├── multimodal.py       # 多模态模型判定（用户消息图片）+ 图片 content blocks 构造
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
│   │   ├── read.py / write.py / edit.py / read_image.py
│   │   ├── grep.py / glob.py / ls.py
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

## 能力现状与路线

> 本仓库的变更历史由 git 提交记录承载（提交信息含动机与验证结论）；本节只描述
> **当前能力**与**尚未实现的方向**，不再逐批罗列已完成的实现细节。

### 已具备的能力（摘要）

| 领域 | 能力 |
|---|---|
| 渲染 | 增量流式 Markdown（CommonMark / GFM 主体 + Pandoc 扩展，含行内代码属性 / 独立属性块 / 定义列表（块级正文）/ 脚注 / 表格表注 / 自动编号列表（`#.`）/ 定义标记 `~`）、LaTeX 公式终端排版、Mermaid 11 类图 ASCII 渲染（支持 `%%` 注释）、HTML 块与行内元素（datalist / hgroup / map / slot / ruby / strike 等，含行内背景色）、Front Matter、代码高亮与行号、TOC、Diff 高亮；自绘 ANSI 渲染路径与共享解析层 |
| TUI | React Ink 风格自研框架（调和器 / flexbox / hooks / 帧差异渲染 / 标准控件库）、恒定 30Hz 渲染、行级增量与 committed 前缀复用、全屏视图族（轨迹 / 日志 / 配置 / 插件 / 模型 / 文件沙盒 / 变更审查 / MCP / 技能 / 用量 / 搜索 / 键位 / 通知 / 导出 / 命令面板）、模态与底部视图机制、vim 风格导航与搜索、模糊命令面板与工作区状态段、8 套配色主题 |
| Agent | Pipeline 中间件链、上下文自动压缩（dsh 同款）、工具调用 DAG 并行调度（显式引用 / 路径重叠 / user_select 独占 / 类别约束四层依赖 + Kahn 分层）、SubAgent 并发派发（plan / map / review / execute）、会话日志与投影、断点与恢复 |
| 内核 | 无特权插件内核（Fiber 生命周期 / 服务容器 / 五模式事件分发 / 作用域与拦截）、Profile / Bundle / Patch 三层组装、热重载与依赖诊断、运行时不变量自检 |
| 能力接缝 | 六边形端口-适配器（配置 / 模型 / 持久化 / 事件 / 中断 / 可观测性 / 工具注册表）、三段式 seam（`ctx.fs` / `shell` / `subprocess` / `terminals` / `jobs` / `sandbox`，Provider 可整体替换） |
| 工具 | bash / bash_opt（后台任务与 GUI 操作：截图、窗口、键鼠、宏、像素、OCR 定位）、文件读写与检索、Web 搜索与抓取、MCP 外部工具、技能、SubAgent 调度 |
| 工程 | 全量测试 + 覆盖率门槛、ruff（F/E9 阻塞，`src` 与 `tests` 零缺陷）/ mypy 基线、CI（3.9 与 3.13 双版本）、pre-commit、依赖锁定与打包元数据守卫、架构分层与无环守卫测试、静态检查卫生守卫（`tests/test_lint_hygiene.py`） |

### 尚未实现的方向

**Agent 并行度**

| 改进项 | 现状 | 目标 |
|---|---|---|
| 工具调用并行 | 已实现独立的工具调用 DAG（无依赖的工具并行执行） | 动态扩缩容与优先级队列 |
| SubAgent 并发 | 已支持同轮批量并发派发 | 可复用 Worker 池，减少派发冷启动 |
| 流水线并行 | 各阶段等待完整输出 | 前序 Agent 输出流式送入后续 Agent |
| 资源感知调度 | 固定并发上限 | 按 CPU / 内存 / I/O 负载动态调整 |
| 分布式执行 | 单机 | 派发到远程计算节点，跨会话共享工作池 |

**其他**

- 渲染层：`src/renderer` 的解析层与 ANSI 渲染层仍有各自实现的块渲染逻辑，可继续把纯逻辑下沉为共享模块。
- 类型收窄：剩余 mypy 错误集中在动态属性与联合类型分支，需逐一收窄或显式标注。
- 巨型组件：全屏视图已统一复用骨架（`_inspector_pane` 检查器状态与导航 / `_keymap_pane` 帮助面板 / `_modal_view` 模态框架 / `_view_common` 分隔线与搜索），并由 `tests/tui/test_view_skeleton_shared.py` 守卫；各视图剩余的 `_handle_*_event` 承载**语义各异**的按键分发，不再强行抽取。
- 测试目录：`tests/` 已按被测模块分组（`renderer/` `tui/` `tools/` `api/` `core/` `kernel/` `integrations/`）；顶层保留跨模块/工程守卫用例（路径深度敏感的用例仍留在顶层，其迁移依赖 `tests/_paths` 的深度无关定位，见 `tests/test_paths_helper.py` 的存量豁免清单）。
- 静态检查：`src` / `tests` 现已零 F 组缺陷（未使用导入 / 未定义名 / 未使用局部变量 / 无效全局声明），并由 `tests/test_lint_hygiene.py` 在测试套件内守卫（本地优先 ruff、缺失时回退 pyflakes；后者不识别 `# noqa` 且更严格，故需要「副作用导入」处统一用 `importlib.import_module("…")` 表达，不保留 `# noqa: F401` 一类抑制）。

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
