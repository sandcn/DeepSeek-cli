"""InkSession suspend/崩溃恢复期间短内容不丢失测试（2026-08-15 修复）。

修复背景：模型在交互工具挂起（suspend）/ 渲染线程崩溃恢复 / flush 超时兜底
期间输出的短思考/短回答命令，被 ``_drain_queue_safe`` **无条件丢弃**——
命令既未应用（模型状态缺失）也未渲染，视觉上「很短的回答跟思考没显示」
（偶发，取决于命令入队与 suspend 清理的时序）。

修复：``_drain_queue_safe(keep_content=True)`` 保留用户可见核心内容命令
（``_KEEP_CONTENT_CMDS``：REASONING/CONTENT/PHASE_DONE/TOOL_*/USER_MSG 等），
仅丢弃非内容命令（WRITE_LINE/DISPLAY_MSGS/SUBAGENT_FRAME 等）；保留的命令
resume 后由渲染线程处理显示。

本测试锁定：
  1. suspend 清理期间入队的内容命令被保留（不丢弃），resume 后正常显示；
  2. 非内容命令（WRITE_LINE）在 keep_content 清理时被丢弃；
  3. keep_content=False（默认/stop）仍丢弃全部命令（原语义不回归）。
"""

from __future__ import annotations

import io
import time

from src.tui.app.app import build_app_element
from src.tui.app.apply import apply_cmd
from src.tui.app.model import AppModel
from src.tui._config import TuiConfig
from src.tui._screen import TerminalWidthCache
from src.tui.ink.session import InkSession
from src.tui._const import (
    MainPhaseCmd, ReasoningCmd, ContentCmd, PhaseDoneCmd, WriteLineCmd,
)
from src.tui.ink._cmd_priority import _get_cmd_id, _cmd_name


def _make_session() -> tuple[InkSession, AppModel, io.StringIO]:
    cache = TerminalWidthCache.get_default()
    cache._width = 80
    cache._height = 24
    model = AppModel()
    stream = io.StringIO()
    session = InkSession(
        model=model,
        apply_cmd=apply_cmd,
        build_tree=build_app_element,
        config=TuiConfig.defaults(),
        stream=stream,
    )
    session.set_line_tracker(None)
    return session, model, stream


def _push_short_content(session) -> None:
    """推入典型短内容命令序列（思考+回答）。"""
    session.push_cmd(MainPhaseCmd(phase="thinking"))
    session.push_cmd(ReasoningCmd(text="短思"))
    session.push_cmd(PhaseDoneCmd(phase="reasoning"))
    session.push_cmd(ContentCmd(text="短答"))
    session.push_cmd(PhaseDoneCmd(phase="content"))


# ── keep_content 保留语义 ─────────────────────────────

def test_keep_content_preserves_content_cmds():
    """keep_content=True 清理时保留内容命令（REASONING/CONTENT/PHASE_DONE）。"""
    session, _, _ = _make_session()
    _push_short_content(session)
    # 同时入队一条非内容命令
    session.push_cmd(WriteLineCmd(text="外部输出"))
    qsize_before = session._cmd_queue.qsize()
    assert qsize_before == 6, f"应入队 6 条命令，实际 {qsize_before}"

    dropped = session._drain_queue_safe(keep_content=True)
    # 丢弃 1 条非内容命令（WRITE_LINE），保留 5 条内容命令
    assert dropped == 1, f"应丢弃 1 条非内容命令，实际 {dropped}"
    assert session._cmd_queue.qsize() == 5, (
        f"应保留 5 条内容命令，实际 {session._cmd_queue.qsize()}"
    )
    # 保留的命令顺序正确（按 seq：MAIN_PHASE → REASONING → PHASE_DONE → CONTENT → PHASE_DONE）
    items = sorted(session._cmd_queue.queue, key=lambda x: x[1])
    ids = [_get_cmd_id(item[2]) for item in items]
    assert ids == [20, 0, 2, 1, 2], f"保留命令 id 顺序异常: {ids}"


def test_keep_content_drops_non_content_cmds():
    """keep_content=True 清理时丢弃非内容命令（WRITE_LINE/DISPLAY_MSGS）。"""
    session, _, _ = _make_session()
    session.push_cmd(WriteLineCmd(text="外部输出"))
    session.push_cmd(ReasoningCmd(text="短思"))
    dropped = session._drain_queue_safe(keep_content=True)
    assert dropped == 1, f"应丢弃 WRITE_LINE，实际 {dropped}"
    assert session._cmd_queue.qsize() == 1, "应保留 REASONING"
    item = session._cmd_queue.get()
    session._cmd_queue.task_done()
    assert _get_cmd_id(item[2]) == 0  # REASONING


def test_keep_content_false_drops_all():
    """keep_content=False（默认/stop）仍丢弃全部命令（原语义不回归）。"""
    session, _, _ = _make_session()
    _push_short_content(session)
    dropped = session._drain_queue_safe(keep_content=False)
    assert dropped == 5, f"应丢弃全部 5 条，实际 {dropped}"
    assert session._cmd_queue.empty()


def test_keep_content_empty_queue():
    """keep_content=True 清理空队列：零丢弃、无异常。"""
    session, _, _ = _make_session()
    dropped = session._drain_queue_safe(keep_content=True)
    assert dropped == 0
    assert session._cmd_queue.empty()


# ── suspend 期间短内容不丢失（端到端） ──────────────────

def test_suspend_preserves_short_content():
    """suspend（交互工具挂起）期间入队的短内容命令被保留，resume 后正常显示。

    模拟：模型输出短内容命令入队后立即 suspend（suspend 的 ``_drain_queue_safe``
    会清理队列）——修复前内容命令被丢弃（模型状态缺失、屏幕不显示）；修复后
    命令保留，resume 后渲染线程处理，短思/短答写入模型并显示。
    """
    session, model, stream = _make_session()
    session.start()
    time.sleep(0.15)
    try:
        # 先有历史（撑起文档）
        for i in range(3):
            session.push_cmd(MainPhaseCmd(phase="thinking"))
            session.push_cmd(ReasoningCmd(text=f"历史思考 {i}"))
            session.push_cmd(PhaseDoneCmd(phase="reasoning"))
            session.push_cmd(ContentCmd(text=f"历史回答 {i}"))
            session.push_cmd(PhaseDoneCmd(phase="content"))
            time.sleep(0.05)
        time.sleep(0.3)

        # hook：在 suspend 清理前注入短内容命令（模拟 API 线程在挂起期间输出）
        orig_drain = session._drain_queue_safe
        pushed = {"done": False}

        def drain_with_push(*args, **kwargs):
            if not pushed["done"]:
                pushed["done"] = True
                _push_short_content(session)
            return orig_drain(*args, **kwargs)

        session._drain_queue_safe = drain_with_push

        # suspend → resume（suspend 清理期间注入短内容）
        session.suspend()
        session.resume()
        time.sleep(0.8)

        # 模型状态应包含短思/短答（命令被保留并应用）
        tail = [l.plain for l in model.committed_lines]
        assert "短思" in tail, f"短思应显示在 committed_lines，实际尾部: {tail[-8:]}"
        assert "短答" in tail, f"短答应显示在 committed_lines，实际尾部: {tail[-8:]}"
        # 输出流应包含短思/短答（渲染到终端）
        out = stream.getvalue()
        assert "短思" in out, "输出流应包含短思"
        assert "短答" in out, "输出流应包含短答"
    finally:
        session.stop()


def test_suspend_preserves_tool_content():
    """suspend 期间入队的工具卡命令（TOOL_OPEN/TOOL_OUTPUT/TOOL_CLOSE）被保留。"""
    session, model, _ = _make_session()
    session.start()
    time.sleep(0.15)
    try:
        orig_drain = session._drain_queue_safe
        pushed = {"done": False}

        def drain_with_push(*args, **kwargs):
            if not pushed["done"]:
                pushed["done"] = True
                from src.tui._const import (
                    ToolOpenCmd, ToolOutputCmd, ToolCloseCmd,
                    ToolCountIncCmd, ToolCountDecCmd,
                )
                session.push_cmd(ToolOpenCmd(tool_name="Bash", tool_id="t1", detail="ls"))
                session.push_cmd(ToolCountIncCmd())
                session.push_cmd(ToolOutputCmd(text="file.txt", tool_id="t1"))
                session.push_cmd(ToolCloseCmd(tool_id="t1", success=True))
                session.push_cmd(ToolCountDecCmd())
            return orig_drain(*args, **kwargs)

        session._drain_queue_safe = drain_with_push
        session.suspend()
        session.resume()
        time.sleep(0.8)

        # 工具卡命令被保留并应用
        assert len(model.tool_boxes) == 0  # 工具已关闭
        assert model.status.tool_total >= 1, "工具计数应保留"
    finally:
        session.stop()


def test_stop_still_drops_all():
    """stop()（进程退出）仍丢弃全部命令（keep_content 不影响退出路径）。

    stop 前不 sleep——确保命令仍留在队列中未被渲染线程处理；stop 内部
    ``_drain_queue_safe()``（keep_content=False）+ 渲染线程 finally 的保留由
    stop 的排空覆盖，最终队列清空且命令未应用。
    """
    session, model, stream = _make_session()
    session.start()
    time.sleep(0.15)
    try:
        _push_short_content(session)
    finally:
        session.stop()
    # stop 后队列应清空（stop 内部 _drain_queue_safe 默认 keep_content=False）
    assert session._cmd_queue.empty()
    # 未应用命令（模型状态不含短思）——但渲染线程可能已在 stop 前处理了一部分
    # （10Hz 拍竞态），此处只断言队列清空 + 无异常（应用与否由渲染线程时序决定）
    assert session._cmd_queue.qsize() == 0
