"""bash_opt 截图标注（op=annotate）测试。

覆盖：schema 暴露、按已有 PNG 标注并另存、按路径就地覆盖、从窗口现截后
自动命名输出、boxes/points/labels、参数校验与错误提示。
"""

from __future__ import annotations

import json
import os

from src.tools._screenshot import png
from src.tools._screenshot.color import RGB, pixel_at
from src.tools._screenshot.png_decode import decode_png_file
from src.tools.bash_opt import BashOptFunc


class _FakeAgent:
    def __init__(self, records: dict):
        self._background_tasks = records
        self._subagent_tasks = {}


def _record(pid=4321):
    return {"read_buffer": "", "status": "running", "done": False, "pid": pid,
            "task": None}


def _make_png(path, width=40, height=30, fill=(255, 255, 255)):
    path.write_bytes(png.encode_png_rgb(width, height,
                                        bytes(fill) * (width * height)))
    return str(path)


def _run(records=None, **kwargs):
    func = BashOptFunc(task_id="bg-1", op="annotate", **kwargs)
    func.set_agent(_FakeAgent(records or {"bg-1": _record()}))
    return func


# ── schema ──────────────────────────────────────────────

def test_schema_exposes_annotate_parameters():
    params = BashOptFunc.to_tool_schema()["function"]["parameters"]
    assert "annotate" in params["properties"]["op"]["enum"]
    for name in ("output", "boxes", "points", "labels"):
        assert name in params["properties"], name


# ── 标注 ────────────────────────────────────────────────

async def test_annotate_existing_png_to_output(tmp_path):
    source = _make_png(tmp_path / "a.png")
    out = str(tmp_path / "marked.png")
    result = await _run(path=source, boxes=["5,5,10,10"], points=[(25, 20)],
                        labels=["1", "2"], output=out, color="#FF0000").execute()
    payload = json.loads(result)
    assert payload["boxes"] == 1 and payload["points"] == 1
    assert payload["path"] == out
    image = decode_png_file(out)
    assert pixel_at(image, 5, 5) == RGB(255, 0, 0)


async def test_annotate_overwrites_source_in_place(tmp_path):
    source = _make_png(tmp_path / "a.png")
    await _run(path=source, points=[(3, 3)], color="blue").execute()
    image = decode_png_file(source)
    assert pixel_at(image, 3, 3) == RGB(0, 0, 255)


async def test_annotate_from_window_uses_auto_output(monkeypatch, tmp_path):
    shot = _make_png(tmp_path / "shot.png")
    shots_dir = tmp_path / "shots"
    monkeypatch.setattr(BashOptFunc, "_SHOT_AUTO_DIR", str(shots_dir))

    async def _impl(self, pid, window):
        return shot
    monkeypatch.setattr(BashOptFunc, "_temp_screenshot", _impl)
    payload = json.loads(await _run(boxes=["1,1,5,5"]).execute())
    assert os.path.isfile(payload["path"])
    assert str(shots_dir) in payload["path"]


async def test_annotate_with_grid_only(tmp_path):
    source = _make_png(tmp_path / "a.png", width=20, height=20)
    payload = json.loads(await _run(path=source, grid=5).execute())
    assert payload["grid"] == 5
    assert payload["boxes"] == 0


# ── 错误 ────────────────────────────────────────────────

async def test_annotate_requires_marks_or_grid(tmp_path):
    source = _make_png(tmp_path / "a.png")
    result = await _run(path=source).execute()
    assert "boxes" in result and "points" in result


async def test_annotate_missing_source_file(tmp_path):
    result = await _run(path=str(tmp_path / "none.png"), points=["1,1"]).execute()
    assert "不存在" in result


async def test_annotate_requires_pid_without_path():
    func = BashOptFunc(task_id="bg-1", op="annotate", points=["1,1"])
    func.set_agent(_FakeAgent({"bg-1": {"read_buffer": "", "status": "running",
                                        "done": False}}))
    assert "尚无进程句柄" in await func.execute()


async def test_annotate_rejects_bad_mark(tmp_path):
    source = _make_png(tmp_path / "a.png")
    result = await _run(path=source, boxes=["not-a-box"]).execute()
    assert result.startswith("(annotate 失败")
