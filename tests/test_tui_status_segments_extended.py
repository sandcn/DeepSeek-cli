"""状态栏信息增强单元测试（2026-10-07）。

覆盖：
  - 工具计数段新格式（运行中 ``⚙ n/m`` / 完成 ``✔ m`` / 含失败 ``✔ n/m ✖ f``）；
  - ``_build_status_runs`` 按**段级**插入分隔符（段内多 run 不被拆开）；
  - ``provider`` / ``theme`` / ``context`` 段已从内置段删除（2026-10-07 用户需求）。
"""

from __future__ import annotations

from types import SimpleNamespace

from src.tui.app.model import AppModel
from src.tui.app.status_bar import (
    StatusContext,
    _build_status_runs,
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


# ── 已删除的段（provider / theme / context）───────────────


def test_removed_segments_absent_from_builtin():
    from src.tui.app._status_segments import builtin_segment_ids

    ids = set(builtin_segment_ids())
    assert "provider" not in ids
    assert "theme" not in ids
    assert "context" not in ids


def test_removed_segment_handlers_gone():
    import src.tui.app.status_bar as sb

    assert not hasattr(sb, "_provider_segment")
    assert not hasattr(sb, "_theme_segment")
    assert not hasattr(sb, "_context_segment")
    assert not hasattr(sb, "_fmt_tokens")


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


def test_build_status_runs_excludes_removed_segments(monkeypatch):
    """空闲状态栏不再包含 provider/theme/context 段。"""
    monkeypatch.setattr(
        "src.config.proxy.config",
        SimpleNamespace(get=lambda key, default=None: "glm" if key == "provider" else default),
    )
    m = AppModel()
    m.status.model_name = "glm-4"
    text = _text(_build_status_runs(m, 0.0, "\u00b7", ""))
    assert "glm-4" in text
    assert "\u2b21" not in text   # ⬡ provider
    assert "\u25d0" not in text   # ◐ theme
    assert "\u25a3" not in text   # ▣ context
