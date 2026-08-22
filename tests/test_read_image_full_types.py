"""read_image 全量图片类型 + 转换到 deepseek-v4-flash-vision-exp 支持格式测试。

背景：用户要求 read_image 支持全量图片类型（所有 Pillow 可解码图像格式），
并转换为 deepseek-v4-flash-vision-exp 支持的格式（JPEG/PNG/GIF/WebP）——
本实现统一转码为 PNG 返回（模型支持格式之一）。

覆盖：
- 非 vision 原生格式（BMP/TIFF/ICO/PNM(PPM)/TGA/SGI/PCX…）可读取并输出 PNG；
- 输出 data URI 为 image/png（转换到模型支持类型）；
- 扩展名别名归一化（.jpg/.jpeg/.jfif → JPEG，.tif/.tiff → TIFF，.cur → ICO）；
- 非图像扩展名（.pdf/.mpg/.txt）门禁拒绝；
- magic-byte / 声明格式一致校验（.jpg 内容为 PNG → 拒绝）。
"""

from __future__ import annotations

import asyncio
import base64
import io

import pytest

pytest.importorskip("PIL")

from src.tools.read_image import ReadImageFunc, IMAGE_EXTENSIONS, _declared_format


def _make_image_bytes(w: int = 8, h: int = 6, fmt: str = "PNG") -> bytes:
    from PIL import Image
    if fmt == "PPM":
        img = Image.new("RGB", (w, h))
    elif fmt == "ICO":
        img = Image.new("RGBA", (16, 16), (255, 0, 0, 255))
    else:
        img = Image.new("RGB", (w, h))
    for y in range(h):
        for x in range(w):
            img.putpixel((x, y), (x * 30 % 256, y * 40 % 256, (x + y) % 256))
    buf = io.BytesIO()
    try:
        img.save(buf, format=fmt)
    except Exception as e:
        pytest.skip(f"Pillow 缺少 {fmt} 编码能力: {e}")
    return buf.getvalue()


def _write(path, fmt: str):
    with open(str(path), "wb") as f:
        f.write(_make_image_bytes(fmt=fmt))


def _multimodal(monkeypatch, value: bool = True):
    monkeypatch.setattr("src.tools.read_image.is_multimodal_model", lambda m: value)


# ── 多格式读取并转换为 PNG（vision 支持格式） ─────────────

@pytest.mark.parametrize("fmt,ext,mime", [
    ("BMP", ".bmp", "image/bmp"),
    ("TIFF", ".tiff", "image/tiff"),
    ("PPM", ".ppm", "image/x-portable-pixmap"),
    ("ICO", ".ico", "image/vnd.microsoft.icon"),
    ("TGA", ".tga", "image/x-targa"),
    ("SGI", ".sgi", "image/x-sgi"),
    ("PCX", ".pcx", "image/x-pcx"),
])
async def test_non_vision_format_converts_to_png(tmp_path, monkeypatch, fmt, ext, mime):
    """非 vision 原生格式 → 读取成功并统一转码为 PNG（模型支持格式）。"""
    _multimodal(monkeypatch, True)
    p = tmp_path / f"img{ext}"
    _write(p, fmt)
    f = ReadImageFunc(path=str(p))
    out = await f.execute()
    assert "图片:" in out
    assert "格式: PNG" in out  # 输出统一为 PNG（vision 支持）
    blocks = f.result_blocks
    assert blocks is not None and len(blocks) == 2
    url = blocks[1]["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")
    raw = base64.b64decode(url.split(",", 1)[1])
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"


async def test_bmp_alias_dib_converts(tmp_path, monkeypatch):
    """DIB（BMP 变体）→ 读取并转 PNG。"""
    _multimodal(monkeypatch, True)
    p = tmp_path / "img.dib"
    _write(p, "BMP")
    f = ReadImageFunc(path=str(p))
    out = await f.execute()
    assert "格式: PNG" in out


# ── 扩展名别名归一化 ─────────────────────────────────────

def test_declared_format_aliases():
    """扩展名别名归一化到 Pillow 格式名。"""
    assert _declared_format(".jpg") == "JPEG"
    assert _declared_format(".jpeg") == "JPEG"
    assert _declared_format(".jfif") == "JPEG"
    assert _declared_format(".tif") == "TIFF"
    assert _declared_format(".tiff") == "TIFF"
    assert _declared_format(".pnm") == "PPM"
    assert _declared_format(".pbm") == "PPM"
    assert _declared_format(".cur") == "ICO"  # CUR 是 ICO 变体
    assert _declared_format(".apng") == "PNG"


def test_declared_format_non_image_none():
    """非图像扩展名（PDF/视频/数据文件）返回 None（门禁拒绝）。"""
    assert _declared_format(".pdf") is None
    assert _declared_format(".mpg") is None
    assert _declared_format(".h5") is None
    assert _declared_format(".grib") is None
    assert _declared_format(".txt") is None


# ── 门禁拒绝非图像扩展名 ────────────────────────────────

async def test_pdf_path_rejected(tmp_path):
    """PDF/数据文件等非图像扩展名 → 门禁拒绝（不尝试解码）。"""
    p = tmp_path / "doc.pdf"
    p.write_bytes(b"%PDF-1.4 fake")
    out = await ReadImageFunc(path=str(p)).execute()
    assert out.startswith('(cannot read "')
    assert "not a recognized image extension" in out


# ── magic-byte / 声明格式一致校验（保持原契约） ──────────

async def test_declared_mismatch_still_rejected(tmp_path, monkeypatch):
    """.jpg 扩展名但内容是 BMP → 声明与实际格式不符，拒绝。"""
    _multimodal(monkeypatch, True)
    p = tmp_path / "t.jpg"
    with open(str(p), "wb") as f:
        f.write(_make_image_bytes(4, 4, "BMP"))
    out = await ReadImageFunc(path=str(p)).execute()
    assert out.startswith('(cannot read "')
    assert "extension declares image/jpeg" in out
    assert "use a different image format" in out


# ── IMAGE_EXTENSIONS 全量映射一致 ───────────────────────

def test_image_extensions_derived_consistency():
    """IMAGE_EXTENSIONS 与 _declared_format 派生一致。"""
    assert IMAGE_EXTENSIONS[".webp"] == "image/webp"
    assert IMAGE_EXTENSIONS[".gif"] == "image/gif"
    for ext in (".bmp", ".tiff", ".ico", ".ppm", ".sgi", ".pcx"):
        declared_fmt = _declared_format(ext)
        assert declared_fmt is not None
        # 声明的 MIME 非空且与格式名兜底一致
        assert IMAGE_EXTENSIONS[ext]
