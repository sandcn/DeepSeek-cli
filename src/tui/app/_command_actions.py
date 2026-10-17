"""_command_actions — 视图内执行 / 填入斜杠命令的共享动作。

命令面板、帮助速查等视图需要把「选中一条命令」转成真实输入：
  - ``submit=True``：立即提交（等价用户输入 ``/name`` 后按 Enter）；
  - ``submit=False``：只填入输入框（保留给用户补充参数）。

真实输入由装配层注入的两个回调承担（``model.palette_submit`` /
``model.palette_prefill``，见 ``_assembly_steps.create_framework``），视图
不直接持有 Input 实例——保持视图层与输入层解耦，测试可注入桩回调。

最近使用命令记录在 ``model.palette_recent``（内存态，随 AppModel 生命周期），
供命令面板在空查询时优先展示。
"""

from __future__ import annotations

import logging

__all__ = [
    "RECENT_LIMIT",
    "command_text",
    "recent_commands",
    "remember_recent",
    "run_command",
]

_logger = logging.getLogger(__name__)

#: 最近使用命令保留条数。
RECENT_LIMIT = 8


def command_text(name: str, *, with_args_space: bool = False) -> str:
    """命令名 → 可提交文本（``/name``；``with_args_space`` 时补一个尾随空格）。"""
    clean = str(name or "").strip().lstrip("/")
    if not clean:
        return ""
    return "/" + clean + (" " if with_args_space else "")


def recent_commands(model) -> list:
    """读取最近使用命令名列表（异常/缺省回退空列表）。"""
    try:
        value = getattr(model, "palette_recent", None) or []
        return [str(item) for item in value if item]
    except Exception:
        return []


def remember_recent(model, name: str) -> list:
    """把命令名插入最近使用列表头部（去重 + 截断到 ``RECENT_LIMIT``）。"""
    clean = str(name or "").strip().lstrip("/")
    if not clean:
        return recent_commands(model)
    recent = [item for item in recent_commands(model) if item != clean]
    recent.insert(0, clean)
    recent = recent[:RECENT_LIMIT]
    try:
        model.palette_recent = recent
    except Exception:
        _logger.debug("写入 palette_recent 失败", exc_info=True)
    return recent


def run_command(model, name: str, *, submit: bool = True) -> bool:
    """执行或填入一条命令。

    Args:
        model: AppModel（读取注入的 ``palette_submit`` / ``palette_prefill``）。
        name: 命令名（可带前导 ``/``）。
        submit: True 立即提交；False 填入输入框并补尾随空格。

    Returns:
        True — 已交给输入层；False — 命令名为空或未注入执行器。
    """
    text = command_text(name, with_args_space=not submit)
    if not text:
        return False
    remember_recent(model, name)
    attr = "palette_submit" if submit else "palette_prefill"
    callback = getattr(model, attr, None)
    if not callable(callback):
        _logger.debug("命令动作未注入执行器（%s）", attr)
        return False
    try:
        return bool(callback(text))
    except Exception:
        _logger.warning("命令动作执行失败: %s", text, exc_info=True)
        return False
