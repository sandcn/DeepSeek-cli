"""渲染状态关闭/重开回归测试（步骤 3 渲染状态层兜底）。

2026-08-01 ink 重构：ChatRenderState → AppModel 阶段状态机（app/model.py），
语义原样保留：
- content 关闭后丢弃后续内容（不重建不错位）
- reopen_content() 后惰性重建（多轮会话语义保留）
- MainPhaseCmd("answering"/"thinking") 触发 reopen_content（先于新内容渲染）
- 推理 CLOSED 后丢弃（不重建不错位）
- 渲染器关闭后到达的内容仅显式丢弃，不抛异常、不重建
"""

from __future__ import annotations

from src.tui._const import MainPhaseCmd, ContentCmd, ReasoningCmd, PhaseDoneCmd
from src.tui.app.model import AppModel, ReasoningState
from src.tui.app.apply import apply_cmd


class TestRenderStateCloseReopen:
    """AppModel content/推理通道关闭与重开语义。"""

    def test_content_after_close_discarded(self):
        """close_content 后内容被丢弃（不新建块、不追加行）。"""
        m = AppModel()
        apply_cmd(m, ContentCmd(text="x"))
        apply_cmd(m, PhaseDoneCmd(phase="content"))
        assert m.content_closed is True
        n_blocks = len(m.blocks)
        n_lines = len(m.blocks[0].lines)
        apply_cmd(m, ContentCmd(text="late"))
        assert len(m.blocks) == n_blocks
        assert len(m.blocks[0].lines) == n_lines

    def test_reopen_content_rebuilds(self):
        """close 后 reopen_content 再收到内容惰性重建（多轮会话语义）。"""
        m = AppModel()
        apply_cmd(m, ContentCmd(text="x"))
        apply_cmd(m, PhaseDoneCmd(phase="content"))
        assert m.content_closed is True
        m.reopen_content()
        assert m.content_closed is False
        apply_cmd(m, ContentCmd(text="new"))
        assert len(m.blocks) == 2
        assert m.blocks[-1].kind == "content"

    def test_reopen_content_idempotent(self):
        """reopen_content 未关闭时调用无副作用（幂等）。"""
        m = AppModel()
        m.content_closed = False
        m.reopen_content()
        assert m.content_closed is False

    def test_reasoning_closed_discards(self):
        """推理 CLOSED 后到达的文本被丢弃（不新建块、不追加行）。"""
        m = AppModel()
        apply_cmd(m, ReasoningCmd(text="think"))
        apply_cmd(m, PhaseDoneCmd(phase="reasoning"))
        assert m.reasoning_state == ReasoningState.CLOSED
        n_blocks = len(m.blocks)
        n_lines = len(m.blocks[0].lines)
        apply_cmd(m, ReasoningCmd(text="late"))
        assert len(m.blocks) == n_blocks
        assert len(m.blocks[0].lines) == n_lines

    def test_close_all_flush_open_channels(self):
        """flush_open_channels 幂等（reasoning/content 均关闭）。"""
        m = AppModel()
        apply_cmd(m, ContentCmd(text="x"))
        apply_cmd(m, ReasoningCmd(text="r"))
        m.flush_open_channels()
        m.flush_open_channels()  # 幂等
        assert m.reasoning_state == ReasoningState.CLOSED
        assert m.content_closed is True
        assert m.content_renderer is None
        assert m.reasoning_renderer is None

    def test_flush_open_channels_exception_logged(self, caplog):
        """flush_open_channels 内部异常被记录（非关键降级不抛，后续通道仍关闭）。"""
        import logging
        from unittest.mock import patch

        m = AppModel()
        with patch.object(m, "close_reasoning", side_effect=RuntimeError("boom")):
            with caplog.at_level(logging.DEBUG, logger="src.tui.app.model"):
                m.flush_open_channels()  # 不抛异常
        assert m.content_closed is True  # close_content 仍执行
        assert any(
            rec.name == "src.tui.app.model"
            and "flush_open_channels" in rec.getMessage()
            for rec in caplog.records
        )


class TestRenderStateApplyIntegration:
    """apply_cmd 与渲染状态的集成语义。"""

    def test_main_phase_answering_reopens(self):
        """MainPhaseCmd("answering") 触发 reopen_content。"""
        m = AppModel()
        apply_cmd(m, ContentCmd(text="x"))
        apply_cmd(m, PhaseDoneCmd(phase="content"))
        assert m.content_closed is True
        apply_cmd(m, MainPhaseCmd(phase="answering"))
        assert m.content_closed is False
        assert m.status.main_phase == "answering"

    def test_main_phase_thinking_reopens(self):
        """MainPhaseCmd("thinking") 同时触发 reopen_reasoning 与 reopen_content。"""
        m = AppModel()
        apply_cmd(m, ContentCmd(text="x"))
        apply_cmd(m, PhaseDoneCmd(phase="content"))
        apply_cmd(m, ReasoningCmd(text="r"))
        apply_cmd(m, PhaseDoneCmd(phase="reasoning"))
        apply_cmd(m, MainPhaseCmd(phase="thinking"))
        assert m.content_closed is False
        assert m.reasoning_state == ReasoningState.INACTIVE

    def test_content_closed_drops_without_error(self):
        """content 关闭后到达的内容显式丢弃，不抛异常。"""
        m = AppModel()
        apply_cmd(m, ContentCmd(text="x"))
        apply_cmd(m, PhaseDoneCmd(phase="content"))
        apply_cmd(m, ContentCmd(text="y"))  # 不抛异常

    def test_reasoning_closed_drops_without_error(self):
        """推理关闭后到达的文本显式丢弃，不抛异常。"""
        m = AppModel()
        apply_cmd(m, ReasoningCmd(text="r"))
        apply_cmd(m, PhaseDoneCmd(phase="reasoning"))
        apply_cmd(m, ReasoningCmd(text="late"))  # 不抛异常


class TestClosedToolBoxFreezeCache:
    """步骤6.1 — 关闭块冻结行缓存（开放 content 块后关闭的 tool box 免每帧重渲染）。"""

    def test_closed_tool_box_after_open_content_cached_regression(self):
        """content 开放中关闭 tool box：_cached_ink_lines 非空且未提交尾复用缓存 runs 引用。"""
        from src.tui.app.chat_view import _block_styled_lines
        from src.tui.ink.reconciler import Reconciler
        from src.tui.app.app import build_app_element
        from src.tui.ink import components as _components

        m = AppModel()
        apply_cmd(m, ContentCmd(text="content line\n"))
        box = m.open_tool_box("t1", "read_file")
        m.append_tool_output("t1", "output1\n")
        m.append_tool_output("t1", "output2\n")
        m.close_tool_box("t1", True)

        # content 块仍开放（未关闭 → tool box 不在 committed_lines 中）
        assert m.content_closed is False
        # 冻结缓存已建立（全块行）
        assert box._cached_ink_lines is not None
        assert len(box._cached_ink_lines) == len(box.lines)
        # 未提交尾（状态行）经缓存复用 runs 引用
        tail = _block_styled_lines(box, box.committed_line_count)
        assert len(tail) == len(box.lines) - box.committed_line_count
        assert len(tail) >= 1
        assert any("\u2714" in "".join(r.text for r in runs) for runs in tail)
        # 引用级复用：同一 runs 列表对象（免每帧 Style merge）
        tail2 = _block_styled_lines(box, box.committed_line_count)
        assert tail2[0] is tail[0]

        # 全树渲染不抛异常，工具输出与状态行可见
        r = Reconciler()
        root = r.create_root()
        el = build_app_element(m, 80)
        r.render(root, el, 80, 24)
        frame = _components.render_frame(root, 80)
        plains = [line.plain for line in frame.lines]
        assert any("output2" in p for p in plains)
        assert any("\u2714" in p for p in plains)
        # 二次渲染帧相同（不追加/不重复）
        r.render(root, el, 80, 24)
        frame2 = _components.render_frame(root, 80)
        assert [line.plain for line in frame2.lines] == plains

    def test_close_content_freezes_block_lines_regression(self):
        """close_content 后 content 块冻结缓存建立（内容行缓存）。"""
        m = AppModel()
        apply_cmd(m, ContentCmd(text="frozen content\n"))
        apply_cmd(m, PhaseDoneCmd(phase="content"))
        assert m.content_closed is True
        content_block = m.blocks[0]
        assert content_block._cached_ink_lines is not None
        assert len(content_block._cached_ink_lines) == len(content_block.lines)
        # 缓存行含内容文本
        plains = [line.plain for line in content_block._cached_ink_lines]
        assert any("frozen content" in p for p in plains)
