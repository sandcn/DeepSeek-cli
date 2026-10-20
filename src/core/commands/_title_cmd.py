"""标题命令 — /title（显示状态 / 显式重新生成 / 用户重命名钉住 / 解钉）。

对齐 DeepSeek Harness 的标题服务语义：

- ``/title`` 或 ``/title show``：显示当前标题、来源、生成模型、参与消息与
  钉住状态（排查「标题为什么没变」的入口）；
- ``/title refresh``：显式重新生成（**有意的解钉手段**）——自动生成失败只
  保留旧标题，重试必须由用户显式发起；
- ``/title set <文本>``：用户重命名 → 标题「钉住」，后续用户消息不再自动
  重命名（只有 refresh 才解钉）；
- ``/title auto``：解除钉住并立即按当前对话重新生成一次。
"""

from __future__ import annotations

import logging
from datetime import datetime

from ..adapters.output import get_default_output_port
from ..constants import CYAN, DIM, GREEN, RESET, YELLOW
from .base import CommandMeta, CommandPlugin, declare_command_plugin

_logger = logging.getLogger(__name__)

_out = get_default_output_port()

#: 子命令用法提示。
_USAGE = "用法: /title [show | refresh | set <文本> | auto]"

#: 标题来源 → 展示文案。
_SOURCE_LABELS = {
    "fallback": "回退（首条用户消息）",
    "provider": "模型生成",
    "user": "用户重命名（已钉住）",
}

#: 参与消息序号列表的最大展示条数（其余以「…」收尾）。
_MAX_SEQ_DISPLAY = 8


def _resolve_service(ctx):
    """取当前会话的标题服务（无会话 / 无服务返回 None）。"""
    session = getattr(ctx, "session", None)
    if session is None:
        return None
    return getattr(session, "title_service", None)


def _format_time(timestamp: float) -> str:
    """时间戳 → ``YYYY-MM-DD HH:MM:SS``（0 / 非法值显示「-」）。"""
    try:
        value = float(timestamp or 0.0)
    except (TypeError, ValueError):
        return "-"
    if value <= 0:
        return "-"
    try:
        return datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M:%S")
    except (OSError, OverflowError, ValueError):
        return "-"


def _show(service) -> None:
    """打印标题状态（来源 / 模型 / 参与消息 / 钉住 / 最近失败）。"""
    status = service.status()
    title = status.get("title") or ""
    source = status.get("source") or ""
    _out.write("", level="raw", source="cmd")
    if title:
        _out.write(f"  {CYAN}▸ 会话标题{RESET}  {title}", level="raw", source="cmd")
    else:
        _out.write(
            f"  {CYAN}▸ 会话标题{RESET}  {DIM}(尚未生成——首条消息发送后自动取名){RESET}",
            level="raw", source="cmd",
        )
    if source:
        _out.write(
            f"    {DIM}来源      {_SOURCE_LABELS.get(source, source)}{RESET}",
            level="raw", source="cmd",
        )
    model = status.get("model") or ""
    if model:
        _out.write(f"    {DIM}模型      {model}{RESET}", level="raw", source="cmd")
    seqs = list(status.get("message_seqs") or [])
    if seqs:
        shown = ", ".join(str(seq) for seq in seqs[:_MAX_SEQ_DISPLAY])
        suffix = "…" if len(seqs) > _MAX_SEQ_DISPLAY else ""
        _out.write(
            f"    {DIM}消息      {len(seqs)} 条（seq {shown}{suffix}）{RESET}",
            level="raw", source="cmd",
        )
    if status.get("updated_at"):
        _out.write(
            f"    {DIM}更新      {_format_time(status.get('updated_at'))}{RESET}",
            level="raw", source="cmd",
        )
    state = "已钉住（不再自动重命名）" if status.get("pinned") else "自动（每条新消息后重新评估）"
    _out.write(f"    {DIM}状态      {state}{RESET}", level="raw", source="cmd")
    if status.get("generating"):
        _out.write(f"    {DIM}进行中    正在生成标题…{RESET}", level="raw", source="cmd")
    error = status.get("last_error") or ""
    if error:
        _out.write(f"    {YELLOW}最近失败  {error}{RESET}", level="raw", source="cmd")
    _out.write("", level="raw", source="cmd")


def _refresh(service) -> None:
    """显式重新生成标题（阻塞等待结果；失败保留旧标题）。"""
    _out.write(f"  {DIM}… 正在重新生成会话标题（失败将保留当前标题）{RESET}", level="raw", source="cmd")
    try:
        snapshot = service.refresh_sync()
    except Exception as exc:  # noqa: BLE001 - 命令层给出可读提示即可
        _logger.warning("标题重新生成失败: %s", exc, exc_info=True)
        _out.write(f"  {YELLOW}! 重新生成失败: {exc}{RESET}", level="raw", source="cmd")
        return
    if snapshot is None:
        _out.write(
            f"  {YELLOW}! 未生成标题（无合格用户消息或模型输出无效，保留当前标题）{RESET}",
            level="raw", source="cmd",
        )
        return
    _out.write(
        f"  {GREEN}+ 会话标题: {snapshot.title}{RESET}"
        f"  {DIM}（来源: {_SOURCE_LABELS.get(snapshot.source, snapshot.source)}）{RESET}",
        level="raw", source="cmd",
    )


def _cmd_title(ctx) -> bool:
    """``/title`` 命令入口。"""
    service = _resolve_service(ctx)
    if service is None:
        _out.write(
            f"  {YELLOW}! 当前会话没有标题服务（无活动 ChatSession）{RESET}",
            level="raw", source="cmd",
        )
        return True

    arg = str(getattr(ctx, "arg", "") or "").strip()
    if not arg or arg == "show":
        _show(service)
        return True

    action, _, rest = arg.partition(" ")
    action = action.strip().lower()
    rest = rest.strip()

    if action == "refresh":
        _refresh(service)
        return True

    if action == "set":
        if not rest:
            _out.write(
                f"  {YELLOW}! 用法: /title set <新标题>{RESET}",
                level="raw", source="cmd",
            )
            return True
        try:
            snapshot = service.rename(rest)
        except ValueError as exc:
            _out.write(f"  {YELLOW}! {exc}{RESET}", level="raw", source="cmd")
            return True
        if snapshot is None:
            _out.write(f"  {YELLOW}! 标题不能为空{RESET}", level="raw", source="cmd")
            return True
        _out.write(
            f"  {GREEN}+ 会话标题已更新（已钉住，后续不再自动重命名）: "
            f"{snapshot.title}{RESET}",
            level="raw", source="cmd",
        )
        return True

    if action == "auto":
        was_pinned = service.unpin()
        prefix = "已解除钉住，" if was_pinned else ""
        _out.write(
            f"  {DIM}… {prefix}按当前对话重新生成会话标题{RESET}",
            level="raw", source="cmd",
        )
        try:
            snapshot = service.refresh_sync()
        except Exception as exc:  # noqa: BLE001 - 命令层给出可读提示即可
            _logger.warning("标题重新生成失败: %s", exc, exc_info=True)
            _out.write(f"  {YELLOW}! 重新生成失败: {exc}{RESET}", level="raw", source="cmd")
            return True
        if snapshot is None:
            _out.write(
                f"  {YELLOW}! 未生成标题（保留当前标题，后续消息仍会自动重试）{RESET}",
                level="raw", source="cmd",
            )
            return True
        _out.write(
            f"  {GREEN}+ 会话标题: {snapshot.title}{RESET}",
            level="raw", source="cmd",
        )
        return True

    _out.write(f"  {YELLOW}! {_USAGE}{RESET}", level="raw", source="cmd")
    return True


class TitleCommand(CommandPlugin):
    """显示 / 重新生成 / 钉住会话标题。"""

    def __init__(self):
        self.meta = CommandMeta(
            name="title",
            description="会话标题（查看来源 / 重新生成 / 重命名钉住）",
            group="session",
            usage="[show|refresh|set <文本>|auto]",
        )

    def execute(self, ctx) -> bool:
        return _cmd_title(ctx)


# ── 声明插件（注册由清单条目 / ctx.commands 服务按需触发） ──
declare_command_plugin(TitleCommand())

__all__ = ["TitleCommand", "_cmd_title"]
