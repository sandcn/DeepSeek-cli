"""状态栏信息增强单元测试（2026-10-07）。

覆盖：
  - 新增 provider / context 段（数据源注入）；
  - 工具计数段新格式（运行中 ``⚙ n/m`` / 完成 ``✔ m`` / 含失败 ``✔ n/m ✖ f``）；
  - ``_build_status_runs`` 按**段级**插入分隔符（段内多 run 不被拆开）；
  - ``_fmt_tokens`` 紧凑显示。
"""

from __future__ import annotations

from types import SimpleNamespace

from src.tui.app.model import AppModel
from src.tui.app.status_bar import (
    StatusContext,
    _build_status_runs,
    _context_segment,
    _fmt_tokens,
    _provider_segment,
    _tools_segment,
)


def _ctx(model, active: bool = False, snapshot: dict | None = None) -> StatusContext:
    return StatusContext(
        model=model, status=model.status, status_active=active,
        dot_elapsed=0.0, spinner_char="\u00b7", reasoning_effort="",
        snapshot=snapshot or {},
    )


def _text(runs) -> str:
    return "".join(r.text for r in runs)


# ── 工具计数段 ──────────────────────────────────────────────


def test_tools_segment_running_format():
    m = AppModel()
    m.status.status_active = True
    m.status.tool_total = 5
    m.status.tool_count = 2
    text = _text(_tools_segment(_ctx(m, True)))
    assert "\u2699" in text          # ⚙
    assert "2/5" in text


def test_tools_segment_done_all_success():
    m = AppModel()
    m.status.status_active = True
    m.status.tool_total = 3
    m.status.tool_count = 0
    text = _text(_tools_segment(_ctx(m, True)))
    assert "\u2714" in text          # ✔
    assert "3" in text
    assert "\u2716" not in text


def test_tools_segment_with_failures():
    m = AppModel()
    m.status.status_active = True
    m.status.tool_total = 3
    m.status.tool_count = 0
    m.status.tool_fail = 1
    text = _text(_tools_segment(_ctx(m, True)))
    assert "2/3" in text             # 成功数/总数
    assert "\u2716" in text


def test_tools_segment_idle_empty():
    m = AppModel()
    m.status.tool_total = 3
    m.status.tool_count = 1
    assert _tools_segment(_ctx(m, False)) == []


# ── provider / context 段 ──────────────────────────────────


def test_provider_segment_reads_config(monkeypatch):
    monkeypatch.setattr(
        "src.config.proxy.config",
        SimpleNamespace(get=lambda key, default=None: "deepseek" if key == "provider" else default),
    )
    text = _text(_provider_segment(_ctx(AppModel())))
    assert "deepseek" in text


def test_provider_segment_empty_when_missing(monkeypatch):
    monkeypatch.setattr(
        "src.config.proxy.config",
        SimpleNamespace(get=lambda key, default=None: default),
    )
    assert _provider_segment(_ctx(AppModel())) == []


def test_context_segment_percent(monkeypatch):
    monkeypatch.setattr(
        "src.core.context_manager.get_context_usage_percent", lambda: 45.3,
    )
    monkeypatch.setattr(
        "src.config.proxy.config",
        SimpleNamespace(get=lambda key, default=None: 60000 if key == "max_context_tokens" else default),
    )
    text = _text(_context_segment(_ctx(AppModel())))
    assert "45%" in text
    assert "27.2k/60.0k" in text


def test_context_segment_none_hidden(monkeypatch):
    monkeypatch.setattr(
        "src.core.context_manager.get_context_usage_percent", lambda: None,
    )
    assert _context_segment(_ctx(AppModel())) == []


def test_context_segment_malformed_hidden(monkeypatch):
    monkeypatch.setattr(
        "src.core.context_manager.get_context_usage_percent", lambda: "bad",
    )
    assert _context_segment(_ctx(AppModel())) == []


def test_fmt_tokens():
    assert _fmt_tokens(500) == "500"
    assert _fmt_tokens(1500) == "1.5k"
    assert _fmt_tokens(60000) == "60.0k"


# ── 段级分隔 ────────────────────────────────────────────────


def test_build_status_runs_segment_level_separator():
    """工具段内 ``⚙`` 与计数之间不插入 `` · `` 分隔符。"""
    m = AppModel()
    m.status.status_active = True
    m.status.model_name = "test-model"
    m.status.tool_total = 1
    m.status.tool_count = 1
    text = _text(_build_status_runs(m, 0.0, "\u00b7", ""))
    assert "\u2699 1/1" in text
    assert "\u2699 \u00b7" not in text


def test_build_status_runs_idle_includes_provider(monkeypatch):
    monkeypatch.setattr(
        "src.config.proxy.config",
        SimpleNamespace(get=lambda key, default=None: "glm" if key == "provider" else default),
    )
    m = AppModel()
    m.status.model_name = "glm-4"
    text = _text(_build_status_runs(m, 0.0, "\u00b7", ""))
    assert "glm-4" in text
    assert "glm" in text
