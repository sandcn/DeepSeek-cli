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
    _model_segment,
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


def _line_text(line) -> str:
    """``Line`` 对象 → 纯文本（``Line.runs`` 拼接）。"""
    return "".join(run.text for run in line.runs)


# ── 模型名旁的多模态读图徽标（「图」） ────────────────────


def test_model_segment_multimodal_badge():
    """开启「多模态读图」→ 模型名旁显示「图」；关闭 → 不显示。"""
    m = AppModel()
    m.status.model_name = "vision-model"
    on = _text(_model_segment(_ctx_with_multimodal(m, True)))
    off = _text(_model_segment(_ctx_with_multimodal(m, False)))
    assert "vision-model" in on and "\u56fe" in on
    assert "vision-model" in off and "\u56fe" not in off


def test_build_status_runs_multimodal_badge_and_effort_order():
    """状态行顺序：模型名 → 图 → [思考级别]（「图」在模型名隔壁）。"""
    m = AppModel()
    m.status.model_name = "vision-model"
    text = _text(_build_status_runs(m, 0.0, "\u00b7", "high", True))
    assert "vision-model \u56fe [high]" in text


def test_model_segment_thinking_off_replaces_effort():
    """Ctrl+T 关闭思考 → 模型名后显示 [off]（不再显示 [high]）。"""
    m = AppModel()
    m.status.model_name = "deepseek-flash"
    ctx = StatusContext(
        model=m, status=m.status, status_active=False,
        dot_elapsed=0.0, spinner_char="\u00b7", reasoning_effort="high",
        snapshot={}, thinking_enabled=False,
    )
    text = _text(_model_segment(ctx))
    assert "deepseek-flash [off]" in text
    assert "[high]" not in text


def test_build_status_runs_thinking_off():
    m = AppModel()
    m.status.model_name = "deepseek-flash"
    text = _text(_build_status_runs(m, 0.0, "\u00b7", "high", False, False))
    assert "[off]" in text and "[high]" not in text


def _ctx_with_multimodal(model, value: bool) -> StatusContext:
    return StatusContext(
        model=model, status=model.status, status_active=False,
        dot_elapsed=0.0, spinner_char="\u00b7", reasoning_effort="",
        snapshot={}, multimodal=value,
    )


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


# ── 会话标题生成中：显示在「上下文百分比」旁（2026-10-20 用户需求） ──


def test_title_generating_shown_after_context_percent():
    """生成中：紧跟 main 百分比显示「✎ 标题」（用户最常看的位置）。"""
    from src.tui.app.input_area import _build_bg_task_prefix

    line = _build_bg_task_prefix(45.3, 0, 0, 0, title_generating=True)
    text = _line_text(line)
    assert "45.3%" in text
    assert "\u270e \u6807\u9898" in text
    assert text.index("45.3%") < text.index("\u270e")


def test_title_generating_hidden_when_idle():
    from src.tui.app.input_area import _build_bg_task_prefix

    line = _build_bg_task_prefix(45.3, 0, 0, 0, title_generating=False)
    assert "\u270e" not in _line_text(line)


def test_mode_line_carries_title_generating():
    from src.tui.app.input_area import _build_mode_line

    assert "\u270e \u6807\u9898" in _line_text(
        _build_mode_line(80, "standard", 12.0, title_generating=True),
    )
    assert "\u270e" not in _line_text(_build_mode_line(80, "standard", 12.0))


def test_title_segment_removed_from_status_bar():
    """标题状态已从状态栏段迁到上下文百分比旁（用户要求），段不再存在。"""
    from src.tui.app import status_bar
    from src.tui.app._status_segments import active_segment_ids, builtin_segment_ids

    assert "title" not in builtin_segment_ids()
    assert "title" not in active_segment_ids()
    assert not hasattr(status_bar, "_title_segment")


# ── 标题请求的 token / tok/s 在 status 行可见（用户需求 2026-10-20） ──


def test_tokens_visible_while_title_generating():
    import time

    from src.tui.app.status_bar import _tokens_visible

    m = AppModel()
    m.status.title_generating = True
    assert _tokens_visible(False, m.status, time.monotonic()) is True


def test_tokens_visible_title_grace_window():
    import time

    from src.tui.app.status_bar import _TITLE_TOKEN_GRACE_SEC, _tokens_visible

    m = AppModel()
    now = time.monotonic()
    m.status.title_last_end_ts = now - 1.0
    assert _tokens_visible(False, m.status, now) is True
    assert _tokens_visible(
        False, m.status, now + _TITLE_TOKEN_GRACE_SEC + 1,
    ) is False


def test_tokens_visible_idle_without_title():
    import time

    from src.tui.app.status_bar import _tokens_visible

    m = AppModel()
    assert _tokens_visible(False, m.status, time.monotonic()) is False


def test_status_runs_show_title_token_and_speed(monkeypatch):
    """标题生成中：状态行显示 ◆ 总tok / » tok/s（流式请求的真实用量）。"""
    import src.tui.app.status_bar as sb

    monkeypatch.setattr(
        sb, "_snapshot",
        lambda model, ttl=sb._SNAPSHOT_TTL: {
            "total_tokens": 1234, "per_second_speed": 42.0,
        },
    )
    m = AppModel()
    m.status.model_name = "test-model"
    m.status.title_generating = True
    text = _text(_build_status_runs(m, 0.0, "\u00b7", ""))
    assert "\u25c6 1.2kt" in text
    assert "42" in text
