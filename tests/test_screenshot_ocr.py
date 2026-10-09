"""OCR 层（``_screenshot.ocr``）测试。

覆盖：文本块模型与查找（单框 / 跨词短语 / 嵌套抑制 / 空查询报错）、
tesseract TSV 解析、Windows OCR JSON 解析、后端注册表探测与识别调度、
无可用后端时的错误提示。
"""

from __future__ import annotations

import os

import pytest

from src.tools._screenshot import ocr
from src.tools._screenshot.ocr import (
    OcrError,
    TextBox,
    find_text,
    parse_tesseract_tsv,
    parse_windows_ocr_json,
    recognize,
)


def _box(text, left=0, top=0, width=40, height=12):
    return TextBox(text, left, top, width, height)


# ── TextBox ─────────────────────────────────────────────

def test_textbox_center_and_dict():
    box = _box("保存", left=10, top=20, width=50, height=10)
    assert box.center_x == 35 and box.center_y == 25
    payload = box.to_dict()
    assert payload["text"] == "保存" and payload["center_x"] == 35


# ── 查找 ────────────────────────────────────────────────

def test_find_text_direct_match():
    boxes = [_box("文件", 0, 0), _box("保存", 50, 0), _box("编辑", 0, 30)]
    result = find_text(boxes, "保存")
    assert len(result) == 1 and result[0].text == "保存"


def test_find_text_phrase_across_words():
    boxes = [_box("保存", 0, 0, 40, 10), _box("文件", 45, 0, 40, 10),
             _box("另存", 0, 30, 40, 10)]
    result = find_text(boxes, "保存 文件")
    assert result
    merged = result[0]
    assert merged.left == 0 and merged.top == 0
    assert merged.width == 85  # 0..85
    assert "保存" in merged.text and "文件" in merged.text


def test_find_text_suppresses_nested_matches():
    boxes = [_box("OCR", 10, 0, 30, 10), _box("123", 45, 0, 30, 10)]
    merged = find_text(boxes, "OCR 123")
    # 只有最外层包围盒保留（内层被包含的丢弃），不重复
    assert len(merged) == 1


def test_find_text_rejects_empty_query():
    with pytest.raises(OcrError):
        find_text([], "   ")


def test_find_text_case_insensitive_default():
    boxes = [_box("Hello")]
    assert find_text(boxes, "hello")
    assert find_text(boxes, "HELLO", case_sensitive=True) == []


# ── tesseract TSV ───────────────────────────────────────

_TSV = (
    "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
    "1\t1\t0\t0\t0\t0\t0\t0\t100\t50\t-1\t\n"
    "5\t1\t1\t1\t1\t1\t10\t5\t20\t10\t96.5\tHello\n"
    "5\t1\t1\t1\t1\t2\t35\t5\t25\t10\t88.0\tWorld\n"
)


def test_parse_tesseract_tsv():
    boxes = parse_tesseract_tsv(_TSV)
    assert [box.text for box in boxes] == ["Hello", "World"]
    assert boxes[0].left == 10 and boxes[0].top == 5
    assert boxes[0].confidence == pytest.approx(0.965)


# ── Windows OCR JSON ────────────────────────────────────

def test_parse_windows_ocr_json_array_and_single():
    array = '[{"text":"确定","x":1,"y":2,"w":3,"h":4}]'
    boxes = parse_windows_ocr_json(array)
    assert boxes and boxes[0].text == "确定" and boxes[0].width == 3
    single = '{"text":"取消","x":5,"y":6,"w":7,"h":8}'
    assert parse_windows_ocr_json(single)[0].text == "取消"
    assert parse_windows_ocr_json("") == []
    assert parse_windows_ocr_json("null") == []


def test_parse_windows_ocr_json_invalid():
    with pytest.raises(OcrError):
        parse_windows_ocr_json("not json")


# ── 后端注册表 ──────────────────────────────────────────

class _FakeBackend:
    name = "fake"

    def __init__(self, boxes=None):
        self._boxes = boxes or [_box("OK")]

    def available(self):
        return True

    def recognize(self, image_path, lang=None):
        return list(self._boxes)


@pytest.fixture
def _isolated_registry(monkeypatch):
    monkeypatch.setattr(ocr, "_BACKENDS", [])
    monkeypatch.setattr(ocr, "_BUILTINS_LOADED", True)
    yield


def test_recognize_uses_registered_backend(_isolated_registry, monkeypatch, tmp_path):
    image = tmp_path / "shot.png"
    image.write_bytes(b"fake")
    backend = _FakeBackend([_box("确定")])
    ocr.register_ocr_backend(backend)
    boxes = recognize(str(image))
    assert [box.text for box in boxes] == ["确定"]
    assert ocr.available() is True


def test_recognize_without_backend_raises(_isolated_registry, tmp_path):
    image = tmp_path / "shot.png"
    image.write_bytes(b"fake")
    assert ocr.available() is False
    with pytest.raises(OcrError) as excinfo:
        recognize(str(image))
    assert "OCR 后端" in str(excinfo.value)


def test_recognize_missing_file_raises(_isolated_registry):
    ocr.register_ocr_backend(_FakeBackend())
    with pytest.raises(OcrError) as excinfo:
        recognize("/no/such/file.png")
    assert "不存在" in str(excinfo.value)


def test_register_backend_undo(_isolated_registry):
    undo = ocr.register_ocr_backend(_FakeBackend())
    assert ocr.available() is True
    undo()
    assert ocr.available() is False


# ── Windows 路径转换 ────────────────────────────────────

def test_windows_path_returns_usable_string():
    converted = ocr._windows_path("/tmp/example.png")
    assert converted and isinstance(converted, str)
    if not ocr.sys.platform.startswith(("cygwin", "msys")):
        assert converted == os.path.abspath("/tmp/example.png")
