"""轨迹 Trace 多模态输入显示测试（2026-08-22）。

验证：
- parse_image_blocks 从 content 提取 image block 元信息（media_type/b64/sha/近似字节）；
- image_summary 生成紧凑单行摘要（不再把超长 base64 灌进台账）；
- thumbnail_rows 把图片渲染成半块（▀）真彩/256 色缩略图行（list[StyledRun]）；
- _records_from_messages 对 user/assistant/tool 多模态消息挂 images + 摘要行；
- _inspector_content_rows 在检查器追加图片缩略图行。
"""

from __future__ import annotations

import base64
import io

import pytest

pytest.importorskip("PIL")  # 缩略图渲染依赖 Pillow

from src.tui.app.trace import _records_from_messages, _next_record_index, TraceRecord
from src.tui.app.trace_view import _inspector_content_rows
from src.tui.app.trace_image import (
    parse_image_blocks, image_summary, thumbnail_rows,
)


def _png_data_uri(w: int = 12, h: int = 8) -> str:
    """生成简单 PNG 的 data URI（每像素颜色与坐标相关）。"""
    from PIL import Image
    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(h):
        for x in range(w):
            px[x, y] = (x * 20 % 256, y * 30 % 256, (x * y) % 256)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{b64}"


def _img_block(uri: str) -> dict:
    return {"type": "image_url", "image_url": {"url": uri}}


# ── 1. parse_image_blocks ────────────────────────────────

def test_parse_image_blocks_extracts_image_url():
    uri = _png_data_uri()
    imgs = parse_image_blocks([{"type": "text", "text": "看图"}, _img_block(uri)])
    assert len(imgs) == 1
    img = imgs[0]
    assert img["media_type"] == "image/png"
    assert img["b64"]  # 非空 base64
    assert len(img["sha"]) == 64
    assert img["approx_bytes"] > 0


def test_parse_image_blocks_ignores_non_image():
    assert parse_image_blocks([{"type": "text", "text": "hello"}]) == []
    assert parse_image_blocks("纯文本") == []
    assert parse_image_blocks(None) == []


def test_parse_image_blocks_ignores_http_url():
    """非 data URI 的 image_url 不提取（无法内联渲染）。"""
    blk = {"type": "image_url", "image_url": {"url": "https://x.com/a.png"}}
    assert parse_image_blocks([blk]) == []


# ── 2. image_summary ────────────────────────────────────

def test_image_summary_compact():
    img = {"media_type": "image/png", "approx_bytes": 12 * 1024}
    assert image_summary(img) == "[图片 image/png ~12KB]"
    img2 = {"media_type": "image/jpeg", "approx_bytes": 500}
    assert image_summary(img2) == "[图片 image/jpeg ~500B]"


# ── 3. thumbnail_rows ────────────────────────────────────

def test_thumbnail_rows_produces_halfblocks():
    imgs = parse_image_blocks([_img_block(_png_data_uri(12, 8))])
    assert imgs
    rows = thumbnail_rows(imgs[0], 60)
    assert rows
    row = rows[0]
    assert row and isinstance(row, list)
    sr = row[0]
    assert sr.text == "\u2580"  # 半块字符
    assert sr.style is not None
    assert sr.style.fg is not None  # 前景色（上像素）


def test_thumbnail_rows_zero_width_guard():
    imgs = parse_image_blocks([_img_block(_png_data_uri(4, 4))])
    assert thumbnail_rows(imgs[0], 0) == []


# ── 4. _records_from_messages（user 多模态） ─────────────

def test_user_record_carries_images_and_summary():
    uri = _png_data_uri()
    msgs = [{"role": "user", "content": [{"type": "text", "text": "看这张图"}, _img_block(uri)]}]
    records, _rows = _records_from_messages(msgs)
    users = [r for r in records if r.kind == "user"]
    assert users, "应生成 user 记录"
    rec = users[0]
    assert rec.images, "user 记录应携带 images"
    assert len(rec.images) == 1
    assert rec.images[0]["media_type"] == "image/png"
    # 摘要行包含图片摘要，且不含超长 base64
    joined = "\n".join(rec.lines)
    assert "[图片 image/png" in joined
    assert "base64," not in joined


def test_assistant_image_message_skips_garbage():
    uri = _png_data_uri()
    msgs = [{"role": "assistant", "content": [_img_block(uri), {"type": "text", "text": "内容"}]}]
    records, _rows = _records_from_messages(msgs)
    recs = [r for r in records if r.kind == "content"]
    assert recs and "base64," not in "\n".join(recs[0].lines)


# ── 5. _inspector_content_rows（检查器渲染缩略图） ─────────

def test_inspector_appends_thumbnail_row():
    uri = _png_data_uri()
    msgs = [{"role": "user", "content": [_img_block(uri)]}]
    records, _rows = _records_from_messages(msgs)
    rec = next(r for r in records if r.kind == "user")
    rows, keys = _inspector_content_rows(rec, 60)
    styled = [r for r in rows if isinstance(r, list)]
    assert styled, "检查器应渲染半块缩略图行"
    assert styled[0][0].text == "\u2580"


def test_next_record_index_handles_tools_record():
    """P2-2/P3-4：_next_record_index 以「现有最大 index + 1」编号，避免台账 #N 断号。

    修复前 _subagent_records/_live_records 以 len(records) 作起点：_records_from_messages
    在 #0 工具列表存在时记录 index 为 0..len-1（连续），末条 = len-1，函数体
    index_holder[0] += 1 从 len 起 → 首条追加取 len+1 → 跳过 len（断号）。
    """
    recs = [
        TraceRecord(index=0, kind="tools"),   # #0 工具列表
        TraceRecord(index=1, kind="user"),    # 消息记录
        TraceRecord(index=2, kind="content"),
    ]
    # 末条 index=2 → 下一条应为 3（修复前 len(records)=3 → 3+1=4 断号）
    assert _next_record_index(recs) == 3
    assert _next_record_index([]) == 0
