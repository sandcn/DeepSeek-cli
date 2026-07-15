"""工具参数格式化函数

从 src/ui/formatters/param_formatter.py 迁移，用于格式化工具调用的参数显示。
"""

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
