"""``/loop`` 续跑提词子系统。

- ``provider``：提示词构建（用户消息 + 最后一次回答）与 JSON 输出解析；
- ``service``：每次 ``Agent.run()`` 末尾、等待后台 bash/subagent 之前同步调用
  辅助 LLM，暂存 ``{是否继续, 提词}``；由 ``/loop`` 编排在下一轮运行处取用。

判定只以模型返回的 JSON 为准，不做本地启发式改判。

分层：本包属于 core 领域层，零 UI 依赖；事件由 ``ChatSession._emit`` 广播给
应用层（TUI 状态行 / 循环浮窗）。
"""

from .provider import (
    LOOP_PROMPT_MARKER,
    LoopPromptDecision,
    LoopPromptLoadError,
    build_loop_prompt_messages,
    build_system_prompt,
    human_user_texts,
    is_human_user_text,
    last_assistant_text,
    parse_loop_prompt_response,
)
from .service import LoopPromptService

__all__ = [
    "LOOP_PROMPT_MARKER",
    "LoopPromptDecision",
    "LoopPromptLoadError",
    "LoopPromptService",
    "build_loop_prompt_messages",
    "build_system_prompt",
    "human_user_texts",
    "is_human_user_text",
    "last_assistant_text",
    "parse_loop_prompt_response",
]
