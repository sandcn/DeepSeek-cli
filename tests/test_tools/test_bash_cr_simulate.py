"""bash 工具回车（\\r）终端模拟测试。

覆盖 ``src/tools/bash._simulate_terminal``——bash 工具卡片（toolcard）显示
输出时，把 \\r（0x0D）按真实终端语义兑现：\\r 使光标回到当前行首，后续字符
覆盖已有内容（进度条 ``10%\\r20%\\r30%`` 显示为最终 ``30%``），而非把 \\r
当普通字符渲染（乱码/宽度异常）。
"""

from __future__ import annotations

import pytest

from src.tools.bash import _simulate_terminal, _strip_ansi


class TestSimulateTerminal:
    """_simulate_terminal 核心语义。"""

    @pytest.mark.parametrize("text,expected", [
        # 进度条：多次 \r 后只保留最终状态
        ("10%\r20%\r30%", "30%"),
        # 短内容覆盖长内容：只覆盖前几列，后续字符保留
        ("abc\rXY", "XYc"),
        ("12345\rAB", "AB345"),
        # 连续 \r 只回行首一次
        ("\r\r\r", ""),
        ("a\rb\rc", "c"),
        # \r\n 是换行（先回行首再换行）→ 前一行内容保留
        ("abc\r\nXY", "abc\nXY"),
        # 每行独立处理
        ("a\rb\r\nc\rd", "b\nd"),
        ("1\r2\n3\r4", "2\n4"),
        # 无 \r 原样返回（快路径）
        ("no-cr", "no-cr"),
        ("", ""),
        ("\n", "\n"),
    ])
    def test_simulate(self, text: str, expected: str) -> None:
        assert _simulate_terminal(text) == expected

    def test_no_cr_returns_same_object(self) -> None:
        """无 \\r 时返回原字符串（零开销快路径，不新建对象）。"""
        s = "plain\nline"
        assert _simulate_terminal(s) is s

    def test_multiline_mixed(self) -> None:
        """多行混合：各行为独立 \\r 语义。"""
        assert _simulate_terminal("A\rB\nC\rD\nE\rF\rG") == "B\nD\nG"

    def test_with_ansi_stripped_first(self) -> None:
        """含 ANSI 序列时先 _strip_ansi 再模拟（bash 显示路径顺序）。"""
        raw = "\x1b[31m10%\x1b[0m\r20%"
        assert _simulate_terminal(_strip_ansi(raw)) == "20%"
        # 真实进度条 + 颜色：\r 后 Done 只覆盖前 4 字符，其余保留
        # （与真实终端一致：Downloading... 50% → \r → Done 覆盖 Down）
        raw2 = "\x1b[32mDownloading...\x1b[0m 50%\r\x1b[32mDone\x1b[0m"
        assert _simulate_terminal(_strip_ansi(raw2)) == "Doneloading... 50%"


class TestDisplayPath:
    """display/web_display 输出经 _strip_ansi + _simulate_terminal 后发布。"""

    def test_read_loop_line_normalization_preserves_inline_cr(self) -> None:
        """行内独立 \\r 保留供模拟；\\r\\n 规范化为 \\n。"""
        decoded = "10%\r20%\r30%\r\n"
        clean = decoded.replace("\r\n", "\n")
        assert clean == "10%\r20%\r30%\n"
        simulated = _simulate_terminal(_strip_ansi(clean))
        assert simulated == "30%\n"

    def test_display_on_line_uses_simulate(self) -> None:
        """display() 的 _on_line 源码路径应调用 _simulate_terminal。"""
        import inspect
        from src.tools import bash

        src = inspect.getsource(bash.BashFunc.display)
        assert "_simulate_terminal(_strip_ansi(text))" in src

    def test_web_display_on_line_uses_simulate(self) -> None:
        """web_display() 的 _on_line 发布前端文本应使用模拟后纯文本。"""
        import inspect
        from src.tools import bash

        src = inspect.getsource(bash.BashFunc.web_display)
        assert "_simulate_terminal(_strip_ansi(text))" in src
        # 旧逻辑：直接 text.replace('\\r', '') 发布原始文本，应已移除
        assert "text.replace('\\r', '')" not in src
