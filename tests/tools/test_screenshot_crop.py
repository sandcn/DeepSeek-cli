"""截图裁剪能力（transform + png_decode）单元测试。

覆盖：
  - CropRegion 解析 / 校验 / 越界 / 全图判断 / 字典输出
  - RGB / BGRA 像素裁剪（越界与长度不匹配）
  - PNG 文件级裁剪（原子重写、整图跳过重编码、非 PNG 报错）
  - 零依赖 PNG 解码（色彩类型 0/2/3/4/6、位深 1/2/4/8/16、Adam7 隔行、
    5 种行过滤器、异常数据与尺寸限制）
  - Pillow 交叉验证（环境有 Pillow 时；无则跳过）
"""

from __future__ import annotations

import struct
import zlib

import pytest

from src.tools._screenshot import png as png_mod
from src.tools._screenshot import png_decode, transform
from src.tools._screenshot.transform import CropError, CropRegion

#: Adam7 的 7 个 pass（与解码器同源，用于构造交错测试数据）
_ADAM7 = (
    (0, 0, 8, 8),
    (4, 0, 8, 8),
    (0, 4, 4, 8),
    (2, 0, 4, 4),
    (0, 2, 2, 4),
    (1, 0, 2, 2),
    (0, 1, 1, 2),
)


def _chunk(tag: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + tag
        + payload
        + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
    )


def _build_png(width: int, height: int, bit_depth: int, color_type: int,
               raw: bytes, *, palette: bytes | None = None,
               interlace: int = 0) -> bytes:
    """手工构造 PNG（raw 为含 filter 字节的扫描行数据）。"""
    ihdr = struct.pack(">IIBBBBB", width, height, bit_depth, color_type,
                       0, 0, interlace)
    parts = [png_mod.PNG_SIGNATURE, _chunk(b"IHDR", ihdr)]
    if palette is not None:
        parts.append(_chunk(b"PLTE", palette))
    parts.append(_chunk(b"IDAT", zlib.compress(raw)))
    parts.append(_chunk(b"IEND", b""))
    return b"".join(parts)


def _adam7_raw_rgb(width: int, height: int,
                   pixels: list[tuple[int, int, int]]) -> bytes:
    """把 RGB 像素网格按 Adam7 打包为 filter=0 的扫描行数据。"""
    out = bytearray()
    for x0, y0, dx, dy in _ADAM7:
        columns = list(range(x0, width, dx))
        rows = list(range(y0, height, dy))
        if not columns or not rows:
            continue
        for y in rows:
            out.append(0)
            for x in columns:
                out += bytes(pixels[y * width + x])
    return bytes(out)


def _grid(width: int, height: int) -> list[tuple[int, int, int]]:
    """生成易于断言的 RGB 像素网格（每像素颜色唯一）。"""
    return [(x * 10, y * 20, (x + y) * 7) for y in range(height) for x in range(width)]


def _grid_bytes(pixels: list[tuple[int, int, int]]) -> bytes:
    out = bytearray()
    for pixel in pixels:
        out += bytes(pixel)
    return bytes(out)


# ── CropRegion：解析 ─────────────────────────────────────

def test_parse_variants_are_equivalent():
    for text in ("10,20,300,200", "10 20 300 200", "10，20，300，200", " 10\t20 300 200 "):
        assert CropRegion.parse(text) == CropRegion(10, 20, 300, 200)


def test_parse_rejects_malformed_input():
    for bad in ("", "   ", "1,2,3", "1,2,3,4,5", "a,b,c,d", "1.5,2,3,4",
                "1,2,3,", "x,y,width,height"):
        with pytest.raises(CropError):
            CropRegion.parse(bad)
    with pytest.raises(CropError):
        CropRegion.parse(None)  # type: ignore[arg-type]


def test_region_constructor_validates_numbers():
    with pytest.raises(CropError):
        CropRegion(-1, 0, 10, 10)
    with pytest.raises(CropError):
        CropRegion(0, -5, 10, 10)
    with pytest.raises(CropError):
        CropRegion(0, 0, 0, 10)
    with pytest.raises(CropError):
        CropRegion(0, 0, 10, -1)
    with pytest.raises(CropError):
        CropRegion(0.5, 0, 10, 10)  # type: ignore[arg-type]
    with pytest.raises(CropError):
        CropRegion(True, 0, 10, 10)  # type: ignore[arg-type]


def test_validate_against_boundaries():
    region = CropRegion(1, 2, 3, 4)
    region.validate_against(4, 6)
    CropRegion(0, 0, 4, 6).validate_against(4, 6)
    with pytest.raises(CropError):
        region.validate_against(3, 6)
    with pytest.raises(CropError):
        CropRegion(0, 0, 5, 1).validate_against(4, 6)
    with pytest.raises(CropError):
        region.validate_against(0, 0)


def test_validate_against_out_of_range_message_has_size():
    with pytest.raises(CropError) as excinfo:
        CropRegion(100, 50, 800, 600).validate_against(640, 480)
    message = str(excinfo.value)
    assert "超出截图范围" in message
    assert "640x480" in message
    assert "100" in message and "800" in message


def test_is_full_and_to_dict():
    assert CropRegion(0, 0, 4, 6).is_full(4, 6) is True
    assert CropRegion(1, 0, 4, 6).is_full(4, 6) is False
    assert CropRegion(0, 0, 3, 6).is_full(4, 6) is False
    assert CropRegion(2, 3, 5, 7).to_dict() == {
        "x": 2, "y": 3, "width": 5, "height": 7,
    }


# ── 像素裁剪 ─────────────────────────────────────────────

def test_crop_rgb_extracts_region_and_full_image():
    width, height = 4, 3
    pixels = _grid(width, height)
    raw = _grid_bytes(pixels)
    region = CropRegion(1, 1, 2, 2)
    cropped = transform.crop_rgb(raw, width, height, region)
    expected = bytearray()
    for y in range(1, 3):
        for x in range(1, 3):
            expected += bytes(pixels[y * width + x])
    assert cropped == bytes(expected)
    assert transform.crop_rgb(raw, width, height, CropRegion(0, 0, 4, 3)) == raw


def test_crop_rgb_rejects_out_of_range_and_bad_length():
    raw = b"\x00" * (4 * 3 * 3)
    with pytest.raises(CropError):
        transform.crop_rgb(raw, 4, 3, CropRegion(3, 0, 2, 3))
    with pytest.raises(CropError):
        transform.crop_rgb(b"\x00" * 5, 4, 3, CropRegion(0, 0, 1, 1))


def test_crop_bgra_keeps_four_channel_layout():
    width, height = 3, 2
    bgra = bytes(range(0, 24))
    cropped = transform.crop_bgra(bgra, width, height, CropRegion(1, 1, 2, 1))
    assert cropped == bgra[(1 * 3 + 1) * 4:(1 * 3 + 3) * 4]
    assert len(cropped) == 2 * 4


# ── 文件级裁剪 ───────────────────────────────────────────

def test_apply_crop_to_png_file_rewrites_region(tmp_path):
    width, height = 4, 3
    pixels = _grid(width, height)
    target = tmp_path / "shot.png"
    target.write_bytes(png_mod.encode_png_rgb(width, height, _grid_bytes(pixels)))

    assert transform.apply_crop_to_png_file(str(target), CropRegion(1, 0, 2, 3)) == (2, 3)

    image = png_decode.decode_png_file(str(target))
    assert (image.width, image.height) == (2, 3)
    expected = bytearray()
    for y in range(3):
        for x in range(1, 3):
            expected += bytes(pixels[y * width + x])
    assert image.rgb == bytes(expected)
    assert list(tmp_path.glob("crop-*")) == []


def test_apply_crop_full_region_keeps_file_bytes(tmp_path):
    target = tmp_path / "full.png"
    data = png_mod.encode_png_rgb(2, 2, b"\x01\x02\x03" * 4)
    target.write_bytes(data)
    assert transform.apply_crop_to_png_file(str(target), CropRegion(0, 0, 2, 2)) == (2, 2)
    assert target.read_bytes() == data


def test_apply_crop_rejects_out_of_range_and_non_png(tmp_path):
    target = tmp_path / "shot.png"
    target.write_bytes(png_mod.encode_png_rgb(2, 2, b"\x00" * 12))
    with pytest.raises(CropError):
        transform.apply_crop_to_png_file(str(target), CropRegion(1, 1, 2, 2))
    broken = tmp_path / "broken.png"
    broken.write_bytes(b"not a png")
    with pytest.raises(CropError):
        transform.apply_crop_to_png_file(str(broken), CropRegion(0, 0, 1, 1))


# ── PNG 解码：基础 ───────────────────────────────────────

def test_decode_roundtrip_rgb_and_bgra_encoder():
    pixels = _grid_bytes(_grid(3, 2))
    image = png_decode.decode_png(png_mod.encode_png_rgb(3, 2, pixels))
    assert (image.width, image.height, image.rgb) == (3, 2, pixels)

    bgra = bytes([1, 2, 3, 255, 4, 5, 6, 0])
    image = png_decode.decode_png(png_mod.encode_png_bgra(2, 1, bgra))
    assert image.rgb == bytes([3, 2, 1, 6, 5, 4])


def test_decode_rejects_bad_data():
    with pytest.raises(png_decode.PNGDecodeError):
        png_decode.decode_png(b"")
    with pytest.raises(png_decode.PNGDecodeError):
        png_decode.decode_png(b"not a png at all")
    with pytest.raises(png_decode.PNGDecodeError):
        png_decode.decode_png(png_mod.PNG_SIGNATURE + _chunk(b"IDAT", zlib.compress(b"")))
    ihdr = struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0)
    with pytest.raises(png_decode.PNGDecodeError):
        png_decode.decode_png(png_mod.PNG_SIGNATURE + _chunk(b"IHDR", ihdr))


def test_decode_rejects_bad_ihdr_fields():
    def _png(**overrides):
        fields = {"width": 2, "height": 2, "bit_depth": 8, "color_type": 2,
                  "compression": 0, "filter_method": 0, "interlace": 0}
        fields.update(overrides)
        ihdr = struct.pack(">IIBBBBB", fields["width"], fields["height"],
                           fields["bit_depth"], fields["color_type"],
                           fields["compression"], fields["filter_method"],
                           fields["interlace"])
        return (png_mod.PNG_SIGNATURE + _chunk(b"IHDR", ihdr)
                + _chunk(b"IDAT", zlib.compress(b"\x00" * 3))
                + _chunk(b"IEND", b""))

    for overrides in (
        {"width": 0}, {"height": 0}, {"color_type": 9}, {"bit_depth": 3},
        {"bit_depth": 4, "color_type": 2}, {"compression": 1},
        {"filter_method": 1}, {"interlace": 2},
        {"width": png_mod.MAX_DIMENSION + 1},
    ):
        with pytest.raises(png_decode.PNGDecodeError):
            png_decode.decode_png(_png(**overrides))


def test_decode_reports_truncated_scanlines():
    ihdr = struct.pack(">IIBBBBB", 4, 4, 8, 2, 0, 0, 0)
    data = (png_mod.PNG_SIGNATURE + _chunk(b"IHDR", ihdr)
            + _chunk(b"IDAT", zlib.compress(b"\x00" + b"\x01" * 5))
            + _chunk(b"IEND", b""))
    with pytest.raises(png_decode.PNGDecodeError):
        png_decode.decode_png(data)


def test_decode_rejects_unknown_filter_type():
    raw = bytes([9, 0, 0, 0])
    data = _build_png(1, 1, 8, 2, raw)
    with pytest.raises(png_decode.PNGDecodeError):
        png_decode.decode_png(data)


def test_decode_rejects_palette_index_out_of_range():
    raw = bytes([0, 5])
    data = _build_png(1, 1, 8, 3, raw, palette=bytes([1, 2, 3]))
    with pytest.raises(png_decode.PNGDecodeError):
        png_decode.decode_png(data)


# ── PNG 解码：色彩类型与位深 ─────────────────────────────

def test_decode_grayscale_and_gray_alpha():
    gray = png_decode.decode_png(_build_png(3, 1, 8, 0, bytes([0, 0, 40, 255])))
    assert gray.rgb == bytes([0, 0, 0, 40, 40, 40, 255, 255, 255])

    gray_alpha = png_decode.decode_png(
        _build_png(2, 1, 8, 4, bytes([0, 30, 128, 90, 200])))
    assert gray_alpha.rgb == bytes([30, 30, 30, 90, 90, 90])


def test_decode_rgba_drops_alpha():
    raw = bytes([0, 1, 2, 3, 200, 4, 5, 6, 1])
    image = png_decode.decode_png(_build_png(2, 1, 8, 6, raw))
    assert image.rgb == bytes([1, 2, 3, 4, 5, 6])


def test_decode_palette():
    palette = bytes([10, 20, 30, 40, 50, 60])
    raw = bytes([0, 1, 0])
    image = png_decode.decode_png(_build_png(2, 1, 8, 3, raw, palette=palette))
    assert image.rgb == bytes([40, 50, 60, 10, 20, 30])


def test_decode_low_bit_depth_grayscale():
    raw = bytes([0, 0x0F, 0, 0x88])
    image = png_decode.decode_png(_build_png(2, 2, 4, 0, raw))
    assert image.rgb == bytes([0, 0, 0, 255, 255, 255, 136, 136, 136, 136, 136, 136])

    raw = bytes([0, 0b1010_0000, 0, 0b0101_0000])
    image = png_decode.decode_png(_build_png(4, 2, 1, 0, raw))
    assert image.rgb == bytes(
        [255, 255, 255, 0, 0, 0, 255, 255, 255, 0, 0, 0,
         0, 0, 0, 255, 255, 255, 0, 0, 0, 255, 255, 255])


def test_decode_low_bit_depth_palette():
    palette = bytes([1, 1, 1, 2, 2, 2, 3, 3, 3, 4, 4, 4])
    raw = bytes([0, 0b0001_1011])
    image = png_decode.decode_png(_build_png(4, 1, 2, 3, raw, palette=palette))
    assert image.rgb == bytes([1, 1, 1, 2, 2, 2, 3, 3, 3, 4, 4, 4])


def test_decode_16bit_takes_high_byte():
    raw = bytes([0, 0x12, 0xAB, 0x34, 0xCD, 0x56, 0xEF])
    image = png_decode.decode_png(_build_png(1, 1, 16, 2, raw))
    assert image.rgb == bytes([0x12, 0x34, 0x56])


# ── PNG 解码：行过滤器 ───────────────────────────────────

def test_decode_filter_sub_average_paeth_first_row():
    original = bytes([10, 40, 90, 200])
    cases = {
        1: bytes([10, 30, 50, 110]),
        3: bytes([10, 35, 70, 155]),
        4: bytes([10, 30, 50, 110]),
    }
    for filter_type, raw in cases.items():
        data = _build_png(4, 1, 8, 0, bytes([filter_type]) + raw)
        image = png_decode.decode_png(data)
        expected = b"".join(bytes([value] * 3) for value in original)
        assert image.rgb == expected, f"filter={filter_type}"


def test_decode_filter_up_and_paeth_second_row():
    first = bytes([10, 40, 90, 200])
    second = bytes([200, 10, 200, 10])
    expected = (b"".join(bytes([v] * 3) for v in first)
                + b"".join(bytes([v] * 3) for v in second))

    up_row = bytes((second[i] - first[i]) & 0xFF for i in range(4))
    raw = bytes([0]) + first + bytes([2]) + up_row
    image = png_decode.decode_png(_build_png(4, 2, 8, 0, raw))
    assert image.rgb == expected

    paeth_row = bytes([190, 66, 160, 66])
    raw = bytes([0]) + first + bytes([4]) + paeth_row
    image = png_decode.decode_png(_build_png(4, 2, 8, 0, raw))
    assert image.rgb == expected


# ── PNG 解码：Adam7 隔行 ─────────────────────────────────

def test_decode_adam7_interlaced_rgb():
    width, height = 5, 3
    pixels = _grid(width, height)
    raw = _adam7_raw_rgb(width, height, pixels)
    data = _build_png(width, height, 8, 2, raw, interlace=1)
    image = png_decode.decode_png(data)
    assert (image.width, image.height) == (width, height)
    assert image.rgb == _grid_bytes(pixels)


def test_adam7_png_matches_pillow(tmp_path):
    Image = pytest.importorskip("PIL.Image")
    width, height = 5, 3
    pixels = _grid(width, height)
    target = tmp_path / "interlaced.png"
    target.write_bytes(_build_png(
        width, height, 8, 2, _adam7_raw_rgb(width, height, pixels), interlace=1))

    with Image.open(target) as image:
        assert image.size == (width, height)
        assert list(image.convert("RGB").getdata()) == pixels


def test_decode_matches_pillow_for_common_modes(tmp_path):
    Image = pytest.importorskip("PIL.Image")
    base = Image.new("RGB", (3, 2))
    base.putdata([(255, 0, 0), (0, 255, 0), (0, 0, 255),
                  (10, 20, 30), (200, 100, 50), (7, 7, 7)])
    for mode in ("RGB", "RGBA", "L", "LA", "P", "1"):
        target = tmp_path / f"{mode}.png"
        base.convert(mode).save(target)
        decoded = png_decode.decode_png_file(str(target))
        expected = list(base.convert(mode).convert("RGB").getdata())
        actual = [
            tuple(decoded.rgb[index * 3:index * 3 + 3])
            for index in range(decoded.width * decoded.height)
        ]
        assert actual == expected, f"mode={mode}"


def test_decode_png_file_reports_missing_file(tmp_path):
    with pytest.raises(OSError):
        png_decode.decode_png_file(str(tmp_path / "missing.png"))
