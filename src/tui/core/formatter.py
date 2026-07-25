"""文本格式化工具函数 — 零依赖核心层，避免循环导入。

从 parallel/_text_formatter.py 的 TextFormatter 类提取纯格式化函数，
下沉到 core 层以消除循环依赖（cost → parallel → frame → components → cost）。

所有函数为纯函数，无副作用，无 I/O，不依赖任何外部模块。
"""

from __future__ import annotations


def format_duration(seconds: float) -> str:
    """格式化持续时间为可读格式。

    < 60s → "Xs"
    < 3600s → "XmYs"
    >= 3600s → "XhYm"

    Args:
        seconds: 秒数。

    Returns:
        格式化后的时间字符串。
    """
    if seconds < 0:
        return "0s"
    if seconds < 60:
        return f"{seconds:.0f}s"
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    if minutes < 60:
        return f"{minutes}m{secs}s"
    hours = minutes // 60
    minutes %= 60
    return f"{hours}h{minutes}m"


def format_token_count(tokens: int) -> str:
    """格式化 token 数为可读格式（含 k 后缀）。

    < 1000 → 整数显示
    >= 1000 → "X.Xk"（一位小数）

    Args:
        tokens: token 数量。

    Returns:
        格式化后的 token 数字符串。
    """
    if tokens >= 1000:
        return f"{tokens / 1000:.1f}k"
    return str(tokens)


def format_compact_speed(speed: float) -> str:
    """格式化紧凑速度，始终使用 /s。

    Args:
        speed: 速率（个/秒）。

    Returns:
        格式化后的速率字符串（如 "15.3/s"）。
    """
    if speed <= 0:
        return "0/s"
    if speed >= 0.1:
        value = f"{speed:.1f}"
    else:
        value = f"{speed:.2f}"
    value = value.rstrip("0").rstrip(".")
    return f"{value}/s"


def format_elapsed(seconds: float) -> str:
    """格式化运行时间（秒）为人类可读字符串。

    < 60 秒 → "3.5s"（一位小数）
    >= 60 秒 → "2:05"（分:秒，秒补零）
    >= 3600 秒 → "1:02:34"（时:分:秒）

    Args:
        seconds: 运行时间（秒）。

    Returns:
        格式化后的时间字符串。
    """
    if seconds < 0:
        return "0.0s"
    if seconds < 60:
        return f"{seconds:.1f}s"
    mins = int(seconds // 60)
    secs = int(seconds % 60)
    if mins < 60:
        return f"{mins}:{secs:02d}"
    hours = mins // 60
    mins %= 60
    return f"{hours}:{mins:02d}:{secs:02d}"


def format_speed(tok_per_sec: float) -> str:
    """格式化 token 速率为人类可读字符串。

    >= 10 → "120"（整数，无小数）
    1 ~ 10 → "5.3"（一位小数）
    < 1 → "0.75"（两位小数）
    < 0 → "0.0"

    Args:
        tok_per_sec: token 速率（个/秒）。

    Returns:
        格式化后的速率字符串。
    """
    if tok_per_sec < 0:
        return "0.0"
    if tok_per_sec >= 10:
        return f"{tok_per_sec:.0f}"
    if tok_per_sec >= 1:
        return f"{tok_per_sec:.1f}"
    return f"{tok_per_sec:.2f}"



# ══════════════════════════════════════════════════════════
# 工具参数格式化（从 param_formatter.py 合并）
# ══════════════════════════════════════════════════════════

from typing import Any, Callable

# ── 截断长度常量 ─────────────────────────────────────────
_TRUNC_LONG = 20              # 字符串值截断长度阈值
_TRUNC_SINGLE = 15            # 单元素列表值截断长度阈值
_TRUNC_MEDIUM = 12            # 单元素列表值截断后显示长度
_TRUNC_MULTI = 10             # 多元素列表第一个值截断长度阈值
_TRUNC_SHORT = 7              # 多元素列表第一个值截断后显示长度
_ELLIPSIS_LEN = 3             # 省略号"..."长度


def _escape_str(s):
    """将 \\r 和 \\n 替换为字面字符串"""
    return s.replace('\r', '\\r').replace('\n', '\\n')


def _format_none(value: None) -> str:
    """格式化 None 值。"""
    return "None"


def _format_str(value: str) -> str:
    """格式化字符串值（含截断和转义）。"""
    display_value = _escape_str(value)
    if len(display_value) > _TRUNC_LONG:
        return f"'{display_value[:_TRUNC_LONG - _ELLIPSIS_LEN]}...'"
    return f"'{display_value}'"


def _format_int(value: int) -> str:
    return str(value)


def _format_float(value: float) -> str:
    return str(value)


def _format_bool(value: bool) -> str:
    return str(value)


def _format_list(value: list) -> str:
    """格式化列表值（显示长度和首元素）。"""
    if len(value) == 0:
        return "[]"
    if len(value) == 1:
        first_val = str(value[0])
        display_val = _escape_str(first_val)
        if len(display_val) > _TRUNC_SINGLE:
            display_val = display_val[:_TRUNC_MEDIUM] + "..."
        return f"[{display_val}]"
    first_val = str(value[0])
    display_val = _escape_str(first_val)
    if len(display_val) > _TRUNC_MULTI:
        display_val = display_val[:_TRUNC_SHORT] + "..."
    return f"[{display_val} +{len(value)-1}]"


def _format_dict(value: dict) -> str:
    """格式化字典值（显示键数）。"""
    return f"{{{len(value)} keys}}"


# 类型分发表 — 替代长 if/elif 链
_FORMATTERS: dict[type, Callable[[Any], str]] = {
    str: _format_str,
    int: _format_int,
    float: _format_float,
    bool: _format_bool,
    list: _format_list,
    dict: _format_dict,
    type(None): _format_none,
}


def format_all_params(tool_name, arguments, max_len=80):
    """格式化显示所有参数

    Args:
        tool_name: 工具名称
        arguments: 工具参数字典
        max_len: 最大显示长度

    Returns:
        格式化后的参数字符串
    """
    if not arguments:
        return ""

    # 构建参数字符串
    parts = []
    for key, value in arguments.items():
        formatter = _FORMATTERS.get(type(value))
        val_str = formatter(value) if formatter is not None else f"{type(value).__name__}"
        parts.append(f"{key}={val_str}")

    result = ", ".join(parts)

    # 如果超过最大长度，进行截断
    if len(result) > max_len:
        # 保留工具名称和部分参数
        tool_prefix = f"{tool_name} "
        available_len = max_len - len(tool_prefix) - _ELLIPSIS_LEN  # 为"..."留空间
        if available_len > 10:
            # 尝试保留尽可能多的参数
            truncated = result[:available_len]
            # 确保不会在参数中间截断
            last_comma = truncated.rfind(', ')
            if last_comma <= 10:  # 逗号位置太靠前，直接截断
                result = tool_prefix + truncated[:max(10, available_len)] + "..."
            elif last_comma > available_len * 0.5:  # 如果截断点在一个合理的逗号位置
                result = tool_prefix + truncated[:last_comma] + "..."
            else:
                result = tool_prefix + truncated + "..."
        else:
            result = tool_prefix + "..."

    return result


def extract_key_params(tool_name, arguments, max_len=80, show_all=False):
    """提取工具的关键参数信息用于显示（公共函数）

    优先委托给工具类的 display_params()，未注册的工具 fallback 到通用格式化。
    """
    if not arguments or not isinstance(arguments, dict):
        return ""

    # 先尝试工具类的 display_params，再 fallback 到通用格式化
    try:
        from ...tools.registry import get_tools
        tools = get_tools()
        tool_class = tools.get(tool_name)
        if tool_class:
            result = tool_class.display_params(arguments, max_len)
            if result:
                return result
    except (ImportError, AttributeError, TypeError):
        pass
    return format_all_params(tool_name, arguments, max_len)


__all__ = [
    "format_duration",
    "format_token_count",
    "format_compact_speed",
    "format_elapsed",
    "format_speed",
    "format_all_params",
    "extract_key_params",
]
