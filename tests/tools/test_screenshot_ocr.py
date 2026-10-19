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
    find_text_ex,
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


def test_find_text_cjk_phrase_with_single_char_boxes():
    """Windows 系统 OCR 把中文按单字拆框：query 不带空格也必须命中。"""
    boxes = [_box("文", 10, 5, 14, 14), _box("件", 24, 5, 14, 14),
             _box("编", 10, 30, 14, 14)]
    result = find_text(boxes, "文件")
    assert result
    merged = result[0]
    assert merged.left == 10 and merged.top == 5
    assert merged.width == 28  # 10..38


def test_find_text_compact_phrase_without_spaces():
    """无空格 query 也能跨词命中（英文 / 数字紧邻时）。"""
    boxes = [_box("OCR", 0, 0, 30, 10), _box("123", 34, 0, 30, 10)]
    result = find_text(boxes, "OCR123")
    assert result and result[0].width == 64


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


# ── 置信度：后端不提供时不得输出无意义值 ────────────────

def test_textbox_confidence_optional_in_dict():
    """未提供置信度（后端不支持）时不输出该字段，避免被当成「置信度 0」。"""
    unknown = TextBox("OK", 1, 2, 3, 4)
    assert unknown.confidence is None
    assert "confidence" not in unknown.to_dict()
    scored = TextBox("OK", 1, 2, 3, 4, 0.87)
    assert scored.to_dict()["confidence"] == pytest.approx(0.87)


def test_windows_ocr_json_omits_confidence():
    boxes = parse_windows_ocr_json(
        '[{"text": "OK", "x": 1, "y": 2, "w": 3, "h": 4}]')
    assert boxes[0].confidence is None
    assert "confidence" not in boxes[0].to_dict()


def test_phrase_union_ignores_unknown_confidence():
    """跨词合并时忽略未知置信度（全部未知则合并结果也不带置信度）。"""
    mixed = [TextBox("保存", 0, 0, 10, 10), TextBox("文件", 12, 0, 10, 10, 0.5)]
    hits = find_text(mixed, "保存 文件")
    assert hits and hits[0].confidence == pytest.approx(0.5)
    unknown = [TextBox("保存", 0, 0, 10, 10), TextBox("文件", 12, 0, 10, 10)]
    hits = find_text(unknown, "保存 文件")
    assert hits and hits[0].confidence is None


# ── 近似匹配（OCR 易混字符） ────────────────────────────

def test_find_text_ex_reports_exact_match():
    boxes = [_box("Apply", 0, 0, 50, 12)]
    matches, mode = find_text_ex(boxes, "Apply")
    assert mode == "exact"
    assert matches and matches[0].text == "Apply"


def test_find_text_ex_falls_back_to_confusable_fold():
    """Windows OCR 把 ``All`` 认成 ``AII`` 时 query 仍应命中（近似匹配）。

    真实用例：右键菜单的 ``Select All`` 被识别成 ``Select`` + ``AII``，
    精确匹配（含跨词短语）全部落空，用户侧表现为「画面里明明有这行字，
    locate query 却查不到」。
    """
    boxes = [_box("Select", 0, 0, 60, 14), _box("AII", 65, 0, 30, 14)]
    matches, mode = find_text_ex(boxes, "Select All")
    assert mode == "fuzzy"
    assert len(matches) == 1
    assert matches[0].left == 0 and matches[0].width == 95


def test_find_text_ex_fuzzy_matches_single_box():
    """单框内的易混字符（``1`` 被识别成 ``l`` 的逆过程）也能命中。"""
    boxes = [_box("P1ay", 10, 5, 40, 14)]
    matches, mode = find_text_ex(boxes, "Play")
    assert mode == "fuzzy"
    assert matches and matches[0].left == 10


def test_find_text_ex_skips_fuzzy_for_single_character():
    """单字符查询不做近似匹配（折叠后几乎无敌，误匹配概率过高）。"""
    boxes = [_box("0", 0, 0, 8, 12)]
    matches, mode = find_text_ex(boxes, "o")
    assert matches == [] and mode == "exact"


def test_find_text_ex_keeps_case_sensitive_exact_only():
    """大小写敏感时不做易混字符折叠（折叠会转小写，语义冲突）。"""
    boxes = [_box("AII", 0, 0, 30, 14)]
    matches, mode = find_text_ex(boxes, "All", case_sensitive=True)
    assert matches == [] and mode == "exact"


def test_find_text_wrapper_still_returns_matches_only():
    """``find_text`` 保持旧签名（只返回匹配列表），近似命中也返回。"""
    boxes = [_box("Select", 0, 0, 60, 14), _box("AII", 65, 0, 30, 14)]
    assert len(find_text(boxes, "Select All")) == 1


# ── 近似匹配（分隔符：OCR 把下划线认成破折号等） ────────

def test_find_text_ex_folds_separator_variants():
    """实测 Windows OCR 把 ``bash_opt`` 识别成 ``bash—opt``：仍需命中。"""
    boxes = [_box("bash—opt", 10, 5, 60, 14)]
    matches, mode = find_text_ex(boxes, "bash_opt")
    assert mode == "fuzzy"
    assert matches and matches[0].left == 10


def test_find_text_ex_ignores_spaces_and_dots_in_fuzzy():
    """下划线 / 空格 / 点号在近似匹配时等价（``save_as`` ≈ ``Save As...``）。"""
    boxes = [_box("Save As...", 0, 0, 80, 14)]
    matches, mode = find_text_ex(boxes, "save_as")
    assert mode == "fuzzy"
    assert matches and matches[0].left == 0


def test_find_text_ex_separator_fold_still_requires_two_chars():
    """分隔符折叠后不足 2 字符时不启用近似匹配（避免误匹配）。"""
    boxes = [_box("_", 0, 0, 8, 12)]
    matches, mode = find_text_ex(boxes, "-")
    assert matches == [] and mode == "exact"


# ── 相似度近似匹配（中文 OCR 错字兜底） ─────────────────

def test_find_text_ex_similar_matches_chinese_ocr_typo():
    """实测：Windows OCR 把「确定取消按钮」拆成单字，且把「钮」认成「田」。

    字符级易混表（l/I/1 等）覆盖不了汉字错字，整句精确匹配因此全部落空；
    相似度兜底（5/6 ≈ 0.83 >= 0.8）应命中并给出合并包围盒。
    """
    boxes = [_box(ch, index * 12, 5, 10, 16)
             for index, ch in enumerate("确定取消按田")]
    matches, mode = find_text_ex(boxes, "确定取消按钮")
    assert mode == "similar"
    assert len(matches) == 1
    assert matches[0].left == 0 and matches[0].width == 60 + 10


def test_find_text_ex_similar_requires_cjk():
    """不含中文的查询不启用相似度兜底（避免英文近似误命中）。"""
    boxes = [_box("Hallo", 0, 0, 50, 12)]
    matches, mode = find_text_ex(boxes, "Hello")
    assert matches == [] and mode == "exact"


def test_find_text_ex_similar_skips_short_chinese_query():
    """小于 3 个字符的中文查询不启用相似度兜底（误匹配风险过高）。"""
    boxes = [_box("确", 0, 0, 10, 16), _box("锭", 12, 0, 10, 16)]
    matches, mode = find_text_ex(boxes, "确定")
    assert matches == [] and mode == "exact"


def test_find_text_ex_similar_rejects_unrelated_text():
    """相似度不足（无关文字）时不返回命中，仍报 exact 未找到。"""
    boxes = [_box("取消", 0, 0, 20, 16), _box("关闭窗口", 30, 0, 60, 16)]
    matches, mode = find_text_ex(boxes, "确定取消按钮")
    assert matches == [] and mode == "exact"


def test_similar_phrase_matches_prefers_highest_score():
    """同一画面有多个相近候选时按相似度排序，最匹配的排在前面。"""
    boxes = [_box("确定取消按田", 0, 0, 72, 16),
             _box("确定取取消按钮", 100, 60, 84, 16)]
    matches, mode = find_text_ex(boxes, "确定取消按钮")
    assert mode == "similar"
    # 「确定取取消按钮」多一个字（6/7 ≈ 0.92）比「确定取消按田」（5/6 ≈ 0.83）更接近
    assert matches[0].left == 100
