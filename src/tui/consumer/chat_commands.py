"""聊天域渲染命令枚举。

Layer 1 — 依赖 engine/commands.py（FrameworkCommand），
定义聊天应用专属的渲染命令。与框架层命令共同构成完整的命令命名空间。

命令值范围约定：
  - 聊天域命令使用 0-99 范围，与 FrameworkCommand 共享同一命名空间
  - 值与 RenderCommand 保持一致以维持向后兼容
"""

from __future__ import annotations

from enum import IntEnum


class ChatCommand(IntEnum):
    """聊天域渲染命令 — DeepSeek-CLI Chat 专属。

    每个枚举值对应 TuiRenderer 中的一个 _do_* 处理方法。
    值与 RenderCommand 保持一致，类型不同以支持按层过滤。
    """

    REASONING = 0
    """(0, text: str) — 推理内容增量追加"""

    CONTENT = 1
    """(1, text: str) — 回答内容增量追加"""

    PHASE_DONE = 2
    """(2, phase: str) — 阶段完成标记"""

    TOOL_OUTPUT = 6
    """(6, text: str) — 工具输出内容"""

    TOOL_SUMMARY = 7
    """(7, successful: tuple, failed: tuple) — 工具调用摘要"""

    USER_MSG = 8
    """(8, text: str) — 用户消息回显"""

    PARSE_INFO = 9
    """(9, tool_names: str, tokens: int, elapsed: float) — 解析信息"""

    DISPLAY_MSGS = 13
    """(13, messages: list, speed: int) — 批量显示消息列表"""

    TOOL_COUNT_INC = 14
    """(14,) — 工具计数+1"""

    TOOL_FAIL_INC = 15
    """(15,) — 工具失败计数+1"""

    TOOL_COUNT_DEC = 17
    """(17,) — 工具计数-1"""

    MAIN_PHASE = 20
    """(20, phase: str) — 主Agent模型阶段变更"""
