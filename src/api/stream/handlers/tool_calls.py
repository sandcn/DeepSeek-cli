"""ToolCallsHandler — 处理工具调用增量"""
from __future__ import annotations
import logging
from ...events import publish_event
from ...tokens import estimate_tokens
from ...stats import add_token_size
from ..context import StreamContext

_logger = logging.getLogger(__name__)


class ToolCallsHandler:
    """处理流式 tool_calls chunk"""

    async def handle(self, ctx: StreamContext, delta_tool_calls: list) -> None:
        """处理一段 tool_calls 增量（全异步）

        首次检测到 tool_calls 时 flush 缓冲区并启动 tracker。
        对每个新工具调用索引，通过 ctx.display.tool_parsing()
        发布 ToolParsingEvent 到 EventBus（供 Web UI 消费）。
        """
        if not ctx.tracker.started:
            # 首次检测到工具调用：标记推理阶段结束、同步状态
            # 终端渲染器关闭已移至 ChatUIConsumer 通过 PhaseDoneEvent 处理
            if not ctx.silent:
                # ★ 同步 is_reasoning 状态
                if ctx.is_reasoning:
                    ctx.is_reasoning = False
            await ctx.tracker.start()
            # 🔥 内容阶段结束 → 发布 PhaseDoneEvent（在工具调用前刷新 UI）
            # ChatUIConsumer 收到此事件后关闭 content 渲染器
            if ctx.content_full:
                publish_event("PhaseDoneEvent", label=ctx.label or "", phase="content")

        for tc in delta_tool_calls:
            idx = tc.get("index", 0)
            func = tc.get("function", {})
            tool_name = func.get("name", "") if func else ""
            if idx not in ctx.tool_calls_map:
                tc_id = tc.get("id", "")
                # ★ 缓存流式标签 _stream_label，与 convert_tool_calls_map 保持一致
                #   当 API 未提供 id 时使用 f"auto_{idx}" 而非 str(idx)，
                #   确保流式阶段和后端执行阶段使用相同标签，避免前端重复创建气泡。
                _stream_label = tc_id or f"auto_{idx}"
                ctx.tool_calls_map[idx] = {
                    "id": tc_id or "",
                    "name": tool_name,
                    "arguments": "",
                    "_stream_label": _stream_label,
                }
                # 🔥 发布 ToolParsingEvent 到 EventBus（Web 桥接器消费）
                if ctx.display is not None:
                    try:
                        ctx.display.tool_parsing(
                            _stream_label,
                            tool_name,
                            "",
                        )
                    except Exception:
                        _logger.warning("tool_parsing 首次调用异常", exc_info=True)
            else:
                # 已有此工具调用索引 → 参数在流式累积中，推送带当前参数的更新
                _entry = ctx.tool_calls_map[idx]
                if tool_name:
                    _entry["name"] = tool_name
                if ctx.display is not None:
                    if _entry.get("arguments"):
                        try:
                            # ★ 使用缓存的 _stream_label，确保标签在整个流式过程中一致
                            #   即使后续 chunk 提供了 id，也不改变已创建的流式标签，
                            #   避免前端因标签变化而创建重复气泡。
                            entry_id = _entry.get("_stream_label",
                                                   _entry.get("id", "") or f"auto_{idx}")
                            ctx.display.tool_parsing(
                                entry_id,
                                tool_name or _entry.get("name", ""),
                                _entry["arguments"],
                            )
                        except Exception:
                            _logger.warning("tool_parsing 更新调用异常", exc_info=True)

            entry = ctx.tool_calls_map[idx]
            tc_id = tc.get("id")
            if tc_id:
                entry["id"] = tc_id
                # ★ 当 id 在后续 chunk 中才到达时，同步更新 _stream_label
                #   确保 convert_tool_calls_map 能取到完整的 id，
                #   使流式气泡 label = 执行气泡 label = 真实 tool_call_id。
                entry["_stream_label"] = tc_id
            func = tc.get("function")
            if func:
                fname = func.get("name")
                if fname:
                    entry["name"] = fname
                fargs = func.get("arguments")
                if fargs:
                    entry["arguments"] += fargs
                    # 实时估算工具调用参数的 token 数，确保 token_estimate
                    # 在流式接收 dispatch_agent 等大参数时持续增长，
                    # 驱动 SpeedHandler 发出 update_live_output 更新。
                    ctx.token_estimate += estimate_tokens(fargs)
                    add_token_size(estimate_tokens(fargs))
                    ctx.speed_chunk_count += 1
