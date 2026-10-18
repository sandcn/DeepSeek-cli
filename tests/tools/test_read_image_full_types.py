"""read_image 全量图片类型 + DSH 编码策略测试。

背景（2026-10 对齐 DSH）：
- read_image 支持所有 Pillow 可解码图像格式；
- 干净 8-bit sRGB/sRGBA PNG/JPEG/WebP 在预算内字节直通；
- 其余输入规范化后：alpha → WebP、不透明 → JPEG（质量阶梯 85/75/60）；
- 有扩展名时扩展名必须声明图像格式；无扩展名时按内容识别；
- magic-byte / 声明格式一致校验（.jpg 内容为 PNG → 拒绝）。
"""

from __future__ import annotations

import io

import pytest

pytest.importorskip("PIL")

from src.core.attachments import load_block_image
from src.tools.read_image import ReadImageFunc, IMAGE_EXTENSIONS, _declared_format


def _make_image_bytes(w: int = 8, h: int = 6, fmt: str = "PNG",
                      mode: str = "RGB") -> bytes:
    from PIL import Image
    if fmt == "ICO":
        img = Image.new("RGBA", (16, 16), (255, 0, 0, 255))
    else:
        img = Image.new(mode, (w, h))
    for y in range(h):
        for x in range(w):
            img.putpixel((x, y), (x * 30 % 256, y * 40 % 256, (x + y) % 256))
    buf = io.BytesIO()
    try:
        img.save(buf, format=fmt)
    except Exception as e:
        pytest.skip(f"Pillow 缺少 {fmt} 编码能力: {e}")
    return buf.getvalue()


def _write(path, fmt: str, mode: str = "RGB"):
    with open(str(path), "wb") as f:
        f.write(_make_image_bytes(fmt=fmt, mode=mode))


def _multimodal(monkeypatch, value: bool = True):
    monkeypatch.setattr(
        "src.tools.read_image.model_supports_multimodal", lambda m: value,
    )


def _payload(f: ReadImageFunc):
    return load_block_image(f.result_blocks[1])[:2]


# ── 多格式读取并编码为模型支持格式（JPEG/WebP） ─────────

@pytest.mark.parametrize("fmt,ext", [
    ("BMP", ".bmp"),
    ("TIFF", ".tiff"),
    ("PPM", ".ppm"),
    ("TGA", ".tga"),
    ("SGI", ".sgi"),
    ("PCX", ".pcx"),
])
async def test_non_vision_format_encodes_supported(tmp_path, monkeypatch, fmt, ext):
    """非 vision 原生格式 → 读取成功并编码为 JPEG（不透明）。"""
    _multimodal(monkeypatch, True)
    p = tmp_path / f"img{ext}"
    _write(p, fmt)
    f = ReadImageFunc(file_path=str(p))
    out = await f.execute()
    assert not out.startswith("Error:")
    media_type, raw = _payload(f)
    assert media_type == "image/jpeg"
    assert raw[:2] == b"\xff\xd8"
    assert "image image/jpeg image" in out


async def test_ico_alpha_encodes_webp(tmp_path, monkeypatch):
    """ICO（RGBA）→ alpha 路由 WebP。"""
    _multimodal(monkeypatch, True)
    p = tmp_path / "img.ico"
    _write(p, "ICO")
    f = ReadImageFunc(file_path=str(p))
    out = await f.execute()
    assert not out.startswith("Error:")
    media_type, raw = _payload(f)
    assert media_type == "image/webp"
    assert raw[:4] == b"RIFF"


async def test_bmp_alias_dib_converts(tmp_path, monkeypatch):
    """DIB（BMP 变体）→ 读取并编码。"""
    _multimodal(monkeypatch, True)
    p = tmp_path / "img.dib"
    _write(p, "BMP")
    f = ReadImageFunc(file_path=str(p))
    out = await f.execute()
    assert not out.startswith("Error:")
    assert "image image/jpeg image" in out


async def test_extensionless_bmp_detected(tmp_path, monkeypatch):
    """无扩展名 BMP 字节 → 按内容识别并编码。"""
    _multimodal(monkeypatch, True)
    p = tmp_path / "noext"
    p.write_bytes(_make_image_bytes(fmt="BMP"))
    f = ReadImageFunc(file_path=str(p))
    out = await f.execute()
    assert not out.startswith("Error:")
    assert "image image/jpeg image" in out


# ── 扩展名别名归一化 ─────────────────────────────────────

def test_declared_format_aliases():
    assert _declared_format(".jpg") == "JPEG"
    assert _declared_format(".jpeg") == "JPEG"
    assert _declared_format(".jfif") == "JPEG"
    assert _declared_format(".tif") == "TIFF"
    assert _declared_format(".tiff") == "TIFF"
    assert _declared_format(".pnm") == "PPM"
    assert _declared_format(".pbm") == "PPM"
    assert _declared_format(".cur") == "ICO"
    assert _declared_format(".apng") == "PNG"


def test_declared_format_non_image_none():
    assert _declared_format(".pdf") is None
    assert _declared_format(".mpg") is None
    assert _declared_format(".h5") is None
    assert _declared_format(".grib") is None
    assert _declared_format(".txt") is None


# ── 门禁拒绝非图像扩展名 ────────────────────────────────

async def test_pdf_path_rejected(tmp_path):
    p = tmp_path / "doc.pdf"
    p.write_bytes(b"%PDF-1.4 fake")
    out = await ReadImageFunc(file_path=str(p)).execute()
    assert out.startswith("Error: IMAGE_UNSUPPORTED_EXTENSION:")
    assert "does not declare a supported image format" in out


# ── magic-byte / 声明格式一致校验 ───────────────────────

async def test_declared_mismatch_still_rejected(tmp_path, monkeypatch):
    """.jpg 扩展名但内容是 BMP → 声明与实际格式不符，拒绝。"""
    _multimodal(monkeypatch, True)
    p = tmp_path / "t.jpg"
    p.write_bytes(_make_image_bytes(4, 4, "BMP"))
    out = await ReadImageFunc(file_path=str(p)).execute()
    assert out.startswith("Error: IMAGE_TYPE_MISMATCH:")
    assert "extension declares image/jpeg" in out
    assert "use a different image format" in out


# ── IMAGE_EXTENSIONS 全量映射一致 ───────────────────────

def test_image_extensions_derived_consistency():
    assert IMAGE_EXTENSIONS[".webp"] == "image/webp"
    assert IMAGE_EXTENSIONS[".gif"] == "image/gif"
    for ext in (".bmp", ".tiff", ".ico", ".ppm", ".sgi", ".pcx"):
        assert _declared_format(ext) is not None
        assert IMAGE_EXTENSIONS[ext]
