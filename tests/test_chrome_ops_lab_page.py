"""Chrome 复杂操作测试页（test_chrome/ops_lab.html）的结构回归测试。

该页面是 bash_opt 真实 Chrome 操作验证的载体（配套脚本
test_chrome/verify_bash_opt_ops.py）。本测试只校验页面素材本身的结构契约，
保证后续修改不会悄悄丢掉被验证的交互点：

覆盖：文件可读、必需交互元素 id 齐全、关键事件监听与状态同步函数存在、
画布尺寸属性、离线可用（不引用任何外部 URL）、可被标准解析器解析。
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

PAGE = Path(__file__).resolve().parent.parent / "test_chrome" / "ops_lab.html"

REQUIRED_IDS = (
    "tabs",
    "pane-inter",
    "pane-canvas",
    "pane-list",
    "btnA",
    "btnReset",
    "clickN",
    "dblN",
    "ctxN",
    "ctxPick",
    "ctxmenu",
    "txt",
    "txtOut",
    "sel",
    "selOut",
    "rng",
    "rngOut",
    "cb1",
    "cb2",
    "cbOut",
    "planOut",
    "pad",
    "knob",
    "knobOut",
    "cv",
    "btnClear",
    "strokeOut",
    "clearOut",
    "list",
    "orderOut",
    "inner",
    "innerOut",
    "bottom",
    "log",
    "stPane",
    "stHover",
    "stClickXY",
    "stWheel",
    "stKey",
    "stLastKey",
    "stCombo",
    "stTxt",
    "stSel",
    "stRng",
    "stCb",
    "stPlan",
    "stKnob",
    "stDragMoves",
    "stSize",
    "stScrollY",
    "stCenters",
)

REQUIRED_JS_SNIPPETS = (
    "addEventListener('dblclick'",
    "addEventListener('contextmenu'",
    "addEventListener('wheel'",
    "addEventListener('keydown'",
    "addEventListener('mousemove'",
    "addEventListener('change'",
    "'input'",
    "getContext('2d')",
    "function syncTitle",
    "function refreshCenters",
    "pointerdown",
    "pointermove",
)


class _Collector(HTMLParser):
    """收集 id / class / 标签名，并记录解析过程中的结构错误。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ids: set[str] = set()
        self.classes: set[str] = set()
        self.tags: set[str] = set()
        self.errors: list[str] = []

    def handle_starttag(self, tag, attrs):
        self.tags.add(tag)
        for name, value in attrs:
            if name == "id" and value:
                if value in self.ids:
                    self.errors.append(f"重复 id: {value}")
                self.ids.add(value)
            elif name == "class" and value:
                self.classes.update(value.split())

    def error(self, message):  # pragma: no cover - 标准库回调
        self.errors.append(message)


@pytest.fixture(scope="module")
def page_text() -> str:
    assert PAGE.is_file(), f"测试页缺失: {PAGE}"
    return PAGE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def parsed(page_text: str) -> _Collector:
    collector = _Collector()
    collector.feed(page_text)
    collector.close()
    return collector


def test_page_parses_without_duplicate_ids(parsed: _Collector) -> None:
    assert parsed.errors == []
    assert "html" in parsed.tags
    assert "script" in parsed.tags
    assert "canvas" in parsed.tags


@pytest.mark.parametrize("element_id", REQUIRED_IDS)
def test_required_element_present(parsed: _Collector, element_id: str) -> None:
    assert element_id in parsed.ids, f"缺少元素 id={element_id}"


@pytest.mark.parametrize("snippet", REQUIRED_JS_SNIPPETS)
def test_required_js_snippet_present(page_text: str, snippet: str) -> None:
    assert snippet in page_text, f"缺少脚本片段: {snippet}"


def test_canvas_has_explicit_size(page_text: str) -> None:
    match = re.search(r'<canvas[^>]*id="cv"[^>]*>', page_text)
    assert match, "未找到 canvas#cv"
    tag = match.group(0)
    assert 'width="520"' in tag
    assert 'height="260"' in tag


def test_title_panel_reports_viewport(page_text: str) -> None:
    assert "devicePixelRatio" in page_text
    assert "window.innerWidth" in page_text


def test_page_is_offline_only(page_text: str) -> None:
    """页面必须完全离线可用：不引用外链脚本 / 样式 / 图片。"""
    external = re.findall(r"""(?:src|href)\s*=\s*["'](https?:)?//""", page_text)
    assert external == []


def test_verify_script_present() -> None:
    script = PAGE.parent / "verify_bash_opt_ops.py"
    assert script.is_file()
    text = script.read_text(encoding="utf-8")
    for op in ("click", "drag", "scroll", "key", "type"):
        assert f'"{op}"' in text, f"验证脚本未覆盖 {op}"


def test_verify_script_compiles() -> None:
    import py_compile

    script = PAGE.parent / "verify_bash_opt_ops.py"
    py_compile.compile(str(script), doraise=True, cfile=str(script) + ".pyc")
    Path(str(script) + ".pyc").unlink(missing_ok=True)


def test_popup_verify_script_present() -> None:
    """弹层（下拉浮层）点击验证脚本存在且覆盖关键断言点。"""
    script = PAGE.parent / "verify_popup_click.py"
    assert script.is_file()
    text = script.read_text(encoding="utf-8")
    assert "tool_window" in text
    assert "client_area" in text
    assert "window=selector" in text
    assert "sel=上海" in text


def test_popup_verify_script_compiles() -> None:
    import py_compile

    script = PAGE.parent / "verify_popup_click.py"
    py_compile.compile(str(script), doraise=True, cfile=str(script) + ".pyc")
    Path(str(script) + ".pyc").unlink(missing_ok=True)
