"""窗口选择增强（正则 / 进程名 / 模糊）测试。

覆盖：新选择器解析（title~ / class~ / re / process / fuzzy）、非法正则报错、
匹配语义（正则、进程名归一化、模糊子串优先与相似度回退）、展示字段。
"""

from __future__ import annotations

import pytest

from src.tools._screenshot.windows import (
    FUZZY_THRESHOLD,
    SelectorError,
    WindowInfo,
    filter_windows,
    parse_selector,
)


def _win(handle, title="", class_name="", process_name="", order=0,
         visible=True, minimized=False):
    return WindowInfo(handle=handle, pid=1, title=title, class_name=class_name,
                      width=100, height=50, left=0, top=0, order=order,
                      visible=visible, minimized=minimized,
                      process_name=process_name)


@pytest.fixture
def windows():
    return [
        _win(1, "Chrome — 设置", "Chrome_WidgetWin_1", "chrome.exe", order=0),
        _win(2, "Notepad", "Notepad", "notepad.exe", order=1),
        _win(3, "计算器", "WinUIDesktopWin32WindowClass", "Calculator.exe", order=2),
        _win(4, "隐藏辅助", "Chrome_WidgetWin_0", "chrome.exe", order=3,
             visible=False),
    ]


# ── 解析 ────────────────────────────────────────────────

def test_parse_regex_selectors():
    assert parse_selector("title~:^Chrome").kind == "title_re"
    assert parse_selector("class~:#32770").kind == "class_re"
    assert parse_selector("re:chrome|firefox").kind == "regex"
    assert parse_selector("regex:设置").kind == "regex"


def test_parse_process_and_fuzzy_selectors():
    assert parse_selector("process:chrome.exe").kind == "process"
    assert parse_selector("proc:notepad").kind == "process"
    assert parse_selector("fuzzy:notepd").kind == "fuzzy"


def test_parse_rejects_invalid_regex():
    with pytest.raises(SelectorError) as excinfo:
        parse_selector("title~:([unclosed")
    assert "正则非法" in str(excinfo.value)


# ── 匹配 ────────────────────────────────────────────────

def test_title_regex_matches(windows):
    matched = filter_windows(windows, parse_selector("title~:^Chrome"))
    assert [item.handle for item in matched] == [1]


def test_class_regex_matches(windows):
    matched = filter_windows(windows, parse_selector("class~:WidgetWin"))
    assert matched and matched[0].handle == 1


def test_regex_matches_title_or_class(windows):
    matched = filter_windows(windows, parse_selector("re:设置|Notepad"))
    assert [item.handle for item in matched] == [1, 2]


def test_process_match_ignores_exe_suffix(windows):
    by_name = filter_windows(windows, parse_selector("process:chrome"))
    # 隐藏辅助窗口参与匹配，但可操作窗口优先
    assert by_name and by_name[0].handle == 1
    by_dot = filter_windows(windows, parse_selector("process:Calculator.exe"))
    assert [item.handle for item in by_dot] == [3]


def test_fuzzy_substring_first(windows):
    matched = filter_windows(windows, parse_selector("fuzzy:计算"))
    assert [item.handle for item in matched] == [3]


def test_fuzzy_similarity_fallback(windows):
    matched = filter_windows(windows, parse_selector("fuzzy:notepd"))
    assert matched and matched[0].handle == 2


def test_fuzzy_threshold_value():
    assert 0 < FUZZY_THRESHOLD <= 1


# ── 展示 ────────────────────────────────────────────────

def test_summary_and_dict_include_process_name(windows):
    item = windows[0]
    assert "proc=chrome.exe" in item.summary()
    payload = item.to_dict()
    assert payload["process_name"] == "chrome.exe"
