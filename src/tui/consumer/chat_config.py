"""ChatUI 聊天域配置 — 聊天应用专属常量集中管理。

从 engine/const.py 迁移聊天域常量，实现框架层/应用层分离。
与 TuiConfig（框架层配置）互补：TuiConfig 管渲染引擎参数，
ChatConfig 管聊天域业务常量。

用法::

    from src.tui.consumer.chat_config import ChatConfig
    cfg = ChatConfig.defaults()
    print(cfg.main_label)  # "assistant"
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .._config import ConfigBase


__all__: list[str] = ["ChatConfig"]


@dataclass(frozen=True)
class ChatConfig(ConfigBase):
    """聊天域配置 — 聊天应用专属常量。

    所有属性均为不可变（frozen=True），线程安全。
    通过 ``ChatConfig.defaults()`` 获取默认实例。
    """

    # ── 主 Agent 标识 ──────────────────────────────────
    main_label: str = "assistant"      # 主 Agent 的 DisplayEvent.label
    main_source: str = "agent"         # 主 Agent 的 DisplayEvent.source

    # ── 思考标题 ──────────────────────────────────────
    thinking_header: str = "\n  ─ 思考 ─\n"

    # ── 截断参数 ──────────────────────────────────────
    max_output_len: int = 10000        # 工具输出最大长度（字符）


