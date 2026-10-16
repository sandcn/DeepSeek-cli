"""test_tui_subagent_parse_order — subagent 界面打开时接收参数进度行显示在 subagent 界面上方。

需求（2026-08-20）：有 subagent 界面（SubAgentCard）打开时，工具参数接收
进度行（``_ParseLine`` / ``model.parse_line``，如 ``~ write_file 123t 8.44s``）
显示在 subagent 界面**上面**（修复前渲染在 subagent 卡片下方）。

实现：``_ParseLine`` 组件自 ``app.py`` 迁入 ``chat_view.py``——subagent 卡片
打开（``model.subagent_lines`` 非空）时由 ChatView 在 SubAgentCard **之前**
渲染（进度行在 subagent 界面上方）；无 subagent 时由 App 渲染在 ChatView
之后（原位置，两处互斥不重复）。
"""

from __future__ import annotations

from src.renderer.ansi.helpers import AnsiLine
from src.tui.core.style import Style
from src.tui.ink import TEXT
from src.tui.ink.element import h
from src.tui.ink.output import Line
from src.tui.ink.reconciler import Reconciler
from src.tui.ink import components as _components

_PARSE_TEXT = "  ~ write_file 123t 8.44s"


def _make_model(parse_text=_PARSE_TEXT, subagent=False):
    from src.tui.app.model import AppModel
    model = AppModel()
    model.width = 80
    if parse_text:
        model.parse_line = AnsiLine.of(parse_text, Style(fg=242))
    if subagent:
        model.subagent_lines = [
            Line.of("  \u256d subagent \u256e", Style(fg=45)),
            Line.of("  agent-1 \u00b7 正在分析", Style(fg=242)),
            Line.of("  \u2570\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2572", Style(fg=45)),
        ]
    return model


def _message_area(model, width=80):
    """直接调用 App(props) 取消息区元素列表（App 无 hook，可安全直调）。"""
    from src.tui.app.app import App
    root_el = App({"model": model, "width": width})
    return root_el.children[0].children


def _render_texts(model, width=80):
    """完整渲染 App 组件树，返回帧各行纯文本。"""
    from src.tui.app.app import App
    rec = Reconciler(schedule_callback=None)
    root = rec.create_root()
    rec.render(root, h(App, {"model": model, "width": width}), width, 40)
    frame = _components.render_frame(root, width)
    return ["".join(r.text for r in ln.runs) for ln in frame.lines]


class TestAppMessageAreaLayout:
    """App 消息区：subagent 打开/关闭时进度行渲染位置（元素树级）。"""

    def test_no_subagent_parse_line_after_chatview(self):
        """无 subagent：进度行由 App 渲染在 ChatView 之后（原位置）。"""
        from src.tui.app.chat_view import ChatView, _ParseLine
        model = _make_model(subagent=False)
        area = _message_area(model)
        assert len(area) == 3, "无 subagent 时消息区 = TopHeader + ChatView + _ParseLine"
        assert area[1].type is ChatView, "ChatView 渲染在进度行之前"
        assert area[2].type is _ParseLine, "进度行（_ParseLine 组件）在 ChatView 之后"

    def test_with_subagent_app_defers_parse_line(self):
        """有 subagent：App 不再渲染进度行（由 ChatView 承担），仅 TopHeader + ChatView。"""
        from src.tui.app.chat_view import ChatView
        model = _make_model(subagent=True)
        area = _message_area(model)
        assert len(area) == 2, "有 subagent 时进度行由 ChatView 渲染，App 不重复"
        assert area[-1].type is ChatView


class TestRenderOrder:
    """真实渲染帧：进度行与 subagent 卡片的上下顺序。"""

    def test_parse_line_above_subagent_card(self):
        """★ 核心需求：有 subagent 界面且接收参数时，进度行显示在 subagent 界面上面。"""
        model = _make_model(subagent=True)
        texts = _render_texts(model)
        parse_idx = next(i for i, t in enumerate(texts) if "write_file" in t)
        sub_idx = next(i for i, t in enumerate(texts) if "agent-1" in t)
        assert parse_idx < sub_idx, (
            f"接收参数进度行（index={parse_idx}）应显示在 subagent 界面（index={sub_idx}）上方"
        )

    def test_no_subagent_parse_line_still_rendered(self):
        """无 subagent：进度行仍渲染（由 App 承担，位置不变）。"""
        model = _make_model(subagent=False)
        texts = _render_texts(model)
        assert any("write_file" in t for t in texts), "无 subagent 时进度行仍应渲染"

    def test_subagent_without_parse_line_no_parse_row(self):
        """subagent 打开但无进度行：帧中不出现进度行，subagent 卡片正常显示。"""
        model = _make_model(parse_text=None, subagent=True)
        texts = _render_texts(model)
        assert not any("write_file" in t for t in texts), "无 parse_line 时不显示进度行"
        assert any("agent-1" in t for t in texts), "subagent 卡片应正常渲染"


class TestParseLineImportCompat:
    """_ParseLine 导入路径兼容（既有测试依赖 ``src.tui.app.app._ParseLine``）。"""

    def test_importable_from_app_app(self):
        from src.tui.app.app import _ParseLine
        assert callable(_ParseLine)

    def test_importable_from_chat_view(self):
        from src.tui.app.chat_view import _ParseLine
        assert callable(_ParseLine)
