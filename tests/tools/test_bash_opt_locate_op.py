"""bash_opt 图像 / 文字定位（op=locate）测试。

覆盖：schema 暴露、模板匹配定位（坐标与输入 op 同源 + 屏幕坐标换算）、
OCR 文字定位、crop 限定区域、参数校验与错误提示。
"""

from __future__ import annotations

import json

from src.tools import bash_opt as bash_opt_module
from src.tools._screenshot import png
from src.tools._screenshot.ocr import TextBox
from src.tools._window_input.geometry import WindowFrame
from src.tools.bash_opt import BashOptFunc


class _FakeAgent:
    def __init__(self, records: dict):
        self._background_tasks = records
        self._subagent_tasks = {}


def _record(pid=4321):
    return {"read_buffer": "", "status": "running", "done": False, "pid": pid,
            "task": None}


def _make_png(path, width, height, fill=(0, 0, 0), block=None):
    pixels = bytearray(bytes(fill) * (width * height))
    if block:
        bx, by, bw, bh, color = block
        for row in range(by, by + bh):
            for col in range(bx, bx + bw):
                offset = (row * width + col) * 3
                pixels[offset:offset + 3] = bytes(color)
    path.write_bytes(png.encode_png_rgb(width, height, bytes(pixels)))
    return str(path)


async def _fake_frame(self, pid, window):
    return WindowFrame(100, 50, 800, 600)


def _patch_shot(monkeypatch, path):
    async def _impl(self, pid, window):
        return path
    monkeypatch.setattr(BashOptFunc, "_temp_screenshot", _impl)


# ── schema ──────────────────────────────────────────────

def test_schema_exposes_locate_parameters():
    params = BashOptFunc.to_tool_schema()["function"]["parameters"]
    enum = params["properties"]["op"]["enum"]
    assert "locate" in enum
    for name in ("template", "query", "max_results", "min_scale", "max_scale",
                 "scale_steps", "tolerance"):
        assert name in params["properties"], name


# ── 模板匹配定位 ────────────────────────────────────────

async def test_locate_template_returns_clickable_coordinates(monkeypatch, tmp_path):
    shot = _make_png(tmp_path / "shot.png", 60, 60, (0, 0, 0),
                     block=(20, 20, 8, 8, (255, 0, 0)))
    template = _make_png(tmp_path / "tpl.png", 8, 8, (255, 0, 0))
    _patch_shot(monkeypatch, shot)
    monkeypatch.setattr(BashOptFunc, "_window_frame_for", _fake_frame)
    func = BashOptFunc(task_id="bg-1", op="locate", template=template)
    func.set_agent(_FakeAgent({"bg-1": _record()}))
    payload = json.loads(await func.execute())
    assert payload["mode"] == "template"
    assert payload["matches"]
    top = payload["matches"][0]
    assert (top["x"], top["y"]) == (20, 20)
    assert (top["center_x"], top["center_y"]) == (24, 24)
    # 屏幕坐标 = 窗口内坐标 + frame 原点
    assert (top["screen_center_x"], top["screen_center_y"]) == (124, 74)
    assert payload["frame"]["screen_x"] == 100


async def test_locate_template_with_crop_offsets(monkeypatch, tmp_path):
    shot = _make_png(tmp_path / "shot.png", 60, 60, (0, 0, 0),
                     block=(30, 30, 8, 8, (0, 255, 0)))
    template = _make_png(tmp_path / "tpl.png", 8, 8, (0, 255, 0))
    _patch_shot(monkeypatch, shot)
    monkeypatch.setattr(BashOptFunc, "_window_frame_for", _fake_frame)
    func = BashOptFunc(task_id="bg-1", op="locate", template=template,
                       crop="20,20,30,30")
    func.set_agent(_FakeAgent({"bg-1": _record()}))
    payload = json.loads(await func.execute())
    top = payload["matches"][0]
    # 匹配在裁剪区域内 (10,10)，叠加 crop 偏移后回到窗口坐标 (30,30)
    assert (top["x"], top["y"]) == (30, 30)
    assert payload["crop"]["x"] == 20


async def test_locate_template_missing_file(monkeypatch, tmp_path):
    shot = _make_png(tmp_path / "shot.png", 30, 30)
    _patch_shot(monkeypatch, shot)
    func = BashOptFunc(task_id="bg-1", op="locate",
                       template=str(tmp_path / "none.png"))
    func.set_agent(_FakeAgent({"bg-1": _record()}))
    result = await func.execute()
    assert result.startswith("(locate 失败") and "不存在" in result


# ── OCR 文字定位 ────────────────────────────────────────

async def test_locate_text_finds_query(monkeypatch, tmp_path):
    shot = _make_png(tmp_path / "shot.png", 120, 40)
    _patch_shot(monkeypatch, shot)
    monkeypatch.setattr(BashOptFunc, "_window_frame_for", _fake_frame)
    boxes = [TextBox("保存", 10, 5, 30, 12), TextBox("取消", 60, 5, 30, 12)]
    monkeypatch.setattr(bash_opt_module, "recognize_text",
                        lambda path, lang=None: list(boxes))
    func = BashOptFunc(task_id="bg-1", op="locate", query="保存")
    func.set_agent(_FakeAgent({"bg-1": _record()}))
    payload = json.loads(await func.execute())
    assert payload["mode"] == "text"
    assert payload["recognized"] == 2 and payload["returned"] == 1
    match = payload["matches"][0]
    assert match["text"] == "保存"
    assert match["center_x"] == 25
    assert match["screen_center_x"] == 125


async def test_locate_text_returns_all_when_no_query(monkeypatch, tmp_path):
    shot = _make_png(tmp_path / "shot.png", 120, 40)
    _patch_shot(monkeypatch, shot)
    boxes = [TextBox("A", 0, 0, 10, 10), TextBox("B", 20, 0, 10, 10)]
    monkeypatch.setattr(bash_opt_module, "recognize_text",
                        lambda path, lang=None: list(boxes))
    func = BashOptFunc(task_id="bg-1", op="locate", query="  ")
    func.set_agent(_FakeAgent({"bg-1": _record()}))
    # 空白 query 视为「列出全部」
    payload = json.loads(await func.execute())
    assert payload["returned"] == 2


# ── 参数与错误 ──────────────────────────────────────────

async def test_locate_requires_template_or_query():
    func = BashOptFunc(task_id="bg-1", op="locate")
    func.set_agent(_FakeAgent({"bg-1": _record()}))
    result = await func.execute()
    assert "template" in result and "query" in result


async def test_locate_requires_process_handle():
    func = BashOptFunc(task_id="bg-1", op="locate", query="x")
    func.set_agent(_FakeAgent({"bg-1": {"read_buffer": "", "status": "running",
                                        "done": False}}))
    assert "尚无进程句柄" in await func.execute()


async def test_locate_rejects_bad_parameters(monkeypatch, tmp_path):
    shot = _make_png(tmp_path / "shot.png", 30, 30)
    _patch_shot(monkeypatch, shot)
    func = BashOptFunc(task_id="bg-1", op="locate", query="x", max_results=0)
    func.set_agent(_FakeAgent({"bg-1": _record()}))
    assert "max_results" in await func.execute()

    func = BashOptFunc(task_id="bg-1", op="locate", query="x",
                       min_scale=2.0, max_scale=0.5)
    func.set_agent(_FakeAgent({"bg-1": _record()}))
    assert "min_scale" in await func.execute()


# ── OCR + crop：坐标必须仍是窗口坐标 ─────────────────────

async def test_locate_text_with_crop_keeps_window_coordinates(monkeypatch, tmp_path):
    """crop 只限定命中范围，识别仍在整图上做，坐标不得再叠加 crop 偏移。

    回归：此前 crop 情况下把 crop 原点又加了一次，返回坐标整体偏移了
    (crop.x, crop.y)，模型按该坐标点击会落到错误位置。
    """
    shot = _make_png(tmp_path / "shot.png", 120, 40)
    _patch_shot(monkeypatch, shot)
    boxes = [TextBox("保存", 10, 5, 30, 12), TextBox("取消", 60, 5, 30, 12)]
    monkeypatch.setattr(bash_opt_module, "recognize_text",
                        lambda path, lang=None: list(boxes))
    func = BashOptFunc(task_id="bg-1", op="locate", query="保存",
                       crop="5,0,40,40")
    func.set_agent(_FakeAgent({"bg-1": _record()}))
    payload = json.loads(await func.execute())
    assert payload["recognized"] == 2       # 识别总数（整图）
    assert payload["in_crop"] == 1          # 落在 crop 区域内的框
    match = payload["matches"][0]
    assert match["text"] == "保存"
    assert (match["x"], match["y"]) == (10, 5)
    assert (match["center_x"], match["center_y"]) == (25, 11)


async def test_locate_text_keeps_full_count_when_cropped(monkeypatch, tmp_path):
    """crop 命中多个框时坐标同样保持窗口坐标系。"""
    shot = _make_png(tmp_path / "shot.png", 120, 40)
    _patch_shot(monkeypatch, shot)
    boxes = [TextBox("OK", 10, 5, 20, 12), TextBox("OK", 60, 5, 20, 12)]
    monkeypatch.setattr(bash_opt_module, "recognize_text",
                        lambda path, lang=None: list(boxes))
    func = BashOptFunc(task_id="bg-1", op="locate", query="OK",
                       crop="50,0,40,40")
    func.set_agent(_FakeAgent({"bg-1": _record()}))
    payload = json.loads(await func.execute())
    assert payload["returned"] == 1
    assert payload["matches"][0]["x"] == 60


async def test_locate_text_matches_cjk_phrase_split_into_single_chars(monkeypatch, tmp_path):
    """回归：Windows 系统 OCR 把「文件」拆成「文」「件」两框时，query 不带
    空格也必须命中（此前只在带空格拼接中查找，中文短语永远匹配不到）。"""
    shot = _make_png(tmp_path / "shot.png", 120, 40)
    _patch_shot(monkeypatch, shot)
    monkeypatch.setattr(BashOptFunc, "_window_frame_for", _fake_frame)
    boxes = [TextBox("文", 10, 5, 14, 14), TextBox("件", 24, 5, 14, 14)]
    monkeypatch.setattr(bash_opt_module, "recognize_text",
                        lambda path, lang=None: list(boxes))
    func = BashOptFunc(task_id="bg-1", op="locate", query="文件")
    func.set_agent(_FakeAgent({"bg-1": _record()}))
    payload = json.loads(await func.execute())
    assert payload["returned"] == 1
    match = payload["matches"][0]
    assert (match["x"], match["y"]) == (10, 5)
    assert match["width"] == 28
    assert (match["screen_center_x"], match["screen_center_y"]) == (124, 62)


async def test_locate_text_similar_match_warns_review(monkeypatch, tmp_path):
    """中文 OCR 错字（「钮」→「田」）用相似度兜底命中，并提示复核。

    实测：Windows 系统 OCR 把「确定取消按钮」拆成单字且认错一个字，此前
    精确 / 易混字符匹配全部落空（locate 报未命中）。
    """
    shot = _make_png(tmp_path / "shot.png", 200, 60)
    _patch_shot(monkeypatch, shot)
    monkeypatch.setattr(BashOptFunc, "_window_frame_for", _fake_frame)
    boxes = [TextBox(ch, 10 + index * 20, 8, 18, 22)
             for index, ch in enumerate("确定取消按田")]
    monkeypatch.setattr(bash_opt_module, "recognize_text",
                        lambda path, lang=None: list(boxes))
    func = BashOptFunc(task_id="bg-1", op="locate", query="确定取消按钮")
    func.set_agent(_FakeAgent({"bg-1": _record()}))
    payload = json.loads(await func.execute())
    assert payload["match_mode"] == "similar"
    assert payload["recognized"] == 6
    assert payload["returned"] == 1
    assert "相似度" in payload["note"] and "复核" in payload["note"]
    assert payload["matches"][0]["text"] == "确 定 取 消 按 田"


# ── 未命中时的诊断信息 ──────────────────────────────────

async def test_locate_text_without_match_reports_recognized_texts(monkeypatch, tmp_path):
    shot = _make_png(tmp_path / "shot.png", 120, 40)
    _patch_shot(monkeypatch, shot)
    boxes = [TextBox("开机", 10, 5, 30, 12), TextBox("下载", 60, 5, 30, 12)]
    monkeypatch.setattr(bash_opt_module, "recognize_text",
                        lambda path, lang=None: list(boxes))
    func = BashOptFunc(task_id="bg-1", op="locate", query="OK")
    func.set_agent(_FakeAgent({"bg-1": _record()}))
    payload = json.loads(await func.execute())
    assert payload["matches"] == []
    assert payload["recognized"] == 2
    # 识别到的内容一并返回：模型据此判断是 OCR 认错还是画面没有该文字
    assert payload["recognized_texts"] == ["开机", "下载"]


async def test_locate_text_crop_without_text_falls_back_to_full_samples(monkeypatch, tmp_path):
    shot = _make_png(tmp_path / "shot.png", 120, 40)
    _patch_shot(monkeypatch, shot)
    boxes = [TextBox("保存", 10, 5, 30, 12)]
    monkeypatch.setattr(bash_opt_module, "recognize_text",
                        lambda path, lang=None: list(boxes))
    func = BashOptFunc(task_id="bg-1", op="locate", query="保存",
                       crop="100,0,5,40")
    func.set_agent(_FakeAgent({"bg-1": _record()}))
    payload = json.loads(await func.execute())
    assert payload["in_crop"] == 0
    assert payload["recognized"] == 1
    assert payload["recognized_texts"] == ["保存"]   # 回退为整图样本
    assert "crop 区域内没有识别到任何文字" in payload["note"]


async def test_locate_rejects_crop_outside_image(monkeypatch, tmp_path):
    """crop 越界要立刻报错（OCR 路径不做裁剪，否则会静默变成「没找到」）。"""
    shot = _make_png(tmp_path / "shot.png", 30, 30)
    _patch_shot(monkeypatch, shot)
    func = BashOptFunc(task_id="bg-1", op="locate", query="x",
                       crop="25,25,20,20")
    func.set_agent(_FakeAgent({"bg-1": _record()}))
    result = await func.execute()
    assert result.startswith("(locate 参数非法")
    assert "超出" in result


# ── 近似匹配（OCR 易混字符） ────────────────────────────

async def test_locate_text_reports_fuzzy_match_mode(monkeypatch, tmp_path):
    """OCR 把 ``All`` 认成 ``AII`` 时仍能定位，并在结果里标注近似匹配。

    真实用例：右键菜单 ``Select All`` 被识别成 ``Select`` + ``AII``，
    精确匹配全部落空；近似匹配命中后必须让调用方知道「坐标要复核」。
    """
    shot = _make_png(tmp_path / "shot.png", 120, 40)
    _patch_shot(monkeypatch, shot)
    monkeypatch.setattr(BashOptFunc, "_window_frame_for", _fake_frame)
    boxes = [TextBox("Select", 10, 5, 40, 14), TextBox("AII", 52, 5, 20, 14)]
    monkeypatch.setattr(bash_opt_module, "recognize_text",
                        lambda path, lang=None: list(boxes))
    func = BashOptFunc(task_id="bg-1", op="locate", query="Select All")
    func.set_agent(_FakeAgent({"bg-1": _record()}))
    payload = json.loads(await func.execute())
    assert payload["returned"] == 1
    assert payload["match_mode"] == "fuzzy"
    assert "近似匹配" in payload["note"]
    assert payload["matches"][0]["x"] == 10


async def test_locate_text_reports_exact_match_mode(monkeypatch, tmp_path):
    shot = _make_png(tmp_path / "shot.png", 120, 40)
    _patch_shot(monkeypatch, shot)
    monkeypatch.setattr(BashOptFunc, "_window_frame_for", _fake_frame)
    monkeypatch.setattr(bash_opt_module, "recognize_text",
                        lambda path, lang=None: [TextBox("Apply", 10, 5, 40, 14)])
    func = BashOptFunc(task_id="bg-1", op="locate", query="Apply")
    func.set_agent(_FakeAgent({"bg-1": _record()}))
    payload = json.loads(await func.execute())
    assert payload["returned"] == 1
    assert payload["match_mode"] == "exact"
    assert "note" not in payload


# ── OCR 语言（ocr_lang） ────────────────────────────────

def test_schema_exposes_ocr_lang():
    params = BashOptFunc.to_tool_schema()["function"]["parameters"]
    assert params["properties"]["ocr_lang"]["type"] == "string"
    assert "ocr_lang" in params["properties"]["op"]["description"]


async def test_locate_text_passes_ocr_lang(monkeypatch, tmp_path):
    """ocr_lang 必须透传给 OCR 后端，并在结果里如实回显。"""
    shot = _make_png(tmp_path / "shot.png", 120, 40)
    _patch_shot(monkeypatch, shot)
    seen = {}

    def _recognize(path, lang=None):
        seen["lang"] = lang
        return [TextBox("确定", 0, 0, 20, 12)]

    monkeypatch.setattr(bash_opt_module, "recognize_text", _recognize)
    func = BashOptFunc(task_id="bg-1", op="locate", query="确定",
                       ocr_lang="zh-Hans-CN")
    func.set_agent(_FakeAgent({"bg-1": _record()}))
    payload = json.loads(await func.execute())
    assert seen["lang"] == "zh-Hans-CN"
    assert payload["ocr_lang"] == "zh-Hans-CN"
    assert payload["returned"] == 1


async def test_locate_text_without_match_hints_ocr_lang(monkeypatch, tmp_path):
    """未命中且未指定语言时提示可用 ocr_lang（本机语言包可能不匹配）。"""
    shot = _make_png(tmp_path / "shot.png", 120, 40)
    _patch_shot(monkeypatch, shot)
    monkeypatch.setattr(bash_opt_module, "recognize_text",
                        lambda path, lang=None: [TextBox("开机", 0, 0, 20, 12)])
    func = BashOptFunc(task_id="bg-1", op="locate", query="OK")
    func.set_agent(_FakeAgent({"bg-1": _record()}))
    payload = json.loads(await func.execute())
    assert payload["matches"] == []
    assert "ocr_lang" in payload["hint"]
    assert "zh-Hans-CN" in payload["hint"]


async def test_locate_text_with_lang_omits_language_hint(monkeypatch, tmp_path):
    """已指定 ocr_lang 时不再重复提示切换语言（避免误导）。"""
    shot = _make_png(tmp_path / "shot.png", 120, 40)
    _patch_shot(monkeypatch, shot)
    monkeypatch.setattr(bash_opt_module, "recognize_text",
                        lambda path, lang=None: [TextBox("开机", 0, 0, 20, 12)])
    func = BashOptFunc(task_id="bg-1", op="locate", query="OK",
                       ocr_lang="zh-Hans-CN")
    func.set_agent(_FakeAgent({"bg-1": _record()}))
    payload = json.loads(await func.execute())
    # 基础 hint 始终存在（坐标用法说明），但不应再出现「切换语言」建议
    assert "ocr_lang 指定语言" not in payload["hint"]
    assert "未命中查询文字" not in payload["hint"]
