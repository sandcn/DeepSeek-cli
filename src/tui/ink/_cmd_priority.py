"""命令优先级 — Ink 渲染命令优先级策略（纯函数，模块边界优化 2026-08-05）。

从 ``ink/session.py`` 提取（方向B：InkSession 职责拆分）——优先级常量与
映射函数独立成模块：session 聚焦「队列 + 生命周期 + 渲染循环」，优先级
策略独立可测。``session.py`` 保持模块级 re-export（旧导入路径兼容，
``from src.tui.ink.session import _get_cmd_priority`` 仍可用）。

Layer 0 约束：仅依赖 ``_const``（RenderCommand/RenderCmd），零内部依赖。
"""

from __future__ import annotations

from src.tui._const import RenderCommand, RenderCmd

# ── 命令优先级（值越小越优先） ──────────────────────────────
_CMD_PRIORITY_CRITICAL = 0
_CMD_PRIORITY_HIGH = 1
_CMD_PRIORITY_NORMAL = 2
_CMD_PRIORITY_LOW = 3

_CRITICAL_CMDS = frozenset({
    RenderCommand.PHASE_DONE,
    RenderCommand.TOOL_SUMMARY,
    RenderCommand.TOOL_COUNT_INC,
    RenderCommand.TOOL_COUNT_DEC,
    RenderCommand.TOOL_FAIL_INC,
    RenderCommand.MAIN_PHASE,
    RenderCommand.SPLASH,
    RenderCommand.TOOL_OPEN,
    RenderCommand.TOOL_CLOSE,
})
_STREAM_CMDS = frozenset({
    RenderCommand.REASONING,
    RenderCommand.CONTENT,
    # 工具输出与 Open/Close（prio0）同序——否则 Close 先于 Output 出队，
    # 输出落到无名新 box（每工具 box 增量刷新依赖此顺序）。
    RenderCommand.TOOL_OUTPUT,
})
_HIGH_CMDS = frozenset({
    RenderCommand.SUBAGENT_FRAME,
    RenderCommand.ERROR,
})
_NORMAL_CMDS = frozenset({
    # TOOL_OUTPUT 已在 _STREAM_CMDS（prio 0）——此处不再重复配置（方向3：
    # 重复配置误导——_get_cmd_priority 先查 STREAM，TOOL_OUTPUT 恒为 prio 0）。
    RenderCommand.USER_MSG,
    RenderCommand.PARSE_INFO,
    RenderCommand.NOTIFICATION,
})
_LOW_CMDS = frozenset({
    RenderCommand.WRITE_LINE,
    RenderCommand.DISPLAY_MSGS,
    # 与 WRITE_LINE 同优先级（低优先级批量投递，不抢占流式内容）
    RenderCommand.SUBAGENT_MARKDOWN,
})


def _get_cmd_id(cmd: RenderCmd) -> int:
    return cmd.cid


def _get_cmd_priority(cmd: RenderCmd) -> int:
    """获取命令优先级（与 TuiEngine._get_cmd_priority 语义一致）。"""
    cid = cmd.cid
    if cid in _CRITICAL_CMDS or cid in _STREAM_CMDS:
        return _CMD_PRIORITY_CRITICAL
    if cid in _HIGH_CMDS:
        return _CMD_PRIORITY_HIGH
    if cid in _NORMAL_CMDS:
        return _CMD_PRIORITY_NORMAL
    return _CMD_PRIORITY_LOW


def _cmd_name(cid: int) -> str:
    try:
        return RenderCommand(cid).name
    except ValueError:
        return str(cid)


__all__ = [
    "_CMD_PRIORITY_CRITICAL",
    "_CMD_PRIORITY_HIGH",
    "_CMD_PRIORITY_NORMAL",
    "_CMD_PRIORITY_LOW",
    "_CRITICAL_CMDS",
    "_STREAM_CMDS",
    "_HIGH_CMDS",
    "_NORMAL_CMDS",
    "_LOW_CMDS",
    "_get_cmd_id",
    "_get_cmd_priority",
    "_cmd_name",
]
