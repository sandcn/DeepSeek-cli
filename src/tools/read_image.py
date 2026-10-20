"""read_image — 读取图像文件工具（对齐 DSH read_image 契约 + 本项目扩展）

DSH（DeepSeek Harness）read_image 的加工语义（2026-10 对齐）：
- 参数名 ``file_path``（唯一路径参数，不提供 ``path``）；
- 全量图片类型：接受所有 Pillow 可解码的图像格式（PNG/JPEG/WebP/GIF/BMP/
  TIFF/ICO/PNM(PPM/PGM/PBM)/TGA/SGI/…）；有扩展名时扩展名必须声明图像格式，
  无扩展名时按文件内容（magic-byte / Pillow 解码）识别；
- magic-byte / 声明类型一致校验：解码后实际格式必须与扩展名声明的格式一致，
  否则报 DSH 式「扩展名与实际格式不符，重命名文件」错误；
- 严格图像能力门禁：当前**精确路由模型**必须声明 ``image`` 输入
  （``inputModalities`` 含 image；本项目由模型档案 ``multimodal`` 开关派生），
  否则拒绝并提示切换支持图像输入的模型；
- 规范化：应用 EXIF 方向并转换为 8-bit sRGB 单帧（RGB/RGBA），16 位 PNG 等
  无法转换时返回 DSH 式错误；
- 自动降采样：总像素预算 2048×2048、长边 8192、编码目标 4 MiB；缩小时在
  返回文本中给出原尺寸与坐标换算倍数（模型无需先制作缩略图）；
- 编码策略：干净的 8-bit sRGB/sRGBA PNG/JPEG/WebP 在预算内字节直通；
  否则 alpha 图用 WebP、不透明图用 JPEG（质量阶梯 85/75/60，取满足 4 MiB
  的最小输出）；
- durable attachment：图片字节存入内容寻址附件库，工具结果返回
  ``{"type": "image", "attachment": {...}}`` 引用（发送前展开为 image_url）；
- 返回单行 envelope：``<path> image <mime> image, WxH px, N bytes``（缩小时
  追加 downscaled 说明），不再返回多行中文元信息。

保留当前项目特性（DSH 超集）：
- 分块读取（start_x/start_y/end_x/end_y 像素区域裁剪）
- 图像操作（grayscale / rotate90/180/270 / flip_h / flip_v / scale）
- 路径安全校验拒绝危险路径（设备文件/系统关键路径等）

错误契约对齐 DSH：``Error: <CODE>: <message>``，包含稳定错误码与可执行的
修复提示（not found / not a regular file / 扩展名 / 格式不符 / 16-bit /
字节 / 像素 / 单边限制 / 模型能力）。
"""

from __future__ import annotations

import asyncio
import io
import logging
import math
import os

from .base import Func
from .file_ops import validate_path_security
from ..config.model_profiles import model_supports_multimodal
from ..core.attachments import (
    AttachmentError,
    attachment_image_block,
    save_image,
)

_logger = logging.getLogger(__name__)

# ── 支持的图像操作 ─────────────────────────────────────
_SUPPORTED_OPERATIONS = frozenset({
    "none", "grayscale", "rotate90", "rotate180", "rotate270",
    "flip_h", "flip_v", "scale",
})

# ── 全量图片类型：扩展名 → Pillow 格式名（声明格式） ──
# 覆盖 Pillow 常见可解码图像格式；magic-byte 校验以实际解码为准。
# 有扩展名时扩展名必须声明图像格式；无扩展名时按内容识别。
_EXT_TO_FORMAT: dict[str, str] = {
    ".png": "PNG", ".apng": "PNG",
    ".jpg": "JPEG", ".jpeg": "JPEG", ".jpe": "JPEG", ".jfif": "JPEG",
    ".gif": "GIF",
    ".webp": "WEBP",
    ".bmp": "BMP", ".dib": "BMP",
    ".tif": "TIFF", ".tiff": "TIFF",
    ".ico": "ICO", ".cur": "ICO",
    ".icns": "ICNS",
    ".pbm": "PPM", ".pgm": "PPM", ".pnm": "PPM", ".ppm": "PPM",
    ".tga": "TGA", ".icb": "TGA", ".vda": "TGA", ".vst": "TGA",
    ".sgi": "SGI", ".bw": "SGI", ".rgb": "SGI", ".rgba": "SGI",
    ".ras": "SUN",
    ".pcx": "PCX",
    ".xbm": "XBM",
    ".xpm": "XPM",
    ".psd": "PSD",
    ".im": "IM",
    ".dcx": "DCX",
    ".dds": "DDS",
    ".fli": "FLI", ".flc": "FLI",
    ".fpx": "FPX",
    ".ftc": "FTEX", ".ftu": "FTEX",
    ".gbr": "GBR",
    ".msp": "MSP",
    ".pcd": "PCD",
    ".pxr": "PIXAR",
    ".blp": "BLP",
    ".palm": "PALM",
    ".jp2": "JPEG2000", ".j2k": "JPEG2000", ".j2c": "JPEG2000",
    ".jpc": "JPEG2000", ".jpf": "JPEG2000", ".jpx": "JPEG2000",
    ".mpo": "MPO",
}

# Pillow ``img.format`` → 媒体类型（用于声明媒体类型展示）。
_FORMAT_MEDIA: dict[str, str] = {
    "PNG": "image/png",
    "JPEG": "image/jpeg",
    "GIF": "image/gif",
    "WEBP": "image/webp",
    "BMP": "image/bmp",
    "DIB": "image/bmp",
    "TIFF": "image/tiff",
    "ICO": "image/vnd.microsoft.icon",
    "ICNS": "image/icns",
    "PPM": "image/x-portable-pixmap",
    "TGA": "image/x-targa",
    "SGI": "image/x-sgi",
    "SUN": "image/x-cmu-raster",
    "PCX": "image/x-pcx",
    "XBM": "image/x-xbitmap",
    "XPM": "image/x-xpixmap",
    "PSD": "image/vnd.adobe.photoshop",
    "IM": "image/x-im",
    "DCX": "image/vnd.dcx",
    "DDS": "image/vnd-ms.dds",
    "FLI": "image/x-flic",
    "FPX": "image/vnd.fpx",
    "FTEX": "image/x-ftex",
    "GBR": "image/x-gbr",
    "MSP": "image/x-msp",
    "PCD": "image/x-photo-cd",
    "PIXAR": "image/x-pixar",
    "BLP": "image/x-blp",
    "PALM": "image/x-palm",
    "JPEG2000": "image/jp2",
    "MPO": "image/mpo",
}

# 扩展名 → 声明媒体类型（由 _EXT_TO_FORMAT + _FORMAT_MEDIA 派生，保持一致）。
# 「一切皆插件」：三张表（扩展名→格式 / 格式→媒体类型 / 派生扩展名→媒体类型）
# 登记到 ``const_registry``（清单条目 ``tool_const`` 可覆盖/禁用）；
# ``image_ext_format`` / ``image_format_media`` / ``image_extensions`` 为实时
# 查询入口，模块级名保留向后兼容快照。
from .const_registry import const as _const, declare_constants as _declare_constants  # noqa: E402

IMAGE_EXTENSIONS: dict[str, str] = {
    ext: _FORMAT_MEDIA.get(fmt, f"image/{fmt.lower()}")
    for ext, fmt in _EXT_TO_FORMAT.items()
}

_declare_constants({
    "IMAGE_EXT_FORMAT": _EXT_TO_FORMAT,
    "IMAGE_FORMAT_MEDIA": _FORMAT_MEDIA,
    "IMAGE_EXTENSIONS": IMAGE_EXTENSIONS,
})


def image_ext_format() -> dict:
    """当前生效的「扩展名 → Pillow 格式名」映射（被禁用时为空）。"""
    return dict(_const("IMAGE_EXT_FORMAT", {}) or {})


def image_format_media() -> dict:
    """当前生效的「Pillow 格式名 → 媒体类型」映射（被禁用时为空）。"""
    return dict(_const("IMAGE_FORMAT_MEDIA", {}) or {})


def image_extensions() -> dict:
    """当前生效的「扩展名 → 声明媒体类型」映射（被禁用时为空）。"""
    return dict(_const("IMAGE_EXTENSIONS", {}) or {})


# Pillow 可识别的图像格式名集合兼容快照（用于 registered_extensions 动态过滤：
# 仅接受静态表认可的「图像」格式，排除 PDF/视频/数据文件等非图像扩展名）。
# 运行时经 ``_image_format_names()`` 实时查询（可被清单条目覆盖/禁用）。
_IMAGE_FORMAT_NAMES: frozenset[str] = frozenset(_EXT_TO_FORMAT.values())


def _image_format_names() -> frozenset:
    ext_format = image_ext_format()
    if not ext_format:
        return frozenset()
    return frozenset(ext_format.values())


# ── DSH 对齐限制值 ────────────────────────────────────
# 源准入（对齐 dsh-attachment-local 默认值）
_MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024       # maxImageBytes 20 MiB
_MAX_IMAGE_PIXELS = 64_000_000                # maxImagePixels 6400 万
_MAX_IMAGE_DIMENSION = 8192                   # maxImageDimension 单边 8192
# 规范化预算（对齐 normalizedImage* 默认值）
_NORMALIZED_MAX_PIXELS = 2048 * 2048          # 总像素预算 2048×2048
_NORMALIZED_MAX_DIMENSION = 8192              # 规范化后长边上限
_NORMALIZED_MAX_BYTES = 4 * 1024 * 1024       # 编码目标 4 MiB
_ENCODE_QUALITIES = (85, 75, 60)              # 共用质量阶梯

# 直通允许的结构性标记（不是用户元数据）：JPEG 的 JFIF 文件头、Pillow 为
# 单帧 WebP 填充的容器默认字段。
_PASSTHROUGH_INFO = frozenset({
    "jfif", "jfif_version", "jfif_unit", "jfif_density",
    "jfif_progressive", "jfif_restart_marker",
    "loop", "background", "timestamp", "duration",
})

# ── 稳定错误码（对齐 DSH 的可恢复错误契约） ─────────────
_ERR_INVALID_ARGUMENT = "INVALID_ARGUMENT"
_ERR_NOT_FOUND = "FS_NOT_FOUND"
_ERR_NOT_REGULAR_FILE = "FS_NOT_REGULAR_FILE"
_ERR_UNSUPPORTED_EXTENSION = "IMAGE_UNSUPPORTED_EXTENSION"
_ERR_UNSUPPORTED_FORMAT = "IMAGE_UNSUPPORTED_FORMAT"
_ERR_INVALID_IMAGE = "IMAGE_INVALID"
_ERR_TYPE_MISMATCH = "IMAGE_TYPE_MISMATCH"
_ERR_TOO_LARGE = "IMAGE_TOO_LARGE"
_ERR_TOO_MANY_PIXELS = "IMAGE_TOO_MANY_PIXELS"
_ERR_DIMENSION_TOO_LARGE = "IMAGE_DIMENSION_TOO_LARGE"
_ERR_NORMALIZE_FAILED = "IMAGE_NORMALIZE_FAILED"
_ERR_OPERATION_FAILED = "IMAGE_OPERATION_FAILED"
_ERR_ENCODE_FAILED = "IMAGE_ENCODE_FAILED"
_ERR_READ_FAILED = "IMAGE_READ_FAILED"
_ERR_MODEL_INCAPABLE = "IMAGE_MODEL_INCAPABLE"
_ERR_ATTACHMENT_FAILED = "ATTACHMENT_WRITE_FAILED"

#: 错误文案中展示的支持格式说明（全量 Pillow 解码能力）
_SUPPORTED_FORMATS = (
    "all Pillow-readable image formats "
    "(PNG/JPEG/WebP/GIF/BMP/TIFF/ICO/PNM/TGA/...), including extension-less "
    "files in those formats"
)


def _error(code: str, message: str) -> str:
    """DSH 式错误：``Error: <CODE>: <message>``。"""
    return f"Error: {code}: {message}"


# Pillow transpose 常量（延迟导入 + 兜底数值，避免模块导入期强依赖 PIL）
# 注：Pillow 未安装时 read_image 在 execute 早期即返回错误，不会走到 flip
# 路径——兜底数值仅防御静态引用（review P3 说明）
try:
    from PIL.Image import FLIP_LEFT_RIGHT as _FLIP_LEFT_RIGHT
    from PIL.Image import FLIP_TOP_BOTTOM as _FLIP_TOP_BOTTOM
except ImportError:  # Pillow 未安装：兜底数值（0=左右翻转，1=上下翻转）
    _FLIP_LEFT_RIGHT = 0
    _FLIP_TOP_BOTTOM = 1


def _to_int(value, default: int) -> int:
    """参数转 int，非法/None 时返回默认值。"""
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _clamp(value: int, lo: int, hi: int) -> int:
    """钳制到 [lo, hi]。"""
    return max(lo, min(hi, value))


def _current_model(agent=None) -> str:
    """解析「当前实际使用的模型」名（用于模型档案能力门禁）。

    模型可在运行期通过 /model 或 /models 切换；因此先读 registry.dispatch
    注入的调用代理（self.agent）当前路由模型（agent.model，即当前会话/
    子代理实际请求所用的模型），失败才回退全局 MODEL。
    两种情况都取不到返回空串（视为未声明 image 输入）。
    """
    if agent is not None:
        scoped = getattr(agent, "model", None)
        if scoped:
            return scoped
    try:
        from ..config import MODEL
        return MODEL or ""
    except Exception:
        _logger.debug("读取 MODEL 配置失败（视为非多模态）", exc_info=True)
        return ""


# ── 错误文案（对齐 DSH read_image 的可恢复提示） ─────────

def _format_gate_error(path: str, ext: str) -> str:
    """扩展名非 Pillow 可解码图像格式 → 拒绝（扩展名门禁）。"""
    return _error(
        _ERR_UNSUPPORTED_EXTENSION,
        f'cannot read "{path}": the {ext} extension does not declare a supported '
        f"image format; read_image accepts {_SUPPORTED_FORMATS}",
    )


def _format_mismatch_error(path: str, ext: str, declared: str) -> str:
    """声明（扩展名）与实际解码格式不一致 → 拒绝（对齐 IMAGE_TYPE_MISMATCH）。"""
    return _error(
        _ERR_TYPE_MISMATCH,
        f'cannot read "{path}": the {ext} extension declares {declared}, but the '
        "bytes use a different image format; rename the file to match its actual "
        "format, or convert it to a supported image format",
    )


def _declared_format(ext: str) -> str | None:
    """返回扩展名对应的 Pillow 格式名（声明格式）。

    优先用 Pillow 运行时 ``registered_extensions()``（该 Pillow 实例支持的全部
    扩展名→格式），但仅接受静态表认可的「图像」格式（排除 PDF/视频/数据文件
    等非图像扩展名）；无 PIL 或未覆盖时回退静态 ``_EXT_TO_FORMAT``。
    """
    try:
        from PIL import Image
        dyn = Image.registered_extensions().get(ext)
        if dyn and dyn in _image_format_names():
            return dyn
    except Exception:
        _logger.debug("忽略异常（best-effort 路径）", exc_info=True)
    return image_ext_format().get(ext)


def _capability_error(path: str, model: str) -> str:
    """精确路由模型未声明 image 输入 → 严格拒绝并提示切换模型。"""
    model_part = model if model else "<unknown>"
    return _error(
        _ERR_MODEL_INCAPABLE,
        f'cannot read "{path}" as an image: model "{model_part}" does not '
        "declare image input; switch to an image-capable model to read images",
    )


def _normalization_error(path: str, is_16bit_png: bool = False) -> str:
    """无法归一化为 8-bit sRGB（如 16 位 PNG）→ 拒绝（对齐 normalizeImage 失败）。"""
    if is_16bit_png:
        message = (
            f'cannot read "{path}": the 16-bit PNG could not be converted to the '
            "normalized 8-bit sRGB form; convert it to an 8-bit PNG/JPEG/WebP and retry"
        )
    else:
        message = (
            f'cannot read "{path}": the image could not be converted to the '
            "normalized 8-bit sRGB form; convert it to an 8-bit PNG/JPEG/WebP and retry"
        )
    return _error(_ERR_NORMALIZE_FAILED, message)


def _normalize_srgb(img, apply_exif: bool = True):
    """（可选）应用 EXIF 方向并转换为 8-bit sRGB 单帧（RGB/RGBA）。

    对齐 DSH 规范化（canPassThroughNormalization 之后的重编码）：输出必须为
    单帧、8-bit、sRGB。16 位 PNG / 高位深灰度等无法归一化时抛 ValueError。
    apply_exif=False 时跳过 EXIF 摆正——调用方（_execute_sync）已在裁剪前应用
    过 exif_transpose 一次，避免同一对象重复摆正（Pillow 幂等性不可靠）。
    """
    from PIL import ImageOps
    if apply_exif:
        img = ImageOps.exif_transpose(img)
    # 非 8-bit（16 位灰度 / 32 位 int / float）→ 无法归一化为 8-bit sRGB
    if img.mode in ("I;16", "I;16B", "I;16L", "I", "F"):
        raise ValueError("non-8-bit depth")
    has_alpha = (
        img.mode in ("RGBA", "LA", "PA")
        or "A" in img.mode
        or (img.mode == "P" and img.info.get("transparency") is not None)
    )
    return img.convert("RGBA" if has_alpha else "RGB")


def _format_mime(fmt: str | None) -> str:
    """Pillow 格式名 → 媒体类型（未知格式回退 image/<lower>）。"""
    if not fmt:
        return "image/png"
    return image_format_media().get(fmt, f"image/{fmt.lower()}")


def _exif_orientation(img) -> int:
    """读取 EXIF orientation（缺失/异常 → 1）。"""
    try:
        exif = img.getexif()
        return int(exif.get(0x0112, 1) or 1)
    except Exception:
        return 1


def _is_clean_for_passthrough(img, info: dict, orientation: int) -> bool:
    """判断输入是否满足 DSH 的「字节直通」干净条件。"""
    if img.mode not in ("RGB", "RGBA"):
        return False
    if orientation != 1:
        return False
    if info.get("icc_profile"):
        return False
    if info.get("exif"):
        return False
    if info.get("transparency") is not None:
        return False
    # 只允许 JPEG 的 JFIF 结构标记；其余 info 键（dpi/gamma/text/comment…）
    # 都视为需要移除的元数据 → 不走直通，交给规范化重编码。
    if not set(info) <= _PASSTHROUGH_INFO:
        return False
    return True


def _can_passthrough(
    *,
    input_bytes: bytes,
    actual_format: str | None,
    frame_count: int,
    img,
    info: dict,
    orientation: int,
    region_changed: bool,
    operation: str,
) -> bool:
    """DSH 直通判定：干净 8-bit sRGB/sRGBA PNG/JPEG/WebP、预算内、无加工。"""
    if actual_format not in ("PNG", "JPEG", "WEBP"):
        return False
    if frame_count > 1:
        return False
    if region_changed or operation != "none":
        return False
    if not _is_clean_for_passthrough(img, info, orientation):
        return False
    width, height = img.size
    if width <= 0 or height <= 0:
        return False
    if max(width, height) > _NORMALIZED_MAX_DIMENSION:
        return False
    if width * height > _NORMALIZED_MAX_PIXELS:
        return False
    if len(input_bytes) > _NORMALIZED_MAX_BYTES:
        return False
    return True


def _downscale_to_budget(img):
    """按 DSH 规范化预算等比缩小（总像素 2048×2048、长边 8192）。

    Returns:
        ``(img, original_size_or_None)``；发生缩小时 original_size 为缩放前
        ``(width, height)``。
    """
    width, height = img.size
    if width <= 0 or height <= 0:
        return img, None
    scale = 1.0
    long_side = max(width, height)
    if long_side > _NORMALIZED_MAX_DIMENSION:
        scale = min(scale, _NORMALIZED_MAX_DIMENSION / float(long_side))
    pixels = width * height
    if pixels > _NORMALIZED_MAX_PIXELS:
        scale = min(scale, math.sqrt(_NORMALIZED_MAX_PIXELS / float(pixels)))
    if scale >= 1.0:
        return img, None
    new_w = max(1, int(width * scale))
    new_h = max(1, int(height * scale))
    # 向下取整后仍可能因极端长宽比略超总像素预算：继续收一档。
    while new_w * new_h > _NORMALIZED_MAX_PIXELS and (new_w > 1 or new_h > 1):
        if new_w >= new_h:
            new_w = max(1, new_w - 1)
        else:
            new_h = max(1, new_h - 1)
    if new_w > _NORMALIZED_MAX_DIMENSION:
        new_w = _NORMALIZED_MAX_DIMENSION
    if new_h > _NORMALIZED_MAX_DIMENSION:
        new_h = _NORMALIZED_MAX_DIMENSION
    from PIL import Image
    resampling = getattr(getattr(Image, "Resampling", Image), "LANCZOS")
    return img.resize((new_w, new_h), resampling), (width, height)


def _encode_with_ladder(img) -> tuple[bytes, str]:
    """按 DSH 编码策略输出图片字节与媒体类型。

    - alpha → WebP（保留透明通道）；
    - 不透明 → JPEG；
    - 质量阶梯 85/75/60：优先取满足 4 MiB 的最小输出；若全部超限，保留
      字节数最小的候选（对齐 DSH）。
    """
    has_alpha = img.mode == "RGBA"
    fmt = "WEBP" if has_alpha else "JPEG"
    media_type = "image/webp" if has_alpha else "image/jpeg"
    candidates: list[bytes] = []
    for quality in _ENCODE_QUALITIES:
        buf = io.BytesIO()
        kwargs = {"quality": quality}
        if fmt == "JPEG":
            kwargs["optimize"] = True
        img.save(buf, format=fmt, **kwargs)
        candidates.append(buf.getvalue())
    fitting = [data for data in candidates if len(data) <= _NORMALIZED_MAX_BYTES]
    if fitting:
        return min(fitting, key=len), media_type
    return min(candidates, key=len), media_type


class ReadImageFunc(Func):
    name = "read_image"

    @classmethod
    def to_tool_schema(cls) -> dict:
        return {
            "type": "function",
            "function": {
                "name": "read_image",
                "description": (
                    "Read a PNG/JPEG/WebP/GIF (or any Pillow-readable image) file and "
                    "return the image itself. Large images are downscaled automatically "
                    "to a 2048x2048 total-pixel budget / 4 MiB target; do not install "
                    "image libraries or create thumbnails to inspect an image. "
                    "Requires the current routed model to declare image input "
                    "(多模态读图=true in /models); otherwise execution is refused. "
                    "Extension-less files are identified from their content. "
                    "Project extensions (DSH superset): start_x/start_y/end_x/end_y "
                    "crop a pixel region; operation applies grayscale / rotate90 / "
                    "rotate180 / rotate270 / flip_h / flip_v / scale before return. "
                    "Path security rejects dangerous paths (device files/system paths)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "Path to the image file."
                        },
                        "start_x": {
                            "type": "integer",
                            "minimum": 0,
                            "description": "分块区域起始 x（像素，默认 0）。"
                        },
                        "start_y": {
                            "type": "integer",
                            "minimum": 0,
                            "description": "分块区域起始 y（像素，默认 0）。"
                        },
                        "end_x": {
                            "type": "integer",
                            "minimum": 0,
                            "description": "分块区域结束 x（含，默认图像宽度-1）。"
                        },
                        "end_y": {
                            "type": "integer",
                            "minimum": 0,
                            "description": "分块区域结束 y（含，默认图像高度-1）。"
                        },
                        "operation": {
                            "type": "string",
                            "enum": sorted(_SUPPORTED_OPERATIONS),
                            "description": (
                                "图像操作（先裁剪区域再应用操作）："
                                "none 不操作 / grayscale 灰度化 / rotate90 顺时针旋转90° / "
                                "rotate180 旋转180° / rotate270 旋转270° / "
                                "flip_h 水平翻转 / flip_v 垂直翻转 / scale 缩放"
                                "（需配合 scale_width/scale_height）。"
                            ),
                            "default": "none"
                        },
                        "scale_width": {
                            "type": "integer",
                            "minimum": 1,
                            "description": "缩放目标宽度（operation=scale 时使用，省略时按比例自动计算）。"
                        },
                        "scale_height": {
                            "type": "integer",
                            "minimum": 1,
                            "description": "缩放目标高度（operation=scale 时使用，省略时按比例自动计算）。"
                        },
                    },
                    "required": ["file_path"]
                }
            }
        }

    @classmethod
    def from_args(cls, args: dict) -> "ReadImageFunc":
        path = args.get("file_path") or args.get("paths")
        if isinstance(path, list):
            path = path[0] if path else ""
        if not path:
            raise ValueError("缺少必需参数: file_path")

        operation = str(args.get("operation") or "none").strip().lower()
        if operation not in _SUPPORTED_OPERATIONS:
            _logger.debug("read_image operation %r 非法，回退 'none'", operation)
            operation = "none"

        return cls(
            path=path,
            start_x=args.get("start_x"),
            start_y=args.get("start_y"),
            end_x=args.get("end_x"),
            end_y=args.get("end_y"),
            operation=operation,
            scale_width=args.get("scale_width"),
            scale_height=args.get("scale_height"),
        )

    @classmethod
    def display_params(cls, arguments: dict, max_len: int = 80) -> str:
        """DSH 风格展示：``Read image <path>``（保留区域/操作扩展信息）。"""
        path = arguments.get("file_path") or arguments.get("paths", "")
        if isinstance(path, list):
            path = path[0] if path else ""
        if not path:
            return ""
        display = f"Read image {cls._sanitize_display(path)}"
        extras = []
        if arguments.get("operation") and arguments.get("operation") != "none":
            extras.append(arguments["operation"])
        coords = [
            arguments.get(k) for k in ("start_x", "start_y", "end_x", "end_y")
        ]
        if any(c is not None for c in coords):
            def _cell(v):
                return "" if v is None else v
            extras.append(
                f"({_cell(arguments.get('start_x'))},"
                f"{_cell(arguments.get('start_y'))})-"
                f"({_cell(arguments.get('end_x'))},"
                f"{_cell(arguments.get('end_y'))})"
            )
        if extras:
            display += " " + " ".join(extras)
        return display

    def __init__(
        self,
        path: str = "",
        start_x=None, start_y=None, end_x=None, end_y=None,
        operation: str = "none",
        scale_width=None, scale_height=None,
        file_path: str | None = None,
    ):
        super().__init__()
        # 模型侧路径字段为 file_path（工具参数唯一的路径字段）；
        # path 仅是本构造器的内部参数名（保持既有 Python 调用签名）。
        if file_path:
            path = file_path
        if not isinstance(path, str):
            try:
                path = os.fspath(path)
            except TypeError:
                path = ""
        if not path.strip():
            raise ValueError("缺少必需参数: file_path")
        validate_path_security(path)
        self.path = path
        self.start_x = start_x
        self.start_y = start_y
        self.end_x = end_x
        self.end_y = end_y
        if operation in _SUPPORTED_OPERATIONS:
            self.operation = operation
        else:
            if operation != "none":
                _logger.debug("read_image operation %r 非法，回退 'none'", operation)
            self.operation = "none"
        self.scale_width = scale_width
        self.scale_height = scale_height
        self.result_blocks = None  # 多模态 content blocks（execute 内设置）

    # ── 执行 ──────────────────────────────────────────

    async def execute(self) -> str:
        """异步读取图像（Pillow 同步解码放线程池）。"""
        return await asyncio.to_thread(self._execute_sync)

    def _execute_sync(self) -> str:
        """同步执行读取逻辑（在 asyncio.to_thread 中运行）。

        每次执行前重置 result_blocks——同一 Func 实例可能被多次调用
        （成功后再失败），避免旧 blocks 残留被执行链路误包装为 ToolResult。
        """
        self.result_blocks = None
        file_path = self.path

        # ── 1. 参数 / 扩展名 / 路径门禁 ──────────────────
        if not isinstance(file_path, str) or not file_path.strip():
            return _error(
                _ERR_INVALID_ARGUMENT, "file_path must be a non-empty string"
            )
        ext = os.path.splitext(file_path)[1].lower()
        # 无扩展名：不声明格式，按文件内容（Pillow magic-byte 识别）判定。
        declared_format = _declared_format(ext) if ext else None
        if ext and declared_format is None:
            return _format_gate_error(file_path, ext)

        # ── 2. 严格图像能力门禁（精确路由模型须声明 image 输入） ──
        # 对齐 DSH：能力门禁在文件系统解析（not found / not regular）之前，
        # 避免对无法查看图片的模型先做无意义的磁盘读取。
        model = _current_model(self.agent)
        if not model_supports_multimodal(model):
            return _capability_error(file_path, model)

        if not os.path.exists(file_path):
            return _error(
                _ERR_NOT_FOUND, f'cannot read "{file_path}": not found'
            )
        if not os.path.isfile(file_path):
            return _error(
                _ERR_NOT_REGULAR_FILE,
                f'cannot read "{file_path}": not a regular file',
            )

        try:
            size_bytes = os.path.getsize(file_path)
        except OSError as e:
            return _error(_ERR_READ_FAILED, f'cannot read "{file_path}": {e}')
        if size_bytes > _MAX_FILE_SIZE_BYTES:
            return _error(
                _ERR_TOO_LARGE,
                f'cannot read "{file_path}": the image cannot be stored within '
                "the deployment's byte limits; downscale the image and read the "
                "smaller copy",
            )

        try:
            from PIL import Image
        except ImportError:
            return _error(
                _ERR_READ_FAILED,
                "(读取失败: 需要 Pillow 库（pip install Pillow）才能读取图像)",
            )

        try:
            with open(file_path, "rb") as f:
                input_bytes = f.read()
        except OSError as e:
            return _error(_ERR_READ_FAILED, f'cannot read "{file_path}": {e}')
        if len(input_bytes) > _MAX_FILE_SIZE_BYTES:
            return _error(
                _ERR_TOO_LARGE,
                f'cannot read "{file_path}": the image cannot be stored within '
                "the deployment's byte limits; downscale the image and read the "
                "smaller copy",
            )

        try:
            opened = Image.open(io.BytesIO(input_bytes))
        except Exception:
            return _error(
                _ERR_INVALID_IMAGE,
                f'cannot read "{file_path}": the bytes do not decode as a '
                "supported image; the file may be truncated or corrupt",
            )

        try:
            with opened as img:
                width, height = img.size
                if max(width, height) > _MAX_IMAGE_DIMENSION:
                    return _error(
                        _ERR_DIMENSION_TOO_LARGE,
                        f'cannot read "{file_path}": at least one image side '
                        f"exceeds the {_MAX_IMAGE_DIMENSION}px limit; downscale "
                        "the image and read the smaller copy",
                    )
                if width * height > _MAX_IMAGE_PIXELS:
                    return _error(
                        _ERR_TOO_MANY_PIXELS,
                        f'cannot read "{file_path}": the image exceeds the '
                        f"{_MAX_IMAGE_PIXELS}-pixel decoded-size limit; downscale "
                        "the image and read the smaller copy",
                    )
                img.load()  # 触发解码（校验字节确实可解码）
                actual_format = getattr(img, "format", None)

                # ── 3. magic-byte / 声明类型一致校验 ───────
                if declared_format is not None:
                    if actual_format is None or actual_format != declared_format:
                        declared_mime = image_format_media().get(
                            declared_format, f"image/{declared_format.lower()}")
                        return _format_mismatch_error(
                            file_path, ext, declared_mime)
                elif actual_format is None or actual_format not in _image_format_names():
                    return _error(
                        _ERR_UNSUPPORTED_FORMAT,
                        f'cannot read "{file_path}": the file content is not a '
                        f"supported image format; read_image accepts "
                        f"{_SUPPORTED_FORMATS}",
                    )

                frame_count = int(getattr(img, "n_frames", 1) or 1)
                orientation = _exif_orientation(img)
                original_mode = img.mode
                info = dict(img.info)
                img = img.copy()  # 复制后源文件可关闭，后续操作安全
        except Exception as e:
            return _error(
                _ERR_INVALID_IMAGE,
                f'cannot read "{file_path}": {e}',
            )

        input_format = actual_format

        # ── 应用 EXIF 方向（在取尺寸/裁剪前——带 orientation 的 JPEG 先摆正，
        #   使 start_x/start_y/end_x/end_y 与用户目视方向一致） ──
        try:
            from PIL import ImageOps
            img = ImageOps.exif_transpose(img)
        except Exception:
            _logger.debug("忽略异常（best-effort 路径）", exc_info=True)

        width, height = img.size

        # ── 分块区域（像素坐标，含端点） ────────────────
        start_x = _clamp(_to_int(self.start_x, 0), 0, width - 1)
        start_y = _clamp(_to_int(self.start_y, 0), 0, height - 1)
        end_x = _clamp(_to_int(self.end_x, width - 1), 0, width - 1)
        end_y = _clamp(_to_int(self.end_y, height - 1), 0, height - 1)
        if end_x < start_x:
            start_x, end_x = end_x, start_x
        if end_y < start_y:
            start_y, end_y = end_y, start_y

        region_changed = (
            start_x != 0 or start_y != 0
            or end_x != width - 1 or end_y != height - 1
        )
        if region_changed:
            img = img.crop((start_x, start_y, end_x + 1, end_y + 1))

        # ── 图像操作（先裁剪区域，再应用操作） ──────────
        try:
            img = self._apply_operation(img)
        except Exception as e:
            return _error(
                _ERR_OPERATION_FAILED,
                f'cannot read "{file_path}": image operation failed: {e}',
            )

        # ── 4. DSH 直通 / 规范化 ─────────────────────────
        passthrough = _can_passthrough(
            input_bytes=input_bytes,
            actual_format=input_format,
            frame_count=frame_count,
            img=img,
            info=info,
            orientation=orientation,
            region_changed=region_changed,
            operation=self.operation,
        )

        original_dimensions = None
        if passthrough:
            output_bytes = input_bytes
            media_type = _format_mime(input_format)
            out_w, out_h = img.size
        else:
            # 规范化到 8-bit sRGB 单帧（对齐 DSH）；EXIF 已在裁剪前摆正一次。
            try:
                img = _normalize_srgb(img, apply_exif=False)
            except ValueError:
                is_16bit_png = (
                    input_format == "PNG"
                    and original_mode in ("I;16", "I;16B", "I;16L", "I", "F")
                )
                return _normalization_error(
                    file_path, is_16bit_png=is_16bit_png)
            except Exception as e:
                return _error(
                    _ERR_NORMALIZE_FAILED,
                    f'cannot read "{file_path}": normalization failed: {e}',
                )

            # ── 5. 自动降采样（DSH 规范化预算） ──────────
            img, original_dimensions = _downscale_to_budget(img)
            out_w, out_h = img.size

            # ── 6. 编码策略（alpha→WebP / 不透明→JPEG，质量阶梯） ──
            try:
                output_bytes, media_type = _encode_with_ladder(img)
            except Exception as e:
                return _error(
                    _ERR_ENCODE_FAILED,
                    f'cannot read "{file_path}": image encoding failed: {e}',
                )

        # ── 7. 持久化附件（内容寻址去重） ────────────────
        try:
            ref = save_image(
                output_bytes,
                media_type,
                width=out_w,
                height=out_h,
                name=os.path.basename(file_path),
                original_dimensions=(
                    {"width": original_dimensions[0],
                     "height": original_dimensions[1]}
                    if original_dimensions else None
                ),
            )
        except AttachmentError as exc:
            return _error(
                exc.code or _ERR_ATTACHMENT_FAILED,
                f'cannot read "{file_path}": {exc.message}',
            )
        except Exception as exc:
            return _error(
                _ERR_ATTACHMENT_FAILED,
                f'cannot read "{file_path}": could not store image attachment: {exc}',
            )

        # ── 8. 单行 envelope + 附件图片 block ────────────
        text = self._format_envelope(
            file_path=file_path,
            media_type=media_type,
            width=out_w,
            height=out_h,
            byte_count=len(output_bytes),
            original_dimensions=original_dimensions,
            region=(start_x, start_y, end_x, end_y) if region_changed else None,
            operation=self.operation,
        )
        self.result_blocks = [
            {"type": "text", "text": text},
            attachment_image_block(ref),
        ]
        return text

    @staticmethod
    def _format_envelope(
        *,
        file_path: str,
        media_type: str,
        width: int,
        height: int,
        byte_count: int,
        original_dimensions,
        region,
        operation: str,
    ) -> str:
        """DSH 风格单行 envelope（保留区域/操作扩展说明）。"""
        text = (
            f"{file_path} image {media_type} image, "
            f"{width}x{height} px, {byte_count} bytes"
        )
        if original_dimensions:
            ow, oh = original_dimensions
            ratio_x = ow / float(width) if width else 1.0
            ratio_y = oh / float(height) if height else 1.0
            x_text = f"{ratio_x:.2f}"
            y_text = f"{ratio_y:.2f}"
            if x_text == y_text:
                advice = f"multiply coordinates by {x_text}"
            else:
                advice = (
                    f"multiply x coordinates by {x_text} and y coordinates by {y_text}"
                )
            text += (
                f" (downscaled from {ow}x{oh} px; {advice} to locate features "
                "in the original file)"
            )
        extras = []
        if region is not None:
            sx, sy, ex, ey = region
            extras.append(f"region {sx},{sy}-{ex},{ey}")
        if operation and operation != "none":
            extras.append(f"operation {operation}")
        if extras:
            text += " (" + "; ".join(extras) + ")"
        return text

    # ── 图像操作 ─────────────────────────────────────────

    def _apply_operation(self, img):
        """应用图像操作（Pillow）。"""
        op = self.operation
        if op == "none":
            return img
        if op == "grayscale":
            return img.convert("L")
        if op == "rotate90":
            return img.rotate(-90, expand=True)
        if op == "rotate180":
            return img.rotate(-180, expand=True)
        if op == "rotate270":
            return img.rotate(-270, expand=True)
        if op == "flip_h":
            return img.transpose(_FLIP_LEFT_RIGHT)
        if op == "flip_v":
            return img.transpose(_FLIP_TOP_BOTTOM)
        if op == "scale":
            return self._scale(img)
        return img

    def _scale(self, img):
        """按 scale_width/scale_height 缩放（缺失维度按比例计算）。"""
        w, h = img.size
        if w <= 0 or h <= 0:
            return img
        sw = _to_int(self.scale_width, 0)
        sh = _to_int(self.scale_height, 0)
        if sw <= 0 and sh <= 0:
            return img
        if sw <= 0:
            sw = max(1, round(w * sh / h)) if h else w
        elif sh <= 0:
            sh = max(1, round(h * sw / w)) if w else h
        return img.resize((sw, sh))
