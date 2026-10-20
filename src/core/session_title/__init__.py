"""会话标题子系统 —— 对齐 DeepSeek Harness 的 ``dsh-session-title``。

- ``normalize``：标题清洗 / UTF-8 安全截断 / 确定性回退派生；
- ``provider``：all-prompts 提供方的提示词构建与输出解析；
- ``service``：标题服务（来源仲裁、异步生成、钉住、日志事件、持久化）。

分层：本包属于 core 领域层，零 UI 依赖；终端窗口标题由 ``chat.py`` 的应用层
订阅 ``title_updated`` 事件后写入 OSC 序列。
"""

from .normalize import (
    ELLIPSIS,
    clip_cjk,
    count_cjk,
    derive_fallback_title,
    sanitize_title,
    truncate_utf8,
    utf8_len,
)
from .provider import (
    build_system_prompt,
    build_title_messages,
    build_user_payload,
    parse_title_response,
    request_bytes,
    select_messages_within_budget,
)
from .service import (
    SOURCE_FALLBACK,
    SOURCE_PROVIDER,
    SOURCE_USER,
    SessionTitleService,
    TitleSnapshot,
)

__all__ = [
    "ELLIPSIS",
    "SOURCE_FALLBACK",
    "SOURCE_PROVIDER",
    "SOURCE_USER",
    "SessionTitleService",
    "TitleSnapshot",
    "build_system_prompt",
    "build_title_messages",
    "build_user_payload",
    "clip_cjk",
    "count_cjk",
    "derive_fallback_title",
    "parse_title_response",
    "request_bytes",
    "select_messages_within_budget",
    "sanitize_title",
    "truncate_utf8",
    "utf8_len",
]
