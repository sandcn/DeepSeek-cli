"""ContextPlugin — 显示 / 设置上下文窗口（``/context``）。

对齐 dsh 的 ``/context``：无参数显示当前有效上下文窗口、实时使用率与压缩
预算；带参数（``256k`` / ``1m`` / 纯 token 数）写入模型上下文窗口，后续请求
与自动压缩按新容量实时计算。
"""

from __future__ import annotations

import logging
from typing import Any

from .base import CommandPlugin
from ..base import CommandMeta, declare_command_plugin

_logger = logging.getLogger(__name__)


def _parse_token_arg(arg: str) -> int | None:
    """解析 ``256k`` / ``1m`` / ``262144`` 形式的 token 容量；非法返回 None。"""
    text = (arg or "").strip().lower().replace("_", "").replace(",", "")
    if not text:
        return None
    multiplier = 1
    if text.endswith("k"):
        multiplier, text = 1_000, text[:-1]
    elif text.endswith("m"):
        multiplier, text = 1_000_000, text[:-1]
    try:
        value = float(text)
    except ValueError:
        return None
    if value <= 0:
        return None
    return int(value * multiplier)


class ContextPlugin(CommandPlugin):
    """显示 / 设置上下文窗口（``/context [256k|1m|tokens]``）。"""

    def __init__(self):
        self.meta = CommandMeta(
            name="context",
            description="显示/设置上下文窗口",
            group="session",
            usage="[256k|1m|tokens]",
        )

    def execute(self, ctx: Any) -> bool:
        arg = (getattr(ctx, "arg", "") or "").strip()
        if arg:
            return self._set_window(ctx, arg)
        self._show(ctx)
        return True

    # ── 设置 ─────────────────────────────────────────────

    def _set_window(self, ctx: Any, arg: str) -> bool:
        value = _parse_token_arg(arg)
        if value is None:
            self.output("用法: /context [256k|1m|tokens]")
            return True
        config_port = getattr(ctx, "config_port", None)
        try:
            if config_port is not None and hasattr(config_port, "set"):
                config_port.set("MODEL_CONTEXT_TOKENS", value)
            else:
                from ....config.loader import update_config

                update_config("MODEL_CONTEXT_TOKENS", value)
        except Exception as error:
            _logger.debug("设置上下文窗口失败", exc_info=True)
            self.output(f"设置上下文窗口失败: {error}")
            return True
        state = getattr(ctx, "state", None)
        if isinstance(state, dict):
            state["model_context_tokens"] = value
        self._refresh_usage(ctx)
        self.output(f"上下文窗口已设为 {value}")
        return True

    # ── 显示 ─────────────────────────────────────────────

    def _show(self, ctx: Any) -> None:
        config_port = getattr(ctx, "config_port", None)
        window = 0
        try:
            if config_port is not None:
                window = int(config_port.get_model_context_tokens() or 0)
        except Exception:
            window = 0

        context_manager = self._resolve_context_manager(ctx)
        used_tokens = 0
        percent = None
        if context_manager is not None:
            try:
                _chars, used_tokens = context_manager.measure_context()
            except Exception:
                used_tokens = 0
            try:
                from ....core.context_manager import get_context_usage_percent

                percent = get_context_usage_percent()
            except Exception:
                percent = None

        lines = ["\n  ─ 上下文窗口"]
        if window > 0:
            pct_text = f"{percent:.1f}%" if isinstance(percent, (int, float)) else "—"
            lines.append(f"  │ 容量  {window}  (使用 {pct_text}, ~{used_tokens}t)")
        else:
            lines.append("  │ 容量  未启用（model_context_tokens <= 0）")
        lines.append(f"  │ 已用  ~{used_tokens}t")

        spec_line = self._spec_line(config_port)
        if spec_line:
            lines.append(spec_line)
        lines.append("  └" + "─" * 24)
        self.output("\n".join(lines))

    @staticmethod
    def _spec_line(config_port) -> str:
        """压缩预算摘要（阈值 / 保留尾部），解析失败时为空串。"""
        if config_port is None:
            return ""
        try:
            from ....core.compaction import (
                resolve_compact_spec,
                resolve_config,
                resolve_target_policy,
            )

            raw = config_port.get_compaction_config() if hasattr(
                config_port, "get_compaction_config") else {}
            config = resolve_config(raw or {})
            provider = str(config_port.get("provider", "") or "")
            model = str(config_port.get("model", "") or "")
            policy = resolve_target_policy(config, provider, model)
            spec = resolve_compact_spec(
                policy, int(config_port.get_model_context_tokens() or 0),
                int(config.reserved_tokens or 0),
            )
            prune = ("开" if config.prune_enabled else "关")
            return (
                f"  │ 压缩  阈值 {spec.threshold_tokens}t · 保留 {spec.retain_tokens}t"
                f" · 剪枝 {prune}"
            )
        except Exception:
            return ""

    @staticmethod
    def _resolve_context_manager(ctx: Any):
        session = getattr(ctx, "session", None)
        context_manager = getattr(session, "context_manager", None) if session is not None else None
        if context_manager is None:
            context_manager = getattr(ctx, "context_manager", None)
        return context_manager

    @staticmethod
    def _refresh_usage(ctx: Any) -> None:
        context_manager = ContextPlugin._resolve_context_manager(ctx)
        if context_manager is None:
            return
        try:
            context_manager.refresh_usage(force=True)
        except Exception:
            _logger.debug("刷新上下文使用率失败", exc_info=True)


declare_command_plugin(ContextPlugin())
