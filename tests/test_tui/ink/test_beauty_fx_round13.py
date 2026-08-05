"""BEAUTY-25~30 体验动效回归测试（2026-08-05）。

覆盖 2026-08-05 新增动效/美化：
  - BEAUTY-25：空状态欢迎行 ✦ 活跃期呼吸（空闲静态单例，零重建）；
  - BEAUTY-26：工具卡标题图标运行中呼吸（232↔252 脉动）；
  - BEAUTY-27：思考块角色头 live spinner 化（💭 → spinner 帧，关闭回退静态）；
  - BEAUTY-28：状态栏 thinking 阶段标签弱呼吸（…思考，242↔252）；
  - BEAUTY-29：user_select 弹窗标题模式图标（单选 ▶ / 多选 ☑）；
  - BEAUTY-30：解析进度行 spinner 金色呼吸（178↔190）。

测试原则：动效为时间基（time_glow / _fx.spinner_char），断言聚焦**结构
契约**（文本/前缀/单例引用），不锁定具体呼吸色号（时间敏感断言脆弱）。
"""

from __future__ import annotations

import time

from src.tui.app.model import AppModel
from src.tui.app.app import build_app_element
from src.tui.ink import h, StyledRun
from src.tui.ink.reconciler import Reconciler
from src.tui.ink.components import render_frame
from src.renderer.ansi.helpers import AnsiLine, ansi_to_runs


def _render(model, width=80, height=40):
    """渲染整棵 App 树，返回帧行列表。"""
    r = Reconciler()
    root = r.create_root()
    el = build_app_element(model, width)
    r.render(root, el, width, height)
    frame = render_frame(root, width)
    return [ln.plain for ln in frame.lines]


def _al(text: str) -> AnsiLine:
    return AnsiLine(ansi_to_runs(text))


# ═══════════════════════════════════════════════════════════
# BEAUTY-25：空状态欢迎行 ✦ 活跃期呼吸
# ═══════════════════════════════════════════════════════════

class TestBeauty25WelcomeBreath:
    def test_welcome_idle_static_singleton(self):
        """空闲（status_active=False）欢迎行回退模块级静态单例（零重建）。"""
        from src.tui.app import chat_view as cv
        m = AppModel()
        el = cv._welcome_element(m, 80)
        # 空闲 → 返回静态单例（引用级命中）
        assert el.props["styled"] is cv._WELCOME_STYLED, "空闲欢迎行应回退静态单例"
        assert el.props["key"] == "welcome"

    def test_welcome_active_generates_breath(self):
        """活跃期（status_active=True）✦ 图标呼吸——生成新 styled（非静态单例）。"""
        from src.tui.app import chat_view as cv
        from src.tui.core.style import Style
        m = AppModel()
        m.status.model_name = "deepseek-chat"
        m.status.status_active = True
        el = cv._welcome_element(m, 80)
        styled = el.props["styled"]
        assert styled is not cv._WELCOME_STYLED, "活跃期欢迎行应生成呼吸版"
        # 结构契约：✦ + 欢迎文本 + 分隔 + 提示（与静态单例文本一致）
        assert styled[0].text == "\u2726 "
        assert styled[0].style.bold, "✦ 图标应加粗"
        assert "欢迎使用 DeepSeek CLI" in styled[1].text

    def test_welcome_renders_in_empty_chat(self):
        """空聊天区渲染欢迎行（文本契约）。"""
        m = AppModel()
        m.status.model_name = "deepseek-chat"
        plains = _render(m)
        assert any("欢迎使用 DeepSeek CLI" in p for p in plains), (
            f"空状态应显示欢迎行: {plains[:5]!r}"
        )


# ═══════════════════════════════════════════════════════════
# BEAUTY-26：工具卡标题图标运行中呼吸
# ═══════════════════════════════════════════════════════════

class TestBeauty26ToolIconBreath:
    def _tool_block(self, status="running", closed=False):
        from src.tui.app.model import ChatBlock
        blk = ChatBlock(kind="tool")
        blk.extra["tool_name"] = "bash"
        blk.extra["tool_status"] = status
        blk.extra["tool_detail"] = "echo hi"
        blk.lines = [_al("  · bash"), _al("  hello")]
        if closed:
            blk.closed = True
            blk.extra["_status_line_index"] = len(blk.lines)
            blk.lines.append(_al("  \u2714"))
        return blk

    def test_running_icon_uses_breath_style(self):
        """running 工具卡标题图标呼吸色（非静态 252）。"""
        from src.tui.app.toolcard import tool_card_lines
        blk = self._tool_block("running")
        lines = tool_card_lines(blk, 60)
        head = lines[0]
        # 结构：┌─ + 状态图标● + 空格 + 工具图标⚡（呼吸）+ 显示名 + ...
        # 找到工具图标 run（⚡/📄 等，非状态图标/边框/显示名）
        icon_run = None
        for run in head:
            if run.text and run.text[0] in ("\u26a1", "\u2699", "\U0001f4c4", "\U0001f50d", "\U0001f4d6"):
                icon_run = run
                break
        assert icon_run is not None, f"应找到工具图标 run: {head!r}"
        # 运行中图标色应为呼吸色（232~252 区间，非静态 252）
        fg = icon_run.style.fg if icon_run.style else None
        assert fg is not None, "运行中图标应有颜色"
        assert 232 <= fg <= 252, f"运行中图标色应在呼吸区间: {fg}"

    def test_done_icon_static(self):
        """已关闭工具卡标题图标静态 252（frozen 缓存）。"""
        from src.tui.app.toolcard import tool_card_lines
        blk = self._tool_block("done", closed=True)
        lines = tool_card_lines(blk, 60)
        head = lines[0]
        icon_run = None
        for run in head:
            if run.text and run.text[0] in ("\u26a1", "\u2699", "\U0001f4c4", "\U0001f50d", "\U0001f4d6"):
                icon_run = run
                break
        assert icon_run is not None, f"应找到工具图标 run: {head!r}"
        fg = icon_run.style.fg if icon_run.style else None
        assert fg == 252, f"已关闭卡图标应静态 252: {fg}"


# ═══════════════════════════════════════════════════════════
# BEAUTY-27：思考块角色头 live spinner 化
# ═══════════════════════════════════════════════════════════

class TestBeauty27ReasoningSpinner:
    def test_live_reasoning_header_uses_spinner(self):
        """live 渲染路径（live=True）推理块角色头用 spinner 帧替换 💭。"""
        from src.tui.app.model import _role_header_runs, ChatBlock
        from src.tui.app import _fx
        blk = ChatBlock(kind="reasoning", lines=[_al("thinking...")])
        blk.closed = False
        m = AppModel()
        runs = _role_header_runs(blk, m, live=True)
        text = "".join(r.text for r in runs)
        # spinner 帧字符来自唯一真源 SPINNER_FRAMES（非 💭）
        assert "\U0001f4ad" not in text, f"live 思考头不应为静态 💭: {text!r}"
        assert text.startswith("\u258d"), f"思考头应保留 ▍ 前缀: {text!r}"
        sp_chars = _fx.SPINNER_FRAMES
        body = text[1:]
        assert body[0] in sp_chars, f"live 思考头首字符应为 spinner 帧: {body!r}"

    def test_committed_reasoning_header_static(self):
        """提交/冻结路径（live=False 默认）推理块角色头回退静态 💭。

        BEAUTY-27 修复：提交路径冻结缓存须内容确定——防历史思考头固定为
        随机 spinner 帧字符。
        """
        from src.tui.app.model import _role_header_runs, ChatBlock
        blk = ChatBlock(kind="reasoning", lines=[_al("thinking...")])
        blk.closed = False  # 即使块仍开放，提交路径（live=False）也回退静态
        m = AppModel()
        runs = _role_header_runs(blk, m)
        text = "".join(r.text for r in runs)
        assert "\U0001f4ad" in text, f"提交路径思考头应静态 💭: {text!r}"
        assert "思考" in text

    def test_closed_reasoning_header_static(self):
        """已关闭推理块角色头保持静态 💭（frozen 缓存）。"""
        from src.tui.app.model import _role_header_runs, ChatBlock
        blk = ChatBlock(kind="reasoning", lines=[_al("thinking...")])
        blk.closed = True
        m = AppModel()
        runs = _role_header_runs(blk, m, live=True)
        text = "".join(r.text for r in runs)
        assert "\U0001f4ad" in text, f"关闭思考头应回退静态 💭: {text!r}"
        assert "思考" in text


# ═══════════════════════════════════════════════════════════
# BEAUTY-28：状态栏 thinking 阶段标签弱呼吸
# ═══════════════════════════════════════════════════════════

class TestBeauty28ThinkingPhaseBreath:
    def test_thinking_phase_text(self):
        """thinking 阶段标签显示为 …思考（替代原文 …thinking）。"""
        m = AppModel()
        m.status.model_name = "m"
        m.status.status_active = True
        m.status.main_phase = "thinking"
        plains = _render(m)
        assert any("\u2026思考" in p for p in plains), (
            f"状态栏应显示 …思考 阶段标签: {plains!r}"
        )

    def test_thinking_phase_style_breath_range(self):
        """thinking 阶段标签呼吸色在 242~252 区间（弱呼吸）。"""
        from src.tui.app.status_bar import _build_status_runs
        m = AppModel()
        st = m.status
        st.model_name = "m"
        st.status_active = True
        st.main_phase = "thinking"
        runs = _build_status_runs(m)
        texts = [r.text for r in runs]
        assert any("\u2026思考" in t for t in texts)
        for r in runs:
            if "\u2026思考" in r.text:
                fg = r.style.fg if r.style else None
                assert fg is not None and 242 <= fg <= 252, (
                    f"thinking 标签呼吸色应在 242~252: {fg}"
                )


# ═══════════════════════════════════════════════════════════
# BEAUTY-29：user_select 弹窗标题模式图标
# ═══════════════════════════════════════════════════════════

class TestBeauty29SelectModeIcon:
    def _render_popup(self, multi=False):
        from src.tui.app.user_select import UserSelectPopup
        from src.tui.app.model import UserSelectState
        m = AppModel()
        m.user_select = UserSelectState(
            visible=True, seq=1, title="T",
            options=["A", "B"], multi_select=multi, selected=0,
        )
        r = Reconciler()
        root = r.create_root()
        el = h(UserSelectPopup, {"model": m, "width": 80})
        r.render(root, el, 80, 24)
        frame = render_frame(root, 80)
        return [ln.plain for ln in frame.lines]

    def test_single_title_icon(self):
        """单选弹窗标题前置 ▶ 图标。"""
        lines = self._render_popup(multi=False)
        assert lines[0] == " \u258d \u25b6 T (1/2)", f"单选标题应含 ▶: {lines[0]!r}"

    def test_multi_title_icon(self):
        """多选弹窗标题前置 ☑ 图标。"""
        lines = self._render_popup(multi=True)
        assert lines[0] == " \u258d \u2611 T (1/2)", f"多选标题应含 ☑: {lines[0]!r}"


# ═══════════════════════════════════════════════════════════
# BEAUTY-31：TopHeader 版本号活跃期呼吸
# ═══════════════════════════════════════════════════════════

class TestBeauty31VersionBreath:
    def test_version_runs_idle_static(self):
        """空闲版本号静态 242（BEAUTY-31）。"""
        from src.tui.app.header import _version_runs
        runs = _version_runs(False)
        assert len(runs) == 1
        assert runs[0].text.startswith(" \u00b7 v"), f"版本号前缀: {runs[0].text!r}"
        assert runs[0].style.fg == 242, f"空闲版本号应静态 242: {runs[0].style.fg}"

    def test_version_runs_active_breath(self):
        """活跃期版本号呼吸色在 242~252 区间。"""
        from src.tui.app.header import _version_runs
        runs = _version_runs(True)
        fg = runs[0].style.fg
        assert 242 <= fg <= 252, f"活跃版本号应在呼吸区间: {fg}"

    def test_header_contains_version(self):
        """标题栏仍包含版本号（结构契约）。"""
        m = AppModel()
        plains = _render(m)
        assert any("\u00b7 v" in p for p in plains), (
            f"标题栏应含版本号: {plains[:3]!r}"
        )


# ═══════════════════════════════════════════════════════════
# BEAUTY-30：解析进度行 spinner 金色呼吸
# ═══════════════════════════════════════════════════════════

class TestBeauty30ParseSpinnerGold:
    def test_parse_line_spinner_gold_run(self):
        """解析进度行 spinner 独立金色呼吸 run（178~190 区间）。"""
        from src.tui.app.app import _ParseLine
        from src.tui.app import _fx
        from src.renderer.ansi.helpers import AnsiLine
        m = AppModel()
        # apply.py _S_PARSE 结构：`  ~ {tool} {tokens} {elapsed}s`（fg=242）
        m.parse_line = AnsiLine.of("  ~ rf 51t 0.74s", None)
        m.parse_line.runs[0].style = None  # 简化：无样式（防御分支）
        # 直接构造带 fg=242 样式的行（对齐 apply _S_PARSE）
        from src.tui.core.style import Style
        m.parse_line = AnsiLine([__import__("src.renderer.ansi.helpers", fromlist=["Run"]).Run("  ~ rf ", Style(fg=242)),
                                 __import__("src.renderer.ansi.helpers", fromlist=["Run"]).Run("51t 0.74s", Style(fg=242))])
        el = _ParseLine({"model": m})
        styled = el.props["styled"]
        texts = [r.text for r in styled]
        # 结构：前导空格 + spinner（金色）+ 剩余文本（呼吸灰）
        assert " " in texts[0], f"前导空格应保留: {texts!r}"
        sp_found = False
        for r in styled:
            if r.text in _fx.SPINNER_FRAMES:
                fg = r.style.fg if r.style else None
                assert fg is not None and 178 <= fg <= 190, (
                    f"spinner 应为金色呼吸 178~190: {fg}"
                )
                sp_found = True
        assert sp_found, f"解析行应含独立 spinner run: {texts!r}"


# ═══════════════════════════════════════════════════════════
# BEAUTY-32：live content 流式指示 spinner
# ═══════════════════════════════════════════════════════════

class TestBeauty32LiveContentIndicator:
    def test_live_content_last_line_spinner(self):
        """live content 块（未关闭）最后一行带 spinner 帧（流式指示）。"""
        from src.tui.app import _fx
        m = AppModel()
        m.status.status_active = True
        m.append_block("content", [
            _al(f"  流式内容行 {i} abcdefghijklmnop") for i in range(5)
        ])
        # 不 commit_open_block——行保留在块内 live 渲染
        plains = _render(m, width=60)
        sp_chars = set(_fx.SPINNER_FRAMES)
        found = False
        for p in plains:
            if "流式内容行" in p and p[-1] in sp_chars:
                found = True
                break
        assert found, f"live content 最后一行应带 spinner: {plains!r}"

    def test_closed_content_no_spinner(self):
        """已关闭 content 块（提交冻结）最后一行不带 spinner。"""
        from src.tui.app import _fx
        m = AppModel()
        m.status.status_active = True
        blk = m.append_block("content", [
            _al(f"  内容行 {i} abcdefghijklmnop") for i in range(3)
        ])
        blk.closed = True
        m.commit_block(0)
        plains = _render(m, width=60)
        sp_chars = set(_fx.SPINNER_FRAMES)
        for p in plains:
            if "内容行" in p:
                assert p[-1] not in sp_chars, f"已提交内容行不应带 spinner: {p!r}"

    def test_with_stream_indicator_truncates(self):
        """_with_stream_indicator 截断防溢出：总宽 <= width。"""
        from src.tui.app.chat_view import _with_stream_indicator
        styled = [StyledRun("x" * 60, None)]
        out = _with_stream_indicator(styled, 60, "⠋")
        total = sum(r.width for r in out)
        assert total <= 60, f"指示行总宽应 <= width: {total}"
        assert out[-1].text == "⠋", "末尾应为 spinner"

    def test_with_stream_indicator_empty_sp_noop(self):
        """sp 为空（非 live content）返回原列表（PERF-26 契约）。"""
        from src.tui.app.chat_view import _with_stream_indicator
        styled = [StyledRun("内容", None)]
        out = _with_stream_indicator(styled, 60, "")
        assert out is styled, "sp 为空应返回原列表（零拷贝）"


# ═══════════════════════════════════════════════════════════
# BEAUTY-33：通知/子代理角色头 live 呼吸
# ═══════════════════════════════════════════════════════════

class TestBeauty33NoticeSubagentBreath:
    def _runs(self, kind, closed=False, live=False):
        from src.tui.app.model import _role_header_runs, ChatBlock
        blk = ChatBlock(kind=kind, lines=[_al("body")])
        blk.closed = closed
        return _role_header_runs(blk, AppModel(), live=live)

    def test_notification_live_breath(self):
        """通知角色头 live 渲染路径呼吸色在 242~252 区间。"""
        runs = self._runs("notification", closed=False, live=True)
        assert runs[0].text == "\u258e"
        fg = runs[0].style.fg
        assert 242 <= fg <= 252, f"通知 live 应在呼吸区间: {fg}"
        assert runs[1].text == "通知"

    def test_notification_closed_static(self):
        """通知角色头关闭后静态 pal.notice（242）。"""
        runs = self._runs("notification", closed=True)
        assert runs[0].style.fg == 242, f"关闭通知应静态 242: {runs[0].style.fg}"

    def test_subagent_live_breath(self):
        """子代理角色头 live 渲染路径呼吸色在 242~252 区间。"""
        runs = self._runs("subagent", closed=False, live=True)
        assert runs[1].text == "子代理"
        fg = runs[0].style.fg
        assert 242 <= fg <= 252, f"子代理 live 应在呼吸区间: {fg}"

    def test_subagent_closed_static(self):
        """子代理角色头关闭后静态 pal.dim（242）。"""
        runs = self._runs("subagent", closed=True)
        assert runs[0].style.fg == 242, f"关闭子代理应静态 242: {runs[0].style.fg}"

# ═══════════════════════════════════════════════════════════
# BEAUTY-34：subagent 组卡省略提示呼吸
# ═══════════════════════════════════════════════════════════

class TestBeauty34SubagentOmitBreath:
    def _omit_lines(self, running=True):
        """构造超限组卡，返回省略提示行。"""
        from unittest.mock import patch
        from src.tui._subagent_render import render_frame as _rf
        from src.tui._subagent_panel import SubAgentPanelController, _AgentSlot
        ctrl = SubAgentPanelController()
        with patch("src.tui._subagent_panel.time.monotonic", return_value=0.0):
            slots = {
                f"a{i}": _AgentSlot(
                    label=f"a{i}", description=f"t{i}",
                    status="running" if running else "done",
                )
                for i in range(5)
            }
        ctrl._agents = slots
        ctrl._order = list(slots)
        return _rf(ctrl, max_lines=4)

    def test_running_omit_breath(self):
        """运行中组卡省略提示呼吸色在 110~120 区间。"""
        lines = self._omit_lines(running=True)
        omit_line = next(l for l in lines if "省略" in l.plain)
        # 省略内容 run（`…` / `+K 行省略`）呼吸色；边框 run（│/│）排除
        found = False
        for r in omit_line.runs:
            if "省略" in r.text or r.text == "\u2026":
                fg = r.style.fg if r.style else None
                assert fg is not None and 110 <= fg <= 120, (
                    f"运行中省略提示应在呼吸区间: {fg}"
                )
                found = True
        assert found, f"省略提示应含呼吸 run: {omit_line.runs!r}"

    def test_closed_omit_static(self):
        """全部完成组卡省略提示静态 _S_DIMMER（240）。"""
        lines = self._omit_lines(running=False)
        omit_line = next(l for l in lines if "省略" in l.plain)
        for r in omit_line.runs:
            if "省略" in r.text or r.text == "\u2026":
                assert r.style is not None and r.style.fg == 240, (
                    f"空闲省略提示应静态 240: {r.style}"
                )
