"""SpeedHandler — 速度追踪和实时 token 显示"""
from __future__ import annotations
from ...stats import accumulate_usage, _notify_stream_progress
from ..context import StreamContext


class SpeedHandler:
    """追踪流式渲染速度和实时 token 显示"""

    def _do_accumulate(self, ctx: StreamContext) -> None:
        """执行 token 估计累积逻辑（try_update 和 final_update 共用）。"""
        if ctx._live_total_dirty:
            ctx._live_total_dirty = False
        if ctx.content_full or ctx.reasoning_full:
            est = ctx.token_estimate
            delta_live = est - ctx.last_live_est
            if delta_live > 0:
                # #93 修复：同步通过 update_usage 向前端 status 弹窗发送实时 token 估计值，
                # 确保主 Agent 生成时 status 弹窗显示真实 token 数而非 charCount/3 估算。
                # 实时累积估算的 output token 到全局统计，
                # 确保流式生成 subagent 等大参数时
                # 总 token 数持续增长，避免"总tok数量没实时增加"。
                # 注意：这是实时估算累计，不是真实 API 调用，
                # increment_calls=False 防止 /cost 调用次数虚高
                # （真实 usage 到达后由 _handle_usage 统一计一次 calls）。
                accumulate_usage({"input": 0, "output": delta_live},
                                 increment_calls=False)
                ctx.last_live_est = est
                if ctx.display and ctx.label:
                    ctx.display.update_live_output(ctx.label, delta_live)

    def try_update(self, ctx: StreamContext) -> None:
        """尝试更新速度显示（按间隔控制频率）"""
        if ctx.final_usage_received:
            return
        now = ctx.now
        if now - ctx.speed_last_update < ctx.speed_update_interval or ctx.speed_chunk_count <= 0:
            return

        window = now - ctx.speed_last_update
        if window > 0:
            speed = ctx.speed_chunk_count / window
            if ctx.display and ctx.label:
                ctx.display.update_speed(ctx.label, speed)

        self._do_accumulate(ctx)

        ctx.speed_chunk_count = 0
        ctx.speed_last_update = now

        # 通知 TUI 流式进度（≈0.5s 间隔）
        _notify_stream_progress()

    def final_update(self, ctx: StreamContext) -> None:
        """流结束前的最终速度更新"""
        if ctx.final_usage_received:
            return
        if ctx.speed_chunk_count > 0:
            now = ctx.now
            window = now - ctx.speed_last_update
            if window > 0:
                speed = ctx.speed_chunk_count / window
                if ctx.display and ctx.label:
                    ctx.display.update_speed(ctx.label, speed)

        self._do_accumulate(ctx)
