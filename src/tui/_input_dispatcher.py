"""InputDispatcher — TUI 输入事件分发胶水（提取自 _input.py，方向A 步骤1）。

将 Input 上帝类中 render 线程分发逻辑逐行迁移，保持零逻辑改动：
  - read_stdin_once 主循环 / process_events
  - _dispatch_key_event / _handle_tab / _handle_arrow_up / _handle_arrow_down
  - _dismiss_completion / _trigger_auto_completion
  - _do_interrupt（interrupt 回调注入）/ _handle_special_key
  - _parse_escape_sequence 委托 InputParser

InputDispatcher 组合持有 InputIO + InputBufferEditor + InputParser + 全部回调；
``read_stdin_once`` 状态检查（_fd_status / _active / _stop）委托 InputIO。

设计模式: 模板方法（Template Method）——``read_stdin_once()`` 骨架，
``_do_interrupt()`` / ``_handle_special_key()`` 具体步骤。

依赖方向:
  _input.py → _input_dispatcher.py 单向依赖；本模块不得 import _input（避免循环）。

模块级 ``import select`` / ``import os`` 供读取方法使用；可被
``patch("select.select", ...)`` 经共享 select 模块全局拦截（P3-4 移除
_input.py 的 select import 后，patch 目标统一为顶层模块）。
"""

from __future__ import annotations

import logging
import os
import select
import threading
from typing import TYPE_CHECKING

from ._input_parser import InputParser, KeyEvent
from ._completion_nav import _CompletionNavHandler

if TYPE_CHECKING:
    from ._input_io import InputIO
    from ._input_buffer import InputBufferEditor

_logger = logging.getLogger(__name__)

# ── 批量读取大小（字节） ──
# read_stdin_once 一次 os.read 读入的最大字节数——快速打字/IME 上屏/粘贴时
# 一次读入多个字节（剩余存入 InputIO._pending 由后续调用消费），系统调用数
# 从 2N 降至 2（select+os.read）；4096 足够覆盖单次输入突发。
_READ_BATCH = 4096


# ═══════════════════════════════════════════════════════════
# InputDispatcher — 事件分发胶水
# ═══════════════════════════════════════════════════════════

class InputDispatcher:
    """输入事件分发胶水。

    组合持有 InputIO（原始 I/O）+ InputBufferEditor（缓冲/历史/队列）
    + InputParser（ANSI 解析）与全部回调，承担 render 线程输入分发。

    由 Input 薄外观委托调用；``read_stdin_once()`` 为模板方法骨架。
    """

    def __init__(
        self,
        io: "InputIO",
        buffer_editor: "InputBufferEditor",
        parser: InputParser,
    ) -> None:
        self._io = io
        self._buffer_editor = buffer_editor
        self._parser = parser
        # ★ 补全导航策略（模块边界优化，2026-08-05）：Tab/箭头/翻页/Shift+Tab
        #   的补全弹窗交互独立为 _CompletionNavHandler；本类经组合委托。
        self._completion_nav = _CompletionNavHandler(self)

        # ── 回调引用 ──
        self._special_key_callback = None
        self._completion_callback = None
        self._dismiss_completion_callback = None
        self._completion_navigate_callback = None
        self._auto_completion_callback = None
        # ★ interrupt 回调注入（方向A 步骤1）：由 _loop.py _setup_monitor 注入，
        #   None 缺省时 _do_interrupt 记 debug 日志并跳过（保证测试兼容）。
        self._interrupt_callback = None
        # ★ kill_background 回调注入（2026-08-21 用户需求：按 ESC 杀后台任务）：
        #   仅纯 Esc（kind="escape"）中断时调用（Ctrl+C/双 Esc 不杀后台任务），
        #   由 _loop.py / clawbot.runner 注入（request_kill_background +
        #   跨线程调度杀任务）；None 缺省时跳过（测试兼容）。
        self._kill_background_callback = None

        # ★ P2-8（review）：Enter 提交历史追加回调注入——``_handle_special_key``
        #   的 ``_enter`` 经此回调注入（与 ``Input._enter`` 的 append_history
        #   注入语义一致：测试 patch 外观 ``_append_history_locked`` 拦截路径
        #   有效）。None 时 ``_enter`` 使用 buffer_editor 自身
        #   ``_append_history_locked``（缺省语义，生产行为不变）。
        self._enter_append_history = None

        # ── Enter 抑制 ──
        self._suppress_enter: bool = False
        self._suppress_enter_lock = threading.Lock()

        # ── 残留 Enter 标记（editmsg 竞态修复） ──
        # editmsg 选择确认 Enter（CR）被抑制后，标记可能存在残留 LF（\n）待丢弃。
        # GIL 原子 bool，与 _suppress_enter 同等无锁访问（不改 API 签名）。
        # ★ 2026-08-19（很多上文时按回车不能编辑对应消息修复）：丢弃判定改为
        #   **纯字节序语义**（无时间窗口）——CR 置位后下一个分发的字节是 LF
        #   则丢弃，无论多久之后到达。修复前带 0.5s 固定窗口（
        #   ``_ENTER_RESIDUAL_WINDOW``）：消息很多时渲染线程一帧（大消息区
        #   重放/markdown 渲染）耗时可超 0.5s，CR 与 LF 分开被 os.read 读到
        #   （终端/SSH 分包）时 LF 的消费时刻超出窗口 → 被当作用户新按的
        #   Enter（弹窗被自动确认编辑默认最后一条 / prefill 被 _enter() 误
        #   提交重发）。字节流语义上 CR 后紧邻的 LF 只可能是同一次按键——
        #   时间窗口在该场景是误判源，移除（标记在任何字节处理时先清除，
        #   只影响紧邻 CR 的下一个字节，无误丢用户新输入）。
        self._enter_residual_pending: bool = False

        # ── 窗口期 Enter 提交意图捕获（editmsg「很多上文时按回车不能编辑」修复） ──
        # 弹窗确认后 → prefill 注入前存在长窗口（flush_input_router 等慢帧 +
        # flush_stdin_buffer 丢字节 + 插件清 _input_ready 丢空提交），期间
        # 用户按的 Enter 会被无痕丢弃 →「按回车没反应，要再按一次」。
        # capture 激活期（message_editor 弹窗确认后开启）内被吞/被丢的 Enter
        # 记为「提交意图」（deferred），插件注入 prefill 后消费并自动提交。
        self._enter_capture_active: bool = False
        self._deferred_enter_pending: bool = False

        # ── 非可打印字符捕获 ──
        self._captured_input: bytearray = bytearray()
        self._captured_lock = threading.Lock()

        # ── input hook router（ink useInput 钩子优先分发） ──
        # 注入路由回调后，_dispatch_key_event 先询问 router：返回 True=消费
        # （跳过旧回调路径），False=放行（走旧路径，零行为变化）；router 异常
        # 视为放行（不阻断输入）。None 缺省时行为与未注入完全一致。
        self._input_hook_router = None

        # ── 任意键按下回调（ink useStdin().isAnyKeyPressed 置位） ──
        # 每个输入字节分发前调用（回调注入由 session.set_input 接线；
        # None 缺省零开销）。置位回调幂等（bool 标志），无返回值约定。
        self._key_pressed_callback = None

        # ── Ctrl+C（interrupt）事件 router 放行标志（React Ink
        #    exitOnCtrlC=False 语义） ──
        # 默认 False：interrupt 事件（0x03 Ctrl+C / 双 Esc）直接走
        # ``_do_interrupt``（生产中断路径，行为不变）；render() 独立会话
        # ``exitOnCtrlC=False`` 时置 True——interrupt 事件先问 input router，
        # 消费则跳过中断路径（React Ink 语义：Ctrl+C 交给 useInput handler）。
        self._interrupt_routable: bool = False

        # ── 反向历史搜索（方向D 步骤14，Ctrl+R 配置门控） ──
        # 默认 False 保持既有 Ctrl+R switch_model 语义；装配注入
        # TuiConfig.reverse_search_enabled。
        self._reverse_search_enabled: bool = False
        self._reverse_search_callback = None

        # ── Esc 取消输入（方向D 步骤16，配置门控） ──
        # 默认 False 保持既有 Esc 中断语义；装配注入
        # TuiConfig.esc_cancel_input 与活跃状态回调（生成中不取消输入）。
        self._esc_cancel_input: bool = False
        self._active_status_fn = None

        # ── Ctrl+L 清屏回调（Claude TUI parity 步骤 3.1，装配注入） ──
        # 注入 session.clear_screen；未注入时 Ctrl+L 记 debug 跳过（测试兼容）。
        self._clear_screen_callback = None

        # ── Ctrl+H 轨迹视图开关回调（2026-08-19，装配注入） ──
        # Ctrl+H（0x08 字节 / CSI u \x1b[104;5u、\x1b[8;5u）→ 打开/关闭 DSH
        # 风格轨迹视图（左台账 + 右检查器）。未注入回调时回退 backspace
        # （0x08 传统 BS 语义——行为与修复前一致，测试/无装配场景兼容）。
        self._trace_toggle_callback = None

    # ═══════════════════════════════════════════════════════
    # 中断与特殊按键处理（render 线程调用）
    # ═══════════════════════════════════════════════════════

    def _do_interrupt(self, kill_background: bool = False) -> None:
        """内联中断处理：设置中断标志 + 清空回显 + 请求异步中断（回调注入）。

        在 render 线程中调用（快速路径，由 ``read_stdin_once()`` 直接分发）。

        Args:
            kill_background: True 表示本次中断来自**纯 Esc**（kind="escape"，
                用户明确要求杀掉所有后台 bash/subagent）；False 表示普通中断
                （Ctrl+C/双 Esc，只终止当前生成，不杀后台任务）。

        ★ interrupt 回调注入（方向A 步骤1）：原实现直接调用
        ``src.api.interrupt_async.request_interrupt_async()``（L42 import + L419 调用），
        现改为调用注入回调（``set_interrupt_callback``，由 _loop.py _setup_monitor
        注入 ``lambda: request_interrupt_async()``）；未注入时记 debug 日志并跳过，
        保证测试兼容（不抛异常）。
        """
        if self._io.stop.is_set():
            return
        # P2-5：搜索模式中断（Ctrl+C/双 Esc）→ 先退出搜索并同步 UI 状态，
        # 避免 model.history_search 残留 active=True（input-area 持续渲染
        # (reverse-i-search) 覆盖行）。reset_and_echo 的 buffer_editor.reset()
        # 也会清理搜索内部状态，但未调用 _sync_reverse_search——UI 侧不同步。
        if self._buffer_editor.is_search_active():
            self._buffer_editor.search_exit(apply=False)
            self._sync_reverse_search()
        if not self._buffer_editor.has_queued_input():
            self.reset_and_echo()
        else:
            self._io._flush_stdin_residual()
        self._io.set_interrupted()
        cb = self._interrupt_callback
        if cb is None:
            _logger.debug("_do_interrupt: 未注入 interrupt 回调，跳过异步中断请求")
        else:
            try:
                cb()
            except Exception:
                _logger.debug("_do_interrupt: interrupt 回调异常", exc_info=True)
        # ★ 纯 Esc：额外触发 kill_background 回调（杀所有后台 bash/subagent）。
        #   普通中断（Ctrl+C/双 Esc/命令触发）不置位——后台任务继续运行。
        #   注：kill 回调在 reset_and_echo（清空输入缓冲）之后执行——ESC
        #   中断语义下用户输入缓冲本就清空（既有行为），杀后台任务紧随其后；
        #   若未来需求「ESC 保留输入缓冲」需调整此处顺序。
        if kill_background:
            self._trigger_kill_background()

    def _trigger_kill_background(self) -> None:
        """触发纯 Esc 杀后台任务回调（_do_interrupt / _cancel_input 共用）。

        回调由 UI 层注入（_loop.py / clawbot.runner 的 _on_escape_kill：
        request_kill_background + 跨线程调度杀所有后台任务）；未注入时记
        debug 日志跳过（测试兼容，不抛异常）。
        """
        kcb = self._kill_background_callback
        if kcb is None:
            _logger.debug(
                "_trigger_kill_background: 未注入 kill_background 回调，"
                "跳过杀后台任务",
            )
        else:
            try:
                kcb()
            except Exception:
                _logger.debug(
                    "_trigger_kill_background: 回调异常", exc_info=True,
                )

    def _handle_ctrl_key(self, ch: str) -> None:
        """Ctrl 组合键统一分发（Claude TUI parity 步骤 3）。

        直接控制字符路径与 CSI u 转义路径共用（避免两处分支漂移）：
          - Ctrl+G/O → vim / editmsg（既有）
          - Ctrl+R → 反向历史搜索（配置门控）或 retry（重生成上一轮）
          - Ctrl+N → switch_model（保留）
          - Ctrl+L → 清屏（非流式时；未注入回调跳过）
          - Ctrl+D → EOF（空缓冲提交 exit；非空 no-op 防误退）
          - Ctrl+T → 主题切换
          - P2-5：Ctrl+E（\x05）由 _decode_control_char 映射为 end 事件
            （readline 行尾），本分支不可达（不再有 no-op 兜底）。
        """
        if ch == '\x07':          # Ctrl+G → vim
            self._handle_special_key('vim')
        elif ch == '\x0f':        # Ctrl+O → /editmsg
            self._handle_special_key('editmsg')
        elif ch == '\x08':        # Ctrl+H → 轨迹视图开关（2026-08-19）
            # ★ 字节语义：0x08（BS）在现代终端为 Ctrl+H；Backspace 键发送
            #   0x7f（DEL，_decode_control_char 已改判 backspace）。未注入
            #   轨迹回调时回退 backspace（0x08 传统 BS 语义兼容——仅发送
            #   ^H 而非 DEL 的旧式终端退格仍可用）。
            self._handle_trace_toggle()
        elif ch == '\x12' and self._reverse_search_enabled:
            # 方向D 步骤14：Ctrl+R 反向历史搜索（配置门控，默认 False）
            self._handle_reverse_search()
        elif ch == '\x0c':        # Ctrl+L → 清屏（流式保护：生成中忽略）
            if not self._is_active_status():
                self._handle_clear_screen()
        elif ch == '\x04':        # Ctrl+D → EOF
            self._handle_ctrl_d()
        elif ch == '\x14':        # Ctrl+T → 主题切换
            self._handle_special_key('toggle_theme')
        elif ch == '\x12':        # Ctrl+R → 重新生成上一轮（Claude parity 3.4）
            # ★ review 方向（门控语义明确）：本分支仅在
            # ``reverse_search_enabled=False`` 时可达——elif 链中上一分支
            # ``ch == '\x12' and self._reverse_search_enabled`` 已拦截反向
            # 搜索；两分支互斥，不存在"同时触发"路径。
            self._handle_special_key('retry')
        elif ch == '\x0e':        # Ctrl+N → 切换模型（保留）
            self._handle_special_key('switch_model')
        elif ch == '\x10':        # Ctrl+P → 历史上一条（readline previous-history，
            # 直接调 _up（与 ↑ 语义一致但**不经过补全导航**——补全弹窗可见时
            # ↑ 移动高亮，Ctrl+P 恒为历史浏览，readline 用户习惯）。
            # 与 Ctrl+N（switch_model）非对称——Ctrl+N 已占用，Ctrl+P 独立提供
            # 历史回退（readline 前向键由 ↓ 承担）。
            # 防御：反向搜索激活时先退出搜索（Ctrl+P 不与搜索状态叠加——
            # 搜索模式查询/匹配被历史浏览干扰时状态错乱）。
            if self._buffer_editor.is_search_active():
                self._buffer_editor.search_exit(apply=False)
                self._sync_reverse_search()
            else:
                self._buffer_editor._up()
        elif ch == '\x02':        # Ctrl+B → 主 agent 空模式切换
            self._handle_special_key('empty_mode')
        # else：未知 ctrl_key → no-op

    def _handle_clear_screen(self) -> None:
        """Ctrl+L 清屏：调用注入的 clear_screen 回调（未注入记 debug 跳过）。"""
        cb = self._clear_screen_callback
        if cb is None:
            _logger.debug("Ctrl+L: 未注入 clear_screen 回调，跳过")
            return
        try:
            cb()
        except Exception:
            _logger.debug("Ctrl+L clear_screen 回调异常", exc_info=True)

    def _handle_trace_toggle(self) -> None:
        """Ctrl+H 轨迹视图开关：调用注入的 trace 回调（未注入回退 backspace）。

        2026-08-19：0x08（Ctrl+H）在现代终端与 Backspace 键（0x7f DEL）字节
        可区分——注入回调时作为轨迹视图（DSH 风格台账 + 检查器）开关；未注入
        （测试/无装配场景）时回退 ``_backspace()``，0x08 传统 BS 语义保持
        （发送 ^H 而非 DEL 的旧式终端退格不回归）。
        """
        cb = self._trace_toggle_callback
        if cb is None:
            self._buffer_editor._backspace()
            return
        try:
            cb()
        except Exception:
            _logger.debug("Ctrl+H trace 回调异常", exc_info=True)

    def _handle_ctrl_d(self) -> None:
        """Ctrl+D EOF：空缓冲 → 提交 exit；非空 no-op（防误退）。

        方向1 B2：空缓冲提交 "exit" 不写入历史——复用 ``_enter`` 既有
        ``append_history`` 注入参数传 no-op lambda（零 API 变更），避免
        Ctrl+D 空缓冲 "exit" 污染历史文件。

        方向2（editmsg Ctrl+D 绕过抑制修复）：editmsg 选择期间
        （``_suppress_enter=True``）Ctrl+D no-op——修复前空缓冲 Ctrl+D 提交
        "exit" 绕过 Enter 抑制（编辑期间误退出）。
        """
        if self.get_suppress_enter():
            return
        if self._buffer_editor.get_current_text():
            return
        self._buffer_editor.set_buffer("exit")
        self._buffer_editor._enter(append_history=lambda _text: None)

    def _handle_special_key(self, action: str) -> None:
        """处理特殊按键（Ctrl+G/O/N/R/T）：直接调用回调并应用结果。

        在 render 线程中调用（由 ``read_stdin_once()`` 直接分发）。
        终端模式切换由回调函数内部直接操作 EscapeMonitor 完成。

        ★ 收敛确认（方向A 步骤1）：vim / editmsg / switch_model 业务已完全由
        ``_special_key_callback``（_special_keys.py 工厂，_loop.py 注入）承担；
        Input 仅保留 result 应用——editmsg/retry 的 reset / set_buffer /
        handle_chars + ``_enter`` 属缓冲编辑职责（InputBufferEditor），保留。

        ★ 方向1 B3：retry 路径在 reset 前保存草稿，``_enter()`` 提交后恢复
        草稿到缓冲（不丢用户输入）；editmsg 保持既有行为（编辑流程刻意替换
        缓冲）。评估对照：Claude Code 中 Ctrl+R 为反向搜索（方向4 默认开启），
        retry 路径仅在 reverse_search_enabled=False 时可达，草稿保留为
        「至少不丢草稿」的保守实现。
        """
        cb = self._special_key_callback
        if cb is None:
            return
        text = self._buffer_editor.get_current_text()
        try:
            result = cb(action, text)
        except Exception:
            _logger.warning("特殊按键回调异常 (action=%s)", action, exc_info=True)
            return
        draft: str | None = None
        if result is not None and result != text:
            if action in ('editmsg', 'retry'):
                # 方向1 B3：retry 在 reset 前保存草稿（_enter 提交后恢复，
                # 不丢用户输入）；editmsg 保持既有行为（编辑流程刻意替换）。
                if action == 'retry':
                    draft = self._buffer_editor.get_current_text()
                self.reset()
                self._buffer_editor.set_buffer(result)
            else:
                # P2（2026-08-07）：非 editmsg/retry action（vim/switch_model/
                # toggle_theme/empty_mode）不清空未消费排队输入——用户 Enter
                # 提交后、编排器消费前触发此类 action，reset 清空
                # _submitted_text/_input_ready 会丢弃首次提交文本。
                self.reset(clear_queue=False)
                self._buffer_editor.handle_chars(result)
        # ★ review 方向：仅回调返回非 None（有实际结果）时才提交——回调返回
        #   None（异常/插件返回"无操作"）时不应意外提交当前缓冲文本。当前实际
        #   回调（app_loop/_special_keys.py）恒返回 '/editmsg'/'/retry'（非 None），
        #   此守卫为防御性（未来回调/插件异常路径）。
        if result is not None and action in ('editmsg', 'retry'):
            # editmsg/retry 是用户主动发起的提交操作（Ctrl+O/Ctrl+R），
            # 清除 _suppress_enter 确保 _enter() 不被抑制
            self.set_suppress_enter(False)
            # P2-8（review）：注入 append_history——经 ``set_enter_append_history``
            # 注入的回调（与 ``Input._enter`` 的 append_history 注入语义一致），
            # None 时 ``_enter`` 使用 buffer_editor 自身 ``_append_history_locked``
            # （缺省行为不变）。
            self._buffer_editor._enter(append_history=self._enter_append_history)
            # 方向1 B3：retry 提交后恢复用户草稿（供继续编辑）；draft 为空时
            # 行为与现状一致（不恢复）。用 handle_chars 而非 set_buffer——
            # set_buffer 会清空 _submitted_text/_input_ready，导致 _enter()
            # 已提交的 /retry 丢失；handle_chars 在空缓冲插入草稿，保留
            # _enter() 的提交状态（编排器仍可读到 /retry，无重复提交）。
            if action == 'retry' and draft:
                self._buffer_editor.handle_chars(draft)

    # ═══════════════════════════════════════════════════════
    # stdin 直接读取（render 线程调用）
    # ═══════════════════════════════════════════════════════

    def read_stdin_once(self) -> bool:
        """单次非阻塞 stdin 读取 + 直接分发（不经过事件队列）。

        Render 线程每帧调用一次。使用 select timeout=0 确保不阻塞渲染帧。
        单次迭代逻辑改为直接分发（不经过 queue.Queue 中间队列）。

        ★ 批量读取优化（2026-08-14）：``os.read(fd, 1)`` → ``os.read(fd,
        _READ_BATCH)`` 一次读入多个字节，剩余存入 ``InputIO._pending``——
        后续调用优先消费 pending（零 select/read syscall）。快速打字/IME
        上屏/粘贴场景系统调用数从 2N 降至 2；ESC/UTF-8 序列的后续字节已在
        pending 时解析零等待（经 InputIO.read_with_timeout 消费，见
        ``_input_io.py`` / ``_input_parser.py``）。

        设计模式: 模板方法 — ``read_stdin_once()`` 为骨架，
        保留 ``_do_interrupt()`` / ``_handle_special_key()`` 具体步骤。

        Returns:
            True — 有数据被处理（读取并分发了至少一个输入单元）。
            False — 无数据可读、I/O 未激活、或已停止。
        """
        fd = self._io.fd

        # ── 状态检查（委托 InputIO） ──
        if not self._io.can_read():
            return False

        # ── 优先消费 pending（批量读取剩余字节，零 select/read） ──
        if self._io.has_pending():
            try:
                return self._dispatch_byte(self._io.take_pending_byte()[0])
            except Exception:
                _logger.warning("pending 字节分发异常", exc_info=True)
                return True

        # ── select 非阻塞读取（timeout=0，不阻塞渲染帧） ──
        try:
            ready, _, _ = select.select([fd], [], [], 0)
        except (ValueError, OSError, TypeError, AttributeError):
            self._io.record_select_error()
            return False

        if not ready:
            return False

        self._io.reset_select_error()

        try:
            # 批量读取（一次读入多个字节，剩余存 pending 由后续调用消费）
            raw = os.read(fd, _READ_BATCH)
            if not raw:
                self._io.record_eof()
                return False
            self._io.reset_eof()
        except (ValueError, OSError, TypeError) as exc:
            self._io.mark_fd_error(exc)
            return False

        if len(raw) > 1:
            self._io.set_pending(raw[1:])

        try:
            return self._dispatch_byte(raw[0])
        except Exception:
            _logger.warning("输入分发异常", exc_info=True)
            return True

    def _dispatch_byte(self, first_byte: int) -> bool:
        """分派单个输入字节（read_stdin_once 字节处理逻辑提取）。

        从 read_stdin_once 提取（2026-08-14 批量读取重构）：pending 优先
        消费与批量读取后的首字节分发共用本方法。保留原三路分发结构
        （控制字符 / ASCII 可打印 / 多字节 UTF-8）与全部修复逻辑。

        Args:
            first_byte: 待分发的输入字节（int，0-255）。

        Returns:
            True — 字节已被处理（消费）。
        """
        fd = self._io.fd

        # ── 残留 Enter 后置 LF 丢弃（editmsg 竞态修复） ──
        # 若 _enter_residual_pending 置位（Enter 提交/被抑制/router 消费后可能
        # 残留 LF），先清标记；首字节为 LF（0x0a）时丢弃并返回 True（不触发
        # _enter()，prefill 保持可编辑）；非 LF 首字节（如用户立即输入字符）
        # 不误丢，继续正常分发。
        # ★ 2026-08-19（很多上文时按回车不能编辑对应消息修复）：去除 0.5s
        #   时间窗口——残留 LF 与 CR 是**同一次按键**的两个字节，仅消费时机
        #   受渲染线程帧耗时影响（大量消息时 CR/LF 分包到达，LF 消费可晚于
        #   CR 数百 ms~数秒）。字节流语义上 CR 后紧邻的 LF 恒为残留（LF-only
        #   终端 Enter 不产生 CR、不置标记），时间窗口超时把残留误判为用户
        #   新 Enter（弹窗自动确认 / prefill 误提交）是 bug 根源。标记在任
        #   何字节处理时先清除，只影响紧邻 CR 的下一个字节，不误丢新输入。
        if self._enter_residual_pending:
            self._enter_residual_pending = False
            if (
                # ★ 仅丢弃 LF（0x0a）——CR+LF 终端 Enter 提交后紧随的残留 LF。
                #   CR（0x0d）永不丢弃：单 CR 终端（Enter 只发 0x0d）用户
                #   二次按 Enter 时，第二个 CR 是新的提交而非残留，丢弃会
                #   丢失第二次提交（2026-08-06 双击误吞修复）。
                first_byte == 0x0a
            ):
                return True

        # ── 任意键按下通知（ink useStdin().isAnyKeyPressed 置位） ──
        # 每个输入字节分发前触发（含残留 Enter 丢弃之外的全部输入路径）。
        self._notify_key_pressed()

        # ── ASCII 控制字符分发 ──
        if first_byte < 0x20 or first_byte == 0x7F:
            try:
                event = self._parser.feed_byte(first_byte)
                if event is None:
                    # ESC (0x1b) → 读取完整转义序列
                    event = self._parse_escape_sequence(fd)
                    kind = event.kind
                    if kind in ("escape", "interrupt"):
                        # ★ user_select Esc 取消修复（2026-08-05）：ESC 事件内联
                        #   处理前先询问 input router——修复前 Esc 直接走搜索/
                        #   取消输入/中断路径，React Ink useInput 钩子
                        #   （UserSelectPopup/ConfirmInput/SearchInput 等）收不到
                        #   escape 事件 → user_select 弹窗按 Esc 无法取消。
                        #   router 消费（返回 True）→ 跳过旧中断路径；未消费
                        #   → 走既有搜索/取消输入/中断语义（零行为变化）。
                        if self._router_consume(event):
                            pass
                        elif kind == "escape" and self._buffer_editor.is_search_active():
                            # 方向D 步骤14：搜索模式 Esc 退出搜索（恢复原缓冲）
                            self._buffer_editor.search_exit(apply=False)
                            self._sync_reverse_search()
                        elif kind == "escape" and self._should_cancel_input():
                            # 方向D 步骤16：Esc 取消输入（启用 + 空闲 + 非空缓冲）
                            self._cancel_input()
                            # ★ 2026-08-21（用户需求：按 Esc 杀后台任务）：
                            #   esc_cancel_input 配置下纯 Esc 走取消输入而非中断
                            #   路径，但"杀所有后台 bash/subagent"语义仍应生效
                            #   （取消输入不置位中断标志，仅触发 kill 回调）。
                            self._trigger_kill_background()
                        else:
                            # 方向3（Esc 补全弹窗残留修复）：中断后关闭补全弹窗。
                            # ★ P1-2 修复（顺序调换，editmsg Esc 误判取消）：
                            #   先 ``_do_interrupt()``（置位中断标志）再
                            #   ``_dismiss_completion()``——editmsg 选择期间
                            #   （dismiss 回调被替换为 ``_editmsg_dismiss``）
                            #   Esc 经旧路径触发 dismiss 时可读 ``interrupted``
                            #   标志判定为**取消**而非确认；Enter 路径不设置
                            #   中断标志，dismiss 仍判定为确认。调换前顺序相反，
                            #   dismiss 时标志尚未置位，挂载窗口内 Esc 被误判为
                            #   确认（截断+预填最后一条消息）。
                            #   （``_cancel_input`` 分支在 editmsg 场景不可达：
                            #   进入选择前缓冲恒为空——Ctrl+O reset 清空 /
                            #   /editmsg Enter 提交清空，``_should_cancel_input``
                            #   对空缓冲返回 False。）
                            # ★ 2026-08-21（用户需求：按 Esc 杀后台任务）：
                            #   仅纯 Esc（kind=="escape"）传 kill_background=True
                            #   ——触发杀所有后台 bash/subagent；双 Esc
                            #   （kind=="interrupt"）与 Ctrl+C 语义一致，只
                            #   中断生成不杀后台任务。
                            self._do_interrupt(kill_background=(kind == "escape"))
                            self._dismiss_completion()
                    elif kind in (
                        "arrow_up", "arrow_down", "arrow_right", "arrow_left",
                        "home", "end", "delete", "backspace", "char",
                        # 方向A 步骤1：Alt 组合 / 功能键 / CSI u Shift+Tab 进入分发
                        "alt_char", "f1", "f2", "f3", "f4", "tab",
                        # P2-4：CSI u Ctrl 字母（keycode 103/111/110/114）映射为
                        #   ctrl_key 事件进入分发（增强键盘协议终端 Ctrl+G/O/N/R
                        #   失效修复——修复前 ESC 路径分发元组不含 "ctrl_key"，
                        #   此类事件被静默忽略）；
                        # P3-4：其余 csi_u 事件先进 input router（消费则返回），
                        #   未消费则 no-op（不再静默丢弃）。
                        "ctrl_key", "csi_u",
                        # ★ P0-3（review 2026-08-06）：ESC 转义路径分发元组补齐
                        #   "enter" / "page_up" / "page_down"——修复前 CSI u
                        #   无修饰 Enter（``\x1b[13;1u``，kitty/wezterm 等增强
                        #   键盘协议终端按 Enter 发送）解析为 enter 事件但不进
                        #   分发 → 用户无法提交输入；PageUp/PageDown
                        #   （``\x1b[5~``/``\x1b[6~`` 与 CSI u 57358/57359）解析
                        #   为 page_up/page_down 但被静默忽略 → 补全弹窗翻页失效。
                        #   （``_dispatch_key_event`` 已有 enter/page_up/page_down
                        #   分支，仅 ESC 路径入口元组遗漏。）
                        "enter", "page_up", "page_down",
                    ):
                        self._dispatch_key_event(event)
                    # unknown 静默忽略；csi_u 进分发 debug no-op（router 可消费）
                elif event.kind == "interrupt":
                    # ★ React Ink exitOnCtrlC=False（render() 独立会话）：interrupt
                    #   事件（0x03 Ctrl+C / 双 Esc）在 ``_interrupt_routable``
                    #   置位时先进 input router——消费则跳过中断路径（Ctrl+C
                    #   交给 useInput handler，官方语义）；未消费回退中断。
                    #   生产路径（标志默认 False）行为不变：直接中断。
                    if self._interrupt_routable and self._router_consume(event):
                        pass
                    else:
                        self._do_interrupt()
                elif event.kind == "ctrl_key":
                    # 方向1 B1：内联 ctrl_key 路径 router 先行（经 _router_consume
                    # 统一入口）。router 消费 → 跳过旧回调路径；未消费 →
                    # _handle_ctrl_key 走旧分发（Ctrl+G/O/N/R/L/D/T）。
                    if not self._router_consume(event):
                        self._handle_ctrl_key(event.char)
                else:
                    # enter, tab, backspace, home, end, delete 等 → 直接分发
                    self._dispatch_key_event(event)
            except Exception:
                _logger.warning("控制字符分发异常", exc_info=True)
            return True

        # ── ASCII 可打印字符 ──
        if first_byte < 0x80:
            try:
                paste_text = self._io.try_read_paste(fd, chr(first_byte))
                if len(paste_text) > 1:
                    # ★ 方向2（粘贴绕过 router 修复）：粘贴文本先进 input router
                    #   ——消费（useInput 钩子拦截整段粘贴）则跳过旧路径；未消费
                    #   走 handle_chars + auto completion（零行为变化）。
                    paste_event = KeyEvent(
                        kind="char", char=paste_text,
                        raw=paste_text.encode("utf-8", errors="replace"),
                    )
                    if not self._router_consume(paste_event):
                        self._buffer_editor.handle_chars(paste_text)
                        self._trigger_auto_completion()
                else:
                    event = self._parser.feed_byte(first_byte)
                    if event is not None:
                        self._dispatch_key_event(event)
            except Exception:
                _logger.warning("ASCII 可打印字符分发异常", exc_info=True)
            return True

        # ── 多字节 UTF-8 序列 ──
        try:
            ch = self._io.read_utf8_char(fd, first_byte)
            if ch is not None:
                paste_text = self._io.try_read_paste(fd, ch)
                if len(paste_text) > 1:
                    # ★ 方向2：多字节 UTF-8 粘贴路径同样先问 router
                    paste_event = KeyEvent(
                        kind="char", char=paste_text,
                        raw=paste_text.encode("utf-8", errors="replace"),
                    )
                    if not self._router_consume(paste_event):
                        self._buffer_editor.handle_chars(paste_text)
                        self._trigger_auto_completion()
                else:
                    self._dispatch_key_event(
                        KeyEvent(kind='char', char=ch,
                                 raw=ch.encode("utf-8", errors="replace"))
                    )
            else:
                # ★ 方向1（慢速多字节首字节重复捕获修复）：read_utf8_char 返回
                #   None 有两种情况——(1) 已读字节可组成合法 UTF-8 前缀 → 存入
                #   ``_io._utf8_partial``（字节已保留待补齐，后续作为完整 char
                #   事件分发）；(2) 首字节非法/无法组成前缀 → 清空丢弃。仅情况
                #   (2) 才 capture first_byte（供 prefill 捕获）——修复前两种
                #   情况都 capture，情况 (1) 保留的首字节稍后补齐分发时又被
                #   capture，drain_captured 把孤立首字节解码为 U+FFFD 泄漏进
                #   会话 prefill。
                if not getattr(self._io, "_utf8_partial", b""):
                    self.capture_bytes(bytes([first_byte]))
        except Exception:
            _logger.warning("多字节 UTF-8 字符分发异常", exc_info=True)
        return True

    # ═══════════════════════════════════════════════════════
    # 事件处理（render 线程调用）
    # ═══════════════════════════════════════════════════════

    def process_events(self) -> None:
        """处理所有输入事件（render 线程调用）。

        循环调用 ``read_stdin_once()`` 直到无可读数据，
        确保一次渲染帧内处理完所有待处理的输入。
        """
        try:
            while self.read_stdin_once():
                pass
        except Exception:
            _logger.warning("process_events 异常", exc_info=True)

    def _router_consume(self, event: KeyEvent) -> bool:
        """router 优先分发统一入口（策略收敛，方向1 步骤1）。

        语义：有 router 且 handler 返回 True 则消费（返回 True）；router 为
        None 或抛异常时返回 False（放行）。供 ``read_stdin_once`` 内联路径
        （ctrl_key 等）与 ``_dispatch_key_event`` 复用，消除两处重复的
        try/except router 调用。

        Args:
            event: 待分发按键事件。

        Returns:
            True — 事件已被 router 消费（跳过旧回调路径）；
            False — 放行（走旧路径，零行为变化）。
        """
        router = self._input_hook_router
        if router is None:
            return False
        try:
            return bool(router(event))
        except Exception:
            _logger.debug("input hook router 异常，放行事件", exc_info=True)
            return False

    def _mark_enter_residual(self, event: KeyEvent) -> None:
        """按触发字节标记残留 LF 待丢弃（editmsg 竞态修复）。

        设计意图：CR+LF 终端（Windows 原生控制台等）Enter 发 ``\\r\\n``——
        CR 触发 enter 事件后紧随的 LF 是同一按键的残留字节，须丢弃防误
        提交/误确认（LF 若被解析为第二个 enter，会在 editmsg 弹窗打开后
        误判确认、或在 prefill 注入后被 ``_enter()`` 误提交）。

        ★ 修复（2026-08-16，LF-only 终端误吞确认 Enter）：Python 3.9
        ``tty.setcbreak`` 只关 ICANON+ECHO、**不关 ICRNL**——POSIX/Cygwin
        驱动将 Enter 的 ``\\r`` 转换为 ``\\n``，程序读到的是 **LF (0x0a)**，
        LF 本身就是完整按键，不存在"残留"。修复前无条件置标记：``/editmsg``
        提交回车（LF）置标记后，用户在弹窗按 Enter 确认（LF）会被
        ``_dispatch_byte`` 误吞 → 弹窗无响应（"按回车有时不能编辑消息"，
        需再按一次）。

        ★ 修复（2026-08-19，很多上文时按回车不能编辑对应消息）：去除
        0.5s 时间窗口——丢弃判定改为**纯字节序语义**：CR 置位后下一个
        分发的字节是 LF 则丢弃，无论多久之后到达（大量消息时渲染线程
        一帧耗时可超 0.5s，CR/LF 分包到达的 LF 消费时刻晚于固定窗口 →
        残留被误判为用户新 Enter：弹窗被自动确认编辑默认最后一条 /
        prefill 被 ``_enter()`` 误提交重发）。标记在任何字节处理时先
        清除（见 ``_dispatch_byte``），只影响紧邻 CR 的下一个字节。

        规则：仅当触发 enter 的原始字节为 CR（0x0d）时置标记丢弃紧随 LF；
        LF 触发（LF-only 终端）或 CSI u 增强键盘协议（``\\x1b[13;1u`` 等，
        完整序列无 CR+LF 字节对）不置标记——LF/CSI u 已是完整按键，后续
        LF/CR 为用户新输入，不误丢。
        """
        raw = getattr(event, "raw", None) or b""
        if raw and raw[0] == 0x0d:
            self._enter_residual_pending = True

    def _dispatch_key_event(self, event: KeyEvent) -> None:
        """根据 KeyEvent.kind 分发到对应的输入处理器。

        Ctrl+G/O/N/R 等 ctrl_key 事件已在 read_stdin_once() 中拦截处理，
        此处分发不会收到 ctrl_key 分支。

        步骤 8（ink useInput 钩子）：已注入 ``_input_hook_router`` 时先询问
        router——返回 True 消费该事件（跳过旧回调路径）；返回 False 或抛异常
        时放行（走旧路径，零行为变化）。read_stdin_once 内联分发（char 键等）
        最终汇入本方法，故统一覆盖。
        """
        kind = event.kind

        # ── ink useInput 钩子优先分发（经 _router_consume 统一入口） ──
        if self._router_consume(event):
            # ★ 残留 LF 源头丢弃（2026-08-05，editmsg prefill 竞态修复）：
            #   UserSelectPopup 等交互组件消费 Enter（写 done）后，CR+LF 中
            #   的 LF 会残留在 stdin——若后续被 read_stdin_once 解析为第二个
            #   Enter，会在 prefill 注入后被 _enter() 误提交（用户看到
            #   「prefill 没效果，要再按回车」）。标记残留 LF 待丢弃：
            #   read_stdin_once 读取紧随的一个 LF（0x0a）时直接丢弃；
            #   无 LF（单 CR 终端）或用户后续输入普通字符时标记自动清除
            #   （不误丢）。非 Enter 事件不设置（↑↓/Esc 等无 CR+LF 问题）。
            # ★ 修复（2026-08-16）：置标记改经 _mark_enter_residual 按触发
            #   字节判断——LF-only 终端（Python 3.9 setcbreak 不关 ICRNL，
            #   POSIX/Cygwin 驱动 \r→\n）Enter 读到 LF，LF 即完整按键无残留，
            #   无条件置标记会误吞用户下一次真实 Enter（见该方法）。
            # ★ 修复（2026-08-19）：丢弃无时间窗口——大量消息时渲染线程
            #   一帧耗时可超原 0.5s 窗口，分包晚到的残留 LF 被误判为用户
            #   新 Enter（弹窗自动确认/误提交），见 _mark_enter_residual。
            if event.kind == "enter":
                self._mark_enter_residual(event)
            return  # 已消费，跳过旧回调路径

        if kind == "enter":
            if self._buffer_editor.is_search_active():
                # 方向D 步骤14：搜索模式 Enter 应用匹配并退出搜索（不提交）
                self._buffer_editor._enter()
                self._sync_reverse_search()
                # ★ 搜索 Enter 应用匹配后同样置残留标记（2026-08-06）：
                #   CR+LF 终端 Enter 应用匹配退出搜索后，紧随 LF 若不丢弃会
                #   被解析为第二个 enter 事件 → 搜索已退出 → _enter() 立即
                #   提交搜索匹配文本（用户无法继续编辑）。与正常 Enter 提交
                #   分支统一标记丢弃。按触发字节判断见
                #   _mark_enter_residual（LF-only 终端不置标记）。
                self._mark_enter_residual(event)
                return
            self._dismiss_completion()
            # ★ 方向1（加锁读取）：与其他访问统一经 get_suppress_enter()
            # （带 _suppress_enter_lock）——修复前此处直接读裸字段，与 setter
            # 加锁不一致（GIL 下原子读良性，但并发访问模式不一致）。
            if not self.get_suppress_enter():
                self._buffer_editor._enter()
                # ★ 正常 Enter 提交后同样置残留标记（2026-08-06）：
                #   /editmsg /deitmsg 等命令的 CR+LF 中 LF 可能晚到（终端/
                #   蓝牙/SSH 延迟）——若在弹窗打开后到达会被 UserSelectPopup
                #   误判为确认 Enter（弹窗自动确认/直接编辑最后一条），或在
                #   prefill 注入后被 _enter() 误提交。统一标记丢弃；
                #   按触发字节判断（LF-only 终端不置标记）见
                #   _mark_enter_residual。
                self._mark_enter_residual(event)
            else:
                # editmsg 选择确认 CR 被抑制后标记残留 LF（\n），
                # 由 read_stdin_once 丢弃，避免 LF 在 prefill 注入后被误提交。
                # 按触发字节判断：LF-only 终端（Enter 读到 LF）不置标记——
                # 修复前 /editmsg 提交回车（LF）置标记，用户在弹窗按 Enter
                # 确认（LF）被 _dispatch_byte 误吞 → 弹窗无响应（"按回车
                # 有时不能编辑消息"）。
                self._mark_enter_residual(event)
                # ★ 窗口期提交意图捕获（editmsg「很多上文时按回车不能编辑」
                #   修复）：弹窗确认后（capture 激活期）被抑制吞掉的 Enter
                #   是用户「提交编辑」的意图——修复前无痕丢弃 → 用户需再按
                #   一次（「按回车没反应」）。记为 deferred，插件注入 prefill
                #   后消费并自动提交（对齐用户单次 Enter 完成编辑的预期）。
                #   capture 未激活（弹窗打开期间 suppress 吞的 Enter 是
                #   「确认弹窗」意图——组件才是确认权威）不记录。
                if self._enter_capture_active:
                    self._deferred_enter_pending = True
        elif kind == "tab":
            if self._buffer_editor.is_search_active():
                # 方向D 步骤14：搜索模式 Tab 应用匹配并退出搜索（与 Enter 一致）
                self._buffer_editor._enter()
                self._sync_reverse_search()
            elif self.get_suppress_enter():
                # ★ 方向2（editmsg Tab 误写缓冲修复）：editmsg 选择期间
                #   （_suppress_enter=True）Tab 经 _handle_editmsg_tab 正向循环
                #   补全高亮——不写输入缓冲、不确认（修复前经 _handle_tab →
                #   _CmplHandler.on_tab → 弹窗可见时 _cycle_tab 确认写入缓冲）。
                self._handle_editmsg_tab()
            elif event.modifier == 2:
                # 方向A 步骤1：Shift+Tab（CSI u 9;2u）→ 补全反向循环；
                # 补全不可见 / 回调未消费 → no-op（不插入制表符）。
                self._handle_shift_tab_reverse()
            else:
                self._handle_tab()
        elif kind == "alt_char":
            # 方向A 步骤1：Alt+B/F 词跳转（等价 Ctrl+左/右）；
            # 2026-08-05（增加操作）：Alt+D 删除光标后的一个词（readline
            # kill-word 对称——Ctrl+W 删词向左，Alt+D 删词向右）。
            # 其余 Alt+组合已先行询问 input router，未消费则 no-op（不产生中断）。
            # P3-3：大小写等效——大写 'B'/'F'/'D'（ESC+B/ESC+F/ESC+D）同样触发。
            if event.char in ('b', 'B'):
                self._buffer_editor._word_left()
            elif event.char in ('f', 'F'):
                self._buffer_editor._word_right()
            elif event.char in ('d', 'D'):
                self._buffer_editor._delete_word_right()
        elif kind == "ctrl_key":
            # P2-4：CSI u Ctrl 字母（keycode 103/111/110/114）映射的 ctrl_key
            # 事件复用同一分发逻辑（含 _handle_reverse_search 门控）。
            # 直接控制字符路径（read_stdin_once 内联）已在读取处处理，不会
            # 重复到达此处——本分支服务 ESC/CSI u 转义序列路径。
            self._handle_ctrl_key(event.char)
        elif kind == "csi_u":
            # P3-4：未映射为已知 kind 的 CSI u 事件——router 已在函数开头
            # 先行询问（消费则返回）；此处为显式 no-op 分支（不再静默丢弃，
            # 供 input router 未来消费）。
            _logger.debug(
                "csi_u 事件未被 input router 消费 (keycode=%s modifier=%s)",
                event.keycode, event.modifier,
            )
        elif kind in ("f1", "f2", "f3", "f4"):
            # 方向A 步骤1：功能键已先行询问 input router；未消费 no-op
            # （不再静默丢弃——router 可经 useInput 钩子消费）。
            _logger.debug("%s 功能键未被 input router 消费", kind)
        elif kind == "backspace":
            # P1-1：modifier==1 表示「词删除」（Ctrl+W / ESC DEL / CSI u 显式
            # Alt+Backspace \x1b[8;3u 传统路径）；modifier==0 表示普通退格
            # （ASCII DEL/BS 与 CSI u 普通退格 \x1b[8;1u——修复前 \x1b[8;1u
            # 误带 modifier=1 落入本分支，普通退格每次删除整个词）。
            self._maybe_dismiss_completion()
            if event.modifier == 1:
                self._buffer_editor._delete_word_left()
            else:
                self._buffer_editor._backspace()
            self._trigger_auto_completion()
        elif kind == "interrupt":
            _logger.debug("_dispatch_key_event: interrupt 事件到达队列（应内联处理）")
        elif kind == "home":
            self._maybe_dismiss_completion()
            self._buffer_editor._home()
        elif kind == "end":
            self._maybe_dismiss_completion()
            self._buffer_editor._end()
        elif kind == "delete":
            # P1-1：modifier==0 普通删除（\x1b[3~ / CSI u 普通 Delete
            # \x1b[127;1u——修复前 \x1b[127;1u 误带 modifier=1 落入词删除）；
            # modifier==1 词删除（Ctrl+W / CSI u 显式 Alt+Delete \x1b[127;3u）；
            # modifier 2/3 为行首/行尾删除（Ctrl+U / Ctrl+K）。
            modifier = event.modifier
            if modifier == 0:
                self._maybe_dismiss_completion()
                self._buffer_editor._delete()
                self._trigger_auto_completion()
            elif modifier == 1:
                self._maybe_dismiss_completion()
                self._buffer_editor._delete_word_left()
                self._trigger_auto_completion()
            elif modifier == 2:
                self._maybe_dismiss_completion()
                self._buffer_editor._kill_to_bol()
                self._trigger_auto_completion()
            elif modifier == 3:
                self._maybe_dismiss_completion()
                self._buffer_editor._kill_to_eol()
                self._trigger_auto_completion()
        elif kind == "arrow_up":
            self._handle_arrow_up()
        elif kind == "arrow_down":
            self._handle_arrow_down()
        elif kind == "arrow_right":
            if event.modifier == 5:
                self._buffer_editor._word_right()
            else:
                self._buffer_editor._right()
        elif kind == "arrow_left":
            if event.modifier == 5:
                self._buffer_editor._word_left()
            else:
                self._buffer_editor._left()
        elif kind in ("page_up", "page_down"):
            # 2026-08-05（增加操作）：PageUp/PageDown 补全弹窗翻页——补全
            # 可见时按页步进（每页 ±5 项，对齐弹窗可见行数）；补全不可见
            # 时 no-op（不改变输入缓冲/光标）。
            self._handle_page_nav(-5 if kind == "page_up" else 5)
        elif kind == "unknown":
            self._maybe_dismiss_completion()
            if event.raw:
                with self._captured_lock:
                    # P2-7（review）：unknown 事件完整捕获——修复前仅捕获
                    # ``event.raw[0]`` 首字节，多字节 unknown（如残缺 CSI/UTF-8
                    # 序列）的后续字节丢失，drain_captured 只能还原残缺文本。
                    self._captured_input.extend(event.raw)
        elif kind == "char":
            if event.char:
                self._buffer_editor.handle_char(event.char)
                self._trigger_auto_completion()

    # ═══════════════════════════════════════════════════════
    # 反向历史搜索（方向D 步骤14，Ctrl+R 配置门控）
    # ═══════════════════════════════════════════════════════

    def _handle_reverse_search(self) -> None:
        """Ctrl+R 反向历史搜索：首次进入（当前缓冲为查询），再次推进到下一匹配。"""
        be = self._buffer_editor
        if be.is_search_active():
            be.search_next()
        else:
            if not be.search_enter(be.get_current_text()):
                return  # 查询为空 → 不进入搜索
        self._sync_reverse_search()

    def _sync_reverse_search(self) -> None:
        """同步反向搜索状态到 UI（装配注入回调：更新 model.history_search + 重绘）。"""
        be = self._buffer_editor
        cb = self._reverse_search_callback
        if cb is None:
            return
        try:
            cb(be._search_query, be._search_matches, be._search_idx, be._search_active)
        except Exception:
            _logger.debug("反向搜索状态同步回调异常", exc_info=True)

    # ═══════════════════════════════════════════════════════
    # Esc 取消输入（方向D 步骤16，配置门控）
    # ═══════════════════════════════════════════════════════

    def _should_cancel_input(self) -> bool:
        """Esc 取消输入判定：启用 + 缓冲非空 + 空闲（无生成中）。"""
        if not self._esc_cancel_input:
            return False
        if not self._buffer_editor.get_current_text():
            return False
        return not self._is_active_status()

    def _is_active_status(self) -> bool:
        """查询活跃状态（生成中）。回调缺失时视为空闲（默认 False）。"""
        fn = self._active_status_fn
        if fn is None:
            return False
        try:
            return bool(fn())
        except Exception:
            _logger.debug("活跃状态回调异常", exc_info=True)
            return False

    def _cancel_input(self) -> None:
        """取消当前输入：清空缓冲 + 回显空串 + 关闭补全弹窗（不触发中断标志）。

        方向2（_cancel_input 未 dismiss 修复）：Esc 取消输入同时关闭补全弹窗
        （修复前仅清缓冲不回显 dismiss，补全弹窗残留）。
        """
        self._buffer_editor.set_buffer("")
        self._buffer_editor._echo("")
        self._dismiss_completion()

    # ═══════════════════════════════════════════════════════
    # 辅助分发方法
    # ═══════════════════════════════════════════════════════

    def _handle_tab(self) -> None:
        """处理 Tab 键：调用补全回调，失败则插入制表符（委托补全导航策略）。"""
        self._completion_nav.handle_tab()

    def _handle_arrow_up(self) -> None:
        """处理上箭头：补全弹窗可见时仅移动高亮，否则历史浏览（委托策略）。"""
        self._completion_nav.handle_arrow_up()

    def _handle_arrow_down(self) -> None:
        """处理下箭头：补全弹窗可见时仅移动高亮，否则历史浏览（委托策略）。"""
        self._completion_nav.handle_arrow_down()

    def _handle_page_nav(self, delta: int) -> None:
        """处理 PageUp/PageDown：补全弹窗可见时按页步进高亮，否则 no-op（委托策略）。"""
        self._completion_nav.handle_page_nav(delta)

    def _handle_shift_tab_reverse(self) -> None:
        """处理 Shift+Tab：补全弹窗可见时反向循环，否则 no-op（委托策略）。"""
        self._completion_nav.handle_shift_tab_reverse()

    def _handle_editmsg_tab(self) -> None:
        """editmsg 模式 Tab：正向循环补全高亮（不写缓冲、不确认，委托策略）。"""
        self._completion_nav.handle_editmsg_tab()

    def _dismiss_completion(self) -> None:
        """如果补全弹窗可见，关闭它（委托补全导航策略）。"""
        self._completion_nav.dismiss_completion()

    def _maybe_dismiss_completion(self) -> None:
        """关闭补全弹窗（editmsg 选择期间除外——_suppress_enter=True 时不触发）。"""
        self._completion_nav.maybe_dismiss_completion()

    def _trigger_auto_completion(self) -> None:
        """获取当前文本并调用自动补全回调（委托补全导航策略）。"""
        self._completion_nav.trigger_auto_completion()

    # ═══════════════════════════════════════════════════════
    # 解析方法（委托 InputParser → _input_parser.py）
    # ═══════════════════════════════════════════════════════

    def _parse_escape_sequence(self, fd: int) -> KeyEvent:
        """读取并解析 ESC 转义序列（含 I/O，委托 InputParser）。"""
        return self._parser._parse_escape_sequence(fd)

    # ═══════════════════════════════════════════════════════
    # 缓冲重置辅助
    # ═══════════════════════════════════════════════════════

    def reset(self, clear_queue: bool = True) -> None:
        """清空缓冲/队列状态 + 清除中断标志（与 _input.py 原 reset 语义等价）。

        Args:
            clear_queue: 是否同时清空未消费排队输入（``_submitted_text`` /
                ``_input_ready``，委托 ``_buffer_editor.reset``）。False 用于
                特殊按键分发路径（非 editmsg/retry 的 action）——保留用户
                Enter 提交后尚未被编排器消费的输入（P2 修复，2026-08-07）。
        """
        self._io.clear_interrupted()
        self._buffer_editor.reset(clear_queue=clear_queue)

    def reset_and_echo(self) -> None:
        """重置缓冲区并回显空字符串（清空输入行视觉）。"""
        self.reset()
        self._buffer_editor._echo("")

    # ═══════════════════════════════════════════════════════
    # 回调接口
    # ═══════════════════════════════════════════════════════

    def set_special_key_callback(self, cb) -> None:
        """设置特殊按键回调（Ctrl+G/O/N/R）。

        cb 签名: (action: str, current_text: str) -> str | None
        """
        self._special_key_callback = cb

    def set_completion_callback(self, cb) -> None:
        """设置 Tab 补全回调。

        cb 签名: (text: str) -> str | None
        """
        self._completion_callback = cb

    def set_dismiss_completion_callback(self, cb) -> None:
        """设置补全弹窗关闭回调。

        cb 签名: () -> None
        """
        self._dismiss_completion_callback = cb

    def get_dismiss_completion_callback(self):
        """获取补全弹窗关闭回调（公开访问器，收敛私有字段直读）。

        与 ``set_dismiss_completion_callback`` 对称——message_editor 经公开
        API 保存/恢复 dismiss 回调（不直接读写私有字段）。
        """
        return self._dismiss_completion_callback

    def set_completion_navigate_callback(self, cb) -> None:
        """设置补全弹窗上下导航回调。

        cb 签名: (delta: int, text: str) -> str | None
        """
        self._completion_navigate_callback = cb

    def set_auto_completion_callback(self, cb) -> None:
        """设置自动补全回调。

        cb 签名: (text: str) -> None
        """
        self._auto_completion_callback = cb

    def set_interrupt_callback(self, cb) -> None:
        """设置中断回调（方向A 步骤1 注入点）。

        cb 签名: () -> None
        None 缺省时 ``_do_interrupt`` 记 debug 日志并跳过（测试兼容）。
        """
        self._interrupt_callback = cb

    def set_kill_background_callback(self, cb) -> None:
        """设置纯 Esc 杀后台任务回调（2026-08-21 用户需求注入点）。

        cb 签名: () -> None
        仅纯 Esc（kind="escape"）中断时调用（Ctrl+C/双 Esc 不触发）；
        由 _loop.py / clawbot.runner 注入（request_kill_background +
        跨线程调度杀所有后台 bash/subagent）。None 缺省时跳过（测试兼容）。
        """
        self._kill_background_callback = cb

    def set_enter_append_history(self, cb) -> None:
        """设置 Enter 提交历史追加回调（P2-8）。

        cb 签名: ``(text: str) -> None``；None 时 ``_handle_special_key`` 的
        ``_enter`` 使用 buffer_editor 自身 ``_append_history_locked``（缺省
        语义）。由 Input 外观注入其 ``_append_history_locked``——与
        ``Input._enter`` 的 append_history 注入一致（测试 patch 外观拦截
        路径有效）。
        """
        self._enter_append_history = cb

    def set_input_hook_router(self, router) -> None:
        """设置 input hook router（步骤 8：ink useInput 钩子优先分发）。

        router 签名: ``(event: KeyEvent) -> bool`` —— True=消费（跳过旧回调
        路径），False=放行（走旧路径）。None 可清除注入。

        Args:
            router: 路由回调或 None。
        """
        self._input_hook_router = router

    def set_key_pressed_callback(self, cb) -> None:
        """设置任意键按下回调（ink useStdin().isAnyKeyPressed 置位）。

        cb 签名: ``() -> None``；None 可清除注入（缺省零开销）。
        回调在每个输入字节分发前调用（幂等置位语义，异常吞掉记 debug）。
        """
        self._key_pressed_callback = cb

    def set_interrupt_routable(self, routable: bool) -> None:
        """设置 interrupt（Ctrl+C）事件 router 放行标志（React Ink
        exitOnCtrlC=False 语义）。

        False（默认）：interrupt 事件直接走 ``_do_interrupt``（生产中断路径，
        行为不变）。True：interrupt 事件先问 input router——消费则跳过中断
        路径（React Ink 语义：Ctrl+C 交给 useInput handler）；未消费仍走
        ``_do_interrupt``（回退中断，不丢事件）。

        Args:
            routable: True=interrupt 事件先进 router；False=直接中断。
        """
        self._interrupt_routable = bool(routable)

    def _notify_key_pressed(self) -> None:
        """通知任意键按下（每个输入字节分发前调用；回调异常吞掉记 debug）。"""
        cb = self._key_pressed_callback
        if cb is not None:
            try:
                cb()
            except Exception:
                _logger.debug("key pressed 回调异常", exc_info=True)

    def set_reverse_search_enabled(self, enabled: bool) -> None:
        """设置 Ctrl+R 反向历史搜索启用标志（方向D 步骤14，默认 False）。

        由装配注入 ``TuiConfig.reverse_search_enabled``；False 保持既有
        switch_model 语义（键位冲突门控）。
        """
        self._reverse_search_enabled = enabled

    def set_reverse_search_callback(self, cb) -> None:
        """设置反向搜索状态同步回调（方向D 步骤14，装配注入）。

        cb 签名: ``(query: str, matches: list[str], index: int, active: bool) -> None``
        None 缺省时搜索功能内部可用，仅 UI 状态不同步。
        """
        self._reverse_search_callback = cb

    def set_esc_cancel_input(self, enabled: bool) -> None:
        """设置 Esc 取消输入启用标志（方向D 步骤16，默认 False）。

        由装配注入 ``TuiConfig.esc_cancel_input``；False 保持既有 Esc 中断语义。
        """
        self._esc_cancel_input = enabled

    def set_active_status_callback(self, fn) -> None:
        """设置活跃状态回调（方向D 步骤16，装配注入）。

        fn 签名: ``() -> bool`` —— True=生成中（Esc 不取消输入，走中断）；
        None 缺省时视为空闲（默认 False）。
        """
        self._active_status_fn = fn

    def set_clear_screen_callback(self, cb) -> None:
        """设置 Ctrl+L 清屏回调（Claude TUI parity 步骤 3.1，装配注入）。

        cb 签名: ``() -> None``（session.clear_screen）；None 可清除注入。
        未注入时 Ctrl+L 记 debug 跳过（测试兼容）。
        """
        self._clear_screen_callback = cb

    def set_trace_toggle_callback(self, cb) -> None:
        """设置 Ctrl+H 轨迹视图开关回调（2026-08-19，装配注入）。

        cb 签名: ``() -> None``（翻转 model.fullscreen "trace" ↔ "" + 请求
        重绘——见 ``_make_fullscreen_toggle_cb`` 通用工厂）；None 可清除注入。
        未注入时 Ctrl+H 回退 backspace（0x08 传统 BS 语义）。
        ★ 2026-08-17（review 方向）：**仅正常界面可达**——Trace 打开期间
        Ctrl+H（0x08）被 TraceView 模态 handler 经 router 消费（关闭/返回
        主轨迹），本回调不再被调用（不会重复翻转）。
        """
        self._trace_toggle_callback = cb

    def set_suppress_enter(self, suppress: bool) -> None:
        """设置 Enter 抑制标志（用于 editmsg 消息选择期间）。

        当 suppress=True 时，_dispatch_key_event 中的 Enter 分支
        将跳过 _enter() 调用，防止选择确认 Enter 被误提交为输入。

        线程安全：使用 _suppress_enter_lock 保护。

        ★ 修复（2026-08-06）：恢复 Enter（suppress=False）时**不**清除
        残留标记——弹窗确认 Enter 的 LF 可能晚到（渲染线程忙/终端 I/O 延迟），
        若清除标记，LF 会在 prefill 注入后被 _enter() 误提交（用户看到
        「编辑无效/要再输入」）。

        ★ 修复（2026-08-19，很多上文时按回车不能编辑对应消息）：不再按
        超时清除残留标记（原 deadline 逻辑已随 0.5s 时间窗口移除）——
        标记只经 ``_dispatch_byte`` 的字节级判定清除（下一个分发字节是 LF
        则丢弃、非 LF 则清标记正常分发），恢复 suppress 期间保留标记：
        弹窗确认 Enter 的 LF 无论多晚到达（大量消息时渲染一帧耗时可超
        数百 ms~数秒）都会被丢弃，prefill 注入后不被 ``_enter()`` 误提交。
        """
        with self._suppress_enter_lock:
            self._suppress_enter = suppress

    def get_suppress_enter(self) -> bool:
        """获取当前 Enter 抑制状态。线程安全。"""
        with self._suppress_enter_lock:
            return self._suppress_enter

    # ═══════════════════════════════════════════════════════
    # 窗口期 Enter 提交意图捕获（editmsg「很多上文时按回车不能编辑」修复）
    # ═══════════════════════════════════════════════════════

    def set_enter_capture(self, active: bool) -> None:
        """开关窗口期 Enter 捕获模式（message_editor 弹窗确认后开启）。

        激活期内：抑制吞掉的 Enter（``_dispatch_key_event`` enter 分支
        else）与 flush 丢弃的 Enter 字节（``notify_flushed_enter``）记为
        「提交意图」（deferred）——弹窗已确认，此后用户的 Enter 语义是
        提交编辑而非确认弹窗。插件注入 prefill 后经
        ``consume_deferred_enter`` 消费并自动提交；结束/取消路径必须
        关闭（False）并清残留（consume），防标志泄漏影响下一轮输入。

        Args:
            active: True 开启捕获；False 关闭（不清除已记录的 deferred）。
        """
        self._enter_capture_active = bool(active)

    def is_enter_capture_active(self) -> bool:
        """查询捕获模式是否激活。"""
        return self._enter_capture_active

    def mark_deferred_enter(self) -> None:
        """外部置位提交意图（窗口期已分发但即将被清理的提交转换）。

        editmsg 插件 finally 清理 ``_input_ready`` / ``set_buffer`` 覆盖
        缓冲**之前**调用：窗口期用户 Enter 产生的排队提交（多为空提交——
        prefill 未注入缓冲恒空）即将被丢弃，先转为 deferred 意图，注入
        prefill 后统一兑现（自动提交）。
        """
        self._deferred_enter_pending = True

    def consume_deferred_enter(self) -> bool:
        """读取并清除提交意图标志。

        Returns:
            True — 捕获期内记录到用户 Enter（调用方应兑现提交）；
            False — 无记录（标志已清，幂等）。
        """
        pending = self._deferred_enter_pending
        self._deferred_enter_pending = False
        return pending

    def notify_flushed_enter(self) -> None:
        """通知 flush 丢弃了 Enter 字节（capture 激活时记为提交意图）。

        由 Input 外观 ``flush_stdin_buffer`` 转发 InputIO 返回值调用——
        ``flush_stdin_buffer`` 排空 stdin 残留字节时丢弃的字节含
        Enter（0x0a/0x0d）即通知：capture 激活期该 Enter 是用户「提交
        编辑」意图，记为 deferred（未激活不记——普通 flush 丢弃的
        Enter 与提交意图无关）。
        """
        if self._enter_capture_active:
            self._deferred_enter_pending = True

    # ═══════════════════════════════════════════════════════
    # 便捷方法
    # ═══════════════════════════════════════════════════════

    def capture_bytes(self, data: bytes) -> None:
        """追加原始字节到捕获缓冲区。线程安全。"""
        with self._captured_lock:
            self._captured_input.extend(data)

    def drain_captured(self) -> str:
        """排出并返回捕获的非可打印字符。"""
        with self._captured_lock:
            data = bytes(self._captured_input).decode("utf-8", errors="replace")
            self._captured_input.clear()
        return data


__all__ = ["InputDispatcher"]
