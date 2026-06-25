"""特性开关统一注册表。

集中管理所有 CHAT_UI_* 环境变量的读取，
避免各模块散落 os.environ.get() 调用。
"""
from __future__ import annotations
from dataclasses import dataclass, field
import os
from functools import lru_cache


@dataclass(frozen=True)
class FeatureFlags:
    """所有 ChatUI 特性开关的不可变容器。"""
    chat_ui_claude_style: bool = False
    chat_ui_use_react_like: bool = False
    chat_ui_layered_render: bool = False
    chat_ui_render_fixed_fps: bool = False
    chat_ui_render_use_rich_live: bool = False
    chat_ui_use_prompt_toolkit: bool = False
    chat_ui_render_legacy_fallback: bool = False

    @classmethod
    def from_env(cls) -> "FeatureFlags":
        """从环境变量读取所有开关。"""
        def _bool(name: str, default: bool = False) -> bool:
            val = os.environ.get(name, "").strip().lower()
            return val in ("1", "true", "yes", "on")
        
        return cls(
            chat_ui_claude_style=_bool("CHAT_UI_CLAUDE_STYLE"),
            chat_ui_use_react_like=_bool("CHAT_UI_USE_REACT_LIKE"),
            chat_ui_layered_render=_bool("CHAT_UI_LAYERED_RENDER"),
            chat_ui_render_fixed_fps=_bool("CHAT_UI_RENDER_FIXED_FPS"),
            chat_ui_render_use_rich_live=_bool("CHAT_UI_RENDER_USE_RICH_LIVE"),
            chat_ui_use_prompt_toolkit=_bool("CHAT_UI_USE_PROMPT_TOOLKIT"),
            chat_ui_render_legacy_fallback=_bool("CHAT_UI_RENDER_LEGACY_FALLBACK"),
        )


@lru_cache(maxsize=1)
def get_feature_flags() -> FeatureFlags:
    """获取特性开关（惰性缓存，首次调用时从环境变量读取）。"""
    return FeatureFlags.from_env()


def reset_feature_flags_cache() -> None:
    """重置缓存（主要用于测试）。"""
    get_feature_flags.cache_clear()
