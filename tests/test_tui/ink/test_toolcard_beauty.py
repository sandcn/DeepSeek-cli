"""BEAUTY-35 工具卡完整美化回归测试（2026-08-06）。

覆盖 2026-08-06 工具卡完整美化：
  - 类别配色：标题 ▎引导线 / 工具图标 / 显示名按工具类别着色（运行中类别色
    邻域呼吸，关闭后静态类别色）；
  - 标题强化：显示名加粗 + ▎引导线（runs[0] 保持状态图标——close_tool_box
    原位翻转依赖）；
  - 内容竖线：每内容行前置 ``│ ``（深灰 238），窄屏总宽 <= width；
  - 状态元信息：状态行追加 ``· N 行 · Xs``（行数/耗时）。

测试原则：动效为时间基（time_glow），断言聚焦**结构契约**（文本/前缀/
类别色号/宽度），不锁定具体呼吸色号（时间敏感断言脆弱——呼吸区间断言
仅验证在类别 lo..hi 区间内）。
"""

from __future__ import annotations

from src.tui.app.model import AppModel, ChatBlock
from src.tui.app.toolcard import tool_card_lines, _category_style
from src.renderer.ansi.helpers import AnsiLine, ansi_to_runs
from src.tui._screen import wcswidth_simple


def _al(text: str) -> AnsiLine:
    return AnsiLine(ansi_to_runs(text))


def _make_block(tool_name, status="running", detail="", lines=None,
                closed=False, duration=None):
    """构造工具块（与模型层 close_tool_box 产出结构一致）。"""
    blk = ChatBlock(kind="tool")
    blk.extra["tool_name"] = tool_name
    blk.extra["tool_status"] = status
    blk.extra["tool_detail"] = detail
    blk.lines = [_al("  \u00b7 x")] + (list(lines) if lines else [])
    if closed:
        blk.closed = True
        blk.extra["_status_line_index"] = len(blk.lines)
        if duration is not None:
            blk.extra["_tool_duration"] = duration
        blk.lines.append(_al("  \u2714" if status == "done" else "  \u2716"))
    return blk


# ═══════════════════════════════════════════════════════════
# 类别配色
# ═══════════════════════════════════════════════════════════

class TestCategoryColor:
    """标题行按工具类别着色（唯一真源 _tool_icons.TOOL_CATEGORY_STYLES）。"""

    def _head(self, tool_name, status="done", closed=True):
        blk = _make_block(tool_name, status, "", [_al("  out")], closed=closed)
        return tool_card_lines(blk, 60, 0, None)[0]

    def test_category_style_mapping(self):
        """类别映射：shell/file_read/file_write/search/agent/interact/delete。"""
        cases = {
            "bash": 41, "execute_command": 41,
            "read_file": 81,
            "write_file": 213, "update_file": 213,
            "web_search": 221, "grep": 221, "find": 221,
            "dispatch_agent": 75,
            "user_select": 51,
            "rm": 203,
        }
        for tool_name, expect_fg in cases.items():
            st = _category_style(tool_name)
            assert st.fg == expect_fg, (
                f"{tool_name} 类别色应为 {expect_fg}: {st.fg}"
            )

    def test_unknown_tool_dim_fallback(self):
        """未知名工具兜底 dim（242）。"""
        st = _category_style("")
        assert st.fg == 242

    def test_closed_icon_name_category_color(self):
        """关闭后工具图标/显示名静态类别色（bash→shell 41），显示名加粗。"""
        head = self._head("bash", "done", True)
        # 结构：✔ + ▎(41) + ⚡(41) + Bash(41 bold) + detail
        assert head[0].text.strip() == "\u2714", f"runs[0] 应为状态图标: {head!r}"
        assert head[1].text == "\u258e", f"应含 ▎ 引导线: {head!r}"
        assert head[1].style.fg == 41, f"▎ 应类别色 41: {head[1].style.fg}"
        assert head[2].text.startswith("\u26a1"), f"工具图标: {head!r}"
        assert head[2].style.fg == 41, f"图标应类别色 41: {head[2].style.fg}"
        assert head[3].style.fg == 41, f"显示名应类别色 41: {head[3].style.fg}"
        assert head[3].style.bold, "显示名应加粗"

    def test_running_icon_breath_in_category_range(self):
        """运行中图标呼吸色在类别区间内（bash→shell 41~49）。"""
        head = self._head("bash", "running", False)
        icon = next(r for r in head if r.text and r.text[0] == "\u26a1")
        fg = icon.style.fg
        assert 41 <= fg <= 49, f"运行中图标应在类别呼吸区间: {fg}"

    def test_closed_state_icon_static(self):
        """关闭后状态图标静态（不呼吸）：done ✔ 47 / fail ✖ 196 bold。"""
        head_done = self._head("bash", "done", True)
        assert head_done[0].style.fg == 47, f"done 状态图标: {head_done[0].style.fg}"
        head_fail = self._head("bash", "fail", True)
        assert head_fail[0].style.fg == 196, f"fail 状态图标: {head_fail[0].style.fg}"
        assert head_fail[0].style.bold, "fail 状态图标应加粗"

    def test_detail_dim_after_close(self):
        """关闭后 detail 静态 pal.dim（242）。"""
        blk = _make_block("bash", "done", "echo hi", [_al("  out")], closed=True)
        head = tool_card_lines(blk, 60, 0, None)[0]
        detail_run = next(r for r in head if "echo hi" in r.text)
        assert detail_run.style.fg == 242, f"关闭 detail 应 dim 242: {detail_run.style.fg}"


# ═══════════════════════════════════════════════════════════
# 内容竖线引导
# ═══════════════════════════════════════════════════════════

class TestContentGuide:
    """内容行竖线引导（``│ `` 深灰 238）+ 宽度不变量。"""

    def _body(self, width=60):
        blk = _make_block("bash", "done", "x", [_al("  hello"), _al("  world")],
                          closed=True)
        return tool_card_lines(blk, width, 0, None)

    def test_content_lines_have_guide(self):
        """内容行以 ``│ `` 开头（深灰 238）。"""
        lines = self._body()
        p1 = "".join(r.text for r in lines[1])
        p2 = "".join(r.text for r in lines[2])
        assert p1.startswith("│  "), f"内容行应有竖线引导: {p1!r}"
        assert p2.startswith("│  "), f"内容行应有竖线引导: {p2!r}"
        assert lines[1][0].style.fg == 238, f"竖线引导深灰 238: {lines[1][0].style.fg}"

    def test_narrow_width_no_overflow(self):
        """窄屏（5/6/8）内容行总宽 <= width。"""
        for width in (8, 6, 5):
            blk = _make_block("bash", "done", "x",
                              [_al("  " + "输出" * 15)], closed=True)
            for i, line in enumerate(tool_card_lines(blk, width, 0, None)):
                text = "".join(r.text for r in line)
                assert wcswidth_simple(text) <= width, (
                    f"width={width} 行超宽: {text!r}"
                )

    def test_empty_output_line_keeps_guide(self):
        """空输出行保留竖线引导（视觉连续）。"""
        blk = _make_block("bash", "done", "x", [_al(""), _al("  hi")], closed=True)
        lines = tool_card_lines(blk, 60, 0, None)
        p1 = "".join(r.text for r in lines[1])
        assert p1 == "│ ", f"空行应保留引导线: {p1!r}"

    def test_omitted_line_has_guide(self):
        """省略提示行（前/后 N 行省略）带竖线引导且宽度 <= width。"""
        blk = _make_block("bash", "done", "x", [_al("  hi")], closed=True)
        blk.extra["_bash_omitted_lines"] = 7
        lines = tool_card_lines(blk, 30, 0, None)
        omit = next(l for l in lines if "省略" in "".join(r.text for r in l))
        text = "".join(r.text for r in omit)
        assert text.startswith("│ "), f"省略提示应有竖线引导: {text!r}"
        assert wcswidth_simple(text) <= 30, f"省略提示超宽: {text!r}"

    def test_head_omitted_line_has_guide(self):
        """head 省略提示行（后 N 行省略）带竖线引导。"""
        blk = _make_block("find", "done", "x", [_al("  hi")], closed=True)
        blk.extra["_head_omitted_lines"] = 7
        lines = tool_card_lines(blk, 30, 0, None)
        omit = next(l for l in lines if "省略" in "".join(r.text for r in l))
        text = "".join(r.text for r in omit)
        assert text.startswith("│ "), f"head 省略提示应有竖线引导: {text!r}"


# ═══════════════════════════════════════════════════════════
# 状态行元信息
# ═══════════════════════════════════════════════════════════

class TestStatusMeta:
    """状态行元信息（``· N 行 · Xs``，dim 灰）。"""

    def test_status_meta_lines_and_duration(self):
        """done 状态行含行数与耗时。"""
        blk = _make_block("bash", "done", "x", [_al("  a"), _al("  b"), _al("  c")],
                          closed=True, duration=0.42)
        lines = tool_card_lines(blk, 60, 0, None)
        status_line = lines[-1]
        text = "".join(r.text for r in status_line)
        assert text == "✔ 完成 · 3 行 · 0.42s", f"状态行应含元信息: {text!r}"
        meta_run = next(r for r in status_line if "行" in r.text)
        assert meta_run.style.fg == 242, f"元信息应 dim 灰: {meta_run.style.fg}"

    def test_status_meta_no_duration_skips(self):
        """无耗时记录时只显示行数。"""
        blk = _make_block("bash", "done", "x", [_al("  a")], closed=True)
        text = "".join(r.text for r in tool_card_lines(blk, 60, 0, None)[-1])
        assert text == "✔ 完成 · 1 行", f"无耗时只显示行数: {text!r}"

    def test_status_meta_fail(self):
        """fail 状态行含失败标记 + 元信息。"""
        blk = _make_block("bash", "fail", "x", [_al("  error")], closed=True,
                          duration=0.1)
        text = "".join(r.text for r in tool_card_lines(blk, 60, 0, None)[-1])
        assert text == "✖ 失败 · 1 行 · 0.10s", f"fail 状态行: {text!r}"

    def test_status_meta_empty_card(self):
        """空工具卡（仅标题行）状态行无行数（只耗时）。"""
        blk = _make_block("bash", "done", "x", [], closed=True, duration=1.2)
        text = "".join(r.text for r in tool_card_lines(blk, 60, 0, None)[-1])
        assert text == "✔ 完成 · 1.20s", f"空卡状态行: {text!r}"

    def test_status_narrow_truncate(self):
        """窄屏状态行截断至 width（不超宽）。"""
        blk = _make_block("bash", "done", "x", [_al("  a")] * 5, closed=True,
                          duration=12.34)
        lines = tool_card_lines(blk, 10, 0, None)
        text = "".join(r.text for r in lines[-1])
        assert wcswidth_simple(text) <= 10, f"状态行超宽: {text!r}"


# ═══════════════════════════════════════════════════════════
# 标题行结构不变式
# ═══════════════════════════════════════════════════════════

class TestTitleStructure:
    """标题行结构不变式（runs[0] 状态图标、▎ 引导线、加粗名称）。"""

    def test_runs0_is_status_icon(self):
        """runs[0] 恒为状态图标（close_tool_box 原位翻转依赖）。"""
        for status, closed, icon in (
            ("running", False, "\u25cf"),
            ("done", True, "\u2714"),
            ("fail", True, "\u2716"),
        ):
            blk = _make_block("bash", status, "d", [_al("  o")], closed=closed)
            head = tool_card_lines(blk, 60, 0, None)[0]
            assert head[0].text.strip() == icon, (
                f"{status} runs[0] 应为 {icon}: {head!r}"
            )

    def test_title_has_guide_and_icon_and_name(self):
        """标题含 ▎引导线 + 类别色图标 + 加粗类别色名称。"""
        blk = _make_block("bash", "done", "echo hi", [_al("  o")], closed=True)
        head = tool_card_lines(blk, 60, 0, None)[0]
        text = "".join(r.text for r in head)
        assert "\u258e" in text, f"应含 ▎ 引导线: {text!r}"
        assert "\u26a1" in text, f"应含工具图标: {text!r}"
        assert "Bash" in text, f"应含显示名: {text!r}"
        assert "echo hi" in text, f"应含 detail: {text!r}"
        # 无边框角字符
        assert not any(ch in text for ch in "\u250c\u2510\u2514\u2518"), text

    def test_title_narrow_truncate_no_overflow(self):
        """窄屏标题行截断至 width。"""
        blk = _make_block("bash", "done", "x" * 30, [_al("  o")], closed=True)
        for width in (5, 10, 20):
            head = tool_card_lines(blk, width, 0, None)[0]
            text = "".join(r.text for r in head)
            assert wcswidth_simple(text) <= width, (
                f"width={width} 标题超宽: {text!r}"
            )


# ═══════════════════════════════════════════════════════════
# 与模型层集成（open/close 记录耗时）
# ═══════════════════════════════════════════════════════════

class TestModelIntegration:
    """open/close 记录耗时并渲染到状态行。"""

    def test_close_sets_duration(self):
        """close_tool_box 后 extra 记录 _tool_duration（>=0）。"""
        m = AppModel()
        m.width = 40
        m.open_tool_box("t1", "bash", "ls")
        m.append_tool_output("t1", "out\n")
        m.close_tool_box("t1", True)
        blk = m.blocks[-1]
        assert blk.extra["_tool_started_at"] is not None
        assert blk.extra["_tool_duration"] is not None
        assert blk.extra["_tool_duration"] >= 0
        # 渲染帧状态行含耗时
        plains = [l.plain for l in m.committed_lines]
        assert any("✔ 完成" in p and "s" in p for p in plains), plains

    def test_reuse_box_keeps_started_at(self):
        """同一 tool_id 复用 box 不重置开始时间（防重复投递刷新耗时）。"""
        m = AppModel()
        m.open_tool_box("t1", "bash", "a")
        first_started = m.tool_boxes["t1"].extra["_tool_started_at"]
        m.open_tool_box("t1", "bash", "a")
        assert m.tool_boxes["t1"].extra["_tool_started_at"] == first_started
