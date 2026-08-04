"""test_helpers — ink/helpers.py 边框块构建工具测试（Claude TUI parity 步骤 1.3）。

覆盖 build_border_box：open 无底边、closed 含状态底行、超宽 title/body 截断、
CJK 宽度计算（不拆宽字符）。
"""

from __future__ import annotations

from src.tui.ink import StyledRun, Line
from src.tui.ink.helpers import (
    build_border_box,
    strip_ansi,
    cursor_control_re,
    _keep_tail,
)
from src.tui.core.style import Style


class TestKeepTail:
    """P-H7 — _keep_tail 字符收集重写（正确性 + 性能冒烟）。"""

    def test_keep_tail_preserves_order(self):
        kept = _keep_tail([StyledRun("abcdef", None)], 3)
        assert len(kept) == 1
        assert kept[0].text == "def"

    def test_keep_tail_multi_run_order(self):
        runs = [StyledRun("abc", None), StyledRun("def", None)]
        kept = _keep_tail(runs, 4)
        # 保留尾部最多 4 宽：尾部 "def"（3 宽）+ "c"（1 宽）→ "c" + "def"
        assert "".join(r.text for r in kept) == "cdef"

    def test_keep_tail_cjk(self):
        # CJK 不拆：budget=3 时保留尾部完整宽字符 "文"（宽 2），"中" 超预算丢弃
        kept = _keep_tail([StyledRun("中文", None)], 3)
        assert "".join(r.text for r in kept) == "文"

    def test_keep_tail_large_smoke(self):
        """100k 字符 _keep_tail 耗时 < 1s（防 O(n²) 回归）。

        预算取内容一半（50000）——确保扫描遍历全串（旧 O(n²) 前插实现
        在 50000 字符前插时为 O(25×10⁸) 字符串复制，会显著超阈值；新 list
        收集 + 反转实现 O(n) 秒级内完成）。修复前用 budget=50 只扫描 50
        字符，旧实现同样能通过，无法锁定 P-H7 目标。
        """
        import time
        runs = [StyledRun("a" * 100000, None)]
        t0 = time.perf_counter()
        _keep_tail(runs, 50000)
        elapsed = time.perf_counter() - t0
        assert elapsed < 1.0, f"_keep_tail 100k chars budget=50000 耗时 {elapsed:.2f}s"


class TestStripAnsi:
    """方向1 步骤2 — 统一 ANSI 工具主真源行为回归。"""

    def test_strip_ansi_unified_regression(self):
        """strip_ansi 剥离 SGR/光标 CSI/OSC 三类合法序列；孤立 ESC 保留。

        注：DECRC（``\\x1b8``）不在 ``_ANSI_RE`` 匹配范围（由
        ``cursor_control_re`` 专供解析，非剥离）——strip_ansi 保留之
        （既有行为，测试锁定）。
        """
        assert strip_ansi("\x1b[31mred\x1b[0m") == "red"
        assert strip_ansi("\x1b[1;31mbold") == "bold"
        # 光标控制 CSI 序列（CUP / SCRC）
        assert strip_ansi("a\x1b[2;5Hb") == "ab"
        assert strip_ansi("a\x1b[ub") == "ab"
        # OSC
        assert strip_ansi("\x1b]0;title\x07x") == "x"
        # 孤立 ESC（非合法序列）保留（strip_ansi 语义；sanitize 兜底移除）
        assert strip_ansi("abc\x1bdef") == "abc\x1bdef"

    def test_cursor_control_re_group_semantics_regression(self):
        """cursor_control_re 保留 CUP row/col 命名组（_stdout_tracker 底部栏过滤依赖）。"""
        m = cursor_control_re.search("\x1b[5;10H")
        assert m is not None
        assert m.group("row") == "5"
        assert m.group("col") == "10"
        # DECRC / SCRC
        assert cursor_control_re.search("\x1b8") is not None
        assert cursor_control_re.search("\x1b[u") is not None


class TestBuildBorderBox:
    def _plain(self, lines: list[Line]) -> list[str]:
        return [l.plain for l in lines]

    def test_open_no_bottom(self) -> None:
        """open 模式：标题行 + 主体行，无底边。"""
        lines = build_border_box(
            [StyledRun("工具")], [Line.of("out1"), Line.of("out2")], width=10
        )
        plain = self._plain(lines)
        assert len(plain) == 3
        assert plain[0].startswith("┌─ 工具")
        assert plain[0].endswith("┐")
        assert plain[1].startswith("│ out1")
        assert plain[2].startswith("│ out2")

    def test_closed_contains_status_bottom(self) -> None:
        """closed 模式：追加含状态文本的底行。"""
        lines = build_border_box(
            [StyledRun("工具")], [], width=12, status="✔ 完成"
        )
        plain = self._plain(lines)
        assert len(plain) == 2
        assert plain[1].startswith("└─ ✔ 完成")
        assert plain[1].endswith("┘")

    def test_width_boundary(self) -> None:
        """所有行宽度不超过给定 width。"""
        for status in ("open", "✖ 失败"):
            lines = build_border_box(
                [StyledRun("a" * 40)],
                [Line.of("b" * 40)],
                width=12,
                status=status,
            )
            for l in lines:
                assert l.width <= 12, f"行超宽: {l.plain!r} width={l.width}"

    def test_wide_title_truncated(self) -> None:
        """超宽 title 被截断（不撑爆边框）。"""
        lines = build_border_box([StyledRun("x" * 30)], [], width=10, status="open")
        assert lines[0].width == 10
        assert "…" not in lines[0].plain or lines[0].plain.endswith("┐")

    def test_cjk_width_calculation(self) -> None:
        """CJK 标题/主体按显示宽度截断（不拆宽字符）。"""
        # "你好世界" 宽 8；width=8 → 标题预算 4 格 → "你"（宽 2），不拆 CJK
        lines = build_border_box(
            [StyledRun("你好世界")], [Line.of("你好世界")], width=8, status="open"
        )
        assert lines[0].width <= 8
        for l in lines:
            assert l.width <= 8

    def test_border_style_default_and_custom(self) -> None:
        """默认边框样式暗青 23；自定义样式生效。"""
        lines = build_border_box([StyledRun("t")], [], width=6, status="open")
        first_run_style = lines[0].runs[0].style
        assert first_run_style is not None and first_run_style.fg == 23
        custom = Style(fg=200)
        lines2 = build_border_box([StyledRun("t")], [], width=6, status="open", border_style=custom)
        assert lines2[0].runs[0].style == custom
