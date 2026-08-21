"""read_image 轨迹 Trace 显示回归测试（2026-08-22）。

验证本次修复：
- _detail_deps / _inspector_content_deps 对 tool 记录含图片指纹（不同图片、
  元信息文本相同时 use_memo 重建缩略图——修复前恒不刷新）；
- _tool_tree_rows 内联「▸ 图片」小节（缩略图作为返回视觉主体，置于
  「▸ 返回值」文本之前），缓存键含图片指纹；
- _inspector_content_rows 对非工具树分支才追加缩略图（避免与图片小节重复）；
- _merge_call_lines 支持 images（缓存 + 图片摘要行）；
- read_image 输出不再携带「图片已编码为 base64 PNG…」技术尾注（Trace 噪音）；
- 缩略图允许等比放大（小图不再显示成几列窄条，上限防模糊）。
"""

from __future__ import annotations

import base64
import io

import pytest

pytest.importorskip("PIL")  # read_image / 缩略图渲染依赖 Pillow

from src.tui.app.trace import _merge_call_lines, _records_from_messages
from src.tui.app.trace_view import (
    _inspector_content_deps,
    _inspector_content_rows,
    _tool_tree_rows,
)
from src.tui.app.trace_image import parse_image_blocks, thumbnail_rows
from src.tools.read_image import ReadImageFunc


def _png_b64(w: int, h: int, seed: int) -> str:
    from PIL import Image
    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(h):
        for x in range(w):
            px[x, y] = (x * seed % 256, y * seed % 256, (x + y + seed) % 256)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _meta_text(w: int = 4, h: int = 4) -> str:
    return (
        f"图片: same.png\n尺寸: {w}x{h} (宽x高)\n"
        f"原始尺寸: {w}x{h} (宽x高)\n操作: none\n格式: PNG"
    )


def _tool_record(b64: str, text: str | None = None):
    """构造 read_image 的 tool 消息并返回工具记录（走 _records_from_messages）。"""
    if text is None:
        text = _meta_text()
    msgs = [
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "read_image", "arguments": '{"path":"same.png"}'}},
        ]},
        {"role": "tool", "tool_call_id": "c1", "content": [
            {"type": "text", "text": text},
            {"type": "image_url",
             "image_url": {"url": "data:image/png;base64," + b64}},
        ]},
    ]
    records, _ = _records_from_messages(msgs)
    return next(r for r in records if r.kind == "tool")


def _label(row) -> str:
    if isinstance(row, list):
        return "".join(getattr(x, "text", "") for x in row)
    return str(row)


def _first_thumb_fg(rows):
    """找到「▸ 图片」小节后的首个缩略图半块前景色。

    返回 TrueColor 对象或 256 色 int（随终端能力而定，见 trace_image._color），
    故不标注具体 int 类型（review P3：真彩终端下非 int）。
    """
    for i, r in enumerate(rows):
        if isinstance(r, list) and _label(r).strip() == "\u25b8 \u56fe\u7247":
            nxt = rows[i + 1]
            return nxt[0].style.fg if nxt else None
    return None


# ── P1：tool 记录 deps 含图片指纹（缩略图随图片变化刷新） ─────────────

def test_detail_deps_include_image_fingerprint():
    a = _tool_record(_png_b64(4, 4, 1))
    b = _tool_record(_png_b64(4, 4, 200))
    d_a = _inspector_content_deps(a, 70)
    d_b = _inspector_content_deps(b, 70)
    # 元信息文本完全相同，但图片 sha 不同 → deps 必须不同（use_memo 才重建）
    assert d_a != d_b
    # 原子值契约：deps 全元素 int/str/None（use_memo 逐项按值比较）
    assert all(isinstance(x, (int, str)) or x is None for x in d_a)
    assert all(isinstance(x, (int, str)) or x is None for x in d_b)
    # 图片指纹是 str（展平 sha）
    assert any(isinstance(x, str) and len(x) == 64 for x in d_a)


def test_tool_tree_cache_rebuilds_on_image_change():
    a = _tool_record(_png_b64(8, 8, 1))
    b = _tool_record(_png_b64(8, 8, 200))
    rows_a, _ = _tool_tree_rows(a, 70)
    rows_b, _ = _tool_tree_rows(b, 70)
    # 不同图片 → 缩略图前景色不同（缓存键含图像指纹，不会命中旧缩略图）
    assert _first_thumb_fg(rows_a) != _first_thumb_fg(rows_b)


def test_tool_record_image_summary_not_duplicated():
    """P1：read_image 多模态 tool 记录的图片摘要行只出现一次。

    修复前 _records_from_messages 工具分支先 ``lines + [image_summary(...)]``，
    再传 _merge_call_lines（内部又 ``+ [...]``）→ ``[图片 image/png …]`` 在
    rec.lines 中重复两次；现由 _merge_call_lines 统一追加一次。
    """
    rec = _tool_record(_png_b64(4, 4, 1))
    img_lines = [ln for ln in rec.lines if isinstance(ln, str) and ln.startswith("[图片")]
    assert len(img_lines) == 1, f"图片摘要行应只出现一次，实际 {img_lines}"
    # 图片摘要行位置在文本元信息之后（_merge_call_lines 追加在返回行末尾）
    assert img_lines and img_lines[0] not in rec.lines[: len(rec.lines) - 1]


# ── P2：缩略图归入「▸ 图片」小节，置于返回值之前 ─────────────────────

def test_tool_tree_image_section_before_return_value():
    rec = _tool_record(_png_b64(16, 16, 7))
    rows, keys = _inspector_content_rows(rec, 70)
    labels = [_label(r) for r in rows]
    i_img = next(i for i, t in enumerate(labels) if t.strip() == "\u25b8 \u56fe\u7247")
    i_ret = next(i for i, t in enumerate(labels) if t.strip() == "\u25b8 \u8fd4\u56de\u503c")
    # 图片小节在返回值文本之前（返回视觉主体优先）
    assert i_img < i_ret
    # 图片小节内确有半块缩略图行
    assert any(labels[i].startswith("\u2580") for i in range(i_img, i_ret))
    # keys 与 rows 对齐（折叠/空格导航依赖）
    assert len(keys) == len(rows)


def test_inspector_non_tool_branch_still_appends_thumbnail():
    """非工具树分支（user 消息带图片）仍追加缩略图，未受 _tool_tree_rows 改造影响。"""
    b64 = _png_b64(12, 8, 3)
    msgs = [{"role": "user", "content": [
        {"type": "text", "text": "看这张图"},
        {"type": "image_url",
         "image_url": {"url": "data:image/png;base64," + b64}},
    ]}]
    records, _ = _records_from_messages(msgs)
    rec = next(r for r in records if r.kind == "user")
    rows, _ = _inspector_content_rows(rec, 60)
    styled = [r for r in rows if isinstance(r, list)]
    assert styled and styled[-1][0].text == "\u2580"


def test_inspector_deps_non_tool_image_flat():
    """P2-3：非 tool 记录（user 带图）的检查器内容 deps 展平为原子值。

    修复前 _detail_deps 非 tool 分支返回嵌套 tuple（图片 sha 元组），use_memo
    deps 对嵌套 tuple 按 is 恒 miss → 带图 user/assistant 记录内容行每帧全量
    重建；现展平为 ";".join(...) str 原子值。
    """
    b64 = _png_b64(12, 8, 3)
    msgs = [{"role": "user", "content": [
        {"type": "text", "text": "看这张图"},
        {"type": "image_url",
         "image_url": {"url": "data:image/png;base64," + b64}},
    ]}]
    records, _ = _records_from_messages(msgs)
    rec = next(r for r in records if r.kind == "user")
    deps = _inspector_content_deps(rec, 60)
    assert all(isinstance(x, (int, str)) or x is None for x in deps)
    assert any(isinstance(x, str) and len(x) == 64 for x in deps)


# ── P3：_merge_call_lines 支持 images（缓存 + 图片摘要行） ────────────

def test_merge_call_lines_images_appends_summary_and_caches():
    img = {"media_type": "image/png", "sha": "a" * 64, "approx_bytes": 1234}
    lines = _merge_call_lines("read_image path", "", ["a", "b"], [img])
    assert lines == ["read_image path", "a", "b", "\u005b\u56fe\u7247 image/png \u007e1KB\u005d"]
    # 同键命中缓存：返回同一列表引用（rec.lines 共享引用契约）
    again = _merge_call_lines("read_image path", "", ["a", "b"], [img])
    assert again is lines
    # 不同图片（sha 不同）→ 缓存键变化 → 不同引用
    other = _merge_call_lines("read_image path", "", ["a", "b"],
                              [{"media_type": "image/png", "sha": "b" * 64, "approx_bytes": 1}])
    assert other is not lines


# ── read_image 输出移除技术尾注（Trace 噪音根因） ─────────────────────

async def test_read_image_output_no_tailnote(tmp_path, monkeypatch):
    from src.tools.read_image import is_multimodal_model
    monkeypatch.setattr("src.tools.read_image.is_multimodal_model", lambda m: True)
    from PIL import Image
    p = tmp_path / "t.png"
    Image.new("RGB", (4, 4)).save(str(p), format="PNG")
    out = await ReadImageFunc(path=str(p)).execute()
    # 核心元信息保留（Trace 台账/检查器可读）
    assert "图片:" in out
    assert "尺寸:" in out
    assert "格式: PNG" in out
    # 不再把「图片已编码为 base64 PNG…」「模式: 多模态」「预计占用…」
    # 「如需细节…」等技术提示拼进返回文本（Trace 返回值树叶噪音）
    assert "已编码为 base64 PNG" not in out
    assert "随 content blocks 返回" not in out
    assert "模式: 多模态" not in out
    assert "预计占用" not in out
    assert "如需细节" not in out


# ── 缩略图等比放大（小图不再窄条，上限防模糊） ───────────────────────

def test_thumbnail_upscale_small_image():
    img = parse_image_blocks([{
        "type": "image_url",
        "image_url": {"url": "data:image/png;base64," + _png_b64(4, 4, 1)},
    }])[0]
    rows = thumbnail_rows(img, 70)
    widths = {len(r) for r in rows}
    # 4x4 原图 4 列；允许放大后应明显变宽（但受限 _THUMB_MAX_UPSCALE=4 → ≤16 列）
    assert max(widths) > 8
    assert max(widths) <= 16
