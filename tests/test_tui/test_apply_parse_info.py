"""解析进度行（parse_line）行为测试（2026-08-16 用户需求）。

需求背景：
1. 工具参数接收进度行（``~ Edit 2608t 8.44s``）在参数接收完成后**删除**，
   不提交到会话文档（修复前 ``append_committed("parse_info", ...)`` 残留为
   会话历史行，工具卡/回答之间永久显示进度信息）。
2. 进度行（接收参数）显示前确保思考内容（reasoning 块）先渲染——ParseInfoCmd
   处理时兜底固化开放推理通道已渲染行（``flush_reasoning_live``），避免
   ReasoningCmd 与 ParseInfoCmd 同批入队时进度行先于思考内容上屏。

测试锁定：
  1. ParseInfoCmd 更新实时进度行（``~ 工具名 tokens耗时`` 格式）；
  2. _CLEAR_PARSE_LINE 后 parse_line 清空且文档无 parse_info 块（需求1）；
  3. 无进度行时 _CLEAR_PARSE_LINE 为空操作（不产生块）；
  4. flush_reasoning_live 将开放推理通道已渲染行固化到块（需求2保障）；
  5. 进度行更新前思考内容先固化（思考内容行先于 parse_line 存在）；
  6. 完整流程后文档只含思考块，无 parse_info 残留。
"""

from __future__ import annotations

from src.tui.app.model import AppModel
from src.tui.app.apply import apply_cmd
from src.tui._const import (
    ParseInfoCmd,
    MainPhaseCmd,
    ReasoningCmd,
    PhaseDoneCmd,
    _CLEAR_PARSE_LINE,
)


def _plain(line) -> str:
    """AnsiLine → 纯文本。"""
    return "".join(r.text for r in line.runs) if hasattr(line, "runs") else str(line)


def _committed_texts(model: AppModel) -> list:
    """committed_lines 纯文本行列表。"""
    return [
        "".join(r.text for r in ln.runs) if hasattr(ln, "runs") else str(ln)
        for ln in model.committed_lines
    ]


def test_parse_info_updates_parse_line():
    """ParseInfoCmd 更新实时进度行（~ 工具名 tokens 耗时 格式）。"""
    m = AppModel()
    m.width = 80
    apply_cmd(m, ParseInfoCmd(tool_names="Edit", tokens=2608, elapsed=8.44))
    assert m.parse_line is not None
    text = _plain(m.parse_line)
    assert "~" in text, f"进度行应含 ~ 前缀: {text!r}"
    assert "Edit" in text, f"进度行应含工具名: {text!r}"
    assert "2608t" in text, f"进度行应含 token 计数: {text!r}"
    assert "8.44s" in text, f"进度行应含耗时: {text!r}"
    # 进度行是实时的（live），未进入文档
    assert not [b for b in m.blocks if b.kind == "parse_info"]


def test_parse_info_clear_deletes_line_not_committed():
    """接收参数完成（_CLEAR_PARSE_LINE）后：进度行删除且不进入文档（需求1）。"""
    m = AppModel()
    m.width = 80
    apply_cmd(m, ParseInfoCmd(tool_names="Edit", tokens=2608, elapsed=8.44))
    assert m.parse_line is not None
    # 接收参数完成 → 进度行删除
    apply_cmd(m, ParseInfoCmd(tool_names="", tokens=_CLEAR_PARSE_LINE, elapsed=0.0))
    assert m.parse_line is None
    # 文档中无 parse_info 块（修复前 append_committed 会残留为历史行）
    assert not [b for b in m.blocks if b.kind == "parse_info"]
    assert m.committed_count == 0
    assert m.committed_lines == []


def test_parse_info_clear_without_line_is_noop():
    """无进度行时 _CLEAR_PARSE_LINE 为空操作（不产生块）。"""
    m = AppModel()
    m.width = 80
    apply_cmd(m, ParseInfoCmd(tool_names="", tokens=_CLEAR_PARSE_LINE, elapsed=0.0))
    assert m.parse_line is None
    assert m.blocks == []
    assert m.committed_lines == []


def test_flush_reasoning_live_freezes_open_reasoning():
    """flush_reasoning_live 将开放推理通道已渲染行固化到块（需求2保障）。"""
    m = AppModel()
    m.width = 80
    apply_cmd(m, MainPhaseCmd(phase="thinking"))
    apply_cmd(m, ReasoningCmd(text="让我先分析。\n\n"))
    assert m.reasoning_block_index == 0
    block = m.blocks[0]
    assert block.kind == "reasoning"
    assert not block.closed
    plains = [l.plain for l in block.lines]
    assert any("让我先分析" in p for p in plains), f"思考内容应固化到块: {plains}"
    # 幂等：再次 flush 不重复/不报错（渲染器已空）
    m.flush_reasoning_live()
    assert m.blocks[0].kind == "reasoning"
    assert len(m.blocks[0].lines) == len(block.lines)


def test_flush_reasoning_live_with_pending_renderer():
    """渲染器有残留行时 flush_reasoning_live 兜底固化（进度行前思考内容上屏）。

    模拟 ReasoningCmd 尚未处理（渲染器已有内容但块未固化）时进度行更新——
    _do_parse_info 调用 flush_reasoning_live 后思考内容进入块。
    """
    m = AppModel()
    m.width = 80
    m.ensure_reasoning()
    assert m.reasoning_block_index == 0
    assert m.blocks[0].lines == []
    # 渲染器直接写入内容（未固化）
    m.reasoning_renderer.write("思考内容兜底固化。\n\n")
    m.flush_reasoning_live()
    plains = [l.plain for l in m.blocks[0].lines]
    assert any("思考内容兜底固化" in p for p in plains), f"思考内容应固化: {plains}"


def test_parse_info_updates_before_reasoning_flush():
    """进度行更新前思考内容已固化（思考内容先于进度行上屏，需求2核心场景）。"""
    m = AppModel()
    m.width = 80
    apply_cmd(m, MainPhaseCmd(phase="thinking"))
    apply_cmd(m, ReasoningCmd(text="分析文件结构。\n\n"))
    # 收到进度行更新（工具参数接收中）
    apply_cmd(m, ParseInfoCmd(tool_names="Edit", tokens=2608, elapsed=8.44))
    # 思考内容已在块中（位于进度行之前/上方）
    assert m.blocks[0].kind == "reasoning"
    assert any("分析文件结构" in l.plain for l in m.blocks[0].lines)
    # 进度行 live 显示（不进入文档）
    assert m.parse_line is not None
    assert not [b for b in m.blocks if b.kind == "parse_info"]
    # 关闭推理通道 + 接收参数完成 → 进度行删除
    apply_cmd(m, PhaseDoneCmd(phase="reasoning"))
    apply_cmd(m, ParseInfoCmd(tool_names="", tokens=_CLEAR_PARSE_LINE, elapsed=0.0))
    assert m.parse_line is None
    # 文档只含思考块，无 parse_info 残留
    assert [b.kind for b in m.blocks] == ["reasoning"]
    texts = _committed_texts(m)
    assert any("分析文件结构" in t for t in texts), f"思考内容应在文档: {texts}"
    assert not any("~" in t and "8.44s" in t for t in texts), (
        f"文档不应残留进度行: {texts}"
    )
