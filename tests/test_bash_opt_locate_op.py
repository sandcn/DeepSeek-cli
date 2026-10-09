"""bash_opt 图像 / 文字定位（op=locate）测试。

覆盖：schema 暴露、模板匹配定位（坐标与输入 op 同源 + 屏幕坐标换算）、
OCR 文字定位、crop 限定区域、参数校验与错误提示。
"""

from __future__ import annotations

import json

import pytest

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
