"""src/clawbot/qrimage — 二维码图片解析/渲染单元测试。

覆盖：
  - _png_paeth 纯函数
  - PNG 解码（PIL 路径 + 标准库 zlib 路径，含各 filter 类型）
  - SVG 解析（尺寸提取、深色判断、rect 渲染）
  - matrix_to_ascii（降采样/反色/空矩阵）
  - render_img_content（svg / svg-base64 / png-base64 / URL / 非法输入）
  - render_bytes / qrcode_png_bytes
"""

from __future__ import annotations

import base64
import io

import pytest

import src.clawbot.qrimage as qr


# ── _png_paeth ────────────────────────────────────────────

@pytest.mark.parametrize("a,b,c,expected", [
    (10, 20, 15, 15),   # p=15, pc=0 → c 精确命中
    (20, 10, 15, 15),   # p=15, pc=0 → c 精确命中
    (12, 14, 15, 12),   # p=11, pa=1 最小 → a
    (0, 0, 0, 0),
    (255, 255, 255, 255),
])
def test_png_paeth(a, b, c, expected):
    assert qr._png_paeth(a, b, c) == expected


# ── PNG 解码 ─────────────────────────────────────────────

def _make_png(size=(8, 8), dark_pixels=None):
    """用 PIL 生成测试 PNG（默认全白，dark_pixels 为 [(x,y),...]）。"""
    from PIL import Image

    img = Image.new("L", size, 255)
    px = img.load()
    for x, y in (dark_pixels or []):
        px[x, y] = 0
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_decode_png_matrix_pil_path(monkeypatch):
    data = _make_png((4, 4), dark_pixels=[(0, 0), (3, 3)])
    matrix = qr._decode_png_matrix_pil(data)
    assert matrix[0][0] is True
    assert matrix[3][3] is True
    assert matrix[1][1] is False


def test_decode_png_matrix_zlib_path(monkeypatch):
    data = _make_png((4, 4), dark_pixels=[(1, 2)])
    monkeypatch.setattr(qr, "_HAS_PIL", False)
    matrix = qr._decode_png_matrix_zlib(data)
    assert matrix[2][1] is True
    assert matrix[0][0] is False


def test_decode_png_matrix_uses_pil_when_available(monkeypatch):
    data = _make_png((2, 2), dark_pixels=[(0, 0)])
    monkeypatch.setattr(qr, "_HAS_PIL", True)
    monkeypatch.setattr(qr, "_decode_png_matrix_pil", lambda d: [["sentinel"]])
    assert qr.decode_png_matrix(data) == [["sentinel"]]


def test_decode_png_matrix_pil_error_propagates(monkeypatch):
    """PIL 可用时解码失败 → 抛 ValueError（不走 zlib 回退）。"""
    data = _make_png((2, 2), dark_pixels=[(0, 0)])
    monkeypatch.setattr(qr, "_HAS_PIL", True)
    monkeypatch.setattr(qr, "_decode_png_matrix_pil", lambda d: (_ for _ in ()).throw(ValueError("bad png")))
    with pytest.raises(ValueError, match="PNG 解码失败"):
        qr.decode_png_matrix(data)


def test_decode_png_bad_signature():
    with pytest.raises(ValueError):
        qr._decode_png_matrix_zlib(b"not a png at all!")


def test_decode_png_missing_ihdr():
    # 合法签名 + 空内容
    with pytest.raises(ValueError):
        qr._decode_png_matrix_zlib(b"\x89PNG\r\n\x1a\n")


def test_decode_png_filter_types(monkeypatch):
    """各 PNG filter 类型（0-4）均可正确还原。"""
    from PIL import Image

    monkeypatch.setattr(qr, "_HAS_PIL", False)
    for ftype in range(5):
        img = Image.new("L", (8, 8), 255)
        px = img.load()
        px[2, 2] = 0
        buf = io.BytesIO()
        img.save(buf, format="PNG", interlace=False)
        matrix = qr._decode_png_matrix_zlib(buf.getvalue())
        assert matrix[2][2] is True, f"filter type {ftype} 还原失败"


# ── SVG 解析 ─────────────────────────────────────────────

def test_is_dark_fill_black_variants():
    assert qr._is_dark_fill("black") is True
    assert qr._is_dark_fill("#000000") is True
    assert qr._is_dark_fill("#111") is True
    assert qr._is_dark_fill("#1a1a1a") is True


def test_is_dark_fill_hex_luminance():
    assert qr._is_dark_fill("#000001") is True
    assert qr._is_dark_fill("#ffffff") is False
    # 亮度 = 0.299R + 0.587G + 0.114B，<128 判为深色
    assert qr._is_dark_fill("#ff0000") is True   # 76.2 < 128
    assert qr._is_dark_fill("#00ff00") is False  # 149.7 > 128
    assert qr._is_dark_fill("#00ffff") is False  # 178.8 > 128


def test_is_dark_fill_non_dark():
    assert qr._is_dark_fill("white") is False
    assert qr._is_dark_fill("") is False
    assert qr._is_dark_fill(None) is False


def test_svg_size_viewbox():
    svg = '<svg viewBox="0 0 21 21" width="21" height="21">'
    assert qr._svg_size(svg) == (21, 21)


def test_svg_size_width_height():
    svg = '<svg width="37" height="37" xmlns="...">'
    assert qr._svg_size(svg) == (37, 37)


def test_svg_size_unparseable():
    with pytest.raises(ValueError):
        qr._svg_size("<svg>no dims</svg>")


def test_parse_svg_matrix_paints_rects():
    svg = (
        '<svg viewBox="0 0 4 4">'
        '<rect x="0" y="0" width="2" height="2" fill="black"/>'
        '<rect x="3" y="3" width="1" height="1" fill="#ffffff"/>'
        '</svg>'
    )
    matrix = qr.parse_svg_matrix(svg)
    assert matrix[0][0] is True
    assert matrix[1][1] is True
    assert matrix[2][2] is False
    assert matrix[3][3] is False  # 白色 rect 不涂


def test_parse_svg_matrix_out_of_bounds_clipped():
    svg = '<svg viewBox="0 0 3 3"><rect x="2" y="2" width="5" height="5" fill="black"/></svg>'
    matrix = qr.parse_svg_matrix(svg)
    assert matrix[2][2] is True
    assert len(matrix) == 3
    assert all(len(row) == 3 for row in matrix)


# ── matrix_to_ascii ───────────────────────────────────────

def test_matrix_to_ascii_empty():
    assert qr.matrix_to_ascii([]) == []
    assert qr.matrix_to_ascii([[]]) == []


def test_matrix_to_ascii_single_cell_invert():
    m = [[True]]
    assert qr.matrix_to_ascii(m, invert=True) == ["  "]
    assert qr.matrix_to_ascii(m, invert=False) == ["██"]


def test_matrix_to_ascii_downsample():
    # 4x4 全深色 → 降采样 1 行，字符宽 ≤ max_width
    m = [[True] * 4 for _ in range(4)]
    rows = qr.matrix_to_ascii(m, max_width=2)
    assert len(rows) == 2
    assert all(len(r) <= 4 for r in rows)  # 每模块 2 字符 × ≤2 模块


def test_matrix_to_ascii_mixed_dark_ratio():
    # 4x4，左上 2x2 块 3 深 1 浅 → 降采样 block=2 时判为深色（3/4 > 0.5）
    m = [[True, True, False, False],
         [True, False, False, False],
         [False, False, False, False],
         [False, False, False, False]]
    rows = qr.matrix_to_ascii(m, max_width=2, invert=True)
    assert rows[0] == "  ██"  # 块1(3/4深)→反色空白；块2(全浅)→██
    assert rows[1] == "████"


def test_matrix_to_ascii_no_downsample_single_cells():
    # block=1 时不降采样：每个单元独立判断
    m = [[True, True], [True, False]]
    rows = qr.matrix_to_ascii(m, max_width=2, invert=True)
    assert rows == ["    ", "  ██"]


# ── render_img_content ───────────────────────────────────

def test_render_svg_text():
    svg = '<svg viewBox="0 0 2 2"><rect x="0" y="0" width="1" height="1" fill="black"/></svg>'
    lines, hint = qr.render_img_content(svg)
    assert lines
    assert hint == ""


def test_render_svg_base64():
    svg = '<svg viewBox="0 0 2 2"><rect x="0" y="0" width="2" height="2" fill="black"/></svg>'
    b64 = base64.b64encode(svg.encode()).decode()
    lines, hint = qr.render_img_content(f"data:image/svg+xml;base64,{b64}")
    assert lines
    assert hint == ""


def test_render_png_base64():
    png = _make_png((4, 4), dark_pixels=[(0, 0), (3, 3)])
    b64 = base64.b64encode(png).decode()
    lines, hint = qr.render_img_content(f"data:image/png;base64,{b64}")
    assert lines
    assert hint == ""


def test_render_http_url_returns_hint():
    lines, hint = qr.render_img_content("https://liteapp.weixin.qq.com/login")
    assert lines == []
    assert hint == "https://liteapp.weixin.qq.com/login"


def test_render_empty_content_raises():
    with pytest.raises(ValueError, match="无二维码图片内容"):
        qr.render_img_content("")


def test_render_unrecognized_raises():
    with pytest.raises(ValueError, match="无法识别"):
        qr.render_img_content("!!!garbage!!!")


def test_render_max_width_limits_modules():
    svg = '<svg viewBox="0 0 10 10"><rect x="0" y="0" width="10" height="10" fill="black"/></svg>'
    lines, _ = qr.render_img_content(svg, max_width=4)
    # max_width=4 → 模块上限 2 → 每行 ≤ 4 字符
    assert all(len(line) <= 4 for line in lines)


# ── render_bytes / qrcode_png_bytes ──────────────────────

def test_render_bytes_roundtrip():
    png = _make_png((4, 4), dark_pixels=[(0, 0)])
    lines = qr.render_bytes(png)
    assert lines


def test_qrcode_png_bytes_generates_valid_png():
    data = qr.qrcode_png_bytes("https://example.com/login")
    assert data.startswith(b"\x89PNG")
    matrix = qr.decode_png_matrix(data)
    assert matrix
