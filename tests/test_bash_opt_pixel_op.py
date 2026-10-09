"""bash_opt 像素取色 / 颜色检测（op=pixel）测试。

覆盖：schema 暴露、point 点取色（含颜色匹配结论与屏幕坐标）、region 区域统计、
find 颜色查找（连通块 + 屏幕坐标）、参数校验与错误提示、缺进程句柄提示。
"""

from __future__ import annotations

import json

from src.tools._screenshot import png
from src.tools._window_input.geometry import WindowFrame
from src.tools.bash_opt import BashOptFunc


class _FakeAgent:
    def __init__(self, records: dict):
        self._background_tasks = records
        self._subagent_tasks = {}


def _record(pid=4321):
    return {"read_buffer": "", "status": "running", "done": False, "pid": pid,
            "task": None}


def _make_png(path, width, height, fill=(0, 0, 0), blocks=()):
    pixels = bytearray(bytes(fill) * (width * height))
    for bx, by, bw, bh, color in blocks:
        for row in range(by, by + bh):
            for col in range(bx, bx + bw):
                offset = (row * width + col) * 3
                pixels[offset:offset + 3] = bytes(color)
    path.write_bytes(png.encode_png_rgb(width, height, bytes(pixels)))
    return str(path)


def _patch_shot(monkeypatch, path):
    async def _impl(self, pid, window):
        return path
    monkeypatch.setattr(BashOptFunc, "_temp_screenshot", _impl)


async def _fake_frame(self, pid, window):
    return WindowFrame(100, 50, 40, 30)


def _run(**kwargs):
    func = BashOptFunc(task_id="bg-1", op="pixel", **kwargs)
    func.set_agent(_FakeAgent({"bg-1": _record()}))
    return func


# ── schema ──────────────────────────────────────────────

def test_schema_exposes_pixel_parameters():
    params = BashOptFunc.to_tool_schema()["function"]["parameters"]
    assert "pixel" in params["properties"]["op"]["enum"]
    for name in ("mode", "color", "region", "min_pixels", "max_regions"):
        assert name in params["properties"], name


# ── point 模式 ──────────────────────────────────────────

async def test_pixel_point_reads_color_and_matches(monkeypatch, tmp_path):
    shot = _make_png(tmp_path / "s.png", 40, 30, blocks=[(5, 5, 1, 1, (255, 0, 0))])
    _patch_shot(monkeypatch, shot)
    monkeypatch.setattr(BashOptFunc, "_window_frame_for", _fake_frame)
    payload = json.loads(await _run(x=5, y=5, color="#FF0000").execute())
    assert payload["mode"] == "point"
    assert payload["color"]["hex"] == "#FF0000"
    assert payload["color"]["match"] is True
    assert (payload["x"], payload["y"]) == (5, 5)
    assert (payload["screen_x"], payload["screen_y"]) == (105, 55)


async def test_pixel_point_semantic_coordinate(monkeypatch, tmp_path):
    shot = _make_png(tmp_path / "s.png", 40, 30, fill=(1, 2, 3))
    _patch_shot(monkeypatch, shot)
    payload = json.loads(await _run(x="center", y="center").execute())
    assert (payload["x"], payload["y"]) == (20, 15)


async def test_pixel_point_requires_xy(monkeypatch, tmp_path):
    shot = _make_png(tmp_path / "s.png", 40, 30)
    _patch_shot(monkeypatch, shot)
    result = await _run(color="#000000").execute()
    assert result.startswith("(pixel") and "x" in result and "y" in result


async def test_pixel_point_out_of_bounds(monkeypatch, tmp_path):
    shot = _make_png(tmp_path / "s.png", 10, 10)
    _patch_shot(monkeypatch, shot)
    result = await _run(x=50, y=50).execute()
    assert "越界" in result


# ── region 模式 ─────────────────────────────────────────

async def test_pixel_region_stats_and_compare(monkeypatch, tmp_path):
    shot = _make_png(tmp_path / "s.png", 20, 20, fill=(200, 100, 50))
    _patch_shot(monkeypatch, shot)
    payload = json.loads(await _run(mode="region", region="0,0,10,10",
                                    color="#C86432").execute())
    assert payload["mode"] == "region"
    assert payload["stats"]["pixels"] == 100
    assert payload["stats"]["average"]["hex"] == "#C86432"
    assert payload["color"]["match"] is True
    assert payload["region"] == {"x": 0, "y": 0, "width": 10, "height": 10}


# ── find 模式 ───────────────────────────────────────────

async def test_pixel_find_returns_regions_with_screen_coords(monkeypatch, tmp_path):
    shot = _make_png(tmp_path / "s.png", 40, 30,
                     blocks=[(10, 8, 4, 4, (0, 255, 0))])
    _patch_shot(monkeypatch, shot)
    monkeypatch.setattr(BashOptFunc, "_window_frame_for", _fake_frame)
    payload = json.loads(await _run(mode="find", color="#00FF00",
                                    tolerance=0).execute())
    assert payload["mode"] == "find"
    assert payload["found"] == 1
    region = payload["regions"][0]
    assert (region["x"], region["y"]) == (10, 8)
    assert (region["center_x"], region["center_y"]) == (12, 10)
    assert (region["screen_center_x"], region["screen_center_y"]) == (112, 60)


async def test_pixel_find_requires_color(monkeypatch, tmp_path):
    shot = _make_png(tmp_path / "s.png", 10, 10)
    _patch_shot(monkeypatch, shot)
    result = await _run(mode="find").execute()
    assert "color" in result


async def test_pixel_find_min_pixels_filters(monkeypatch, tmp_path):
    shot = _make_png(tmp_path / "s.png", 20, 20, blocks=[(1, 1, 1, 1, (0, 0, 255))])
    _patch_shot(monkeypatch, shot)
    payload = json.loads(await _run(mode="find", color="#0000FF", tolerance=0,
                                    min_pixels=2).execute())
    assert payload["found"] == 0


# ── 参数与错误 ──────────────────────────────────────────

async def test_pixel_rejects_invalid_color(monkeypatch, tmp_path):
    shot = _make_png(tmp_path / "s.png", 10, 10)
    _patch_shot(monkeypatch, shot)
    result = await _run(x=1, y=1, color="nope").execute()
    assert "无法解析颜色" in result


async def test_pixel_rejects_invalid_mode(monkeypatch, tmp_path):
    shot = _make_png(tmp_path / "s.png", 10, 10)
    _patch_shot(monkeypatch, shot)
    result = await _run(mode="weird", x=1, y=1).execute()
    assert "mode" in result


async def test_pixel_requires_process_handle():
    func = BashOptFunc(task_id="bg-1", op="pixel", x=1, y=1)
    func.set_agent(_FakeAgent({"bg-1": {"read_buffer": "", "status": "running",
                                        "done": False}}))
    assert "尚无进程句柄" in await func.execute()


async def test_pixel_reports_screenshot_failure(monkeypatch):
    async def _none(self, pid, window):
        return None
    monkeypatch.setattr(BashOptFunc, "_temp_screenshot", _none)
    result = await _run(x=1, y=1).execute()
    assert "无法截取窗口画面" in result
