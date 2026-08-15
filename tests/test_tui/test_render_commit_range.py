"""渲染提交范围测试（BUG-77）。

修复背景：``close_content`` / ``close_reasoning`` 修复前仅
``commit_block(自身块索引)``——content 流式期间打开并关闭的工具卡（位于
content 块之后，``close_tool_box`` 的 ``commit_block(len-1)`` 被未关闭的
content 挡住）在 content 关闭时被遗留为「未提交」：永远走 ToolCard live
渲染（每帧重建，无冻结缓存消费）、后续 open 块增量提交被 BUG-4 连续窗口
守卫阻断。修复后 close 提交到块列表末尾（``commit_block(len-1)``），遇
未关闭块自然停止。

测试锁定：
  1. content 流式 → tool 关闭 → content 关闭：tool 块被提交
     （``committed_line_count == len(lines)``），committed_lines 含工具卡
     标题行与状态图标（✔）；
  2. reasoning 关闭时其后有上一轮遗留已关闭 tool：一并提交；
  3. 未关闭块仍不被越权提交（close 提交遇未关闭块停止）。
"""

from __future__ import annotations

from src.tui.app.model import AppModel
from src.tui.app.apply import apply_cmd
from src.tui._const import (
    ContentCmd,
    PhaseDoneCmd,
    MainPhaseCmd,
    ReasoningCmd,
    ToolOpenCmd,
    ToolOutputCmd,
    ToolCloseCmd,
)


def _committed_texts(model: AppModel) -> list:
    """committed_lines 纯文本行列表。"""
    return [
        "".join(r.text for r in ln.runs) if hasattr(ln, "runs") else str(ln)
        for ln in model.committed_lines
    ]


def test_tool_after_content_committed_on_content_close():
    """content 流式期间工具卡先关闭 → content 关闭时工具卡一并提交（BUG-77）。"""
    m = AppModel()
    m.width = 50
    apply_cmd(m, ContentCmd(text="回答第一段。\n\n"))
    apply_cmd(m, ToolOpenCmd(tool_name="bash", tool_id="t1", detail="ls -la"))
    apply_cmd(m, ToolOutputCmd(tool_id="t1", text="file1\nfile2"))
    apply_cmd(m, ToolCloseCmd(tool_id="t1", success=True))
    # 工具卡被未关闭的 content 挡住，暂未提交
    assert m.blocks[1].kind == "tool"
    assert m.blocks[1].committed_line_count == 0

    apply_cmd(m, ContentCmd(text="回答第二段。\n\n"))
    apply_cmd(m, PhaseDoneCmd(phase="content"))
    # content 关闭后：tool 块应一并提交（cc == len）
    tool = m.blocks[1]
    assert tool.closed
    assert tool.committed_line_count == len(tool.lines), (
        f"工具块未提交: cc={tool.committed_line_count} lines={len(tool.lines)}"
    )
    assert m.committed_count == len(m.blocks), (
        f"全部块应提交: committed_count={m.committed_count} blocks={len(m.blocks)}"
    )
    texts = _committed_texts(m)
    assert any("Bash" in t and "ls -la" in t for t in texts), (
        f"committed_lines 应含工具卡标题行: {texts}"
    )
    assert any(t.strip().startswith("\u2714") for t in texts), (
        f"工具卡标题行应为完成态 ✔: {texts}"
    )
    assert any("file1" in t for t in texts), f"工具输出应在 committed_lines: {texts}"


def test_reasoning_close_commits_stale_tool_after_it():
    """reasoning 关闭时一并提交其后的已关闭遗留工具卡（BUG-77 同族）。"""
    m = AppModel()
    m.width = 50
    # 轮1：回答 + 工具（工具在 content 之后关闭 → content 关闭后一并提交）
    apply_cmd(m, MainPhaseCmd(phase="answering"))
    apply_cmd(m, ContentCmd(text="轮1回答。\n\n"))
    apply_cmd(m, ToolOpenCmd(tool_name="read_file", tool_id="t1", detail="a.py"))
    apply_cmd(m, ToolOutputCmd(tool_id="t1", text="print(1)\n"))
    apply_cmd(m, ToolCloseCmd(tool_id="t1", success=True))
    apply_cmd(m, PhaseDoneCmd(phase="content"))
    assert m.blocks[1].committed_line_count == len(m.blocks[1].lines)
    # 轮2：思考（reasoning）在 tool 之后创建
    apply_cmd(m, MainPhaseCmd(phase="thinking"))
    apply_cmd(m, ReasoningCmd(text="思考中。"))
    apply_cmd(m, PhaseDoneCmd(phase="reasoning"))
    assert m.committed_count == len(m.blocks), (
        f"reasoning 关闭后全部块应提交: committed_count={m.committed_count}"
    )
    texts = _committed_texts(m)
    assert any("Read" in t and "a.py" in t for t in texts), (
        f"工具卡标题行应存在于 committed_lines: {texts}"
    )


def test_tool_output_trailing_newline_no_blank_line():
    """工具输出以 \\n 结尾不产生尾部空行（BUG-78）。

    修复前 ``text.split("\\n")`` 产生尾部空 segment → 追加为仅前缀空行 →
    渲染为「│ 」空引导行（卡片底部多余一行）。修复后剔除末尾空 segment，
    中间空行保留。
    """
    m = AppModel()
    m.width = 50
    # 用非 bash 工具（避免 tail trim 干扰——bash 输出只保留最后 3 行）
    apply_cmd(m, ToolOpenCmd(tool_name="custom_tool", tool_id="t1", detail="gen"))
    # 输出以 \n 结尾（bash 回显常见）：不应产生尾部空行
    apply_cmd(m, ToolOutputCmd(tool_id="t1", text="file1\nfile2\n"))
    block = m.tool_boxes["t1"]
    # lines = [标题行, file1, file2]（无尾空行）
    assert len(block.lines) == 3, f"不应有尾部空行: {[l.plain for l in block.lines]}"
    assert block.lines[-1].plain == "  file2", (
        f"最后一行应为 file2: {block.lines[-1].plain!r}"
    )
    # 中间空行保留（"a\n\nb" 结构分隔；空行 = 仅前缀行）
    apply_cmd(m, ToolOutputCmd(tool_id="t1", text="a\n\nb\n"))
    block = m.tool_boxes["t1"]
    plains = [l.plain for l in block.lines]
    assert plains == ["  · custom_tool · gen", "  file1", "  file2", "  a", "  ", "  b"], (
        f"中间空行应保留、尾部空行剔除: {plains}"
    )
    apply_cmd(m, ToolCloseCmd(tool_id="t1", success=True))
    assert m.blocks[0].committed_line_count == len(m.blocks[0].lines)


def test_close_commit_stops_at_open_block():
    """close 提交遇未关闭块自然停止（不越权提交其后块）。"""
    m = AppModel()
    m.width = 50
    # 回答流式开始（未关闭）
    apply_cmd(m, ContentCmd(text="第一段。\n\n"))
    # 工具打开（未关闭）→ 工具卡在 content 之后
    apply_cmd(m, ToolOpenCmd(tool_name="bash", tool_id="t1", detail="pwd"))
    apply_cmd(m, ToolOutputCmd(tool_id="t1", text="/home\n"))
    # 工具关闭（content 仍未关闭 → 工具被挡住）
    apply_cmd(m, ToolCloseCmd(tool_id="t1", success=True))
    assert m.blocks[0].kind == "content" and not m.blocks[0].closed
    assert m.blocks[1].kind == "tool" and m.blocks[1].closed
    # content 未关闭：committed_count 停在 0（两个块都不提交）
    assert m.committed_count == 0, (
        f"content 未关闭不应提交任何块: committed_count={m.committed_count}"
    )
    # content 关闭 → 两个块一并提交
    apply_cmd(m, ContentCmd(text="第二段。\n\n"))
    apply_cmd(m, PhaseDoneCmd(phase="content"))
    assert m.committed_count == len(m.blocks), (
        f"content 关闭后全部块应提交: committed_count={m.committed_count}"
    )
    assert m.blocks[1].committed_line_count == len(m.blocks[1].lines)
