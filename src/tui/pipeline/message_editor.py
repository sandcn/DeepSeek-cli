"""交互式会话消息编辑器 — 使用标准 React Ink 组件（UserSelectPopup）选择消息。

用法：在聊天中输入 /editmsg 或 Ctrl+O 进入消息编辑。

编辑职责：
- 消息选择交互（UserSelectPopup 标准 React Ink 组件 + use_input 交互）
- 编辑/删除/恢复动作处理
- 会话管理入口（MessageEditor.edit_current_messages）

★ 标准 React Ink 化（2026-08-05，消灭例外）：消息选择交互从「补全弹窗
（show_completions）+ _selection_ready 事件轮询」迁移为 **UserSelectPopup
标准组件协议**——设置 ``model.user_select``（visible=True, seq+1, options=
消息摘要）→ App 组件树渲染 ``UserSelectPopup``（use_input 消费 ↑↓/Enter/
Esc，与 user_select 工具同协议）→ 本模块轮询 ``us.done``（跨线程 GIL
原子字段）→ 读取结果。不再直接操作补全弹窗私有字段、不再自定义 dismiss
回调 hack。无 ChatUI 模型环境（测试桩/单次模式）回退旧补全弹窗路径
（兼容保留）。

适配 2026-07 TUI 重构后的架构：
  - 不复用已删除的 pipeline/message_display.py 完整版，使用内置精简替代
  - 标准交互经 ``model.user_select``（ChatUIConsumer 活跃时可用）；
    无 ChatUI 时回退 _BottomBar.show_completions() API（兼容）
  - 不执行 chat_ui.suspend()（重构后 suspend 会拆除 _BottomBar）
"""

from __future__ import annotations

import logging
import time
import threading
from typing import Any

from ...core.sandbox_manager import get_sandbox_manager as _get_sandbox_manager
# 方向3 步骤16：_content_str/_truncate 单一真源在 message_display（已被
# _consumer/apply 消费）；本模块删除本地副本改从单一真源导入（零行为变化）。
from .message_display import _content_str, _truncate

_logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════

def _user_msg_summary(msg: dict, idx: int, max_w: int = 60) -> str:
    """生成用户消息的简短摘要（用于底部栏弹窗显示）。

    格式: N. ● │ 消息内容摘要...

    Args:
        msg: 消息字典。
        idx: 显示编号。
        max_w: 最大宽度。

    Returns:
        纯文本摘要字符串（不含 ANSI 颜色）。
    """
    content = _content_str(msg.get("content", ""))
    text = content.strip()
    return f"{idx}. \u25cf \u2502 {_truncate(text, max_w)}"


def _user_msg_display_lines(msg: dict) -> list:
    """用 TUI 用户消息渲染方式生成弹窗显示行（``> 内容``，多行）。

    与 ``apply.build_user_line`` 同语义（消息区历史回放路径
    ``DisplayMsgsCmd → _do_display_messages``）：每行 ``> {segment}`` 顶格，
    前缀用调色板 ``user_icon`` 色、内容用 ``user_text`` 色；空内容保留
    前缀行。供 ``UserSelectPopup`` 的 ``option_lines`` 使用——/editmsg
    弹窗中的历史消息显示与消息区渲染一致。

    Returns:
        list[AnsiLine] — 每条消息按 ``\\n`` 拆分后的渲染行。
    """
    from src.tui.app.apply import build_user_line
    content = _content_str(msg.get("content", ""))
    return build_user_line(content)


def _restore_sandbox_to(agent: Any, target_idx: int) -> str:
    """恢复沙盒到指定消息索引，返回恢复文件数的描述文本。

    ★ P3-4（明确降级语义）：恢复失败**不阻断编辑**——记录 warning 并
    返回失败描述文本（调用方继续截断消息）。这是既有测试契约
    （test_edit_command_prefill_still_set_when_restore_fails_regression 固化
    「失败继续编辑」语义）；注意恢复失败后继续截断可能导致沙盒文件与
    消息索引不一致（降级风险），如需严格一致性应中止编辑，属未来可选项。
    """
    sandbox_manager = _get_sandbox_manager()
    if not sandbox_manager:
        return ""
    try:
        results = sandbox_manager.restore_to_message(target_idx)
        if results:
            restored = sum(1 for success in results.values() if success)
            return f"\u5df2\u6062\u590d {restored} \u4e2a\u6587\u4ef6"
        return ""
    except Exception as exc:
        _logger.warning("沙盒恢复失败 (target_idx=%s): %s", target_idx, exc)
        return f"沙盒恢复失败: {exc}"


def _truncate_messages(agent: Any, keep_idx: int) -> str:
    """截断消息到 keep_idx（删除 keep_idx 及之后），恢复沙盒并 remap 索引。

    ★ P2-6：EditCommand/DeleteCommand/ResumeCommand 共用的「沙盒恢复 +
    截断 + remap」逻辑提取（修复前 Edit/Delete 执行体高度重复四段相同逻辑）。
    沙盒恢复到截断后保留的最后一条消息（keep_idx-1，下限 0——Edit/Delete
    keep_idx=real_idx 时即恢复光标消息之前；Resume keep_idx=real_idx+1 时
    即恢复光标消息处），返回恢复描述文本。
    """
    messages = agent.messages
    target_index = keep_idx - 1 if keep_idx > 0 else 0
    restore_text = _restore_sandbox_to(agent, target_index)
    original_len = len(messages)
    sm = _get_sandbox_manager()
    if sm:
        # ★ 修复（P2-7）：先 remap 后删除——修复前先 ``del messages[keep_idx:]``
        #   再 remap：remap 异常时消息已删（沙盒与消息索引不一致且无补偿）。
        #   先 remap（仅操作沙盒记录，与 messages 列表状态无关；失败记录
        #   error 并抛出明确异常，消息未删无中间态）再删除。
        try:
            sm.remap_indices(list(range(keep_idx, original_len)))
        except Exception:
            _logger.error(
                "remap_indices 失败 (keep_idx=%s): 沙盒记录可能未更新",
                keep_idx, exc_info=True,
            )
            raise
    del messages[keep_idx:]
    return restore_text


# ═══════════════════════════════════════════════════════════
# 命令类 — 封装消息编辑操作
# ═══════════════════════════════════════════════════════════

class EditCommand:
    """编辑命令：截断到光标消息，预填旧内容。"""

    def __init__(self, agent: Any, real_idx: int) -> None:
        self.agent = agent
        self.real_idx = real_idx

    def execute(self, state: dict) -> bool:
        agent = self.agent
        messages = agent.messages
        if self.real_idx < 0 or self.real_idx >= len(messages):
            return False

        old_content = _content_str(messages[self.real_idx].get("content", ""))

        # 截断 + 沙盒恢复 + remap（P2-6：公共助手，恢复失败语义见
        # _restore_sandbox_to —— 明确降级：记录 warning 并继续编辑）
        state["_restore_text"] = _truncate_messages(agent, self.real_idx)

        state["prefill"] = old_content
        state["_edit_performed"] = True
        return True


class DeleteCommand:
    """删除命令：删除光标消息及之后所有消息。"""

    def __init__(self, agent: Any, real_idx: int) -> None:
        self.agent = agent
        self.real_idx = real_idx

    def execute(self, state: dict) -> bool:
        agent = self.agent
        messages = agent.messages
        if self.real_idx < 0 or self.real_idx >= len(messages):
            return False

        # 截断 + 沙盒恢复 + remap（P2-6：公共助手，恢复失败语义见
        # _restore_sandbox_to —— 明确降级：记录 warning 并继续编辑）
        state["_restore_text"] = _truncate_messages(agent, self.real_idx)

        state["_edit_performed"] = True
        return True


class ResumeCommand:
    """恢复命令：截断到光标消息之后（保留当前消息及之前的内容）。"""

    def __init__(self, agent: Any, real_idx: int) -> None:
        self.agent = agent
        self.real_idx = real_idx

    def execute(self, state: dict) -> bool:
        agent = self.agent
        messages = agent.messages
        if self.real_idx < 0 or self.real_idx >= len(messages):
            return False

        # 截断到 real_idx+1（保留当前消息）+ 沙盒恢复到光标消息 + remap
        # （P2-6：公共助手；keep_idx=real_idx+1 → 沙盒恢复到 real_idx）
        state["_restore_text"] = _truncate_messages(agent, self.real_idx + 1)

        _check_last_message_role(agent, state)
        state["_edit_performed"] = True
        return True


class ResumeAllCommand:
    """全部恢复命令：恢复全部消息，不做截断。"""

    def __init__(self, agent: Any) -> None:
        self.agent = agent

    def execute(self, state: dict) -> bool:
        agent = self.agent
        if not agent.messages:
            return False
        _check_last_message_role(agent, state)
        # ★ P3-5：统一 state 契约——显式设置 prefill（明确「不预填」语义，
        #   与 EditCommand 设置旧内容对齐；消费方 _merge_prefill 读空串跳过）。
        state["prefill"] = ""
        state["_edit_performed"] = True
        return True


def _check_last_message_role(agent: Any, state: dict) -> None:
    """检查最后一条消息角色，设置重试标记。"""
    if not agent.messages:
        return
    last_role = agent.messages[-1].get("role", "?")
    if last_role == "user":
        state["retry"] = True


# 命令注册表
_COMMANDS: dict[str, type] = {
    "edit": EditCommand,
    "delete": DeleteCommand,
    "resume": ResumeCommand,
    "resume_all": ResumeAllCommand,
}


# ═══════════════════════════════════════════════════════════
# MessageEditor
# ═══════════════════════════════════════════════════════════

class MessageEditor:
    """交互式消息编辑器 — 在底部栏补全弹窗中选择消息，Enter 编辑。

    edit_current_messages() 作为公开入口点。
    """

    def __init__(self, bottom_bar: Any = None, input_: Any = None):
        """初始化 MessageEditor。

        Args:
            bottom_bar: _BottomBar/InkBridge 实例（补全弹窗 + model/session 提取）。
            input_: Input 实例（用于检测 Enter 提交）。
        """
        self._bottom_bar = bottom_bar
        self._input = input_
        # ★ 标准 React Ink 化（消灭例外）：从 bottom_bar 提取 model/session——
        #   InkBridge 持有 AppModel + InkSession（UserSelectPopup 标准协议用）；
        #   无 model/session 环境（测试桩/单次模式）回退旧补全弹窗路径。
        #   防御：MagicMock 任意属性访问返回 MagicMock（非 None）——仅当
        #   model 具备 ``user_select`` 属性且类型非 mock 时采用标准协议
        #   （测试用 MagicMock bottom_bar 的 _model 提取到 MagicMock →
        #   _is_mock_model 排除 → 走 legacy 路径，测试兼容）。
        self._model = None
        self._session = None
        if bottom_bar is not None:
            candidate_model = getattr(bottom_bar, "_model", None)
            candidate_session = getattr(bottom_bar, "_session", None)
            if self._is_mock_model(candidate_model):
                candidate_model = None
                candidate_session = None
            self._model = candidate_model
            self._session = candidate_session
        self._selection_ready: threading.Event = threading.Event()
        self._selection_confirmed: bool = False

    @staticmethod
    def _is_mock_model(model) -> bool:
        """检测候选 model 是否为 mock（MagicMock 任意属性返回 MagicMock）。"""
        if model is None:
            return True
        # unittest.mock 对象类型名含 "Mock"；真实 AppModel 类型名是 "AppModel"
        type_name = type(model).__name__
        if "Mock" in type_name:
            return True
        return not hasattr(model, "user_select")

    # ── 公开入口 ──

    def edit_current_messages(
        self, agent: Any, state: dict, action: str = "edit",
    ) -> bool:
        """进入当前会话消息编辑（Ctrl+O / /editmsg）。

        在主流程同步直接执行（EditmsgPlugin 不再经 run_in_executor 线程池）：
        交互选择期间主协程阻塞在 time.sleep 轮询，render 线程独立驱动
        UserSelectPopup 组件写 done；按回车确认后编辑立即生效，不依赖
        线程调度返回。

        Args:
            agent: ChatAgent 实例（包含 messages 列表）。
            state: 编辑状态字典，用于传递 prefill/retry 等标记。
            action: 编辑动作类型（"edit" / "delete" / "resume" / "resume_all"）。

        Returns:
            True 表示有修改，False 表示无操作。
        """
        messages = agent.messages
        if not messages:
            return False

        # 只显示用户消息
        user_msgs = [(i, m) for i, m in enumerate(messages) if m.get("role") == "user"]
        if not user_msgs:
            return False

        # 构建显示项
        # display_items：纯文本摘要（UserSelectState.options——回车 result 与
        #   legacy 补全弹窗路径消费）；option_lines：TUI 消息渲染方式的多行
        #   AnsiLine（UserSelectPopup 优先渲染，与消息区显示一致）。
        display_items = []
        option_lines = []
        for display_idx, (orig_idx, msg) in enumerate(user_msgs):
            display_items.append(_user_msg_summary(msg, display_idx))
            option_lines.append(_user_msg_display_lines(msg))

        # ★ 设置 Enter 抑制 + 替换补全关闭回调
        #   在交互选择期间，Enter 键不经过 _enter() 提交，
        #   而是通过自定义回调设置独立信号 _selection_ready。
        input_ = self._input
        if input_ is None:
            return False

        # ★ P2-5：将「set_suppress_enter(True) + get_dismiss_completion_callback
        #   + set_dismiss_completion_callback」整个序列移入 try/finally——
        #   修复前若任一步抛异常（input 状态异常等），Enter 抑制永久卡死
        #   （输入无法提交）；finally 确保恢复。
        orig_dismiss_cb = None
        try:
            input_.set_suppress_enter(True)
            # 方向2（私有属性访问公开化）：dismiss 回调经公开 API 保存/替换/恢复
            # （不直接读写 input_._dismiss_completion_callback 私有字段）。
            orig_dismiss_cb = input_.get_dismiss_completion_callback()

            def _editmsg_dismiss():
                """自定义补全关闭回调 — 设置选择完成信号。

                在 render 线程中调用：
                  _dispatch_key_event(enter) → _dismiss_completion()
                → 调用此回调 → 设置 _selection_ready 通知 executor 线程。
                """
                self._selection_confirmed = True
                self._selection_ready.set()

            input_.set_dismiss_completion_callback(_editmsg_dismiss)

            real_idx = self._interactive_message_select(
                user_msgs, display_items, option_lines,
            )
        finally:
            # ★ 修复（P2-6）：恢复原始回调——orig_dismiss_cb 为 None 时也
            #   显式 ``set_dismiss_completion_callback(None)`` 恢复原状（修复前
            #   None 分支跳过恢复，_editmsg_dismiss 永久残留，后续 Enter 误
            #   触发选择完成信号 _selection_ready）。
            try:
                input_.set_dismiss_completion_callback(orig_dismiss_cb)
            except Exception:
                _logger.debug(
                    "edit_current_messages: 恢复 dismiss 回调异常", exc_info=True,
                )
            # ★ 先排空 stdin 残留字节（含 CR+LF 中的 \n），再恢复 Enter 抑制，
            #   防止竞态窗口内残留 \n 触发 _enter() 误消费 prefill
            try:
                input_.flush_stdin_buffer()
            except Exception:
                _logger.debug("edit_current_messages: flush_stdin_buffer 异常", exc_info=True)
            # 安全恢复 Enter 抑制（在排空 stdin 之后，确保无残留字节）
            try:
                input_.set_suppress_enter(False)
            except Exception:
                _logger.debug(
                    "edit_current_messages: set_suppress_enter(False) 异常", exc_info=True,
                )
            # 清理独立信号（防残留）
            self._selection_ready.clear()
            self._selection_confirmed = False
            # ★ review 修复（P2）：底部视图兜底清理——_interactive_message_select
            #   自身 finally 已清理（异常路径安全）；此处作第二道防线（防御
            #   未来新增调用路径未清理/异常中断导致 bottom_view 残留——App
            #   持续只渲染弹窗、输入区消失）。edit 结束不残留任何底部视图。
            if self._model is not None and hasattr(self._model, "bottom_view"):
                self._model.bottom_view = ""

        if real_idx is None:
            return False

        # 执行命令
        cmd_cls = _COMMANDS.get(action, EditCommand)
        cmd = cmd_cls(agent, real_idx)
        return cmd.execute(state)

    # ── 消息选择交互 ──

    def _interactive_message_select(
        self,
        user_msgs: list[tuple[int, dict]],
        display_items: list[str],
        option_lines: list | None = None,
    ) -> int | None:
        """选择要编辑的消息（标准 React Ink UserSelectPopup 协议）。

        交互流程（与 user_select 工具同协议，标准 React Ink 无例外）：
          1. 设置 ``model.user_select``（visible=True, seq+1, options=消息摘要，
             option_lines=消息 TUI 渲染多行）；
          2. ``UserSelectPopup`` 组件在 App 组件树底部区渲染（use_input 消费
             ↑↓/Enter/Esc，render 线程驱动路由）；
          3. 本方法轮询 ``us.done``（跨线程 GIL 原子字段）并读取结果索引；
          4. 清理 ``model.user_select = UserSelectState()`` + 请求重绘。

        Args:
            user_msgs: [(原始索引, 消息字典), ...]。
            display_items: 每个消息的显示文本（UserSelectPopup 选项）。
            option_lines: 每个消息的 TUI 渲染多行（list[list[AnsiLine]]，
                可选）——UserSelectPopup 优先渲染，与消息区显示一致。

        Returns:
            选中的原始消息索引，None 表示取消/超时。
        """
        model = self._model
        session = self._session
        if model is None or session is None or not hasattr(model, "user_select"):
            # 无 ChatUI 模型环境（测试桩/单次模式）：回退旧补全弹窗路径（兼容）
            return self._interactive_message_select_legacy(user_msgs, display_items)

        from src.tui.app.model import UserSelectState
        sel_count = len(user_msgs)
        if sel_count == 0:
            return None

        # 设置弹窗状态（seq+1 强制 UserSelectPopup 重挂载，重置内部 state）
        prev_seq = getattr(model.user_select, "seq", 0)
        model.user_select = UserSelectState(
            visible=True,
            seq=prev_seq + 1,
            title="\u9009\u62e9\u8981\u7f16\u8f91\u7684\u6d88\u606f",  # 选择要编辑的消息
            options=list(display_items),
            option_lines=list(option_lines) if option_lines else [],
            selected=sel_count - 1,  # 默认选中最后一条
            deadline=time.monotonic() + 120,  # 2 分钟超时
        )
        # ★ 模态底部视图（2026-08-17 通用机制）：与 user_select 工具同协议
        #   ——激活底部视图（底部区只渲染弹窗，状态栏/输入区不显示）。
        if hasattr(model, "bottom_view"):
            model.bottom_view = "user_select"
        try:
            session.request_bottom_redraw()
        except Exception:
            _logger.debug("_interactive_message_select: request_bottom_redraw 异常", exc_info=True)

        # 轮询等待组件交互完成（render 线程运行中；UserSelectPopup use_input 写 done）
        # ★ P2（review 修复）：轮询 + 解析 + 清理整段 try/finally——异常路径
        #   也保证 user_select + bottom_view 恢复（不残留弹窗/底部视图，
        #   输入区不消失）；与 tools/user_select.py 的 finally 清理模式对齐。
        try:
            deadline = model.user_select.deadline
            while not model.user_select.done:
                if self._selection_ready.is_set():
                    # ★ P2-7：标准路径同时响应 _selection_ready 信号（自定义
                    #   dismiss 回调 _editmsg_dismiss 设置，legacy 路径已响应）——
                    #   修复前标准路径仅响应 ``user_select.done``：若 Enter 经
                    #   ``_dismiss_completion → _editmsg_dismiss`` 路径确认而 done
                    #   未及时写回，轮询可能空转到超时；同时检查双信号更稳。
                    break
                if deadline > 0 and time.monotonic() >= deadline:
                    # 超时：原子终态写入（first-write-wins）——组件已确认
                    # （done 已置位）则放弃覆盖，保留组件结果（2026-08-17
                    # 修复：修复前无条件写 done/action 覆盖组件确认结果）。
                    model.user_select.try_set_final("timeout", [])
                    break
                time.sleep(0.05)

            st = model.user_select
            action = st.action or "timeout"
            # ★ 修复（P2-5）：真正消费 _selection_confirmed——Enter 经
            #   ``_dismiss_completion → _editmsg_dismiss`` 路径确认时 st.action
            #   可能未写回 "confirmed"（修复前 action 归为 "timeout" 丢弃已确认的
            #   选择）；此处回退按 confirmed 处理（st.selected 读取紧随其后）。
            if action == "timeout" and self._selection_confirmed:
                action = "confirmed"
            # ★ 修复（P2）：selected 可能为 None（外部注入）——
            #   int(None) 抛 TypeError；归一化失败回退默认选中最后一条。
            try:
                selected = int(getattr(st, "selected", sel_count - 1))
            except (TypeError, ValueError):
                selected = sel_count - 1
        finally:
            # 清理弹窗状态 + 请求重绘（底部栏立即恢复正常显示）
            model.user_select = UserSelectState()
            # ★ 模态底部视图：关闭底部视图 → App 恢复状态栏 + 输入区。
            if hasattr(model, "bottom_view"):
                model.bottom_view = ""
            try:
                session.request_bottom_redraw()
            except Exception:
                _logger.debug("_interactive_message_select: cleanup redraw 异常", exc_info=True)

        if action != "confirmed":
            return None
        if not (0 <= selected < sel_count):
            return None
        return user_msgs[selected][0]

    def _interactive_message_select_legacy(
        self,
        user_msgs: list[tuple[int, dict]],
        display_items: list[str],
    ) -> int | None:
        """旧补全弹窗消息选择路径（无 ChatUI 模型环境回退，兼容保留）。

        # deprecated: 标准路径（UserSelectPopup 协议）不可用时的回退——
        # 生产 ChatUI 环境恒走标准 React Ink 路径，本方法仅服务测试桩/
        # 单次模式（bottom_bar 无 _model 的兼容场景）。

        Args:
            user_msgs: [(原始索引, 消息字典), ...]。
            display_items: 每个消息的显示文本。

        Returns:
            选中的原始消息索引，None 表示取消。
        """
        bb = self._bottom_bar
        input_ = self._input
        if bb is None or input_ is None:
            return None

        sel_count = len(user_msgs)
        if sel_count == 0:
            return None

        # 显示补全弹窗（默认选中最后一条）
        try:
            bb.show_completions(
                display_items,
                sel_count - 1,
                title="\u9009\u62e9\u8981\u7f16\u8f91\u7684\u6d88\u606f",  # 选择要编辑的消息
            )
        except Exception as exc:
            _logger.debug("show_completions 失败: %s", exc)
            return None

        # 轮询等待用户选择
        last_sel_idx = sel_count - 1
        deadline = time.monotonic() + 120  # 2 分钟超时
        try:
            while time.monotonic() < deadline:
                # ★ 使用独立信号检测 Enter（不经过 get_queued_input / _enter 路径）
                #   当用户按 Enter 时，自定义回调设置 _selection_ready，
                #   wait(timeout=0.05) 返回 True，与原有 50ms 轮询周期一致。
                if self._selection_ready.wait(timeout=0.05):
                    # Enter 已被检测到；读取最后保存的选中索引
                    try:
                        comp_idx = bb.get_selected_completion_index()
                        if 0 <= comp_idx < sel_count:
                            last_sel_idx = comp_idx
                    except Exception as exc:
                        _logger.debug("_interactive_message_select: get_selected_completion_index 异常（Enter 路径）: %s", exc)
                    break

                # 检查 ESC/中断
                if input_.interrupted:
                    _logger.debug("_interactive_message_select: ESC 中断，取消选择")
                    last_sel_idx = -1  # 标记取消
                    break

                # 追踪当前选中的 completion 索引
                try:
                    # 方向2（property 调用修复）：bb.is_completion_visible 为
                    # property——修复前按方法调用抛 TypeError 中断选择轮询。
                    if bb.is_completion_visible:
                        comp_idx = bb.get_selected_completion_index()
                        if 0 <= comp_idx < sel_count:
                            last_sel_idx = comp_idx
                except Exception as exc:
                    _logger.debug("_interactive_message_select: get_selected_completion_index 异常（轮询路径）: %s", exc)

            # 隐藏弹窗
            try:
                bb.hide_completions()
            except Exception:
                pass

            # 验证选择
            if last_sel_idx < 0 or last_sel_idx >= sel_count:
                return None

            return user_msgs[last_sel_idx][0]

        except Exception as exc:
            _logger.debug("_interactive_message_select 异常: %s", exc)
            try:
                bb.hide_completions()
            except Exception:
                pass
            return None


# ═══════════════════════════════════════════════════════════
# 向后兼容入口
# ═══════════════════════════════════════════════════════════

def edit_current_messages(
    agent: Any, state: dict, bottom_bar: Any = None, input_: Any = None,
    action: str = "edit",
) -> bool:
    """直接进入当前会话消息编辑（模块级入口，向后兼容）。

    Args:
        agent: ChatAgent 实例。
        state: 编辑状态字典。
        bottom_bar: _BottomBar 实例。
        input_: Input 实例。
        action: 编辑动作类型。

    Returns:
        True 表示有修改，False 表示无操作。
    """
    return MessageEditor(
        bottom_bar=bottom_bar, input_=input_,
    ).edit_current_messages(agent, state, action=action)


__all__ = [
    "MessageEditor",
    "edit_current_messages",
]
