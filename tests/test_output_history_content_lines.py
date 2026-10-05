# -*- coding: utf-8 -*-
"""输出历史（line_callback）只记录已提交内容行 —— 回归测试。

Bug：InkRenderer 各渲染分支按「文档末尾行区间」``[prev_h, new_h)`` 推断
「新增提交行」并回调到输出历史。在 App 的组件树里聊天内容位于文档中部
（``TopHeader`` → ``committed`` → live 块 → 解析进度行 → 状态栏 → 输入区），
文档增长时新增的内容行插入在中间，而 ``[prev_h, new_h)`` 落在**文档末尾**
—— 于是状态栏 / 输入区 / 时间线 / 边框等 live 行被当作「新增内容行」
反复写入输出历史（同一底部行内容随行号增长被反复记录），真正的新增内容行
反而大量漏记。

修复：输出历史只回调 committed 内容行（文档第 ``_CONTENT_LINE_OFFSET`` 行
起，长度 = ``AppModel.committed_lines``），基线随内容行数增长推进；终端
resize（``reflow_committed`` 重排已提交行）时同步基线；清屏 / 重放
（``CLEAR_MSGS``）时基线归零。
"""

from __future__ import annotations

import io

import pytest

from src.renderer.ansi import AnsiLine
from src.tui.app.app import build_app_element
from src.tui.app.model import AppModel
from src.tui.ink.renderer import InkRenderer
from tests.test_tui.ink._harness import Harness

_WIDTH = 80


def _renderer():
    collected: list[str] = []
    stream = io.StringIO()
    renderer = InkRenderer(stream=stream, line_callback=collected.append, height=24)
    return renderer, collected


def _plain(text: str) -> str:
    from src.renderer.ansi.helpers import strip_ansi

    return strip_ansi(text).rstrip("\n")


class TestOutputHistoryContentLines:
    def test_committed_growth_emits_content_lines_only(self):
        """新增 committed 行 → 回调新增内容行，绝不回调底部 live 行。"""
        model = AppModel()
        model.width = _WIDTH
        model.status.model_name = "deepseek-flash"
        harness = Harness(_WIDTH)
        renderer, collected = _renderer()

        def frame():
            f = harness.frame(build_app_element(model, _WIDTH), height=0)
            renderer.set_content_line_count(len(model.committed_lines))
            renderer.render(f)
            return f

        frame()
        model.append_committed("content", [AnsiLine.of("第一行内容")])
        frame()
        model.append_committed("content", [AnsiLine.of("第二行内容")])
        frame()

        emitted = [_plain(t) for t in collected]
        assert "第一行内容" in emitted
        assert "第二行内容" in emitted
        # 底部 live 区（状态栏 / 输入区 / 时间线）绝不进入输出历史
        for line in emitted:
            assert "CPU:" not in line
            assert "输入消息" not in line
            assert "空模式" not in line
            assert "◷" not in line
            assert "━" not in line

    def test_no_commit_growth_emits_nothing(self):
        """文档高度增长但不提交内容行（如状态栏刷新）→ 零回调。"""
        model = AppModel()
        model.width = _WIDTH
        harness = Harness(_WIDTH)
        renderer, collected = _renderer()

        def frame():
            f = harness.frame(build_app_element(model, _WIDTH), height=0)
            renderer.set_content_line_count(len(model.committed_lines))
            renderer.render(f)

        frame()
        collected.clear()
        # 仅在底部区制造变化（输入文本/解析行），committed 行数不变
        model.input_text = "正在输入的内容" * 3
        model.input_cursor = len(model.input_text)
        for _ in range(5):
            frame()
        assert collected == []

    def test_first_frame_committed_empty_emits_nothing(self):
        """首帧（committed 为空，仅欢迎屏 + 底部区）→ 零回调。"""
        model = AppModel()
        model.width = _WIDTH
        harness = Harness(_WIDTH)
        renderer, collected = _renderer()
        f = harness.frame(build_app_element(model, _WIDTH), height=0)
        renderer.set_content_line_count(len(model.committed_lines))
        renderer.render(f)
        assert collected == []

    def test_first_frame_with_committed_emits_all_content(self):
        """首帧已带 committed（会话恢复）→ 回调全部内容行（不含 UI 行）。"""
        model = AppModel()
        model.width = _WIDTH
        model.append_committed("content", [AnsiLine.of("恢复内容 A")])
        model.append_committed("content", [AnsiLine.of("恢复内容 B")])
        harness = Harness(_WIDTH)
        renderer, collected = _renderer()
        f = harness.frame(build_app_element(model, _WIDTH), height=0)
        renderer.set_content_line_count(len(model.committed_lines))
        renderer.render(f)
        emitted = [_plain(t) for t in collected]
        assert "恢复内容 A" in emitted
        assert "恢复内容 B" in emitted
        assert len(emitted) == len(model.committed_lines)

    def test_no_injection_means_no_callback(self):
        """未注入内容行数（独立使用 InkRenderer）→ 零回调（安全）。"""
        model = AppModel()
        model.width = _WIDTH
        harness = Harness(_WIDTH)
        renderer, collected = _renderer()
        f = harness.frame(build_app_element(model, _WIDTH), height=0)
        renderer.render(f)
        model.append_committed("content", [AnsiLine.of("内容 X")])
        f = harness.frame(build_app_element(model, _WIDTH), height=0)
        renderer.render(f)
        assert collected == []

    def test_resync_after_reflow_does_not_replay(self):
        """终端 resize（重排已提交行）→ 同步基线，不重复回调已有内容。"""
        model = AppModel()
        model.width = _WIDTH
        harness = Harness(_WIDTH)
        renderer, collected = _renderer()
        f = harness.frame(build_app_element(model, _WIDTH), height=0)
        renderer.set_content_line_count(len(model.committed_lines))
        renderer.render(f)
        model.append_committed("content", [AnsiLine.of("一段很长的内容" * 6)])
        f = harness.frame(build_app_element(model, _WIDTH), height=0)
        renderer.set_content_line_count(len(model.committed_lines))
        renderer.render(f)
        collected.clear()
        # resize：宽度收窄触发 committed_lines 重排（行数可能增加）
        model.reflow_committed(40)
        f = harness.frame(build_app_element(model, 40), height=0)
        renderer.set_content_line_count(len(model.committed_lines), resync=True)
        renderer.render(f)
        assert collected == []

    def test_reset_content_lines_allows_recount(self):
        """清屏 / 重放（CLEAR_MSGS）→ 基线归零，后续内容重新累计。"""
        model = AppModel()
        model.width = _WIDTH
        harness = Harness(_WIDTH)
        renderer, collected = _renderer()

        def frame():
            f = harness.frame(build_app_element(model, _WIDTH), height=0)
            renderer.set_content_line_count(len(model.committed_lines))
            renderer.render(f)

        frame()
        model.append_committed("content", [AnsiLine.of("清屏前内容")])
        frame()
        assert any("清屏前内容" in _plain(t) for t in collected)
        # 清屏：组件树重置 + 基线归零
        renderer.reset_content_lines()
        model.reset_display()
        frame()
        collected.clear()
        model.append_committed("content", [AnsiLine.of("清屏后内容")])
        frame()
        emitted = [_plain(t) for t in collected]
        assert "清屏后内容" in emitted
        assert all("空模式" not in line and "CPU:" not in line for line in emitted)

    def test_emitted_lines_match_committed_lines(self):
        """回调内容与 committed_lines 逐行一致（顺序 + 文本）。"""
        model = AppModel()
        model.width = _WIDTH
        harness = Harness(_WIDTH)
        renderer, collected = _renderer()
        f = harness.frame(build_app_element(model, _WIDTH), height=0)
        renderer.set_content_line_count(len(model.committed_lines))
        renderer.render(f)
        model.append_committed("user", [AnsiLine.of("> 用户问题")])
        model.append_committed("content", [AnsiLine.of("助手回答")])
        f = harness.frame(build_app_element(model, _WIDTH), height=0)
        renderer.set_content_line_count(len(model.committed_lines))
        renderer.render(f)
        emitted = [_plain(t) for t in collected]
        expected = [getattr(ln, "plain", "") for ln in model.committed_lines]
        assert emitted == expected


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
