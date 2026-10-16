"""欢迎屏卡片化单元测试（2026-10-07 美化）。

覆盖：
  - 卡片边框（顶/底/左右竖线）与行宽不变量（行宽 <= 预算）；
  - 窄屏回退无边框平铺（行宽仍 <= 预算）；
  - 运行环境信息新增「分支 / 上下文」项；
  - chat_view._welcome_rows / _welcome_elements 与 apply._do_splash 均卡片化；
  - 边框样式与内容样式分离（边框行不含 ◆/›，活跃期呼吸不污染边框）。
"""

from __future__ import annotations

import pytest

from src.tui.app._welcome import (
    _CARD_MIN_WIDTH,
    display_width,
    environment_info,
    welcome_card_rows,
)
from src.tui.app.model import AppModel


def _model() -> AppModel:
    m = AppModel()
    m.status.model_name = "deepseek-v4-pro"
    return m


def _plain(row) -> str:
    return "".join(text for text, _ in row)


def _row_width(row) -> int:
    return sum(display_width(text) for text, _ in row)


@pytest.fixture(autouse=True)
def _clear_git_cache():
    from src.tui.app import _welcome

    _welcome._GIT_CACHE[:] = ["", 0.0, ""]
    yield
    _welcome._GIT_CACHE[:] = ["", 0.0, ""]


class TestWelcomeCard:
    def test_width_invariant(self):
        for width in (40, 60, 80, 100):
            for row in welcome_card_rows(_model(), width):
                assert _row_width(row) <= width, f"width={width} 行超宽"

    def test_has_card_borders(self):
        rows = welcome_card_rows(_model(), 80)
        texts = [_plain(r) for r in rows]
        assert texts[0].strip().startswith("\u256d"), "首行应为卡片顶边框"
        assert texts[-1].strip().startswith("\u2570"), "末行应为卡片底边框"
        # 内容行带左右竖线
        assert any("\u2502" in t for t in texts[1:-1])

    def test_border_lines_have_no_bullets(self):
        """边框行不含 ◆/›（内容符号不进入边框，活跃期呼吸不污染边框）。"""
        rows = welcome_card_rows(_model(), 80)
        border_rows = [rows[0], rows[-1]]
        for row in border_rows:
            text = _plain(row)
            assert "\u25c6" not in text
            assert "\u203a" not in text

    def test_narrow_falls_back_flat(self):
        width = _CARD_MIN_WIDTH - 1
        rows = welcome_card_rows(_model(), width)
        texts = [_plain(r) for r in rows]
        assert not any("\u256d" in t for t in texts), "窄屏不应渲染边框"
        for row in rows:
            assert _row_width(row) <= width

    def test_info_rows_contain_model(self):
        rows = welcome_card_rows(_model(), 80)
        text = "\n".join(_plain(r) for r in rows)
        assert "deepseek-v4-pro" in text
        assert "\u25c6" in text
        assert "\u203a" in text

    def test_environment_info_has_branch(self):
        info = dict(environment_info(_model()))
        assert "模型" in info
        # 项目根为 git 仓库 → 分支可解析
        assert "分支" in info
        assert info["分支"]

    def test_git_branch_none_outside_repo(self, monkeypatch):
        from src.tui.app import _welcome

        monkeypatch.setattr(_welcome.os, "getcwd", lambda: "/definitely/not/exists")
        _welcome._GIT_CACHE[:] = ["", 0.0, ""]
        assert _welcome._git_branch() == ""


class TestWelcomeIntegration:
    def test_chat_view_rows_card(self):
        from src.tui.app import chat_view

        chat_view._WELCOME_STATIC_CACHE[0] = None
        chat_view._WELCOME_STATIC_CACHE[1] = None
        rows = chat_view._welcome_rows(_model(), False, 100)
        text = "\n".join("".join(r.text for r in row) for row in rows)
        assert "\u256d" in text and "\u2570" in text

    def test_splash_uses_card(self):
        from src.tui._const import SplashCmd
        from src.tui.app.apply import _do_splash

        model = _model()
        model.width = 90
        _do_splash(model, SplashCmd())
        block = model.blocks[-1]
        text = "\n".join(line.plain for line in block.lines)
        assert "\u256d" in text
        assert "deepseek-v4-pro" in text
