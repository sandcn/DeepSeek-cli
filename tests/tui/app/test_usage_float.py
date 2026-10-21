"""``/usage`` 右侧浮窗 — 规格、宽度自适应与内容行构建。"""

from __future__ import annotations

from src.tui.app.floating import (
    USAGE_FLOAT_MAX_WIDTH,
    USAGE_FLOAT_WIDTH_RATIO,
    USAGE_WINDOW_ID,
    FloatingWindow,
    UsageFloatingWindow,
    _compact_usage_sections,
    _usage_content_rows,
    usage_window_spec,
)


def _sections():
    return [
        {
            "title": "Token 用量",
            "rows": [
                ("输入（未命中）", "1.2kt", "info", None),
                ("输出", "3.4kt", "info", None),
            ],
        },
        {
            "title": "上下文窗口",
            "rows": [
                ("使用率", "", "ok", 0.42),
            ],
        },
    ]


def test_usage_window_spec_is_right_side_frame_refreshed():
    spec = usage_window_spec(lambda: _sections())
    assert spec.id == USAGE_WINDOW_ID
    assert spec.x == "right"
    assert spec.bottom == "bottom_area"
    assert spec.width is None
    assert spec.width_ratio == USAGE_FLOAT_WIDTH_RATIO
    assert spec.max_width == USAGE_FLOAT_MAX_WIDTH
    # 每帧刷新（用户选择「最实时」）——不声明定时刷新间隔。
    assert spec.refresh_interval is None
    assert spec.priority > 100


def test_usage_window_width_follows_terminal_and_caps_at_max():
    spec = usage_window_spec(lambda: [])

    wide = FloatingWindow({"window": spec, "model": None, "width": 120})
    assert wide.props["width"] == USAGE_FLOAT_MAX_WIDTH

    narrow = FloatingWindow({"window": spec, "model": None, "width": 80})
    assert narrow.props["width"] == 48


def test_usage_content_rows_render_all_sections_and_values():
    rows = _usage_content_rows(_sections(), 44)
    texts = ["".join(run.text for run in row) for row in rows]
    joined = "\n".join(texts)
    assert "Token 用量" in joined
    assert "输入（未命中）" in joined
    assert "1.2kt" in joined
    assert "上下文窗口" in joined
    assert "42%" in joined


def test_usage_float_is_compact_to_avoid_top_clipping():
    compact = _compact_usage_sections([
        {
            "title": "Token 用量",
            "rows": [
                ("输入（未命中）", "1.2kt", "info", None),
                ("输入（缓存命中）", "0t", "ok", None),
                ("输出", "3.4kt", "info", None),
                ("合计", "4.6kt", "info", None),
                ("API 调用", "2", "info", None),
                ("输出速度", "9.0 tok/s", "info", None),
            ],
        },
        {
            "title": "费用估算",
            "rows": [
                ("输入费用", "$0.0001", "info", None),
                ("输出费用", "$0.0002", "info", None),
                ("合计", "$0.0003", "warn", None),
                ("缓存节省", "$0.0000", "ok", None),
                ("模型", "test-model", "info", None),
            ],
        },
    ])
    token_rows = compact[0]["rows"]
    assert len(token_rows) == 2
    assert token_rows[0][0] == "输入/输出"
    assert "2 次" in token_rows[1][1]
    cost_rows = compact[1]["rows"]
    assert len(cost_rows) == 2
    assert "省" in cost_rows[1][1]


def test_usage_component_uses_provider_and_hint():
    element = UsageFloatingWindow(
        {"width": 44, "window": None}, lambda: _sections(),
    )
    text = "".join(
        "".join(run.text for run in child.props.get("styled", ()))
        for child in element.children
    )
    assert "Token 用量" in text
    assert "/usage 关闭" in text
