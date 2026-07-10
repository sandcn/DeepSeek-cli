"""
终端颜色配置模块

始终启用终端颜色，不依赖 TTY 检测。
"""
from __future__ import annotations

import os
from typing import Dict, Any

# 模块级常量：检测是否在 Android (Termux) 环境中运行
_IS_ANDROID = bool(os.environ.get("TERMUX_VERSION"))


def should_use_color() -> bool:
    """始终启用终端颜色，不依赖 TTY 检测。"""
    return True


def get_safe_console_config() -> Dict[str, Any]:
    """
    获取安全的控制台配置

    根据当前终端环境返回适合的Console配置参数。

    Returns:
        包含Console配置参数的字典
    """
    config: Dict[str, Any] = {
        "force_terminal": True,
        "soft_wrap": True,
        "markup": True,
        "emoji": True,
        "highlight": True,
    }

    # 检测是否在Windows系统（仅旧版cmd需要windows颜色系统）
    if os.name == 'nt' and os.getenv("WT_SESSION") is None:
        config["color_system"] = "windows"

    # 检测是否在 Android (Termux) 环境，启用 truecolor 支持
    if _IS_ANDROID:
        config["color_system"] = "truecolor"

    return config
