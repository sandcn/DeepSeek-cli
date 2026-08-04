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

from src.tui._const import (
    MainPhaseCmd, ContentCmd, ReasoningCmd, PhaseDoneCmd,
    DisplayMsgsCmd, ClearMsgsCmd,
)
from src.tui.app.model import AppModel, ReasoningState, _single_line_detail
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
        # 冻结缓存已建立（全块行：顶边框 + 主体行 + 底边框；标题行被顶边框
        # 替代、状态行跳过移入底边框 → 与块行数相等）
        assert box._cached_ink_lines is not None
        assert len(box._cached_ink_lines) == len(box.lines)
        # 未提交尾（状态行移入底边框后）经缓存复用 runs 引用
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


class TestFrozenTailStartOffset:
    """_block_styled_lines 冻结缓存 start 偏移（_LIVE_TAIL_LINES 截断协同）。

    BUG-69：关闭块冻结缓存（``_cached_ink_lines`` = 未提交部分）被 ChatView
    以 ``live_start``（可能被 _LIVE_TAIL_LINES 截断到 > committed_line_count）
    调用时，旧实现 ``cache[0:]`` 忽略 start 参数 → 截断失效（整段未提交尾
    全部渲染）+ 行 key 错位（调和器复用错 fiber → 换行缓存 miss）。
    """

    def _make_large_frozen_block(self):
        """构造：content 块被未关闭块夹住（committed_line_count=0）+ 100 行冻结尾。"""
        from src.renderer.ansi.helpers import AnsiLine
        m = AppModel()
        m.append_block("tool").closed = False  # 夹住 content 的未关闭块
        block = m.append_block("content")
        for i in range(100):
            block.lines.append(AnsiLine.of(f"line {i}"))
        block.closed = True
        block.committed_line_count = 0
        block._cached_ink_lines = m._block_to_ink_lines(block, 0)
        assert len(block._cached_ink_lines) == 100
        return m, block

    def test_frozen_tail_honors_truncated_start(self):
        """冻结缓存按 start 偏移切片：start 被截断时只渲染尾段（_LIVE_TAIL_LINES 协同）。"""
        from src.tui.app.chat_view import _block_styled_lines
        _, block = self._make_large_frozen_block()
        live_start = 36  # 模拟 _LIVE_TAIL_LINES 截断（100 - 64）
        rows = _block_styled_lines(block, live_start, 80)
        assert len(rows) == 64, (
            f"冻结缓存应尊重 start 截断（64 行），实际 {len(rows)} 行"
        )
        assert "".join(r.text for r in rows[0]).strip() == "line 36"

    def test_frozen_tail_same_start_unchanged(self):
        """start == committed_line_count（正常路径）行为不变：返回全部冻结尾。"""
        from src.tui.app.chat_view import _block_styled_lines
        _, block = self._make_large_frozen_block()
        rows = _block_styled_lines(block, block.committed_line_count, 80)
        assert len(rows) == 100
        # 引用级复用：同一 runs 列表对象（免每帧 Style merge）
        rows2 = _block_styled_lines(block, block.committed_line_count, 80)
        assert rows2[0] is rows[0]

    def test_frozen_tail_partial_incremental_offset(self):
        """已增量提交（committed_line_count>0）且 start 截断：偏移按差值切片。"""
        from src.renderer.ansi.helpers import AnsiLine
        from src.tui.app.chat_view import _block_styled_lines
        m = AppModel()
        block = m.append_block("content")
        for i in range(100):
            block.lines.append(AnsiLine.of(f"line {i}"))
        block.closed = True
        block.committed_line_count = 50
        block._cached_ink_lines = m._block_to_ink_lines(block, 50)
        assert len(block._cached_ink_lines) == 50
        # start=50（=committed_line_count）→ 全部
        assert len(_block_styled_lines(block, 50, 80)) == 50
        # start=86（截断到 100-64=36 后的 live_start=86？不——live_start 截断
        # 只发生在 len - committed_line_count > 64 时，此处 100-50=50<=64
        # 不截断。直接验证偏移语义：start=86 > 50 → 切片 offset=36 → 14 行。
        rows = _block_styled_lines(block, 86, 80)
        assert len(rows) == 14
        assert "".join(r.text for r in rows[0]).strip() == "line 86"


class TestResetDisplay:
    """Claude TUI parity 步骤 2.2 — reset_display 清屏语义。"""

    def test_reset_display_clears_blocks_keeps_status_input(self):
        """清屏后 blocks 为空、status/input 保留。"""
        m = AppModel()
        m.append_committed("user", [])
        m.open_tool_box("t1", "bash", "ls")
        m.status.model_name = "deepseek-chat"
        m.input_text = "hello"
        m.input_cursor = 5
        m.completion = "x"
        m.subagent_lines = [object()]
        m.reset_display()
        assert m.blocks == []
        assert m.committed_lines == []
        assert m.committed_count == 0
        assert m.tool_boxes == {}
        assert m.active_tool is None
        assert m.subagent_lines == []
        assert m.parse_line is None
        # 保留项
        assert m.status.model_name == "deepseek-chat"
        assert m.input_text == "hello"
        assert m.input_cursor == 5
        assert m.completion == "x"

    def test_reset_display_clears_active_tool(self):
        """reset_display 清空进行中工具状态。"""
        m = AppModel()
        m.open_tool_box("t1", "bash", "ls")
        assert m.active_tool is not None
        m.reset_display()
        assert m.active_tool is None

    def test_open_close_maintains_active_tool(self):
        """open/close 正确维护 active_tool。"""
        m = AppModel()
        m.open_tool_box("t1", "bash", "ls")
        assert m.active_tool is not None
        assert m.active_tool["status"] == "running"
        assert m.active_tool["name"] == "Bash"  # get_tool_display_name("bash") → 完整名
        m.close_tool_box("t1", True)
        assert m.active_tool is None


class TestClearMsgsRenderIntegration:
    """CLEAR_MSGS + DISPLAY_MSGS 批次渲染集成 — 编辑后旧消息从屏幕上消失。

    /editmsg 用户需求：按下回车确认选择后，删除消息区原来显示的信息，
    把剩下信息重新渲染一次。本测试从「模型 + 组件树渲染」层面验证：
    clear+display 同批应用后，渲染帧只含剩余消息，被编辑掉的内容不再出现。
    """

    @staticmethod
    def _render_plains(model, width=80) -> list[str]:
        from src.tui.app.app import build_app_element
        from src.tui.ink.reconciler import Reconciler
        from src.tui.ink.components import render_frame
        r = Reconciler()
        root = r.create_root()
        r.render(root, build_app_element(model, width), width, 24)
        frame = render_frame(root, width)
        return [line.plain for line in frame.lines]

    def test_batch_clear_display_removes_old_and_renders_remaining(self):
        """clear+display 同批应用 → 帧只含剩余消息，旧内容消失。"""
        m = AppModel()
        # 编辑前：消息区显示完整会话（含将被编辑的用户消息 + 回复）
        apply_cmd(m, DisplayMsgsCmd(messages=[
            {"role": "user", "content": "第一条"},
            {"role": "assistant", "content": "回复1"},
            {"role": "user", "content": "被编辑的消息"},
            {"role": "assistant", "content": "回复2"},
        ], speed=0))
        before = self._render_plains(m)
        assert any("被编辑的消息" in p for p in before)
        assert any("回复2" in p for p in before)

        # 编辑生效后：clear → 重渲染剩余消息（同批按序）
        apply_cmd(m, ClearMsgsCmd())
        apply_cmd(m, DisplayMsgsCmd(messages=[
            {"role": "user", "content": "第一条"},
            {"role": "assistant", "content": "回复1"},
        ], speed=0))

        after = self._render_plains(m)
        # 剩余消息仍在
        assert any("第一条" in p for p in after)
        assert any("回复1" in p for p in after)
        # 被编辑掉的内容不再出现（消息区信息已删除）
        assert not any("被编辑的消息" in p for p in after), (
            f"被编辑消息不应残留: {after}"
        )
        assert not any("回复2" in p for p in after), (
            f"被编辑消息后的回复不应残留: {after}"
        )
        # 不追加残留副本：剩余消息恰好一次
        assert sum(1 for p in after if "第一条" in p) == 1


class TestChatViewCompositeKey:
    """方向5 — chat_view 开放块行复合 key（流式追加行 key 稳定，fiber 复用）。"""

    @staticmethod
    def _chat_fibers(root) -> dict:
        """收集根树中 key 以 ``chat-`` 开头的 TEXT fiber。"""
        found: dict = {}

        def walk(f):
            f2 = f
            while f2 is not None:
                props = getattr(f2, "props", None)
                key = props.get("key") if isinstance(props, dict) else None
                if isinstance(key, str) and key.startswith("chat-"):
                    found[key] = f2
                walk(f2.child)
                f2 = f2.sibling

        walk(root)
        return found

    def test_open_block_row_key_stable_on_stream_append_regression(self):
        """流式追加新行时已渲染开放块行的 key 不变（调和复用 fiber 断言）。"""
        from src.tui.app.app import build_app_element
        from src.tui.ink.reconciler import Reconciler

        m = AppModel()
        # web_search（非 head/tail 修剪工具）→ 流式追加行数稳定增长
        m.open_tool_box("t1", "web_search")
        m.append_tool_output("t1", "out1\n")
        m.append_tool_output("t1", "out2\n")

        r = Reconciler()
        root = r.create_root()
        r.render(root, build_app_element(m, 80), 80, 24)
        fibers1 = self._chat_fibers(root)
        # 卡片 live 路径：角色头 key（chat-{idx}-h）+ 标题 + out1/out2 已渲染
        assert len(fibers1) >= 3

        # 流式追加新行（开放工具块追加输出）
        m.append_tool_output("t1", "out3\n")
        r.render(root, build_app_element(m, 80), 80, 24)
        fibers2 = self._chat_fibers(root)

        # 已渲染行 key 保留且 fiber 身份复用（修复前位置索引 chat-{line_idx}
        # 使行号前移 → 已渲染行 key 变化 → fiber 重建）
        for key, fiber1 in fibers1.items():
            assert key in fibers2, f"key {key} 应保留"
            assert fibers2[key] is fiber1, (
                f"key {key} 的 fiber 应复用（身份不变）"
            )
        # 新增行产生新 key（key 数量增加）
        assert len(fibers2) > len(fibers1)


class TestToolCardMultilineDetail:
    """bash 多行命令 detail 强制单行（防 \n 拆破工具卡边框显示错乱）。"""

    @staticmethod
    def _render_plains(model, width=40):
        from src.tui.app.app import build_app_element
        from src.tui.ink.reconciler import Reconciler
        from src.tui.ink.components import render_frame
        r = Reconciler()
        root = r.create_root()
        r.render(root, build_app_element(model, width), width, 24)
        frame = render_frame(root, width)
        return [line.plain for line in frame.lines]

    def test_multiline_command_detail_escaped_single_line(self):
        """bash 命令含 \n：detail 存单行字面量，渲染帧无换行符破坏边框。"""
        m = AppModel()
        m.open_tool_box("t1", "bash", "cmd1\ncmd2 && echo hi")
        assert m.active_tool["detail"] == "cmd1\\ncmd2 && echo hi"
        assert m.tool_boxes["t1"].extra["tool_detail"] == "cmd1\\ncmd2 && echo hi"
        # 标题行（block.lines[0]）同样单行（open_tool_box 同源转义）
        assert "\n" not in m.tool_boxes["t1"].lines[0].plain
        plains = self._render_plains(m)
        assert any("\u250c" in p for p in plains)  # 顶边框存在
        for p in plains:
            assert "\n" not in p, f"渲染行含换行符破坏边框: {p!r}"
        # 转义后命令仍可见（字面量 \n）
        assert any("cmd1\\ncmd2" in p for p in plains)

    def test_render_frame_top_border_well_formed_regression(self):
        """回归：修复前 \n 把顶边框拆成两行，┐ 落到下一行错乱。"""
        m = AppModel()
        m.open_tool_box("t1", "bash", "ls\npwd")
        m.append_tool_output("t1", "out\n")
        # 边框 builder 产物：顶边框行单行完整（┌ 与 ┐ 同行），无物理换行
        from src.tui.app.model import _tool_card_styled_lines
        head_lines = _tool_card_styled_lines(m.tool_boxes["t1"], 40, 0, None)
        head_text = "".join(r.text for r in head_lines[0])
        head_width = sum(r.width for r in head_lines[0])
        assert "\n" not in head_text
        assert head_text.startswith("\u250c")
        assert head_text.endswith("\u2510")
        assert head_width == 40  # 显示宽度恰为卡片宽度（不超不欠）
        # 渲染帧同样无换行符破坏边框
        plains = self._render_plains(m, 40)
        for p in plains:
            assert "\n" not in p, f"渲染行含换行符破坏边框: {p!r}"
        assert any("ls\\npwd" in p for p in plains)

    def test_single_line_detail_helper(self):
        """_single_line_detail：\n/\r 转义为字面量，空串原样。"""
        assert _single_line_detail("") == ""
        assert _single_line_detail("a\nb") == "a\\nb"
        assert _single_line_detail("a\rb") == "a\\rb"
        assert _single_line_detail("已含\\n字面量") == "已含\\n字面量"
