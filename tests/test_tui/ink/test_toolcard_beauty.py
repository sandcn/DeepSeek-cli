"""BEAUTY-35 工具卡完整美化回归测试（2026-08-06，Claude Code 极简样式）。

覆盖 2026-08-06 工具卡对齐 Claude Code 极简样式（用户需求，方案 A）：
  - 标题行：状态图标 + 类别色工具名（加粗）+ 参数（空格分隔，dim）——
    去掉 ▎ 引导线 / emoji 工具图标 / ``·`` detail 分隔（Claude Code
    ``Read src/main.py`` 语义）；runs[0] 保持状态图标（close_tool_box 原位
    翻转依赖）；
  - 类别配色：工具名按工具类别着色（唯一真源 TOOL_CATEGORY_STYLES），运行中
    类别色邻域呼吸，关闭后静态类别色；
  - 内容竖线：每内容行前置 ``│ ``（深灰 238），窄屏总宽 <= width；
  - **无独立状态行**：Claude Code 状态由标题行状态图标表达（●/✔/✖），
    无 ``✔ 完成 · N 行 · Xs`` 状态行。

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
    """标题行工具名按工具类别着色（唯一真源 _tool_icons.TOOL_CATEGORY_STYLES）。"""

    def _head(self, tool_name, status="done", closed=True, display=None):
        blk = _make_block(tool_name, status, "", [_al("  out")], closed=closed)
        head = tool_card_lines(blk, 60, 0, None)[0]
        # 找到工具名 run（加粗类别色；display 缺省用显示名）
        from src.tools.registry import get_tool_display_name
        disp = display or get_tool_display_name(tool_name) or tool_name
        for r in head:
            if r.text and r.text.strip() == disp:
                return r
        return None

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
        """关闭后工具名静态类别色（bash→shell 41），加粗；runs[0] 状态图标。

        Claude Code 极简样式：标题行 = 状态图标 + 工具名 + 参数——无 ▎/emoji。
        """
        blk = _make_block("bash", "done", "", [_al("  out")], closed=True)
        head = tool_card_lines(blk, 60, 0, None)[0]
        assert head[0].text.strip() == "\u2714", f"runs[0] 应为状态图标: {head!r}"
        # 无 ▎ 引导线 / 无 emoji 图标
        assert not any(r.text == "\u258e" for r in head), f"不应含 ▎ 引导线: {head!r}"
        assert not any(r.text and r.text[0] in ("\u26a1", "\U0001f4d6", "\u270e")
                       for r in head), f"不应含 emoji 工具图标: {head!r}"
        # 工具名 run：类别色 41 + 加粗
        name_run = self._head("bash", "done", True, display="Bash")
        assert name_run is not None, f"应找到工具名 run: {head!r}"
        assert name_run.style.fg == 41, f"工具名应类别色 41: {name_run.style.fg}"
        assert name_run.style.bold, "工具名应加粗"

    def test_running_icon_breath_in_category_range(self):
        """运行中工具名呼吸色在类别区间内（bash→shell 41~49）。"""
        name_run = self._head("bash", "running", False, display="Bash")
        assert name_run is not None, f"应找到工具名 run: {name_run!r}"
        fg = name_run.style.fg
        assert 41 <= fg <= 49, f"运行中工具名应在类别呼吸区间: {fg}"

    def test_closed_state_icon_static(self):
        """关闭后状态图标静态（不呼吸）：done ✔ 47 / fail ✖ 196 bold。"""
        blk = _make_block("bash", "done", "", [_al("  out")], closed=True)
        head = tool_card_lines(blk, 60, 0, None)[0]
        assert head[0].style.fg == 47, f"done 状态图标: {head[0].style.fg}"
        blk_fail = _make_block("bash", "fail", "", [_al("  out")], closed=True)
        head_fail = tool_card_lines(blk_fail, 60, 0, None)[0]
        assert head_fail[0].style.fg == 196, f"fail 状态图标: {head_fail[0].style.fg}"
        assert head_fail[0].style.bold, "fail 状态图标应加粗"

    def test_detail_dim_after_close(self):
        """关闭后 detail 静态 pal.dim（242）。"""
        blk = _make_block("bash", "done", "echo hi", [_al("  out")], closed=True)
        head = tool_card_lines(blk, 60, 0, None)[0]
        detail_run = next(r for r in head if "echo hi" in r.text)
        assert detail_run.style.fg == 242, f"关闭 detail 应 dim 242: {detail_run.style.fg}"


# ═══════════════════════════════════════════════════════════
# 标题行极简结构（Claude Code 语义）
# ═══════════════════════════════════════════════════════════

class TestTitleMinimal:
    """标题行 = 状态图标 + 工具名 + 参数（空格分隔，无 ▎/emoji/·）。"""

    def test_title_plain_text(self):
        """标题行纯文本：✔ Bash echo hi（空格分隔）。"""
        blk = _make_block("bash", "done", "echo hi", [_al("  out")], closed=True)
        head = tool_card_lines(blk, 60, 0, None)[0]
        text = "".join(r.text for r in head)
        assert text == "\u2714 Bash echo hi", f"标题行文本: {text!r}"
        # 无边框角字符 / 无 ▎ / 无 · 分隔
        assert not any(ch in text for ch in "\u250c\u2510\u2514\u2518"), text
        assert "\u258e" not in text, f"不应含 ▎ 引导线: {text!r}"
        assert " \u00b7 " not in text, f"不应含 · 分隔: {text!r}"

    def test_title_no_emoji_icon(self):
        """标题行无 emoji 工具图标（⚡/📖/✏️ 等）。"""
        for tool_name in ("bash", "read_file", "write_file", "update_file",
                          "web_search", "dispatch_agent"):
            blk = _make_block(tool_name, "done", "arg", [_al("  o")], closed=True)
            head = tool_card_lines(blk, 60, 0, None)[0]
            text = "".join(r.text for r in head)
            assert not any(ord(ch) > 0x2600 for ch in text if ch not in (
                "\u2714", "\u2716", "\u25cf", "\u258e",
            )), f"{tool_name} 标题不应含 emoji: {text!r}"

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

    def test_bash_truncation_message_has_guide(self):
        """落盘截断文案（CC）替代省略行，带竖线引导且宽度 <= width。

        对齐 Claude Code：`Output truncated (XKB total). Full output saved
        to: <path>`。仅在 ``_bash_truncation_file`` 非空时启用（兜底省略行
        由 test_omitted_line_has_guide 覆盖）。
        """
        blk = _make_block("bash", "done", "x", [_al("  hi")], closed=True)
        blk.extra["_bash_omitted_lines"] = 7
        blk.extra["_bash_truncation_file"] = "/tmp/deepseek-bash-abc123"
        blk.extra["_bash_truncation_bytes"] = 2048
        lines = tool_card_lines(blk, 120, 0, None)
        trunc = next(l for l in lines if "Output truncated" in "".join(r.text for r in l))
        text = "".join(r.text for r in trunc)
        assert text.startswith("│ "), f"截断文案应有竖线引导: {text!r}"
        assert "Output truncated (2KB total)." in text, text
        assert "Full output saved to: /tmp/deepseek-bash-abc123" in text, text
        assert wcswidth_simple(text) <= 120, f"截断文案超宽: {text!r}"
        # 无落盘文件（缺省）回退省略行
        blk2 = _make_block("bash", "done", "x", [_al("  hi")], closed=True)
        blk2.extra["_bash_omitted_lines"] = 7
        lines2 = tool_card_lines(blk2, 30, 0, None)
        omit = next(l for l in lines2 if "省略" in "".join(r.text for r in l))
        assert "前 7 行省略" in "".join(r.text for r in omit), lines2


# ═══════════════════════════════════════════════════════════
# 无独立状态行（Claude Code 极简样式）
# ═══════════════════════════════════════════════════════════

class TestNoStatusLine:
    """Claude Code 极简样式：无 ``✔ 完成 · N 行 · Xs`` 独立状态行。"""

    def test_no_status_line_done(self):
        """done 卡无独立状态行（状态由标题行图标 ✔ 表达）。"""
        blk = _make_block("bash", "done", "x", [_al("  a"), _al("  b"), _al("  c")],
                          closed=True, duration=0.42)
        lines = tool_card_lines(blk, 60, 0, None)
        plains = ["".join(r.text for r in l) for l in lines]
        assert not any("\u2714 完成" in p for p in plains), (
            f"不应含独立状态行: {plains!r}"
        )
        # 末行是内容行（竖线引导），不是状态行
        assert plains[-1].startswith("│"), f"末行应为内容行: {plains!r}"
        # 标题行含 ✔（状态图标）
        assert plains[0].startswith("\u2714"), f"标题行应含 ✔: {plains[0]!r}"

    def test_no_status_line_fail(self):
        """fail 卡无独立状态行（状态由标题行图标 ✖ 表达）。"""
        blk = _make_block("bash", "fail", "x", [_al("  error")], closed=True,
                          duration=0.1)
        lines = tool_card_lines(blk, 60, 0, None)
        plains = ["".join(r.text for r in l) for l in lines]
        assert not any("\u2716 失败" in p for p in plains), (
            f"不应含独立状态行: {plains!r}"
        )
        assert plains[0].startswith("\u2716"), f"标题行应含 ✖: {plains[0]!r}"

    def test_no_status_line_empty_card(self):
        """空工具卡（仅标题行）无状态行（Claude Code 不显示耗时/行数）。"""
        blk = _make_block("bash", "done", "x", [], closed=True, duration=1.2)
        lines = tool_card_lines(blk, 60, 0, None)
        plains = ["".join(r.text for r in l) for l in lines]
        assert len(plains) == 1, f"空卡仅标题行: {plains!r}"
        assert not any("完成" in p or "行" in p for p in plains), plains

    def test_status_data_line_skipped(self):
        """模型层状态数据行（``  ✔``/``  ✖``）不渲染为内容行。"""
        blk = _make_block("bash", "done", "x", [_al("  a"), _al("  b")],
                          closed=True)
        lines = tool_card_lines(blk, 60, 0, None)
        plains = ["".join(r.text for r in l) for l in lines]
        assert plains == ["\u2714 Bash x", "│   a", "│   b"], plains


# ═══════════════════════════════════════════════════════════
# 与模型层集成（open/close 记录耗时）
# ═══════════════════════════════════════════════════════════

class TestModelIntegration:
    """open/close 记录耗时（模型层数据保留，渲染层不再显示状态行）。"""

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
        # 渲染帧无独立状态行（Claude Code 极简）；标题行含 ✔
        plains = [l.plain for l in m.committed_lines]
        assert not any("✔ 完成" in p for p in plains), plains
        assert any(p.startswith("✔") for p in plains), plains

    def test_reuse_box_keeps_started_at(self):
        """同一 tool_id 复用 box 不重置开始时间（防重复投递刷新耗时）。"""
        m = AppModel()
        m.open_tool_box("t1", "bash", "a")
        first_started = m.tool_boxes["t1"].extra["_tool_started_at"]
        m.open_tool_box("t1", "bash", "a")
        assert m.tool_boxes["t1"].extra["_tool_started_at"] == first_started
