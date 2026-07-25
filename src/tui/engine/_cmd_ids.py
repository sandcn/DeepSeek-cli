"""RenderCommand 枚举整数值 — 零依赖子模块。

Layer 0 — 仅定义整数常量，无任何内部依赖。
供 ComponentRegistry 等模块顶层导入，避免循环导入。

命令值分层（与 RenderCommand/IntEnum 保持一致）：
  [框架通用] NOTIFICATION=11, WRITE_LINE=12, ERROR=16, SUBAGENT_FRAME=18, SPLASH=19
  [聊天域]   REASONING=0, CONTENT=1, PHASE_DONE=2, ..., MAIN_PHASE=20

注意：此模块仅定义整数值，不是 IntEnum。
需要枚举类型时请使用 engine/const.py 中的 RenderCommand。
"""

# ── 聊天域命令 ──
REASONING: int = 0
CONTENT: int = 1
PHASE_DONE: int = 2
TOOL_OUTPUT: int = 6
TOOL_SUMMARY: int = 7
USER_MSG: int = 8
PARSE_INFO: int = 9
DISPLAY_MSGS: int = 13
TOOL_COUNT_INC: int = 14
TOOL_FAIL_INC: int = 15
TOOL_COUNT_DEC: int = 17
MAIN_PHASE: int = 20

# ── 框架通用命令 ──
NOTIFICATION: int = 11
WRITE_LINE: int = 12
ERROR: int = 16
SUBAGENT_FRAME: int = 18
SPLASH: int = 19
