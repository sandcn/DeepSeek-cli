"""ToolCallsHandler — 处理工具调用增量"""
from __future__ import annotations
import logging
from ..context import StreamContext

_logger = logging.getLogger(__name__)


class ToolCallsHandler:
    """处理流式 tool_calls chunk"""

    async def handle(self, ctx: StreamContext, delta_tool_calls: list) -> None:
        """处理一段 tool_calls 增量（全异步）

        首次检测到 tool_calls 时 flush 缓冲区并启动 tracker。
        对每个新工具调用索引，通过 ctx.display.tool_parsing()
        发布 ToolParsingEvent 到 EventBus。
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
            # ChatUIConsumer 收到此事件后关闭 content 渲染器（去重助手，每流恰一次）
            if ctx.content_full:
                ctx.publish_phase_done_once("content")

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
                # 🔥 发布 ToolParsingEvent 到 EventBus
                #    label 使用 ctx.label（agent 标签，如 "agent-1"），
                #    确保 SubAgentPanelController 能正确查找对应 agent slot。
                if ctx.display is not None:
                    try:
                        ctx.display.tool_parsing(
                            ctx.label or "",
                            tool_name,
                            "",
                            tool_id=_stream_label,
                        )
                    except Exception:
                        _logger.warning("tool_parsing 首次调用异常", exc_info=True)
            else:
                # 已有此工具调用索引 → 参数在流式累积中，推送带当前参数的更新
                _entry = ctx.tool_calls_map[idx]
                if tool_name:
                    _entry["name"] = tool_name
                if ctx.display is not None:
                    _preview = _entry.get("_args_preview")
                    # ★ 2026-09-20（解析进度行卡住修复）：参数预览**未变化**时
                    #   不再重复发布 tool_parsing——修复前每个 tool_calls 增量
                    #   chunk 都发布一次（预览一旦达到 200 字符上限即恒定），
                    #   主 agent 侧每次映射为一条 CRITICAL（prio0）的
                    #   ``MainPhaseCmd(phase="parsing")``：单次工具解析实测可产生
                    #   上千条**完全幂等**的重复命令（``_do_main_phase`` 对同
                    #   phase 不重置计时，重复推送零信息增量），洪流挤满渲染
                    #   队列批处理 → 低优先级的解析进度行（ParseInfoCmd）被饿死
                    #   → 进度行数字长时间不刷新（「数据仍在传」）。
                    #   预览值变化才发布（前 200 字符内约数次），命令数从 O(分片
                    #   数) 降到 O(预览变化次数)；subagent 面板依赖的预览详情在
                    #   值不变时无需刷新，行为等价。
                    if _preview and _preview != _entry.get("_published_preview"):
                        try:
                            # ★ 使用缓存的 _stream_label，确保标签在整个流式过程中一致
                            #   即使后续 chunk 提供了 id，也不改变已创建的流式标签，
                            #   避免前端因标签变化而创建重复气泡。
                            entry_id = _entry.get("_stream_label",
                                                   _entry.get("id", "") or f"auto_{idx}")
                            # ★ 只发布参数**预览**（截断 200 字符）——修复前发布
                            #   完整累积参数（write_file 大 content）→ 子代理工具
                            #   记录 detail 存超大 JSON → 卡片渲染卡顿/CPU 满。
                            ctx.display.tool_parsing(
                                ctx.label or "",
                                tool_name or _entry.get("name", ""),
                                _preview,
                                tool_id=entry_id,
                            )
                            _entry["_published_preview"] = _preview
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
                    # ★ list 累积参数片段——避免 `entry["arguments"] += fargs`
                    #   对超大参数（write_file 大 content）O(n²) 复制
                    #   （1MB ≈ 175ms 接收卡顿）。
                    entry.setdefault("_args_parts", []).append(fargs)
                    # 惰性维护完整参数串：仅当消费方需要时 join（见
                    # stream_parse/_tool_parse_utils 的 _args_parts 读取）。
                    entry["arguments"] = ""
                    # ★ 参数预览增量累积（≤200 字符，避免 join 全部片段）——
                    #   供 tool_parsing 发布，防超大参数进入工具记录。
                    if len(entry.get("_args_preview", "")) < 200:
                        entry["_args_preview"] = entry.get("_args_preview", "") + fargs
                        if len(entry["_args_preview"]) > 200:
                            entry["_args_preview"] = entry["_args_preview"][:200]
                    # 实时估算工具调用参数的 token 数，确保 token_estimate
                    # 在流式接收 subagent 等大参数时持续增长，
                    # 驱动 SpeedHandler 发出 update_live_output 更新。
                    # 字符分类增量 → 整体估算（修复逐 fargs 分片分别估算的
                    # max(1,...) 系统性高估）。
                    # ★ 该参数增量同时计入 streamed_output_tokens（上下文
                    #   占用口径含 content + 工具参数）——模型流式生成工具
                    #   调用参数时模式行 ``main · N%`` 亦实时上升。
                    ctx.add_args_delta(fargs)
                    ctx.speed_chunk_count += 1
