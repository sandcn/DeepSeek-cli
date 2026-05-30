"""选择器 — 兼容性入口，委托给 picker.Picker。"""

from __future__ import annotations

from .picker import Picker, PickerResult


def _build_picker(
    title: str, options: list[str], multi_select: bool,
    default_options: list[str] | None, timeout: int,
) -> Picker:
    """构建 Picker 实例（消除 run_picker / run_picker_async 重复代码）。"""
    default_options = default_options or []
    default_indices = [
        i for i, o in enumerate(options) if o in default_options
    ]
    return Picker(
        title=title,
        items=options,
        multi_select=multi_select,
        default_indices=default_indices or None,
        timeout=timeout,
    )


def run_picker(
    title: str,
    options: list[str],
    multi_select: bool = False,
    default_options: list[str] | None = None,
    timeout: int = 120,
) -> dict:
    """
    运行交互式选择器（兼容原接口，内部委托给 Picker）。

    Args:
        title: 选择界面标题
        options: 可选选项列表
        multi_select: 是否允许多选
        default_options: 默认选中的选项列表
        timeout: 超时时间（秒），0 表示无超时

    Returns:
        {"selected": [选项文本列表], "action": "confirmed"|"cancel"|"timeout"}
    """
    picker = _build_picker(title, options, multi_select, default_options, timeout)
    return _picker_result_to_dict(picker.run())


async def run_picker_async(
    title: str,
    options: list[str],
    multi_select: bool = False,
    default_options: list[str] | None = None,
    timeout: int = 120,
) -> dict:
    """
    异步运行交互式选择器（在 asyncio 事件循环中直接运行）。

    与 run_picker 功能相同，但使用 Picker.run_async()，
    不需额外线程，兼容 Termux 等终端环境。
    """
    picker = _build_picker(title, options, multi_select, default_options, timeout)
    return _picker_result_to_dict(await picker.run_async())


def _picker_result_to_dict(result: PickerResult) -> dict:
    """将 PickerResult 映射为 dict 返回值格式。"""
    return {
        "selected": result.selected_items,
        "action": result.action if result.action in ("confirmed", "cancel", "timeout") else "unknown",
    }
