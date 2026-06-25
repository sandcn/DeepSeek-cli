"""chat_ui 常量模块 — RenderCommand 枚举、ANSI 样式常量、推理状态机。

Layer 0 — 无内部依赖，被所有上层模块引用。
"""

from __future__ import annotations

from enum import IntEnum

# ── 主 Agent 标识 ───────────────────────────────────────
_MAIN_LABEL = "assistant"
_MAIN_SOURCE = "agent"

# ── Claude Code 风格默认值 ───────────────────────────────
_CLAUDE_STYLE_ENABLED_DEFAULT: bool = False

# ── ANSI 样式常量（供渲染管线使用） ──
_STYLE_DIM = "dim"
_STYLE_FAIL = "red"
_STYLE_WARN = "yellow"  # orange1 不可用，使用 yellow
_STYLE_SUCCESS = "green"
_STYLE_ERROR = "bold red"
_STYLE_BOLD = "bold"

_THINKING_HEADER = "\n  ─ 思考 ─\n"
_CLAUDE_THINKING_HEADER = "\n  ⏺ Thinking…\n"

# ── 解析进度清除哨兵 ───────────────────────────────────
_CLEAR_PARSE_LINE = -1
_THINKING_SEPARATOR = "\n  " + "\u2500" * 25 + "\n"

# ── 统一错误消息截断长度 ─────────────────────────────
_MAX_ERROR_LENGTH = 200
_MAX_OUTPUT_LEN = 10000  # 工具输出最大长度（字符），与 _MAX_ERROR_LENGTH 对齐

# ── render 线程刷新间隔 ─────────────────────────────────
_RENDER_INTERVAL = 0.1  # 100ms = 10Hz

# ── 自适应渲染参数 ──────────────────────────────────────
_ACTIVE_RENDER_INTERVAL = 0.005  # 5ms（活动帧间隔）
_IDLE_DRAIN_THRESHOLD = 5        # 连续空闲轮次阈值（超过后切换空闲间隔）
_CONSECUTIVE_FULL_THRESHOLD = 10  # 连续满队列阈值（触发拥堵告警）

# ── 固定帧率常量（Phase 3: 固定帧率渲染循环） ──
_FIXED_FRAME_INTERVAL = 0.016  # ~60fps (16ms)
_ENV_FIXED_FPS = "CHAT_UI_RENDER_FIXED_FPS"

# ── 紧急路径 ANSI 转义序列（直写终端，绕过 Rich 管线） ──
# 用于队列满/render 崩溃等无法通过正常渲染管线输出的场景。
# 提取为常量而非散落硬编码，确保可维护性。
_ANSI_RED = "\033[31m"
_ANSI_YELLOW = "\033[33m"
_ANSI_RESET = "\033[0m"
_ANSI_CURSOR_BOTTOM = "\033[9999;1H"


# ═══════════════════════════════════════════════════════════
# RenderCommand — 渲染命令枚举（IntEnum，类型安全 + 自文档化）
# ═══════════════════════════════════════════════════════════

class RenderCommand(IntEnum):
    """渲染命令类型，替代魔数整数。

    每个枚举值对应 _render() 分发的方法签名，
    值用于 _RENDER_DISPATCH 的 O(1) 字典查找。
    格式: (cmd_value, *args) — cmd_value 即枚举值。
    """
    REASONING     = 0   # (0, text: str)
    CONTENT       = 1   # (1, text: str)
    PHASE_DONE    = 2   # (2, phase: str)
    TOOL_OUTPUT   = 6   # (6, text: str)
    TOOL_SUMMARY  = 7   # (7, successful: tuple, failed: tuple)
    USER_MSG      = 8   # (8, text: str)
    PARSE_INFO    = 9   # (9, tool_names: str, tokens: int, elapsed: float)
    NOTIFICATION  = 11  # (11, text: str)
    WRITE_LINE    = 12  # (12, text: str)
    DISPLAY_MSGS  = 13  # (13, messages: list, speed: int)
    TOOL_COUNT_INC = 14  # (14,) — 工具计数+1
    TOOL_FAIL_INC  = 15  # (15,) — 工具失败计数+1
    ERROR          = 16  # (16, message: str) — 系统错误（红色 ! 样式）
    TOOL_COUNT_DEC     = 17  # (17,) — 工具计数-1
    # ── drain 锁超时 ─────────────────────────────────────
_DRAIN_LOCK_TIMEOUT = 0.1  # drain_queue 获取输出锁的超时（秒），与 _RENDER_INTERVAL (0.1) 对齐，避免一方修改引入竞态

# ── 层级渲染 Feature Flag ──────────────────────────────────────────

# 环境变量：控制分层渲染开关（默认关闭，向后兼容）
_ENV_LAYERED_RENDER = "CHAT_UI_LAYERED_RENDER"

# 环境变量：最大层级数（默认 8）
_ENV_LAYER_MAX_COUNT = "CHAT_UI_LAYER_MAX_COUNT"

# 环境变量：全量刷新阈值（变化行数占比，默认 0.5）
_ENV_LAYER_DIFF_THRESHOLD = "CHAT_UI_LAYER_DIFF_THRESHOLD"

# 默认值
_DEFAULT_LAYER_MAX_COUNT = 8
_DEFAULT_LAYER_DIFF_THRESHOLD = 0.5
