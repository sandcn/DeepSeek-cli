# -*- coding: utf-8 -*-
"""WRITE_LINE 裸终端控制序列过滤 —— 回归测试。

Bug：``publish_output("\\r\\033[K")``（工具解析结束时清理进度行，见
``pipeline_async._cleanup_display``）经 OutputEvent → WriteLineCmd 进入 TUI
渲染层。``ansi_to_line`` 只解析 SGR 样式，``\\r`` / ``\\x1b[K`` 原样保留为
**文档内容行**：InkRenderer 写该行到终端时执行回车 + 清行（清掉刚写入的
本行），并且每轮工具调用都在文档中部插入一行、把后续行整体下移（触发滚动
与重写）。

修复：``_do_write_line`` 过滤「去控制序列后无可见文本」的段（不产出内容行），
同时保留真正的空白段（空格）与含样式的可见文本。
"""

from __future__ import annotations

import pytest

from src.renderer.ansi import AnsiLine
from src.tui.app.apply import apply_cmd, _strip_control_text
from src.tui.app.model import AppModel
from src.tui._const import WriteLineCmd


class TestStripControlText:
    def test_pure_control_sequence(self):
        assert _strip_control_text("\r\x1b[K") == ""
        assert _strip_control_text("\x1b[2J\x1b[H") == ""
        assert _strip_control_text("\r\n\t") == ""

    def test_visible_text_kept(self):
        assert _strip_control_text("\x1b[33m警告\x1b[0m") == "警告"
        assert _strip_control_text("Chat v2.2.0") == "Chat v2.2.0"

    def test_spaces_are_visible(self):
        assert _strip_control_text("  ") == "  "

    def test_zero_width_joiner_kept(self):
        # 不用 str.isprintable（会误伤 ZWJ 等组合字符）
        assert _strip_control_text("\U0001F44D\u200d 测试") == "\U0001F44D\u200d 测试"


class TestWriteLineControlFilter:
    def test_pure_control_sequence_emits_no_line(self):
        model = AppModel()
        apply_cmd(model, WriteLineCmd(text="\r\x1b[K"))
        assert model.committed_lines == []
        assert model.blocks == []

    def test_visible_text_still_emitted(self):
        model = AppModel()
        apply_cmd(model, WriteLineCmd(text="\x1b[33m提示\x1b[0m"))
        plains = [getattr(ln, "plain", "") for ln in model.committed_lines]
        assert "提示" in plains

    def test_mixed_lines_keep_visible_only(self):
        model = AppModel()
        apply_cmd(model, WriteLineCmd(text="第一行\n\r\x1b[K\n第三行"))
        plains = [getattr(ln, "plain", "") for ln in model.committed_lines]
        assert "第一行" in plains
        assert "第三行" in plains
        # 中间那段的纯控制序列被过滤，不产出文档行
        assert all("\\r" not in p and "\r" not in p for p in plains)

    def test_blank_line_structure_preserved(self):
        model = AppModel()
        apply_cmd(model, WriteLineCmd(text="上\n\n下"))
        plains = [getattr(ln, "plain", "") for ln in model.committed_lines]
        # 段落间空行保留；卡片尾空行（块分隔）由提交路径追加
        assert plains == ["上", "", "下", ""]

    def test_pure_control_sequence_emits_no_block(self):
        model = AppModel()
        apply_cmd(model, WriteLineCmd(text="\r\x1b[K"))
        assert model.blocks == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
