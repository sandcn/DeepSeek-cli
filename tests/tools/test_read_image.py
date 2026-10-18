"""read_image 工具测试（对齐 DSH read_image 契约，2026-10）。

覆盖：
- ``file_path`` 首选参数 + ``path`` 兼容别名
- 扩展名门禁 / 无扩展名按内容识别
- magic-byte / 声明类型一致校验
- 精确路由模型 image 输入门禁
- 8-bit sRGB 规范化（16 位 PNG 拒绝）
- durable attachment 输出（``{"type": "image", "attachment": {...}}``）
- 自动降采样（2048×2048 总像素预算）+ 原尺寸坐标倍数
- 编码策略（干净 8-bit sRGB 直通；alpha→WebP；不透明→JPEG）
- 源限制（20 MiB / 6400 万像素 / 单边 8192）
- 分块裁剪 / 图像操作（本项目 DSH 超集）
- 稳定错误码（``Error: <CODE>: …``）
- ToolResult / Anthropic 转换 / 缩略图 / content_to_text / token 估算
"""

from __future__ import annotations

import base64
import io

import pytest

pytest.importorskip("PIL")  # read_image 依赖 Pillow，未安装时跳过全部测试

from src.tools.read_image import ReadImageFunc, IMAGE_EXTENSIONS
from src.tools.base import ToolResult
from src.core.tool_executor_async import ToolScheduler
from src.core.attachments import load_block_image, resolve_attachment_blocks
from src.core.multimodal import content_to_text
from src.core.image_tokens import estimate_image_block_tokens
from src.api.adapters.anthropic import _convert_tool_content_blocks


def _make_image_bytes(w: int = 4, h: int = 3, fmt: str = "PNG") -> bytes:
    """生成简单 RGBA 测试图像（每像素颜色与坐标相关）。"""
    from PIL import Image
    img = Image.new("RGBA", (w, h))
    for y in range(h):
        for x in range(w):
            img.putpixel((x, y), (x * 60 % 256, y * 80 % 256, 30, 255))
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


def _write_test_image(path, w: int = 4, h: int = 3, fmt: str = "PNG") -> str:
    with open(path, "wb") as f:
        f.write(_make_image_bytes(w, h, fmt))
    return str(path)


def _multimodal(monkeypatch, value: bool = True):
    """强制 read_image 的模型输入模态判定结果为 value。"""
    monkeypatch.setattr(
        "src.tools.read_image.model_supports_multimodal", lambda m: value,
    )


def _attachment(f: ReadImageFunc) -> dict:
    blocks = f.result_blocks
    assert isinstance(blocks, list) and len(blocks) == 2
    assert blocks[0]["type"] == "text"
    assert blocks[1]["type"] == "image"
    assert "attachment" in blocks[1]
    return blocks[1]["attachment"]


def _payload(f: ReadImageFunc) -> tuple[str, bytes]:
    blocks = f.result_blocks
    assert blocks is not None
    return load_block_image(blocks[1])[:2]


# ── 0. 参数名 / 扩展名门禁 ────────────────────────────

async def test_file_path_argument(tmp_path, monkeypatch):
    """DSH 对齐：file_path 是首选参数。"""
    _multimodal(monkeypatch, True)
    p = _write_test_image(tmp_path / "t.png", 2, 2)
    f = ReadImageFunc.from_args({"file_path": p})
    out = await f.execute()
    assert out.startswith(p)
    assert f.result_blocks is not None


async def test_path_alias_argument(tmp_path, monkeypatch):
    """旧参数 path 保留兼容。"""
    _multimodal(monkeypatch, True)
    p = _write_test_image(tmp_path / "t.png", 2, 2)
    f = ReadImageFunc.from_args({"path": p})
    out = await f.execute()
    assert out.startswith(p)
    assert f.result_blocks is not None


async def test_format_gate_rejects_txt(tmp_path):
    """非图片扩展名（.txt）→ 扩展名门禁拒绝（稳定错误码）。"""
    p = tmp_path / "t.txt"
    p.write_text("hello", encoding="utf-8")
    out = await ReadImageFunc(file_path=str(p)).execute()
    assert out.startswith("Error: IMAGE_UNSUPPORTED_EXTENSION:")
    assert "does not declare a supported image format" in out


async def test_extensionless_file_detected_from_content(tmp_path, monkeypatch):
    """无扩展名 → 按文件内容（magic-byte）识别为 PNG。"""
    _multimodal(monkeypatch, True)
    p = tmp_path / "noext"
    p.write_bytes(_make_image_bytes(3, 2, "PNG"))
    f = ReadImageFunc(file_path=str(p))
    out = await f.execute()
    assert not out.startswith("Error:")
    media_type, data = _payload(f)
    assert media_type == "image/png"
    assert data == p.read_bytes()  # 干净 PNG 字节直通


async def test_extensionless_non_image_rejected(tmp_path, monkeypatch):
    """无扩展名且内容不是图像 → IMAGE_INVALID。"""
    _multimodal(monkeypatch, True)
    p = tmp_path / "noext"
    p.write_bytes(b"not an image at all")
    out = await ReadImageFunc(file_path=str(p)).execute()
    assert out.startswith("Error: IMAGE_INVALID:")


async def test_bmp_converts_to_jpeg(tmp_path, monkeypatch):
    """BMP（非 vision 原生格式）→ 读取成功并编码为 JPEG（不透明）。"""
    _multimodal(monkeypatch, True)
    from PIL import Image
    p = tmp_path / "t.bmp"
    Image.new("RGB", (2, 2)).save(str(p), format="BMP")
    f = ReadImageFunc(file_path=str(p))
    out = await f.execute()
    assert "image image/jpeg image" in out
    media_type, raw = _payload(f)
    assert media_type == "image/jpeg"
    assert raw[:2] == b"\xff\xd8"


# ── 1. 能力门禁（精确路由模型 image 输入） ─────────────

async def test_capability_gate_refuses_non_multimodal(tmp_path, monkeypatch):
    """未声明 image 输入 → DSH 式拒绝。"""
    _multimodal(monkeypatch, False)
    p = _write_test_image(tmp_path / "t.png", 2, 2)
    out = await ReadImageFunc(file_path=str(p)).execute()
    assert out.startswith("Error: IMAGE_MODEL_INCAPABLE:")
    assert "does not declare image input" in out
    assert "switch to an image-capable model" in out


async def test_capability_gate_uses_routed_agent_model(tmp_path, monkeypatch):
    """门禁取「调用代理当前路由模型」（agent.model），而非全局 MODEL。"""
    seen = []

    def spy(model):
        seen.append(model)
        return model == "vision-model"

    monkeypatch.setattr("src.tools.read_image.model_supports_multimodal", spy)
    p = _write_test_image(tmp_path / "t.png", 2, 2)
    f = ReadImageFunc(file_path=str(p))

    class FakeAgent:
        model = "vision-model"

    f.set_agent(FakeAgent())
    out = await f.execute()
    assert seen and seen[0] == "vision-model"
    assert not out.startswith("Error:")
    assert f.result_blocks is not None


# ── 2. magic-byte / 声明类型一致校验 ──────────────────

async def test_type_mismatch_png_bytes_jpg_ext(tmp_path, monkeypatch):
    """.jpg 扩展名但内容是 PNG → IMAGE_TYPE_MISMATCH。"""
    _multimodal(monkeypatch, True)
    p = tmp_path / "t.jpg"
    p.write_bytes(_make_image_bytes(2, 2, "PNG"))
    out = await ReadImageFunc(file_path=str(p)).execute()
    assert out.startswith("Error: IMAGE_TYPE_MISMATCH:")
    assert "extension declares image/jpeg" in out
    assert "use a different image format" in out
    assert "rename the file" in out


# ── 3. 8-bit sRGB 规范化 ─────────────────────────────

async def test_16bit_png_refused(tmp_path, monkeypatch):
    """16 位 PNG → 无法归一化为 8-bit sRGB（DSH 文案）。"""
    _multimodal(monkeypatch, True)
    from PIL import Image
    p = tmp_path / "t16.png"
    Image.new("I;16", (2, 2)).save(str(p), format="PNG")
    out = await ReadImageFunc(file_path=str(p)).execute()
    assert out.startswith("Error: IMAGE_NORMALIZE_FAILED:")
    assert "the 16-bit PNG could not be converted" in out


# ── 4. durable attachment 输出 ────────────────────────

async def test_attachment_output(tmp_path, monkeypatch):
    """成功 → ``{"type": "image", "attachment": ref}``（不再内联 data URI）。"""
    _multimodal(monkeypatch, True)
    p = _write_test_image(tmp_path / "t.png", 2, 2)
    f = ReadImageFunc(file_path=p)
    out = await f.execute()
    ref = _attachment(f)
    assert ref["attachmentId"].startswith("sha256:")
    assert ref["mediaType"] == "image/png"
    assert ref["bytes"] > 0
    assert ref["width"] == 2 and ref["height"] == 2
    # 返回文本为单行 envelope（DSH 风格）
    assert "\n" not in out
    assert out == (
        f"{p} image image/png image, 2x2 px, {ref['bytes']} bytes"
    )
    # 附件字节可读取且与直通输入一致
    media_type, data = _payload(f)
    assert media_type == "image/png"
    assert data == open(p, "rb").read()


async def test_attachment_dedup(tmp_path, monkeypatch):
    """相同图片两次读取 → 同一 attachmentId（内容寻址去重）。"""
    _multimodal(monkeypatch, True)
    p1 = _write_test_image(tmp_path / "a.png", 2, 2)
    p2 = tmp_path / "b.png"
    p2.write_bytes(open(p1, "rb").read())
    f1, f2 = ReadImageFunc(file_path=p1), ReadImageFunc(file_path=str(p2))
    await f1.execute()
    await f2.execute()
    assert _attachment(f1)["attachmentId"] == _attachment(f2)["attachmentId"]


async def test_result_blocks_reset_on_failure(tmp_path, monkeypatch):
    """同一实例：成功后失败路径重置 result_blocks（防残留误包装）。"""
    _multimodal(monkeypatch, True)
    p = _write_test_image(tmp_path / "t.png")
    f = ReadImageFunc(file_path=p)
    await f.execute()
    assert f.result_blocks is not None
    f.path = str(tmp_path / "nope.png")
    out2 = await f.execute()
    assert out2.startswith("Error: FS_NOT_FOUND:")
    assert f.result_blocks is None


# ── 5. 分块读取 / 图像操作（DSH 超集） ────────────────

async def test_region_crop(tmp_path, monkeypatch):
    """start_x/start_y/end_x/end_y 指定区域裁剪（含端点）。"""
    _multimodal(monkeypatch, True)
    p = _write_test_image(tmp_path / "t.png", 4, 3)
    f = ReadImageFunc(
        file_path=p, start_x=1, start_y=0, end_x=2, end_y=1)
    out = await f.execute()
    assert "2x2 px" in out
    assert "region 1,0-2,1" in out
    assert _attachment(f)["width"] == 2
    assert _attachment(f)["height"] == 2


async def test_region_reversed_swaps(tmp_path, monkeypatch):
    """start > end 时自动交换（与 read 语义一致）。"""
    _multimodal(monkeypatch, True)
    p = _write_test_image(tmp_path / "t.png", 4, 3)
    f = ReadImageFunc(
        file_path=p, start_x=3, end_x=0, start_y=2, end_y=0)
    out = await f.execute()
    assert "4x3 px" in out


async def test_operation_grayscale(tmp_path, monkeypatch):
    _multimodal(monkeypatch, True)
    p = _write_test_image(tmp_path / "t.png", 1, 1)
    f = ReadImageFunc(file_path=p, operation="grayscale")
    out = await f.execute()
    assert "operation grayscale" in out
    # 灰度 → 不透明 RGB → JPEG
    assert _attachment(f)["mediaType"] == "image/jpeg"


async def test_operation_rotate90(tmp_path, monkeypatch):
    """4x2 顺时针旋转 → 2x4。"""
    _multimodal(monkeypatch, True)
    p = _write_test_image(tmp_path / "t.png", 4, 2)
    f = ReadImageFunc(file_path=p, operation="rotate90")
    out = await f.execute()
    assert "operation rotate90" in out
    assert "2x4 px" in out


async def test_operation_flip_h(tmp_path, monkeypatch):
    _multimodal(monkeypatch, True)
    p = _write_test_image(tmp_path / "t.png", 2, 1)
    f = ReadImageFunc(file_path=p, operation="flip_h")
    out = await f.execute()
    assert "operation flip_h" in out


async def test_operation_scale(tmp_path, monkeypatch):
    _multimodal(monkeypatch, True)
    p = _write_test_image(tmp_path / "t.png", 4, 2)
    f = ReadImageFunc(
        file_path=p, operation="scale", scale_width=8, scale_height=4)
    out = await f.execute()
    assert "operation scale" in out
    assert "8x4 px" in out


async def test_operation_scale_proportional(tmp_path, monkeypatch):
    """scale 只给一个维度时按比例计算另一维。"""
    _multimodal(monkeypatch, True)
    p = _write_test_image(tmp_path / "t.png", 4, 2)
    f = ReadImageFunc(file_path=p, operation="scale", scale_width=8)
    out = await f.execute()
    assert "8x4 px" in out


async def test_operation_invalid_falls_back(tmp_path, monkeypatch):
    """非法 operation 回退 none。"""
    _multimodal(monkeypatch, True)
    p = _write_test_image(tmp_path / "t.png", 2, 2)
    f = ReadImageFunc.from_args({"file_path": p, "operation": "blur_xyz"})
    assert f.operation == "none"
    out = await f.execute()
    assert "; operation" not in out


# ── 6. 错误路径 ──────────────────────────────────────

async def test_file_not_exists(tmp_path, monkeypatch):
    # 对齐 DSH：能力门禁先于文件系统解析；测试需声明 image 输入。
    _multimodal(monkeypatch, True)
    out = await ReadImageFunc(
        file_path=str(tmp_path / "nope.png")).execute()
    assert out.startswith("Error: FS_NOT_FOUND:")
    assert "not found" in out


async def test_directory_not_regular_file(tmp_path, monkeypatch):
    _multimodal(monkeypatch, True)
    d = tmp_path / "dir.png"
    d.mkdir()
    out = await ReadImageFunc(file_path=str(d)).execute()
    assert out.startswith("Error: FS_NOT_REGULAR_FILE:")
    assert "not a regular file" in out


async def test_dangerous_path_rejected():
    import os

    unsafe = "/etc/passwd" if os.name != "nt" else "\\\\.\\NUL"
    with pytest.raises(ValueError):
        ReadImageFunc(file_path=unsafe)


async def test_missing_path_raises():
    with pytest.raises(ValueError):
        ReadImageFunc.from_args({})


async def test_source_byte_limit(tmp_path, monkeypatch):
    """源文件超过 20 MiB → IMAGE_TOO_LARGE（DSH 可恢复文案）。"""
    _multimodal(monkeypatch, True)
    p = _write_test_image(tmp_path / "t.png", 2, 2)
    monkeypatch.setattr("src.tools.read_image._MAX_FILE_SIZE_BYTES", 4)
    out = await ReadImageFunc(file_path=p).execute()
    assert out.startswith("Error: IMAGE_TOO_LARGE:")
    assert "byte limits" in out and "downscale" in out


async def test_pixel_limit(tmp_path, monkeypatch):
    _multimodal(monkeypatch, True)
    p = _write_test_image(tmp_path / "t.png", 4, 3)
    monkeypatch.setattr("src.tools.read_image._MAX_IMAGE_PIXELS", 1)
    out = await ReadImageFunc(file_path=p).execute()
    assert out.startswith("Error: IMAGE_TOO_MANY_PIXELS:")
    assert "decoded-size limit" in out


async def test_dimension_limit(tmp_path, monkeypatch):
    _multimodal(monkeypatch, True)
    p = _write_test_image(tmp_path / "t.png", 4, 3)
    monkeypatch.setattr("src.tools.read_image._MAX_IMAGE_DIMENSION", 1)
    out = await ReadImageFunc(file_path=p).execute()
    assert out.startswith("Error: IMAGE_DIMENSION_TOO_LARGE:")
    assert "side exceeds" in out


# ── 7. 自动降采样 / 编码策略 ─────────────────────────

async def test_auto_downscale_budget(tmp_path, monkeypatch):
    """超过 2048×2048 总像素预算 → 自动等比缩小并给出坐标倍数。"""
    _multimodal(monkeypatch, True)
    from PIL import Image
    p = tmp_path / "big.png"
    Image.new("RGB", (2500, 2500), (10, 20, 30)).save(str(p), format="PNG")
    f = ReadImageFunc(file_path=str(p))
    out = await f.execute()
    assert "downscaled from 2500x2500 px" in out
    assert "multiply coordinates by" in out
    ref = _attachment(f)
    assert ref["width"] == 2048 and ref["height"] == 2048
    assert ref["originalDimensions"] == {"width": 2500, "height": 2500}
    assert ref["mediaType"] == "image/jpeg"


async def test_clean_png_passthrough(tmp_path, monkeypatch):
    """干净 8-bit sRGBA PNG 在预算内 → 字节直通（不重编码）。"""
    _multimodal(monkeypatch, True)
    p = _write_test_image(tmp_path / "t.png", 4, 4)
    f = ReadImageFunc(file_path=p)
    await f.execute()
    media_type, data = _payload(f)
    assert media_type == "image/png"
    assert data == open(p, "rb").read()


async def test_clean_webp_passthrough(tmp_path, monkeypatch):
    """干净单帧 WebP（Pillow 容器默认字段）→ 字节直通。"""
    _multimodal(monkeypatch, True)
    from PIL import Image
    p = tmp_path / "t.webp"
    Image.new("RGB", (4, 4), (1, 2, 3)).save(str(p), format="WEBP")
    f = ReadImageFunc(file_path=str(p))
    await f.execute()
    media_type, data = _payload(f)
    assert media_type == "image/webp"
    assert data == p.read_bytes()


async def test_alpha_image_encodes_webp(tmp_path, monkeypatch):
    """非直通路径：alpha 图 → WebP。"""
    _multimodal(monkeypatch, True)
    p = _write_test_image(tmp_path / "t.png", 4, 4)  # RGBA
    f = ReadImageFunc(file_path=p, operation="flip_h")
    await f.execute()
    assert _attachment(f)["mediaType"] == "image/webp"


async def test_opaque_image_encodes_jpeg(tmp_path, monkeypatch):
    """非直通路径：不透明图 → JPEG。"""
    _multimodal(monkeypatch, True)
    from PIL import Image
    p = tmp_path / "t.png"
    Image.new("RGB", (4, 4), (1, 2, 3)).save(str(p), format="PNG")
    f = ReadImageFunc(file_path=p, operation="rotate180")
    await f.execute()
    assert _attachment(f)["mediaType"] == "image/jpeg"


# ── 8. ToolResult / Anthropic 转换 / 展示辅助 ─────────

async def test_run_tool_func_wraps_tool_result(tmp_path, monkeypatch):
    """_run_tool_func：工具设置 result_blocks → 包装为 ToolResult。"""
    _multimodal(monkeypatch, True)
    p = _write_test_image(tmp_path / "t.png")
    f = ReadImageFunc(file_path=p)
    await f.execute()
    scheduler = ToolScheduler()
    out, ok = await scheduler._run_tool_func(f, {"name": "read_image"}, None)
    assert ok is True
    assert isinstance(out, ToolResult)
    assert out.text == f.result_blocks[0]["text"]
    assert out.to_content() == f.result_blocks


def test_convert_tool_content_blocks_attachment():
    """DSH 附件图片 block → Anthropic image block（防御路径）。"""
    from src.core.attachments import save_image, attachment_image_block
    ref = save_image(
        b"\x89PNG\r\n\x1a\n", "image/png", width=1, height=1, name="a.png")
    blocks = [attachment_image_block(ref)]
    converted = _convert_tool_content_blocks(blocks)
    assert converted[0]["type"] == "image"
    assert converted[0]["source"]["type"] == "base64"
    assert base64.b64decode(converted[0]["source"]["data"]) == b"\x89PNG\r\n\x1a\n"


def test_content_to_text_attachment():
    from src.core.attachments import save_image, attachment_image_block
    ref = save_image(
        b"\x89PNG\r\n\x1a\n", "image/png", width=1, height=1)
    text = content_to_text([
        {"type": "text", "text": "看图"},
        attachment_image_block(ref),
    ])
    assert text == "看图 [图片]"


def test_image_tokens_attachment_uses_metadata():
    from src.core.attachments import save_image, attachment_image_block
    ref = save_image(
        b"\x89PNG\r\n\x1a\n", "image/png", width=28, height=28)
    tokens = estimate_image_block_tokens(attachment_image_block(ref))
    assert tokens == 1


# ── 9. content blocks 构造 / schema / display ─────────

def test_tool_schema_mentions_dsh_contract():
    schema = ReadImageFunc.to_tool_schema()["function"]
    desc = schema["description"]
    assert "file_path" in schema["parameters"]["properties"]
    assert "path" in schema["parameters"]["properties"]
    assert "downscaled" in desc or "downscale" in desc
    assert "2048x2048" in desc
    assert "grayscale" in desc
    props = schema["parameters"]["properties"]
    assert "max_dimension" not in props and "max_tokens" not in props


def test_image_extensions_full():
    for ext, mime in {
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".gif": "image/gif", ".webp": "image/webp",
    }.items():
        assert IMAGE_EXTENSIONS[ext] == mime
    assert IMAGE_EXTENSIONS[".bmp"] == "image/bmp"
    assert IMAGE_EXTENSIONS[".tiff"] == "image/tiff"
    assert ".txt" not in IMAGE_EXTENSIONS


def test_from_args_paths_list():
    f = ReadImageFunc.from_args({"paths": ["/tmp/a.png", "/tmp/b.png"]})
    assert f.path == "/tmp/a.png"


def test_display_params():
    assert ReadImageFunc.display_params(
        {"file_path": "/tmp/a.png"}) == "Read image /tmp/a.png"
    d = ReadImageFunc.display_params({
        "file_path": "/tmp/a.png", "operation": "grayscale",
        "start_x": 1, "start_y": 0, "end_x": 2, "end_y": 1,
    })
    assert "Read image /tmp/a.png" in d
    assert "grayscale" in d and "(1,0)-(2,1)" in d
    assert ReadImageFunc.display_params(
        {"path": "/tmp/a.png"}) == "Read image /tmp/a.png"


def test_display_params_partial_coords():
    d = ReadImageFunc.display_params({"file_path": "/tmp/a.png", "start_x": 1})
    assert "None" not in d
    assert "(1,)-(" in d


# ── 10. 附件解析（发送副本） ─────────────────────────

def test_resolve_attachment_blocks():
    from src.core.attachments import save_image, attachment_image_block
    ref = save_image(
        b"\x89PNG\r\n\x1a\n", "image/png", width=1, height=1)
    messages = [{"role": "tool", "content": [
        {"type": "text", "text": "x"},
        attachment_image_block(ref),
    ]}]
    stats = resolve_attachment_blocks(messages)
    assert stats == {"resolved": 1, "missing": 0}
    block = messages[0]["content"][1]
    assert block["type"] == "image_url"
    assert block["image_url"]["url"].startswith("data:image/png;base64,")
    # 原存档 block 不被修改（调用方传发送副本）
    assert attachment_image_block(ref)["type"] == "image"


def test_resolve_missing_attachment_is_text_placeholder():
    messages = [{"role": "tool", "content": [
        {"type": "image", "attachment": {
            "attachmentId": "sha256:" + "0" * 64,
            "mediaType": "image/png",
            "bytes": 1,
            "width": 1,
            "height": 1,
        }},
    ]}]
    stats = resolve_attachment_blocks(messages)
    assert stats == {"resolved": 0, "missing": 1}
    assert messages[0]["content"][0]["type"] == "text"
    assert "图片附件不可用" in messages[0]["content"][0]["text"]
