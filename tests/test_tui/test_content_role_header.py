"""回答块角色头测试（2026-08-16 用户需求）。

需求：回答也要像 ``▍💭 思考`` 这样显示——content（回答）块提交后带
``▍💬 回答`` 角色头（与 reasoning ``▍💭 思考`` 同格式），live 路径呼吸色、
提交路径静态亮青；窄屏截断满足行级 diff 宽度不变量；终端宽度变化重排
（reflow_committed）后头仍存在。

测试锁定：
  1. content 块提交后 committed_lines 首行为 ``▍💬 回答``（提交路径静态）；
  2. reasoning 块提交后 committed_lines 首行为 ``▍💭 思考``（回归锁定）；
  3. live 路径（未关闭 + live=True）content 头呼吸色（45-61 区间）；
  4. 窄屏下 content 头截断至 width（不超宽）；
  5. reflow_committed 重建后 content 头仍存在且恰好一次；
  6. append_committed（历史回放路径）同样带 ``▍💬 回答`` 头。
"""

from __future__ import annotations

from src.tui.app.model import AppModel
from src.tui.app.apply import apply_cmd
from src.tui.app._model_helpers import _role_header_line
from src.tui._const import (
    ContentCmd,
    PhaseDoneCmd,
    MainPhaseCmd,
    ReasoningCmd,
)
from src.renderer.ansi.helpers import AnsiLine


def _committed_texts(model: AppModel) -> list:
    """committed_lines 纯文本行列表。"""
    return [
        "".join(r.text for r in ln.runs) if hasattr(ln, "runs") else str(ln)
        for ln in model.committed_lines
    ]


def test_content_block_committed_has_role_header():
    """content 块提交后 committed_lines 首行为 ▍💬 回答 角色头。"""
    m = AppModel()
    m.width = 80
    apply_cmd(m, ContentCmd(text="回答内容。\n\n"))
    apply_cmd(m, PhaseDoneCmd(phase="content"))
    texts = _committed_texts(m)
    assert texts[0] == "\u258d\U0001f4ac 回答", f"首行应为回答头: {texts[:2]}"
    assert any("回答内容" in t for t in texts), f"正文应在文档: {texts}"


def test_reasoning_block_committed_has_role_header():
    """reasoning 块提交后 committed_lines 首行为 ▍💭 思考（回归锁定）。"""
    m = AppModel()
    m.width = 80
    apply_cmd(m, MainPhaseCmd(phase="thinking"))
    apply_cmd(m, ReasoningCmd(text="思考内容。\n\n"))
    apply_cmd(m, PhaseDoneCmd(phase="reasoning"))
    texts = _committed_texts(m)
    assert texts[0] == "\u258d\U0001f4ad 思考", f"首行应为思考头: {texts[:2]}"
    assert any("思考内容" in t for t in texts), f"思考应在文档: {texts}"


def test_content_header_static_style_after_commit():
    """提交路径 content 头为静态亮青（pal.accent，随活动主题）。"""
    from src.tui.app._theme import get_active_palette
    m = AppModel()
    m.width = 80
    apply_cmd(m, ContentCmd(text="回答内容。\n\n"))
    apply_cmd(m, PhaseDoneCmd(phase="content"))
    header = m.committed_lines[0]
    run = header.runs[0]
    assert run.text == "\u258d\U0001f4ac 回答"
    expected = get_active_palette().accent.fg
    assert run.style.fg == expected, f"提交路径应为调色板 accent 静态: {run.style.fg}"


def test_content_header_live_glow():
    """live 路径（未关闭 + live=True）content 头与思考同动效：spinner 帧 + 呼吸色。"""
    from src.tui.core._fx import SPINNER_FRAMES
    m = AppModel()
    m.width = 80
    apply_cmd(m, ContentCmd(text="流式回答。\n\n"))
    block = m.blocks[0]
    assert block.kind == "content" and not block.closed
    header = _role_header_line(block, m, 80, live=True)
    assert header is not None
    run = header.runs[0]
    # live 路径：▍ + spinner 帧字符 + 回答（💬 图标被 spinner 帧替代，与思考头一致）
    assert run.text.startswith("\u258d"), f"头应含 ▍ 前缀: {run.text!r}"
    assert run.text[1] in SPINNER_FRAMES, f"图标应为 spinner 帧: {run.text!r}"
    assert run.text.endswith(" 回答"), f"头应以 回答 结尾: {run.text!r}"
    assert 45 <= run.style.fg <= 61, f"live 头应为呼吸色: {run.style.fg}"


def test_content_header_truncated_to_width():
    """窄屏下 content 头截断至 width（行级 diff 宽度不变量）。"""
    m = AppModel()
    m.width = 4
    apply_cmd(m, ContentCmd(text="回答内容。\n\n"))
    block = m.blocks[0]
    header = _role_header_line(block, m, 4)
    assert header is not None
    assert header.width <= 4, f"头行不应超宽: {header.width}"


def test_reflow_committed_keeps_content_header():
    """终端宽度变化重排后 content 头仍存在且恰好一次。"""
    m = AppModel()
    m.width = 40
    apply_cmd(m, ContentCmd(text="回答内容。\n\n"))
    apply_cmd(m, PhaseDoneCmd(phase="content"))
    m.reflow_committed(60)
    texts = _committed_texts(m)
    assert texts[0] == "\u258d\U0001f4ac 回答", f"重排后头应保留: {texts[:2]}"
    assert texts.count("\u258d\U0001f4ac 回答") == 1, f"头应恰好一次: {texts}"


def test_append_committed_content_has_header():
    """append_committed（历史回放路径）同样带 ▍💬 回答 头。"""
    m = AppModel()
    m.width = 80
    m.append_committed("content", [AnsiLine.of("历史回答文本")])
    texts = _committed_texts(m)
    assert texts[0] == "\u258d\U0001f4ac 回答", f"首行应为回答头: {texts[:2]}"
    assert any("历史回答文本" in t for t in texts), f"正文应在文档: {texts}"
