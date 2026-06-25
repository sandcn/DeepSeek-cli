"""
ParallelExecutor — 使用 asyncio.gather 并行调度多个 SubAgent

支持两种模式：
1. 独立模式：run() 直接创建并执行 agents
2. 批量模式：多个 dispatch_agent 调用共享同一实例，
   通过 add_agent() 注册，barrier 协调，execute_all() 统一执行
"""

from __future__ import annotations

import asyncio
import os
import random
import logging
from typing import List, Dict, Any

from ._capture_manager import _safe_restore as safe_restore_stdout
from ._subagent_spawner import SubAgentSpawner
from .subagent import SubAgent
from ..ui.parallel import ParallelDisplay
from .ports.chat_ui import get_default_chat_ui_port
# TODO(Phase 3.2): 替换 ParallelDisplay → DisplayPort.create_sub_display()
#   await_stop 已添加至 SubDisplayPort 协议（步骤 16）。
#   - update_agent_status 签名差异（ParallelDisplay: (label, status) → AgentStatusPort: (agent_id, status, detail)）
#   - set_result 参数顺序差异
#   → 需统一接口签名后再替换
from ..config import STAGGER_MIN_DELAY, STAGGER_MAX_DELAY
from .constants import RED, RESET, AGENT_TYPE_ABBREV
from .constants import AGENT_TYPE_COLORS

_logger = logging.getLogger(__name__)

# ── 常量 ──────────────────────────────────────────────
_TIMEOUT = 3.0  # 显示停止等超时（秒）

# ── 结果字典键常量 ─────────────────────────────────
_DESCRIPTION_KEY = "description"
_ERROR_KEY = "error"
_LABEL_KEY = "label"
_RESULT_KEY = "result"
_AGENT_TYPE_KEY = "agent_type"


# ── 终端尺寸查询 ─────────────────────────────────────
# 复用 TerminalAdapter 的 ioctl 策略获取真实终端宽度。
# 不能依赖 shutil.get_terminal_size()（Android Termux 上返回
# 陈旧环境变量值），必须通过 /dev/tty ioctl 查询。
def _get_terminal_width() -> int:
    """获取终端宽度（列数），优先通过 /dev/tty ioctl 查询。"""
    import fcntl, termios, struct

    try:
        fd = os.open("/dev/tty", os.O_RDONLY)
        try:
            data = fcntl.ioctl(fd, termios.TIOCGWINSZ,
                               struct.pack("HHHH", 0, 0, 0, 0))
            rows, cols, _, _ = struct.unpack("HHHH", data)
            return cols if cols > 0 else 80
        finally:
            os.close(fd)
    except Exception:
        pass
    # 回退
    try:
        import shutil
        return shutil.get_terminal_size().columns
    except Exception:
        return 80


class ParallelExecutor:
    """并行执行多个 SubAgent，统一管理显示

    支持批量模式：多个 dispatch_agent 调用共享同一实例，
    通过 add_agent() 注册 agent specs，register_and_wait() 协调全部注册完成后
    由 _execute_all() 统一创建 SubAgent 并并发执行。

    同步机制：
    - 使用 asyncio.Event（纯异步等待，不消耗线程池工人）
    - 最后一个注册的协程触发执行
    """

    def __init__(self, parent_agent, max_history: int = 3, agent_factory=None, is_web: bool = False,
                 output_port=None, event_port=None):
        self.parent = parent_agent
        self.max_history = max_history
        self._agent_factory = agent_factory or SubAgent
        self._is_web = is_web

        # Ports（依赖注入，替代 ui 层直接引用）
        from .ports.output import get_default_output_port
        from .ports.events import get_default_event_port
        self._output_port = output_port if output_port is not None else get_default_output_port()
        self._event_port = event_port if event_port is not None else get_default_event_port()

        # SubAgent 创建 + 显示委托
        self._spawner = SubAgentSpawner(parent_agent, self._agent_factory, is_web)

        # 批量模式状态
        self._pending_specs: List[Dict[str, Any]] = []
        self._results: List[Dict[str, Any]] = []
        self._expected_count = 0
        self._registered_count = 0
        self._agents_lock = asyncio.Lock()
        self._all_done = asyncio.Event()

    # -- 批量模式 API --

    @property
    def is_batch_mode(self) -> bool:
        """是否已设置为批量模式（用于 dispatch_agent 判断）。"""
        return self._expected_count > 0

    def setup_barrier(self, count: int):
        """初始化并行执行，等待 count 个 agent 注册后统一执行。"""
        if count <= 0:
            _logger.warning("setup_barrier: count=%d <= 0，跳过 barrier 设置", count)
            return
        self._pending_specs.clear()
        self._results.clear()
        self._expected_count = count
        self._registered_count = 0
        self._all_done.clear()

    async def register_and_wait(self) -> None:
        """
        注册当前协程，等待全部 agent 注册完成后执行。

        设计要点：
        - 最后一个注册的协程自动触发 _execute_all()
        - 其他协程通过 asyncio.Event.wait() 纯异步等待，不消耗线程池工人
        """
        if self._expected_count <= 0:
            return
        async with self._agents_lock:
            self._registered_count += 1
            all_registered = (self._registered_count >= self._expected_count)
        if all_registered:
            await self._execute_all()
        else:
            await self._all_done.wait()

    def add_agent(self, description: str, prompt: str, agent_type: str = "plan_execute",
                  model: str = None, tool_label: str = None) -> int:
        """注册一个 agent spec，返回其在结果列表中的索引。

        Args:
            description: Agent 描述
            prompt: 完整指令
            agent_type: 子Agent 类型（默认 plan_execute，后续可扩展）
            model: 模型名（可选）
            tool_label: 所属 dispatch_agent 工具的 label，用于前端路由到正确容器
        """
        idx = len(self._pending_specs)
        self._pending_specs.append({
            _DESCRIPTION_KEY: description,
            "prompt": prompt,
            "agent_type": agent_type,
            "model": model,
            "tool_label": tool_label,
        })
        return idx

    def get_result(self, index: int) -> Dict[str, Any]:
        """获取指定索引的 agent 执行结果。"""
        if 0 <= index < len(self._results):
            return self._results[index]
        spec = self._pending_specs[index] if index < len(self._pending_specs) else {}
        return {
            _LABEL_KEY: f"agent-{index + 1}",
            _DESCRIPTION_KEY: spec.get(_DESCRIPTION_KEY, "?"),
            _RESULT_KEY: "",
            _ERROR_KEY: "内部错误：子代理结果尚未就绪",
        }

    async def _execute_all(self):
        """所有 agent 注册完毕后，统一创建 SubAgent 并并发执行。"""
        self._spawner.render_display(self._pending_specs)

        display = ParallelDisplay(max_history=self.max_history)

        # ★ 先发布 agent_added 事件，让前端提前创建 agent DOM 和 activeAgents 条目
        #   这样后续统一批量发布的 AgentResultEvent 才能被前端正确处理
        #   （handleAgentResult 依赖 activeAgents[label] 存在，否则丢弃结果）
        for i, spec in enumerate(self._pending_specs):
            label = f"agent-{i + 1}"
            desc = spec.get(_DESCRIPTION_KEY, label)
            dispatch_label = spec.get("tool_label", "")
            self._event_port.publish("agent_added", {
                "label": label, "description": desc, "status": "running",
                "dispatch_label": dispatch_label,
            }, source="parallel")

        try:
            coro = self._run_agents(self._pending_specs, display)
            self._results = await self._execute_with_error_handling(
                coro, self._pending_specs, display, is_batch=True,
            )
        finally:
            # 确保 barrier 释放（防止 pre-try 步骤失败导致其他协程永久等待）
            self._all_done.set()

    # -- 独立模式 --

    def _stream_results_markdown(self, results: List[Dict[str, Any]]):
        """将 subagent 结果以渲染后的 markdown 格式打印到终端。

        通过 _file=sys.__stdout__ 绕过 stdout 捕获（_SharedCapture），
        直接写入真实终端 stdout。
        仅在 ChatUIConsumer 未激活（无底部栏分屏）时使用。
        """
        import sys as _sys
        from src.api.renderer import IncrementalRenderer

        renderer = IncrementalRenderer(typing_speed=0, show_indicator=False,
                                       _file=_sys.__stdout__)
        try:
            for i, r in enumerate(results, 1):
                label = r.get(_LABEL_KEY, f"agent-{i}")
                desc = r.get(_DESCRIPTION_KEY, label)
                agent_type = r.get(_AGENT_TYPE_KEY, "plan_execute")
                abbr = AGENT_TYPE_ABBREV.get(agent_type, "??")
                result_text = r.get(_RESULT_KEY, "")
                error = r.get(_ERROR_KEY, "")
                renderer.write(f"### {i}. {AGENT_TYPE_COLORS.get(agent_type, RESET)}[{abbr}]{RESET} {desc}")
                if error:
                    renderer.write(f"\n> 错误: {error}\n")
                if result_text:
                    renderer.write(result_text)
                if not error and not result_text:
                    renderer.write("\n_空结果_\n")
        finally:
            renderer.close()

    def _stream_results_via_chatui(self, results: List[Dict[str, Any]]) -> None:
        """通过 ChatUI write_line 将子代理结果以 markdown 格式上屏。

        策略：先将 markdown 文本用 IncrementalRenderer 渲染为 ANSI 格式
        （保留完整的 markdown 渲染能力：标题加粗、代码高亮、引用块等），
        再将 ANSI 输出通过 port.write_line() 由 render 线程统一上屏。

        ChatUI 激活时用此路径替代 _stream_results_markdown，原因：
        - _stream_results_markdown 直接写 __stdout__ 会破坏 DECSTBM 分屏布局
        - 此路径用 StringIO 捕获 IncrementalRenderer 的 ANSI 输出，再路由到
          ChatUI 的统一渲染管线（render 线程持 output_lock 渲染，尊重分屏布局）

        线程安全：write_line 是线程安全的（入队 → render 线程统一消费）。
        StringIO 无竞态（仅在当前线程中访问）。
        Parser/Engine 无实例锁（单线程专用，不与其他渲染器共享）。
        """
        import io
        from src.api.renderer import IncrementalRenderer

        port = get_default_chat_ui_port()
        if not port.is_active():
            return

        # ── 构造完整的 markdown 文本 ──────────────────────────────────
        md_parts: list[str] = []
        for i, r in enumerate(results, 1):
            desc = r.get(_DESCRIPTION_KEY, f"子任务 {i}")
            agent_type = r.get(_AGENT_TYPE_KEY, "plan_execute")
            abbr = AGENT_TYPE_ABBREV.get(agent_type, "??")
            result_text = r.get(_RESULT_KEY, "")
            error = r.get(_ERROR_KEY, "")

            md_parts.append(f"### {i}. {AGENT_TYPE_COLORS.get(agent_type, RESET)}[{abbr}]{RESET} {desc}")
            if error:
                md_parts.append(f"\n> 错误: {error}\n")
            if result_text:
                md_parts.append(result_text)
            if not error and not result_text:
                md_parts.append("\n_空结果_\n")

        md_text = "\n".join(md_parts)
        if not md_text.strip():
            return

        # ── IncrementalRenderer 渲染 markdown → ANSI 字符串 ──────────
        # 使用 StringIO 捕获 Console 的 ANSI 输出，不直接写 __stdout__。
        # IncrementalRenderer 内部 Console 的 force_terminal=True 保证
        # 即使 file=StringIO 也输出完整 ANSI 序列。
        #
        # ★ 显式传入 width 参数：当 _file=StringIO 时 Rich Console 无法
        #   通过 ioctl 探测终端宽度，默认回退到 shutil.get_terminal_size()
        #   （Android Termux 上因环境变量陈旧返回 120），导致渲染输出行宽
        #   与真实终端（~70列）不匹配，在 ChatUI 中换行显示错乱。
        #   通过 ioctl(/dev/tty) 获取真实宽度并传入，消除错位重叠问题。
        term_width = _get_terminal_width()
        buf = io.StringIO()
        renderer = IncrementalRenderer(
            typing_speed=0, show_indicator=False, _file=buf, width=term_width,
        )
        try:
            renderer.write(md_text)
        finally:
            renderer.close()

        # ── ANSI 输出通过 ChatUI 上屏 ─────────────────────────────────
        # write_line 内部用 Text.from_ansi() 解析 ANSI 序列为 Rich Text，
        # 再由 render 线程的 OutputAdapter（Console with __stdout__）渲染。
        # 双 Console 转换（StringIO Console → __stdout__ Console）对标准
        # ANSI SGR 序列（颜色/样式）无损，保留完整 markdown 渲染效果。
        output = buf.getvalue()
        if output:
            port.write_line("")  # 开头空行
            for line in output.rstrip("\n").split("\n"):
                port.write_line(line)
            port.write_line("")  # 结尾空行

    def _do_terminal_output(self, results: List[Dict[str, Any]]):
        """在线程池中执行所有终端输出操作，避免同步 IO 阻塞事件循环。

        路由策略（二选一）：
        1. ChatUIConsumer 激活 → _stream_results_via_chatui
           （write_line 入队 → render 线程统一渲染，尊重 DECSTBM 分屏布局）
        2. ChatUIConsumer 未激活 → _stream_results_markdown
           （IncrementalRenderer 直接写 __stdout__，适用于非分屏模式）

        ChatUI 激活时无需光标修复：write_line 内部自动处理输出位置
        （render 线程持 output_lock，渲染后底部栏 force_redraw 刷新光标）。
        """
        port = get_default_chat_ui_port()
        if port.is_active():
            # ChatUI 激活 → 走统一渲染管线
            self._stream_results_via_chatui(results)
            return

        # ChatUI 不可用 → 回退到直接写 __stdout__
        # ────────────────────────────────────────────────────────────────
        # ★ 安全约束：该方法在线程池 (asyncio.to_thread) 中运行，
        #   所有 I/O 必须以 sys.__stdout__（真实终端）为准，禁止操作
        #   sys.stdout，因为它可能被同一事件循环中其他并发工具的
        #   _start_tool_output_capture 临时改为 _SharedCapture 实例。
        #   sys.stdout.write('\r\n') 写入 _SharedCapture 会触发
        #   real_stdout 间接写入，导致终端光标同步不可靠 & 产生多余事件。
        # ────────────────────────────────────────────────────────────────
        import sys as _sys

        self._stream_results_markdown(results)

        # ★ 终端光标重定位到下一行行首
        #   _stream_results_markdown 写入了大量内容到 __stdout__，终端光标已
        #   移至最后一行末尾，但 mainagent 的 Spinner \r\033[K 依赖"当前在行首"
        #   的假设修正上一次的 Spinner 行，否则 Spinner 不可见。
        #
        #   全部使用 __stdout__（真实终端），而非 sys.stdout（可能被并发工具
        #   的 _SharedCapture 劫持），消除竞态窗口。
        from ..ui._lock import _try_acquire_output_lock
        with _try_acquire_output_lock(name="parallel_executor.cursor_fix", timeout=1.0):
            _sys.__stdout__.write('\r\n')
            _sys.__stdout__.flush()

    async def _execute_with_error_handling(
        self, coro, specs: List[Dict[str, Any]], display: ParallelDisplay,
        *, is_batch: bool,
    ) -> List[Dict[str, Any]]:
        """封装 try/except/finally 错误处理模式，消除 _execute_all / run 重复。

        Args:
            coro: 主协程（通常是 self._run_agents(specs, display)）
            specs: agent specs 列表（用于构造降级结果）
            display: ParallelDisplay 实例
            is_batch: True=_execute_all 批量模式, False=run 独立模式

        Returns:
            结果列表 [{label, description, result, error}]
        """
        error_prefix = "_execute_all 异常" if is_batch else "独立模式异常"
        # ── 日志/错误键前缀（与 safe_restore 前缀不同） ──────
        # 原 run() 日志用 "独立模式"，safe_restore 用 "run"
        log_prefix = "_execute_all" if is_batch else "独立模式"
        trace_prefix = "_execute_all" if is_batch else "run"
        mode_name = "批量模式" if is_batch else "独立模式"

        results: list | None = None
        try:
            results = await coro
        except asyncio.CancelledError:
            _logger.warning("%s 被取消，降级为空结果", mode_name)
            results = [
                {_LABEL_KEY: f"agent-{i+1}",
                 _DESCRIPTION_KEY: spec.get(_DESCRIPTION_KEY, f"子任务 {i+1}"),
                 _RESULT_KEY: "", _ERROR_KEY: "cancelled",
                 _AGENT_TYPE_KEY: spec.get("agent_type", "plan_execute")}
                for i, spec in enumerate(specs)
            ] if specs else []

            # ★ 在 CancelledError 传播前执行输出逻辑，避免 finally 中
            #   asyncio.to_thread 被取消导致输出丢失
            try:
                await display.await_stop(timeout=_TIMEOUT)
            except Exception:
                _logger.exception("%s 取消路径 await_stop 异常", log_prefix)

            if results:
                # 取消路径下直接调用（非 to_thread），避免子任务被取消
                if is_batch:
                    self._do_terminal_output(results)
                else:
                    self._stream_results_markdown(results)
                    import sys as _sys
                    _sys.__stdout__.flush()

            # ★ sys.stdout 泄漏检测
            try:
                safe_restore_stdout(
                    f"{trace_prefix} 取消路径检测到 sys.stdout 泄漏 (孤立 _SharedCapture)"
                )
            except Exception:
                _logger.warning("%s 取消路径 stdout 泄漏检测异常",
                                trace_prefix, exc_info=True)

            if results:
                self._spawner.publish_summary(results)
            raise
        except Exception as e:
            _logger.error("%s: %s", error_prefix, e, exc_info=True)
            results = [
                {_LABEL_KEY: f"agent-{i+1}",
                 _DESCRIPTION_KEY: spec.get(_DESCRIPTION_KEY, f"子任务 {i+1}"),
                 _RESULT_KEY: "", _ERROR_KEY: f"{error_prefix}: {e}",
                 _AGENT_TYPE_KEY: spec.get("agent_type", "plan_execute")}
                for i, spec in enumerate(specs)
            ] if specs else [{_LABEL_KEY: "?", _DESCRIPTION_KEY: "?",
                              _RESULT_KEY: "", _ERROR_KEY: f"{error_prefix}: {e}",
                              _AGENT_TYPE_KEY: "plan_execute"}]
        finally:
            # 用 None 哨兵检查 results 是否已被赋值
            if results is None:
                results = [
                    {_LABEL_KEY: f"agent-{i+1}",
                     _DESCRIPTION_KEY: spec.get(_DESCRIPTION_KEY, f"子任务 {i+1}"),
                     _RESULT_KEY: "", _ERROR_KEY: "结果未就绪",
                     _AGENT_TYPE_KEY: spec.get("agent_type", "plan_execute")}
                    for i, spec in enumerate(specs)
                ]

            # 停止 display（终止刷新线程 + 渲染最终帧）
            try:
                await display.await_stop(timeout=5.0 if is_batch else _TIMEOUT)
            except Exception:
                _logger.exception("%s await_stop 异常", log_prefix)

            # ★ 批量模式：在打印 markdown 结果前，停止 dispatch_agent 的 Spinner
            if is_batch and not self._is_web and results:
                import sys as _sys
                parent_display = getattr(self.parent, 'display', None)
                if parent_display is not None:
                    for spec in specs:
                        dispatch_label = spec.get("tool_label", "")
                        if dispatch_label:
                            try:
                                parent_display.tool_done(
                                    dispatch_label, "dispatch_agent", success=True,
                                )
                            except Exception:
                                _logger.warning("dispatch_agent tool_done 异常", exc_info=True)
                # 换行，确保后续 markdown 内容从新行开始
                # ChatUI 激活时 write_line 自带换行，无需写 __stdout__ 破坏分屏布局
                if not get_default_chat_ui_port().is_active():
                    self._output_port.write_with_lock("")

            # 在线程池中执行所有终端输出操作，避免同步 IO 阻塞事件循环
            # 统一通过 _do_terminal_output 路由：ChatUI 激活时走 write_line，
            # ChatUI 未激活时走 IncrementalRenderer 直接写 __stdout__。
            if results:
                await asyncio.to_thread(self._do_terminal_output, results)

            # ★ sys.stdout 泄漏检测
            try:
                safe_restore_stdout(
                    f"{trace_prefix} 检测到 sys.stdout 泄漏 (孤立 _SharedCapture)"
                )
            except Exception:
                _logger.warning("%s stdout 泄漏检测异常", trace_prefix, exc_info=True)

            # 统一批量发布 AgentResultEvent
            if results:
                self._spawner.publish_summary(results)
        return results

    async def run(self, agent_specs: List[Dict[str, Any]], max_workers: int | None = None) -> List[Dict[str, Any]]:
        """
        并行运行多个子 Agent。

        agent_specs: [{_DESCRIPTION_KEY: str, "prompt": str, "model": str (可选)}]
        max_workers: 最大并行数，默认 None（无限制，等于 task 数量）
        返回: [{_LABEL_KEY: str, _DESCRIPTION_KEY: str, _RESULT_KEY: str, _ERROR_KEY: str}]
        """
        if not self._is_web:
            self._spawner.render_display(agent_specs)

        display = ParallelDisplay(max_history=self.max_history)
        coro = self._run_agents(agent_specs, display)
        return await self._execute_with_error_handling(
            coro, agent_specs, display, is_batch=False,
        )

    async def _run_agents(self, specs: List[Dict[str, Any]], display: ParallelDisplay) -> List[Dict[str, Any]]:
        """创建 SubAgent 列表 → gather 执行 → 结果收集

        提取自 run() 和 _execute_all() 的公共逻辑。

        Args:
            specs: agent specs 列表
            display: ParallelDisplay 实例

        Returns:
            结果列表 [{"label", "description", "result", "error"}]
        """
        agents: List[SubAgent] = []
        display.set_panel_context(get_default_chat_ui_port().get_panel_context())
        display.start()

        for i, spec in enumerate(specs):
            sa = self._spawner.spawn(spec, i, display)
            agents.append(sa)
        coros = [self._run_one(sa, display, stagger=i) for i, sa in enumerate(agents)]
        raw_results = await asyncio.gather(*coros, return_exceptions=True)

        results = []
        for r in raw_results:
            if isinstance(r, BaseException):
                if isinstance(r, asyncio.CancelledError):
                    _logger.info("SubAgent %s was cancelled (expected)", getattr(r, 'label', '?'))
                else:
                    _logger.error("SubAgent task failed with: %s", r)
                results.append({_LABEL_KEY: "?", _DESCRIPTION_KEY: "?",
                               _RESULT_KEY: "", _ERROR_KEY: str(r)})
            else:
                results.append(r)
        return results

    async def _run_one(self, sa: SubAgent, display: ParallelDisplay, stagger: int = 0) -> Dict[str, Any]:
        if stagger > 0:
            # 限制最大总延迟不超过 STAGGER_MAX_DELAY，避免大量并发时线性累积
            base = random.uniform(STAGGER_MIN_DELAY, STAGGER_MAX_DELAY)
            delay = min(stagger * base, STAGGER_MAX_DELAY * 3)
            await asyncio.sleep(delay)
        try:
            result = await sa.run()
            display.update_agent_status(sa.label, "done")
            self._event_port.publish("agent_status_changed", {
                "label": sa.label, "status": "done",
            }, source="parallel")
            display.set_result(sa.label, result_text=result)
            # AgentResultEvent 不再逐个发布，待全部 subagent 完成后统一批量发布
            return {_LABEL_KEY: sa.label, _DESCRIPTION_KEY: sa.description,
                    _RESULT_KEY: result, _ERROR_KEY: "",
                    _AGENT_TYPE_KEY: sa.agent_type}
        except asyncio.CancelledError:
            _logger.warning("SubAgent %s 被取消", sa.label)
            display.update_model_phase(sa.label, "error", "cancelled")
            display.update_agent_status(sa.label, "fail")
            display.set_result(sa.label, error="cancelled")
            self._event_port.publish("agent_status_changed", {
                "label": sa.label, "status": "fail",
            }, source="parallel")
            # 不 raise，改为返回结果 dict，保证 agent 身份不丢失
            return {_LABEL_KEY: sa.label, _DESCRIPTION_KEY: sa.description,
                    _RESULT_KEY: "", _ERROR_KEY: "cancelled",
                    _AGENT_TYPE_KEY: sa.agent_type}
        except Exception as e:
            _logger.error("SubAgent %s failed: %s", sa.label, e)
            display.update_model_phase(sa.label, "error", str(e))
            display.update_agent_status(sa.label, "fail")
            display.set_result(sa.label, error=str(e))
            self._event_port.publish("agent_status_changed", {
                "label": sa.label, "status": "fail",
            }, source="parallel")
            # AgentResultEvent 不再逐个发布，待全部 subagent 完成后统一批量发布
            return {_LABEL_KEY: sa.label, _DESCRIPTION_KEY: sa.description,
                    _RESULT_KEY: "", _ERROR_KEY: str(e),
                    _AGENT_TYPE_KEY: sa.agent_type}
