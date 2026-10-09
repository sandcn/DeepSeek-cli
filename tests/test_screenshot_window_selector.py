"""窗口目录与选择器（``_screenshot.windows``）单元测试。

覆盖：选择器解析（main/active/#N/handle/title/class/pid/popup/dialog/all、
裸数字与裸字符串、非法输入）、主窗口排序与标注、按选择器过滤与挑选
（含无匹配时的可读错误）、Z 序排序、清单序列化与提示文本、窗口控制请求
的解析与参数校验。
"""

from __future__ import annotations

import pytest

from src.tools._screenshot.windows import (
    DEFAULT_SELECTOR,
    SELECTOR_KINDS,
    WINDOW_CONTROL_ACTIONS,
    SelectorError,
    WindowControlRequest,
    WindowInfo,
    WindowSelector,
    describe_windows,
    filter_windows,
    main_rank,
    main_window,
    mark_main,
    parse_control_request,
    parse_selector,
    pick_window,
    sort_by_z,
    window_hint,
)


def _win(handle, *, pid=10, title="", class_name="Chrome_WidgetWin_1",
         width=800, height=600, left=0, top=0, tool=False, minimized=False,
         visible=True, foreground=False, order=0, main=False):
    return WindowInfo(
        handle=handle, pid=pid, title=title, class_name=class_name,
        width=width, height=height, left=left, top=top,
        tool_window=tool, minimized=minimized, visible=visible,
        foreground=foreground, order=order, main=main,
    )


@pytest.fixture
def sample_windows():
    """浏览器进程的典型窗口集合（主窗口 + 右侧弹出菜单 + 无标题浮层）。"""
    return [
        _win(0x1001, title="设置 - Google Chrome", width=1280, height=900,
             order=0, foreground=True),
        _win(0x1002, title="", class_name="Chrome_WidgetWin_1",
             width=320, height=240, left=700, top=400, order=1, tool=True),
        _win(0x1003, title="参数设置", class_name="#32770",
             width=420, height=260, left=300, top=200, order=2),
    ]


# ── 属性与序列化 ─────────────────────────────────────────

def test_window_info_properties_and_dict():
    info = _win(0x1A2B, title="标题", width=100, height=50, left=7, top=8)
    assert info.area == 5000
    assert info.handle_hex == "0x1A2B"
    payload = info.to_dict()
    assert payload["handle"] == 0x1A2B
    assert payload["handle_hex"] == "0x1A2B"
    assert payload["class"] == "Chrome_WidgetWin_1"
    assert payload["index"] == 1
    assert "标题" in payload["summary"]


def test_window_info_summary_marks_flags():
    info = _win(1, title="", minimized=True, tool=True, main=True, foreground=True)
    summary = info.summary()
    assert "「无标题」" in summary
    assert "main" in summary and "foreground" in summary
    assert "minimized" in summary and "tool" in summary


# ── 选择器解析 ───────────────────────────────────────────

def test_parse_selector_defaults_to_main():
    for value in (None, "", "   ", "main", "MAIN", "primary", "default"):
        selector = parse_selector(value)
        assert selector.kind == "main"
        assert selector.is_default
    assert parse_selector(None).raw == DEFAULT_SELECTOR


def test_parse_selector_active_and_keywords():
    assert parse_selector("active").kind == "active"
    assert parse_selector("foreground").kind == "active"
    assert parse_selector("popup").kind == "popup"
    assert parse_selector("menu").kind == "popup"
    assert parse_selector("dialog").kind == "dialog"
    assert parse_selector("all").kind == "all"
    assert parse_selector("*").kind == "all"


def test_parse_selector_index_and_handle():
    index = parse_selector("#3")
    assert index.kind == "index" and index.value == 3
    handle = parse_selector("handle:0x1a2b")
    assert handle.kind == "handle" and handle.value == 0x1A2B
    assert parse_selector("id:4096").value == 4096
    assert parse_selector("1234").kind == "handle"
    assert parse_selector("1234").value == 1234


def test_parse_selector_title_class_pid():
    assert parse_selector("title:设置").value == "设置"
    assert parse_selector("class:Chrome_WidgetWin_1").kind == "class"
    assert parse_selector("pid:4321").kind == "pid"
    assert parse_selector("pid:4321").value == 4321
    # 裸字符串按标题匹配（最常用写法）
    bare = parse_selector("设置")
    assert bare.kind == "title" and bare.value == "设置"


def test_parse_selector_rejects_bad_values():
    with pytest.raises(SelectorError):
        parse_selector("#0")
    with pytest.raises(SelectorError):
        parse_selector("handle:abc")
    with pytest.raises(SelectorError):
        parse_selector("title:")   # 缺取值
    with pytest.raises(SelectorError):
        parse_selector(True)       # 布尔值非法
    with pytest.raises(SelectorError):
        WindowSelector("nope")


def test_selector_describe_returns_raw_text():
    assert parse_selector("title:设置").describe() == "title:设置"
    assert parse_selector(None).describe() == "main"


# ── 主窗口 ───────────────────────────────────────────────

def test_main_window_prefers_untooled_titled_largest(sample_windows):
    assert main_window(sample_windows).handle == 0x1001
    assert main_window([]) is None


def test_main_rank_prefers_restored_and_titled():
    minimized = _win(1, title="a", width=1600, height=900, minimized=True)
    restored = _win(2, title="a", width=800, height=600)
    assert main_window([minimized, restored]).handle == 2


def test_mark_main_flags_single_window(sample_windows):
    marked = mark_main(sample_windows)
    assert [item.main for item in marked] == [True, False, False]
    # 原列表不被修改（frozen dataclass + 新列表）
    assert not any(item.main for item in sample_windows)


# ── 过滤与挑选 ───────────────────────────────────────────

def test_filter_by_index_follows_z_order(sample_windows):
    assert filter_windows(sample_windows, parse_selector("#1"))[0].handle == 0x1001
    assert filter_windows(sample_windows, parse_selector("#3"))[0].handle == 0x1003
    assert filter_windows(sample_windows, parse_selector("#9")) == []


def test_filter_by_title_class_handle_pid(sample_windows):
    assert filter_windows(sample_windows, parse_selector("title:chrome"))[0].handle == 0x1001
    assert filter_windows(sample_windows, parse_selector("class:#32770"))[0].handle == 0x1003
    assert filter_windows(sample_windows, parse_selector("handle:0x1002"))[0].handle == 0x1002
    assert filter_windows(sample_windows, parse_selector("pid:10")) == sample_windows


def test_filter_title_falls_back_to_class(sample_windows):
    """标题匹配不到时按类名兜底（模型常把类名当标题用）。"""
    matched = filter_windows(sample_windows, parse_selector("Chrome_WidgetWin_1"))
    assert {item.handle for item in matched} == {0x1001, 0x1002}


def test_filter_popup_prefers_untitled_non_main(sample_windows):
    marked = mark_main(sample_windows)
    assert filter_windows(marked, parse_selector("popup"))[0].handle == 0x1002


def test_filter_dialog_matches_class_hint(sample_windows):
    marked = mark_main(sample_windows)
    assert filter_windows(marked, parse_selector("dialog"))[0].handle == 0x1003


def test_filter_active_uses_foreground_flag():
    windows = [_win(1), _win(2, order=1, foreground=True)]
    assert filter_windows(windows, parse_selector("active"))[0].handle == 2
    assert filter_windows([_win(3)], parse_selector("active")) == []


def test_pick_window_returns_first_match(sample_windows):
    assert pick_window(sample_windows, None).handle == 0x1001
    assert pick_window(sample_windows, "#2").handle == 0x1002
    assert pick_window(sample_windows, WindowSelector("handle", 0x1003)).handle == 0x1003


def test_pick_window_error_lists_candidates(sample_windows):
    with pytest.raises(SelectorError) as excinfo:
        pick_window(sample_windows, "title:不存在")
    message = str(excinfo.value)
    assert "没有匹配的窗口" in message
    assert "设置 - Google Chrome" in message


def test_pick_window_rejects_empty_and_all():
    with pytest.raises(SelectorError):
        pick_window([], "main")
    with pytest.raises(SelectorError) as excinfo:
        pick_window([_win(1)], "all")
    assert "具体窗口" in str(excinfo.value)


# ── 排序与清单 ───────────────────────────────────────────

def test_sort_by_z_orders_by_enumeration_index(sample_windows):
    shuffled = [sample_windows[2], sample_windows[0], sample_windows[1]]
    assert [item.handle for item in sort_by_z(shuffled)] == [0x1001, 0x1002, 0x1003]


def test_describe_windows_truncates(sample_windows):
    described = describe_windows(sample_windows, limit=2)
    assert len(described) == 2
    assert described[0]["handle"] == 0x1001


def test_window_hint_mentions_total_when_truncated(sample_windows):
    hint = window_hint(sample_windows, limit=1)
    assert "共 3 个窗口" in hint
    assert window_hint([]) == "（无）"


def test_selector_kinds_cover_documented_forms():
    assert set(SELECTOR_KINDS) == {
        "main", "active", "index", "handle", "title", "class", "pid",
        "popup", "dialog", "all", "title_re", "class_re", "regex", "process",
        "fuzzy",
    }


# ── 窗口控制请求 ─────────────────────────────────────────

def test_parse_control_request_accepts_aliases_and_numbers():
    request = parse_control_request("MAX", window="#2")
    assert request.action == "maximize"
    assert request.selector == "#2"
    fit = parse_control_request("move_resize", x="10", y="20",
                                width="300", height="200")
    assert fit.action == "fit"
    assert (fit.x, fit.y, fit.width, fit.height) == (10, 20, 300, 200)


def test_parse_control_request_requires_geometry():
    with pytest.raises(SelectorError):
        parse_control_request("move", x=1)
    with pytest.raises(SelectorError):
        parse_control_request("resize", width=200)
    with pytest.raises(SelectorError):
        parse_control_request("", x=1, y=1)
    with pytest.raises(SelectorError):
        parse_control_request("teleport")


def test_control_request_validates_size_and_serializes():
    with pytest.raises(SelectorError):
        WindowControlRequest(action="resize", width=0, height=10)
    with pytest.raises(SelectorError):
        WindowControlRequest(action="unknown")
    payload = WindowControlRequest(action="fit", selector="#1", x=1, y=2,
                                   width=3, height=4).to_dict()
    assert payload == {"window_action": "fit", "x": 1, "y": 2, "width": 3, "height": 4}


def test_control_actions_cover_documented_set():
    assert set(WINDOW_CONTROL_ACTIONS) == {
        "activate", "maximize", "minimize", "restore", "close",
        "move", "resize", "fit",
        # 2026-10-08 新增：置顶 / 取消置顶（操作期间防遮挡）
        "always_on_top", "not_on_top",
    }
    with pytest.raises(SelectorError):
        parse_control_request("maximize", window="#0")
