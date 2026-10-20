"""``bash_opt`` 用已有 PNG 做图像分析（``op=pixel`` / ``op=locate`` 的 path 参数）测试。

覆盖：

  - ``path`` 模式**不截取窗口**（不调用 ``_temp_screenshot``），且不需要
    ``task_id``（后台任务表为空也能执行）；
  - 坐标原点为图片左上角，结果不带 ``screen_*`` / ``frame``（图片不绑定窗口）；
  - pixel 的 point / region / find 三种模式与 locate 的模板匹配 / OCR 均可用；
  - ``path`` 不存在时给出可读错误；schema 与 ``_can_run_without_task`` 一致。
"""

from __future__ import annotations

import json

from src.tools import bash_opt as bash_opt_module
from src.tools._screenshot import png
from src.tools._screenshot.ocr import TextBox
from src.tools.bash_opt import BashOptFunc


class _FakeAgent:
    """最小 Agent 桩：后台任务表为空（验证 path 模式不依赖任务）。"""

    def __init__(self, records: dict | None = None):
        self._background_tasks = records or {}
        self._subagent_tasks = {}


def _make_png(path, width, height, fill=(0, 0, 0), blocks=()):
    pixels = bytearray(bytes(fill) * (width * height))
    for bx, by, bw, bh, color in blocks:
        for row in range(by, by + bh):
            for col in range(bx, bx + bw):
                offset = (row * width + col) * 3
                pixels[offset:offset + 3] = bytes(color)
    path.write_bytes(png.encode_png_rgb(width, height, bytes(pixels)))
    return str(path)


def _no_screenshot(monkeypatch):
    """path 模式不允许截图：被调用即判失败。"""
    async def _boom(self, pid, window):
        raise AssertionError("path 模式不应截取窗口")
    monkeypatch.setattr(BashOptFunc, "_temp_screenshot", _boom)


def _run(**kwargs) -> BashOptFunc:
    func = BashOptFunc(task_id="bg-1", **kwargs)
    func.set_agent(_FakeAgent())
    return func


# ── pixel：path 模式 ────────────────────────────────────

async def test_pixel_path_point_without_task(monkeypatch, tmp_path):
    """point 取色只读图片：无任务、无截图、无屏幕坐标。"""
    image = _make_png(tmp_path / "img.png", 40, 30,
                      blocks=[(7, 9, 1, 1, (10, 20, 30))])
    _no_screenshot(monkeypatch)
    payload = json.loads(
        await _run(op="pixel", path=image, x=7, y=9).execute())
    assert payload["source"] == "path"
    assert payload["color"]["hex"] == "#0A141E"
    assert payload["image"] == {"width": 40, "height": 30}
    assert "frame" not in payload
    assert "screen_x" not in payload and "screen_y" not in payload


async def test_pixel_path_region_and_find(monkeypatch, tmp_path):
    image = _make_png(
        tmp_path / "img.png", 40, 30, fill=(0, 0, 0),
        blocks=[(10, 8, 4, 4, (0, 255, 0))])
    _no_screenshot(monkeypatch)
    region = json.loads(await _run(
        op="pixel", path=image, mode="region", region="0,0,20,20").execute())
    assert region["source"] == "path"
    assert region["stats"]["pixels"] == 400

    found = json.loads(await _run(
        op="pixel", path=image, mode="find", color="#00FF00",
        tolerance=0).execute())
    assert found["source"] == "path"
    assert found["found"] == 1
    block = found["regions"][0]
    assert (block["x"], block["y"]) == (10, 8)
    assert "screen_center_x" not in block
    assert found["image"] == {"width": 40, "height": 30}


async def test_pixel_path_missing_file_is_readable_error(tmp_path):
    missing = tmp_path / "nope.png"
    result = await _run(op="pixel", path=str(missing), x=1, y=1).execute()
    assert result.startswith("(pixel 参数非法")
    assert "不存在" in result


# ── locate：path 模式 ───────────────────────────────────

async def test_locate_path_template_without_task(monkeypatch, tmp_path):
    image = _make_png(tmp_path / "img.png", 60, 60, (0, 0, 0),
                      blocks=[(20, 20, 8, 8, (255, 0, 0))])
    template = _make_png(tmp_path / "tpl.png", 8, 8, (255, 0, 0))
    _no_screenshot(monkeypatch)
    payload = json.loads(await _run(
        op="locate", path=image, template=template).execute())
    assert payload["source"] == "path"
    assert payload["image"] == {"width": 60, "height": 60}
    top = payload["matches"][0]
    assert (top["x"], top["y"]) == (20, 20)
    assert "screen_x" not in top and "frame" not in payload


async def test_locate_path_text_ocr(monkeypatch, tmp_path):
    image = _make_png(tmp_path / "img.png", 120, 40)
    _no_screenshot(monkeypatch)
    boxes = [TextBox("保存", 10, 5, 30, 12), TextBox("取消", 60, 5, 30, 12)]
    monkeypatch.setattr(bash_opt_module, "recognize_text",
                        lambda path, lang=None: list(boxes))
    payload = json.loads(await _run(
        op="locate", path=image, query="保存").execute())
    assert payload["source"] == "path"
    assert payload["total"] == 1
    assert (payload["matches"][0]["center_x"],
            payload["matches"][0]["center_y"]) == (25, 11)
    assert "screen_center_x" not in payload["matches"][0]


async def test_locate_path_crop_uses_image_size(monkeypatch, tmp_path):
    image = _make_png(tmp_path / "img.png", 40, 30)
    _no_screenshot(monkeypatch)
    monkeypatch.setattr(bash_opt_module, "recognize_text",
                        lambda path, lang=None: [])
    result = await _run(
        op="locate", path=image, query="x", crop="30,20,20,20").execute()
    assert result.startswith("(locate 参数非法")
    assert "超出截图范围" in result and "40x30" in result


async def test_locate_path_missing_file_is_readable_error(tmp_path):
    result = await _run(
        op="locate", path=str(tmp_path / "nope.png"), query="x").execute()
    assert result.startswith("(locate 参数非法")
    assert "不存在" in result


# ── 任务依赖与 schema ───────────────────────────────────

def test_path_ops_can_run_without_task():
    assert BashOptFunc(op="pixel", path="x.png")._can_run_without_task() is True
    assert BashOptFunc(op="locate", path="x.png")._can_run_without_task() is True
    assert BashOptFunc(op="pixel")._can_run_without_task() is False
    assert BashOptFunc(op="locate", template="t.png")._can_run_without_task() is False


def test_schema_documents_path_for_image_ops():
    props = BashOptFunc.to_tool_schema()["function"]["parameters"]["properties"]
    description = props["path"]["description"]
    assert "locate" in description and "pixel" in description
    assert "没有 screen_*" in description
