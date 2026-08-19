"""交互式会话消息编辑器 — 使用独立 React Ink 组件（EditMsgSelectPopup）选择消息。

用法：在聊天中输入 /editmsg 或 Ctrl+O 进入消息编辑。

编辑职责：
- 消息选择交互（EditMsgSelectPopup 独立 React Ink 组件 + use_input 交互）
- 编辑/删除/恢复动作处理
- 会话管理入口（MessageEditor.edit_current_messages）

★ 2026-08-18（用户需求：editmsg 与 user_select 不能用同一份代码）：
消息选择交互从「user_select 协议（model.user_select + UserSelectPopup +
bottom_view="user_select"）」拆分为**独立协议**——设置
``model.editmsg_select``（EditMsgSelectState，visible=True, seq+1,
options=单行摘要）→ App 组件树渲染 ``EditMsgSelectPopup``（独立组件，
use_input 消费 ↑↓/Enter/Esc）→ 本模块轮询 ``es.done``（跨线程 GIL
原子字段）→ 读取结果。**每条消息只显示一行**（options 为单行摘要，
不再使用多行 option_lines）。不再直接操作补全弹窗私有字段、不再自定义
dismiss 回调 hack。无 ChatUI 模型环境（测试桩/单次模式）回退旧补全弹窗
路径（兼容保留）。

适配 2026-07 TUI 重构后的架构：
  - 不复用已删除的 pipeline/message_display.py 完整版，使用内置精简替代
  - 标准交互经 ``model.editmsg_select``（ChatUIConsumer 活跃时可用）；
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
    """生成用户消息的简短摘要（用于消息选择弹窗显示，**每条一行**）。

    格式: N. ● │ 消息内容摘要...（N 为 1 基显示编号——与 user_select
    弹窗视觉一致，用户可直接对应第几条）

    ★ 2026-08-18（用户需求：editmsg 每条信息只显示一行）：消息选择弹窗
    不再使用 TUI 多行渲染（option_lines），改为单行摘要——多行消息内容
    经 ``_truncate`` 折叠为单行（换行 → 空格），超宽截断加 "..."。

    Args:
        msg: 消息字典。
        idx: 显示编号（0 基；内部 +1 转为 1 基显示）。
        max_w: 最大宽度。

    Returns:
        纯文本单行摘要字符串（不含 ANSI 颜色）。
    """
    content = _content_str(msg.get("content", ""))
    text = content.strip()
    return f"{idx + 1}. \u25cf \u2502 {_truncate(text, max_w)}"


def _restore_feedback(restore_text: str) -> tuple[str, bool]:
    """沙盒恢复反馈行（editmsg/deitmsg 共用，P2-3 修复）。

    恢复失败（降级继续编辑语义，见 ``_restore_sandbox_to``）返回的文本
    以「沙盒恢复失败: …」标识——调用方须以 ⚠（黄）渲染而非 ✓（绿），
    修复前无条件绿色 ✓ 把失败提示显示成成功结果。

    Args:
        restore_text: ``_restore_sandbox_to`` / ``_truncate_messages`` 返回文本。

    Returns:
        (显示文本, is_failure)：is_failure=True 时调用方用警告色渲染。
    """
    if not restore_text:
        return ("\u6c99\u76d2\u65e0\u6587\u4ef6\u9700\u8fd8\u539f", False)
    is_failure = "\u5931\u8d25" in restore_text
    return (restore_text, is_failure)


def _content_has_nontext(content: Any) -> bool:
    """检测多模态 content 是否含非文本部分（图片等，P3-3）。

    Edit 语义是「预填旧文本供编辑重发」——多模态消息的非文本部分无法经
    文本编辑行表达，重发后将静默丢失。检测到时调用方须显式提示用户。

    Args:
        content: 消息 content（str 或 list[dict]）。

    Returns:
        True — content 为 list 且含 type 非 text/None 的部分。
    """
    if not isinstance(content, list):
        return False
    for part in content:
        if isinstance(part, dict) and part.get("type") not in (None, "text"):
            return True
    return False


def _text_part_str(content: Any) -> str:
    """提取多模态 content 的纯文本部分（P3-3）。

    ``_content_str``（显示摘要用）会把非文本 dict 拍平为 str 混入结果
    （如 ``看图 {'type': 'image_url', ...}``）——**编辑预填**用途不应携带：
    prefill 是文本编辑行内容，图片等部分重发后丢失（调用方经
    ``_content_has_nontext`` 提示用户）。本函数只拼接 text 部分
    （type 为 text/None 的 ``text`` 字段），非 list 输入回退 ``_content_str``。

    Args:
        content: 消息 content（str 或 list[dict]）。

    Returns:
        纯文本字符串（多 text 部分以空格拼接，与 _content_str 分隔一致）。
    """
    if not isinstance(content, list):
        return _content_str(content)
    parts: list[str] = []
    for part in content:
        if isinstance(part, dict) and part.get("type") in (None, "text"):
            t = part.get("text", "")
            if t:
                parts.append(str(t))
        elif isinstance(part, str):
            parts.append(part)
    return " ".join(parts)


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
    # ★ 2026-08-19（用户需求修复：editmsg 按回车后不更新上下文百分比）：
    #   消息列表被截断（长度变化）后**立即**刷新上下文使用率全局快照
    #   （ContextManager.refresh_usage——模式行行首 ``main · N%`` 即时更新）。
    #   修复前 Edit/Delete/Resume 命令直接操作 messages 列表、未触发
    #   ContextManager 缓存同步点，全局百分比保持旧值直至下一次消息追加
    #   （用户按回车确认编辑后看到百分比不变）。refresh_usage 内部按
    #   len 变化自动 resync 重算（O(n)，编辑为低频路径可接受）。经
    #   BaseAgent._refresh_context_usage 调用（getattr 防御——SubAgent/
    #   测试桩无 context_manager 或方法时静默跳过，不崩溃）。
    try:
        _refresh = getattr(agent, "_refresh_context_usage", None)
        if callable(_refresh):
            _refresh()
    except Exception:
        _logger.debug("截断消息后刷新上下文使用率失败", exc_info=True)
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

        old_msg = messages[self.real_idx]
        old_content_raw = old_msg.get("content", "")
        # ★ P3-3：prefill 取**纯文本部分**——多模态消息（含图片等非文本
        #   部分）经 _content_str 拍平会把 image dict 的 str 形式混入编辑行
        #   （垃圾文本）；改用 _text_part_str 只取 text 部分 + 显式警告。
        old_content = _text_part_str(old_content_raw)

        # 截断 + 沙盒恢复 + remap（P2-6：公共助手，恢复失败语义见
        # _restore_sandbox_to —— 明确降级：记录 warning 并继续编辑）
        state["_restore_text"] = _truncate_messages(agent, self.real_idx)

        # ★ P3-3：多模态消息（含图片等非文本部分）经文本预填重发后非文本
        #   部分静默丢失——显式提示用户（editmsg_plugin 渲染 ⚠ 行）。
        if _content_has_nontext(old_content_raw):
            state["_prefill_warning"] = (
                "\u539f\u6d88\u606f\u542b\u975e\u6587\u672c\u5185\u5bb9"
                "\uff08\u5982\u56fe\u7247\uff09\uff0c\u7f16\u8f91\u91cd\u53d1"
                "\u540e\u975e\u6587\u672c\u90e8\u5206\u5c06\u4e22\u5931"
            )
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
        # ★ 独立 React Ink 协议（2026-08-18 拆分）：从 bottom_bar 提取
        #   model/session——InkBridge 持有 AppModel + InkSession
        #   （EditMsgSelectPopup 标准协议用）；无 model/session 环境（测试桩/
        #   单次模式）回退旧补全弹窗路径。防御：MagicMock 任意属性访问返回
        #   MagicMock（非 None）——仅当 model 具备 ``editmsg_select`` 属性且
        #   类型非 mock 时采用标准协议（测试用 MagicMock bottom_bar 的
        #   _model 提取到 MagicMock → _is_mock_model 排除 → 走 legacy 路径，
        #   测试兼容）。
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
        # ★ P1-2：取消信号——dismiss 回调触发时输入中断标志已置位
        #   （Esc 路径 ``_do_interrupt`` 先于 dismiss 执行）视为取消；
        #   与 _selection_confirmed 互斥（中断优先）。
        self._selection_cancelled: bool = False

    def _editmsg_dismiss_cb(self) -> None:
        """自定义补全关闭回调 — 设置选择完成/取消信号（原闭包提取为方法）。

        在 render 线程中调用：``_dispatch_key_event(enter/escape) →
        _dismiss_completion()`` → 本回调。

        ★ P1-2 修复（Esc 误判确认）：dismiss 的语义是「关闭补全弹窗」，
        触发方不止 Enter——组件挂载窗口（编辑器设置 model.editmsg_select
        到 EditMsgSelectPopup 挂载并注册 router 之间，约 1 帧）内按 Esc
        也经旧路径触发本回调。修复前无条件视为「已确认」→ 截断并预填
        最后一条用户消息（用户数据被动截断，且与挂载后 Esc=取消语义相反）。
        现按中断标志区分：
          - ``input.interrupted`` 已置位（escape else 分支 ``_do_interrupt``
            先于 dismiss 执行——见 _input_dispatcher 调换顺序修复）→ 取消；
          - 未置位（enter 分支不设置中断）→ legacy 路径（无 model）视为
            确认（行为与旧补全弹窗确认等价）。

        ★ 2026-08-19 修复（很多上文时按回车不能编辑对应消息）：标准路径
        （EditMsgSelectPopup）弹窗**活跃期间**（visible 且未 done）的
        Enter-dismiss **忽略**——dismiss 确认不携带用户导航后的 selected
        （编辑的是默认选中的最后一条），而组件才是确认权威（持有导航值）；
        挂载窗口期经旧路径到达的 Enter 大概率是 /editmsg 提交回车的残留
        LF（超窗误判）或弹窗未显示时的误触，不应截断消息。Esc（中断标志
        置位）的取消语义保留（挂载窗口 Esc 用户意图即取消）。
        """
        # ── 标准路径守卫：弹窗活跃（组件挂载窗口）→ Enter-dismiss 忽略 ──
        es_active = False
        model = self._model
        if model is not None and hasattr(model, "editmsg_select"):
            es = getattr(model, "editmsg_select", None)
            es_active = bool(
                es is not None
                and getattr(es, "visible", False)
                and not getattr(es, "done", False)
            )
        cancelled = False
        try:
            inp = self._input
            if inp is not None and bool(getattr(inp, "interrupted", False)):
                cancelled = True
        except Exception:
            cancelled = False
        if cancelled:
            self._selection_cancelled = True
            self._selection_ready.set()
        elif es_active:
            # 组件确认权威：忽略挂载窗口期的旧路径 Enter（轮询继续等组件
            # 写 done——含用户导航后的 selected）。
            return
        else:
            self._selection_confirmed = True
            self._selection_ready.set()

    @staticmethod
    def _is_mock_model(model) -> bool:
        """检测候选 model 是否为 mock（MagicMock 任意属性返回 MagicMock）。"""
        if model is None:
            return True
        # unittest.mock 对象类型名含 "Mock"；真实 AppModel 类型名是 "AppModel"
        type_name = type(model).__name__
        if "Mock" in type_name:
            return True
        return not hasattr(model, "editmsg_select")

    # ── 公开入口 ──

    def edit_current_messages(
        self, agent: Any, state: dict, action: str = "edit",
    ) -> bool:
        """进入当前会话消息编辑（Ctrl+O / /editmsg）。

        在主流程同步直接执行（EditmsgPlugin 不再经 run_in_executor 线程池）：
        交互选择期间主协程阻塞在 time.sleep 轮询，render 线程独立驱动
        EditMsgSelectPopup 组件写 done；按回车确认后编辑立即生效，不依赖
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

        # 构建显示项（★ 2026-08-18 用户需求：每条消息只显示一行——单行摘要，
        # 供 EditMsgSelectState.options 与 legacy 补全弹窗路径消费）。
        display_items = []
        for display_idx, (orig_idx, msg) in enumerate(user_msgs):
            display_items.append(_user_msg_summary(msg, display_idx))

        # ★ 设置 Enter 抑制 + 替换补全关闭回调
        #   在交互选择期间，Enter 键不经过 _enter() 提交，
        #   而是通过自定义回调设置独立信号 _selection_ready。
        input_ = self._input
        if input_ is None:
            return False

        # ★ P1-3 前置：清除残留中断标志——上一轮 Esc 中断后
        #   Input.interrupted 可能残留 True（仅 dispatcher.reset() 清除），
        #   不清则选择期间轮询中断检查立即误判取消。clear_interrupted
        #   只清标志不清缓冲/队列（区别于 reset），不丢用户输入。
        try:
            input_.clear_interrupted()
        except Exception:
            _logger.debug(
                "edit_current_messages: clear_interrupted 异常（桩实例无方法）",
                exc_info=True,
            )

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

            input_.set_dismiss_completion_callback(self._editmsg_dismiss_cb)

            real_idx = self._interactive_message_select(user_msgs, display_items)
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
            self._selection_cancelled = False
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
    ) -> int | None:
        """选择要编辑的消息（独立 React Ink EditMsgSelectPopup 协议）。

        ★ 2026-08-18（用户需求：editmsg 与 user_select 不能用同一份代码）：
        本方法使用**独立协议**（model.editmsg_select + EditMsgSelectPopup +
        bottom_view="editmsg"），不复用 user_select 的
        model.user_select / UserSelectPopup / bottom_view="user_select"。
        **每条消息只显示一行**（display_items 为单行摘要）。

        交互流程（与 user_select 工具协议同构，但独立实现）：
          1. 设置 ``model.editmsg_select``（visible=True, seq+1,
             options=单行消息摘要）；
          2. ``EditMsgSelectPopup`` 组件在 App 组件树底部区渲染（use_input
             消费 ↑↓/Enter/Esc，render 线程驱动路由）；
          3. 本方法轮询 ``es.done``（跨线程 GIL 原子字段）并读取结果索引；
          4. 清理 ``model.editmsg_select = EditMsgSelectState()`` + 请求重绘。

        Args:
            user_msgs: [(原始索引, 消息字典), ...]。
            display_items: 每个消息的单行显示文本（EditMsgSelectState
                options）。

        Returns:
            选中的原始消息索引，None 表示取消/超时。
        """
        model = self._model
        session = self._session
        if model is None or session is None or not hasattr(model, "editmsg_select"):
            # 无 ChatUI 模型环境（测试桩/单次模式）：回退旧补全弹窗路径（兼容）
            return self._interactive_message_select_legacy(user_msgs, display_items)

        from src.tui.app.model import EditMsgSelectState
        sel_count = len(user_msgs)
        if sel_count == 0:
            return None

        # 设置弹窗状态（seq+1 强制 EditMsgSelectPopup 重挂载，重置内部 state）
        prev_seq = getattr(model.editmsg_select, "seq", 0)
        model.editmsg_select = EditMsgSelectState(
            visible=True,
            seq=prev_seq + 1,
            title="\u9009\u62e9\u8981\u7f16\u8f91\u7684\u6d88\u606f",  # 选择要编辑的消息
            options=list(display_items),
            selected=sel_count - 1,  # 默认选中最后一条
            deadline=time.monotonic() + 120,  # 2 分钟超时
        )
        # ★ W6 修复（2026-08-19，编辑错消息——不能编辑对应的用户消息）：
        #   Enter 落在「dismiss 回调已替换 → es 设置前」窗口时经
        #   ``_editmsg_dismiss_cb``（es 未设置 visible=False → es_active
        #   守卫不生效）**提前置位** ``_selection_ready``——轮询第一轮即
        #   break，selected 取 es 初始值（默认最后一条）→ 用户导航目标被
        #   丢弃，编辑的是最后一条。es 设置（visible=True）之后立即清除
        #   信号：此后到达的 Enter 由 es_active 守卫忽略（c56a21e——组件
        #   才是确认权威），组件确认经 ``es.done`` 生效。仅标准路径清除
        #   （legacy 路径依赖 ``_selection_ready`` 作为 Enter 确认信号）。
        self._selection_ready.clear()
        self._selection_confirmed = False
        self._selection_cancelled = False
        # ★ 模态底部视图（2026-08-17 通用机制）：激活独立底部视图
        #   （bottom_view="editmsg"——底部区只渲染 EditMsgSelectPopup，
        #   状态栏/输入区不显示；与 user_select 的 "user_select" 视图独立）。
        if hasattr(model, "bottom_view"):
            model.bottom_view = "editmsg"
        try:
            session.request_bottom_redraw()
        except Exception:
            _logger.debug("_interactive_message_select: request_bottom_redraw 异常", exc_info=True)

        # 轮询等待组件交互完成（render 线程运行中；EditMsgSelectPopup
        # use_input 写 done）
        # ★ P2（review 修复）：轮询 + 解析 + 清理整段 try/finally——异常路径
        #   也保证 editmsg_select + bottom_view 恢复（不残留弹窗/底部视图，
        #   输入区不消失）；与 tools/user_select.py 的 finally 清理模式对齐。
        # ★ P1-3：进入轮询前保存 es 引用——循环内每轮检测
        #   ``model.editmsg_select is not es``（外部替换，如 Ctrl+L 清屏
        #   reset_display 重建实例）与输入中断标志，二者均视为取消立即退出，
        #   不再空转到 120s 超时挂起。
        es = model.editmsg_select
        try:
            while not es.done:
                if self._selection_ready.is_set():
                    # ★ P2-7：标准路径同时响应 _selection_ready 信号（自定义
                    #   dismiss 回调 _editmsg_dismiss 设置，legacy 路径已响应）——
                    #   修复前标准路径仅响应 ``editmsg_select.done``：若 Enter 经
                    #   ``_dismiss_completion → _editmsg_dismiss`` 路径确认而 done
                    #   未及时写回，轮询可能空转到超时；同时检查双信号更稳。
                    break
                # ★ P1-3：Ctrl+C / 双 Esc 中断（_do_interrupt 置位
                #   Input.interrupted）→ 视为取消退出——修复前弹窗期间 Ctrl+C
                #   完全失效（SelectInput consumeAll 放行 \x03 的意图经
                #   kind=="char" 判定永假：raw 0x03 解析为 kind="interrupt"
                #   直接走 _do_interrupt，不进 router），轮询也不检查中断标志
                #   → 无响应无取消，只能 Esc 或等 120s 超时。
                try:
                    if self._input is not None and bool(
                        getattr(self._input, "interrupted", False)
                    ):
                        break
                except Exception:
                    pass
                # ★ P3-5：es 实例被外部替换（清屏 reset_display 等）→ 取消退出
                if model.editmsg_select is not es:
                    break
                # ★ 每轮重读 es.deadline：deadline 可在轮询期间被外部更新
                #   （提前超时/延长等待），循环外只读一次会忽略更新——
                #   空转到初始 120s 截止才退出（2026-08-20 性能修复）。
                deadline = es.deadline
                if deadline > 0 and time.monotonic() >= deadline:
                    # 超时：原子终态写入（first-write-wins）——组件已确认
                    # （done 已置位）则放弃覆盖，保留组件结果（2026-08-17
                    # 修复：修复前无条件写 done/action 覆盖组件确认结果）。
                    es.try_set_final("timeout", [])
                    break
                time.sleep(0.05)

            st = model.editmsg_select
            action = st.action or "timeout"
            # ★ 修复（P2-5）：真正消费 _selection_confirmed——Enter 经
            #   ``_dismiss_completion → _editmsg_dismiss`` 路径确认时 st.action
            #   可能未写回 "confirmed"（修复前 action 归为 "timeout" 丢弃已确认的
            #   选择）；此处回退按 confirmed 处理（st.selected 读取紧随其后）。
            # ★ P1-2：取消信号优先于确认（二者互斥，防御性排序）——Esc 路径
            #   dismiss（中断标志已置位）判定为取消；中断检查 break 时
            #   st.action 为空 → timeout，同样经此处归一为 cancel。
            if action == "timeout":
                if self._selection_cancelled:
                    action = "cancel"
                elif self._selection_confirmed:
                    action = "confirmed"
            # ★ 修复（P2）：selected 可能为 None（外部注入）——
            #   int(None) 抛 TypeError；归一化失败回退默认选中最后一条。
            try:
                selected = int(getattr(st, "selected", sel_count - 1))
            except (TypeError, ValueError):
                selected = sel_count - 1
        finally:
            # 清理弹窗状态 + 请求重绘（底部栏立即恢复正常显示）。
            # ★ 2026-08-18（连续弹出显示错乱修复）：先关闭底部视图再清理
            #   状态（避免「状态已重置但 bottom_view 仍指向弹窗」的空白帧
            #   窗口）；且清理**保留 seq**——seq 单调递增保证 App key
            #   （em-{seq}）永不重复 → 调和器强制重挂载 EditMsgSelectPopup
            #   （修复前归零 → 连续打开 key 复用 → fiber 复用 → use_state
            #   残留旧选中，弹窗显示错乱）。
            if hasattr(model, "bottom_view"):
                model.bottom_view = ""
            prev_es_seq = getattr(model.editmsg_select, "seq", 0)
            model.editmsg_select = EditMsgSelectState(seq=prev_es_seq)
            try:
                session.request_bottom_redraw()
            except Exception:
                _logger.debug("_interactive_message_select: cleanup redraw 异常", exc_info=True)
            # ★ 窗口期 Enter 捕获开启（2026-08-19，editmsg「很多上文时按回车
            #   不能编辑对应消息」根因修复）：弹窗已到终态（确认/取消/超时）
            #   ——此后用户按 Enter 的语义是「提交编辑」而非「确认弹窗」。
            #   弹窗关闭 → prefill 注入之间存在长窗口（flush_input_router
            #   等慢帧 + flush_stdin_buffer 丢字节 + 截断 + 插件清 _input_ready
            #   丢空提交 + clear/display 全量重放 flush——大量上文时 1s~10s），
            #   期间 Enter 被 suppress 吞 / 被 flush 丢弃均无痕丢失（用户
            #   「按回车没反应，要再按一次」）。capture 开启后这些 Enter 记
            #   为提交意图（deferred），editmsg_plugin 注入 prefill 后消费
            #   并自动提交兑现。放在 flush_input_router **之前**——覆盖
            #   flush 等待期间（大量上文 200ms~数秒）被吞的 Enter（弹窗
            #   刚关闭用户最可能立即按 Enter 的时机）。取消/超时路径同样
            #   开启（editmsg_plugin 结束时统一关闭并清残留，无泄漏）。
            try:
                set_capture = getattr(self._input, "set_enter_capture", None)
                if callable(set_capture):
                    set_capture(True)
            except Exception:
                _logger.debug(
                    "_interactive_message_select: set_enter_capture 异常",
                    exc_info=True,
                )
            # ★ 2026-08-19 根因修复（很多上文时按回车不能编辑对应消息，
            #   1 条消息快速连按也复现）：弹窗清理后、渲染线程发布新
            #   input router 前，旧 router 仍含已卸载弹窗的 SelectInput
            #   handler + use_modal 吞噬——用户确认后紧接着的 Enter
            #   （prefill 注入后提交）被旧 router 消费（``_enter()`` 不
            #   执行 → prefill 不提交 →「按回车没反应，要再按一次」）。
            #   窗口 = 清理 → 渲染线程完成下一帧（10Hz 节流 + 帧耗时，
            #   大量上文重放时一帧 100ms~1s+）。同步等待两帧完成（新
            #   router 已发布，不含弹窗 hooks）再返回——后续 Enter 走
            #   正常提交路径。超时（2s）降级继续（渲染线程挂起不死锁）。
            flush_router = getattr(session, "flush_input_router", None)
            if callable(flush_router):
                try:
                    flush_router(2.0)
                except Exception:
                    _logger.debug(
                        "_interactive_message_select: flush_input_router 异常",
                        exc_info=True,
                    )

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

        # deprecated: 标准路径（EditMsgSelectPopup 协议）不可用时的回退——
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
