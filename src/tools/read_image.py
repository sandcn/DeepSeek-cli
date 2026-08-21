"""read_image — 读取图像文件工具（对齐 DSH read_image 加工契约）

DSH 对齐的加工语义（2026-08 重构）：
- 仅接受 PNG/JPEG/WebP/GIF：按扩展名路由声明媒体类型，拒绝 BMP 等其它格式；
- magic-byte / 声明类型一致校验：解码后实际格式必须与扩展名声明一致，
  否则报 DSH 式「扩展名与实际格式不符，重命名文件」错误；
- 严格图像能力门禁：当前模型若不声明图像输入（非多模态），直接拒绝并提示
  切换到图像模型——对齐 DSH 的「switch to an image-capable model」，不再降级
  为 rgba_hex / palette 文本；
- 规范化：应用 EXIF 方向并转换为 8-bit sRGB 单帧（RGB/RGBA），16 位 PNG 等
  无法转换时返回 DSH 式错误；输出前按 max_dimension 硬上限（DSH 长边 2048）
  与 max_tokens 预算（默认 8000）等比缩小，保证上下文可控。

保留当前项目特性：
- 分块读取（start_x/start_y/end_x/end_y 像素区域裁剪）
- 图像操作（grayscale / rotate90/180/270 / flip_h / flip_v / scale）
- 防爆上下文：max_tokens 预算 + 字节硬上限（自动等比缩小，保证上下文可控）

返回格式固定为多模态：文本元信息 + OpenAI 兼容 image_url data URI
content blocks（Anthropic 适配器自动转 image block）。
"""

from __future__ import annotations

import asyncio
import io
import logging
import os

from .base import Func, tool_metadata
from .file_ops import validate_path_security
from ..api.multimodal import is_multimodal_model, build_image_content_blocks

_logger = logging.getLogger(__name__)

# ── 支持的图像操作 ─────────────────────────────────────
_SUPPORTED_OPERATIONS = frozenset({
    "none", "grayscale", "rotate90", "rotate180", "rotate270",
    "flip_h", "flip_v", "scale",
})

# ── DSH 对齐：扩展名 → 声明媒体类型（仅四种） ──────────
# 与 dsh-tool-fs 的 IMAGE_EXTENSIONS 一致；magic-byte 校验以实际解码为准。
IMAGE_EXTENSIONS = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}
# Pillow ``img.format`` → 媒体类型（用于声明/实际一致校验）。
# 仅覆盖 read_image 接受的四种格式；其它（BMP 等）走扩展名门禁先拒绝。
_ACTUAL_FORMAT_MEDIA = {
    "PNG": "image/png",
    "JPEG": "image/jpeg",
    "WEBP": "image/webp",
    "GIF": "image/gif",
}

# ── DSH 对齐：规范化上限 ───────────────────────────────
# normalizedImageMaxDimension / normalizedImageMaxBytes（attachment-local 默认值）。
_DSH_NORMALIZED_MAX_DIMENSION = 2048            # 长边硬上限
_DSH_NORMALIZED_MAX_BYTES = 4 * 1024 * 1024     # PNG 编码字节硬上限

# ── 输出体积控制（防爆上下文） ─────────────────────────
# 默认 token 预算：单次 read_image 输出上限（estimate_tokens 口径）。
_DEFAULT_MAX_TOKENS = 8000
# 预算适配最小边长（低于此值放弃继续缩小，接受超预算并提示）
_MIN_DIMENSION = 16
# 预算/字节上限迭代上限（每次迭代需真实 PNG 编码，控制耗时）
_BUDGET_MAX_ITER = 5
# 单文件大小上限（MB）
_MAX_FILE_SIZE_MB = 50
# 解码前像素面积上限（3000 万像素：解码 RGBA ≈120MB、copy 后峰值 ≈240MB；
# 在 Pillow decompression bomb 防护（约 9000 万）之前拦截，控制内存峰值）
_MAX_IMAGE_PIXELS = 30_000_000

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
    """解析「当前实际使用的模型」名（严格门禁用），优先取调用代理的当前路由模型。

    对齐 DSH 的 assertImageCapableRoute：模型可能已在运行期通过 /model（或会话
    切换）换成多模态（如 deepseek-v4-flash-vision-exp），而全局 MODEL 配置仍为
    旧值；因此先读 registry.dispatch 注入的调用代理（self.agent）当前路由模型
    （agent.model，即当前会话/子代理实际请求所用的模型），失败才回退全局 MODEL。
    两种情况都取不到返回空串（视为非多模态）。
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


# ── DSH 风格错误文案（返回给模型；前缀 '(' 对齐本项目错误显示约定） ──

def _format_gate_error(path: str) -> str:
    """扩展名非 PNG/JPEG/WebP/GIF → 拒绝（对齐 DSH extension 路由）。"""
    return f'(cannot read "{path}": read_image only accepts PNG/JPEG/WebP/GIF paths)'


def _format_mismatch_error(path: str, ext: str, declared: str) -> str:
    """声明（扩展名）与实际解码格式不一致 → 拒绝（对齐 IMAGE_TYPE_MISMATCH）。"""
    return (
        f'(cannot read "{path}": the {ext} extension declares {declared}, but the bytes '
        "use a different image format; rename the file to match its actual format if it is "
        "PNG/JPEG/WebP/GIF, or convert it to one of those formats)"
    )


def _capability_error(path: str, model: str) -> str:
    """当前模型未声明 image 输入 → 严格拒绝（对齐 assertImageCapableRoute）。"""
    model_part = model if model else "<unknown>"
    return (
        f'(cannot read "{path}" as an image: model "{model_part}" does not declare image '
        "input; switch to an image-capable model to read images)"
    )


def _normalization_error(path: str) -> str:
    """无法归一化为 8-bit sRGB（如 16 位 PNG）→ 拒绝（对齐 normalizeImage 失败）。"""
    return (
        f'(cannot read "{path}": the image could not be converted to the normalized '
        "8-bit sRGB form; convert it to an 8-bit PNG/JPEG/WebP and retry)"
    )


# ── 图像加工（DSH 规范化语义） ─────────────────────────

def _fit_dimension(img, limit: int):
    """等比缩小图像，使最大边长不超过 limit。零尺寸图像原样返回（无法缩放）。"""
    w, h = img.size
    if w <= 0 or h <= 0:
        return img
    longest = max(w, h)
    if longest <= limit:
        return img
    ratio = limit / longest
    nw = max(1, round(w * ratio))
    nh = max(1, round(h * ratio))
    return img.resize((nw, nh))


def _estimate_uri_tokens(data_len: int) -> int:
    """按编码后 PNG 字节长度估算 data URI 文本的 token 数。

    公式：base64 文本 ≈ len*4/3 + 前缀（data:image/png;base64, 等）；token
    按 0.3/字符，另加 300 缓冲覆盖元信息中文。供 ``_fit_multimodal_to_budget``
    （预算阈值）与字节硬上限适配共用，避免两处公式漂移。
    """
    b64_chars = int(data_len * 4 / 3) + 64
    return max(1, int(b64_chars * 0.3) + 300)


def _fit_multimodal_to_budget(img, budget_tokens: int):
    """多模态预算适配：以真实 PNG 编码长度迭代降采样。

    data URI 文本 ≈ len(png) * 4/3（base64）+ 前缀；token 按 0.3/字符，
    另加 300 缓冲覆盖元信息中文。每次迭代需一次真实编码（尺寸受预算
    约束后边长通常 < 256，编码毫秒级，可接受）。
    """
    if budget_tokens <= 0:
        return img
    for _ in range(_BUDGET_MAX_ITER):
        w, h = img.size
        if w <= 0 or h <= 0:
            return img
        if max(w, h) <= _MIN_DIMENSION:
            return img
        try:
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            data_len = len(buf.getvalue())
        except Exception:
            return img
        current = _estimate_uri_tokens(data_len)
        if current <= budget_tokens:
            return img
        ratio = (budget_tokens / current) ** 0.5
        img = _fit_dimension(img, max(_MIN_DIMENSION, int(max(w, h) * ratio)))
    return img


def _fit_to_byte_cap(img, max_bytes: int):
    """DSH normalizedImageMaxBytes 硬上限：PNG 编码超限时迭代等比缩小。

    独立于 token 预算——即使 max_tokens=0（不限预算），编码字节数也不得
    超过部署上限，避免单张图撑爆上下文/存储。
    """
    for _ in range(_BUDGET_MAX_ITER):
        w, h = img.size
        if w <= 0 or h <= 0 or max(w, h) <= _MIN_DIMENSION:
            return img
        buf = io.BytesIO()
        try:
            img.save(buf, format="PNG")
        except Exception:
            return img
        data_len = len(buf.getvalue())
        if data_len <= max_bytes:
            return img
        ratio = (max_bytes / data_len) ** 0.5
        img = _fit_dimension(img, max(_MIN_DIMENSION, int(max(w, h) * ratio)))
    return img


def _normalize_srgb(img):
    """应用 EXIF 方向并转换为 8-bit sRGB 单帧（RGB/RGBA）。

    对齐 DSH 规范化（canPassThroughNormalization 之后的重编码）：输出必须为
    单帧、8-bit、sRGB。16 位 PNG / 高位深灰度等无法归一化时抛 ValueError。
    """
    from PIL import ImageOps
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


@tool_metadata(
    parallel_safe=True,
    requires_network=False,
    requires_terminal=False,
    timeout_estimate=0,
    category="io",
    priority=10,
    tool_category="read",
    description="读取图像内容",
)
class ReadImageFunc(Func):
    name = "read_image"

    @classmethod
    def to_tool_schema(cls) -> dict:
        return {
            "type": "function",
            "function": {
                "name": "read_image",
                "description": (
                    "读取图像文件内容。仅支持 PNG/JPEG/WebP/GIF，且要求当前模型支持"
                    "图像输入（多模态）；返回 base64 PNG 图片并附文本元信息。"
                    "支持分块读取（start_x/start_y/end_x/end_y 指定像素区域，类似"
                    "read_file 的行号范围）；支持对图像操作后返回（operation：grayscale"
                    "灰度/rotate90/rotate180/rotate270/flip_h/flip_v/scale 缩放，"
                    "先裁剪区域再应用操作）。"
                    "防爆上下文：输出受 max_tokens 预算约束（默认 8000 tokens，"
                    "0=不限制），超预算自动等比缩小，另受字节硬上限约束；"
                    "推荐先默认小图看整体轮廓，需要细节时再用"
                    "start_x/start_y/end_x/end_y 放大局部区域。"
                    "路径安全校验拒绝危险路径（设备文件/系统关键路径等）。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "图像文件路径，支持相对路径或绝对路径（PNG/JPEG/WebP/GIF）。"
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
                        "max_dimension": {
                            "type": "integer",
                            "minimum": 16,
                            "default": 256,
                            "description": (
                                "返回图像最大边长（超出则等比缩小）。上限 2048。"
                                "实际输出还受 max_tokens 预算与字节上限约束。"
                            )
                        },
                        "max_tokens": {
                            "type": "integer",
                            "minimum": 0,
                            "default": 8000,
                            "description": (
                                "输出内容估算 token 预算（防止撑爆上下文；0=不限制）。"
                                "超预算时自动等比缩小图像。默认 8000。"
                            )
                        },
                    },
                    "required": ["path"]
                }
            }
        }

    @classmethod
    def from_args(cls, args: dict) -> "ReadImageFunc":
        path = args.get("path") or args.get("paths")
        if isinstance(path, list):
            path = path[0] if path else ""
        if not path:
            raise ValueError("缺少必需参数: path")

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
            max_dimension=args.get("max_dimension", 256),
            max_tokens=args.get("max_tokens", _DEFAULT_MAX_TOKENS),
        )

    @classmethod
    def display_params(cls, arguments: dict, max_len: int = 80) -> str:
        path = arguments.get("path") or arguments.get("paths", "")
        if isinstance(path, list):
            path = path[0] if path else ""
        if not path:
            return ""
        display = f"'{cls._sanitize_display(path)}'"
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
        path: str,
        start_x=None, start_y=None, end_x=None, end_y=None,
        operation: str = "none",
        scale_width=None, scale_height=None,
        max_dimension: int = 256,
        max_tokens: int = _DEFAULT_MAX_TOKENS,
    ):
        super().__init__()
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
        self.max_dimension = _to_int(max_dimension, 256)
        self.max_tokens = max(0, _to_int(max_tokens, _DEFAULT_MAX_TOKENS))
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

        # ── 1. 扩展名格式门禁（仅 PNG/JPEG/WebP/GIF） ────
        ext = os.path.splitext(file_path)[1].lower()
        declared = IMAGE_EXTENSIONS.get(ext)
        if declared is None:
            return _format_gate_error(file_path)

        if not os.path.exists(file_path):
            return f"(文件不存在: {file_path})"

        # ── 2. 严格图像能力门禁（对齐 DSH：模型须声明 image 输入） ──
        # 用调用代理当前路由模型（运行期可能已切到多模态），而非全局 MODEL。
        model = _current_model(self.agent)
        if not is_multimodal_model(model):
            return _capability_error(file_path, model)

        try:
            size_bytes = os.path.getsize(file_path)
            if size_bytes > _MAX_FILE_SIZE_MB * 1024 * 1024:
                return (f"(读取失败: 文件过大 ({size_bytes / 1024 / 1024:.1f}MB)，"
                        f"超过 {_MAX_FILE_SIZE_MB}MB 限制)")
        except OSError as e:
            return f"(读取失败: {e})"

        try:
            from PIL import Image
        except ImportError:
            return "(读取失败: 需要 Pillow 库（pip install Pillow）才能读取图像)"

        try:
            with Image.open(file_path) as img:
                # 解码前检查像素面积（Pillow decompression bomb 防护的
                # 前置拦截——避免超大图像已解码再被拒，浪费内存/时间）
                img_w, img_h = img.size
                if img_w * img_h > _MAX_IMAGE_PIXELS:
                    return (f"(读取失败: 图像过大 ({img_w}x{img_h}={img_w * img_h} 像素，"
                            f"超过 {_MAX_IMAGE_PIXELS} 像素上限)")
                img.load()  # 触发解码
                # ── 3. magic-byte / 声明类型一致校验 ───────
                actual = _ACTUAL_FORMAT_MEDIA.get(img.format)
                if actual is None or actual != declared:
                    return _format_mismatch_error(file_path, ext, declared)
                img = img.copy()  # 复制后源文件可关闭，后续操作安全
        except Exception as e:
            return f"(读取失败: {e})"

        # ── 应用 EXIF 方向（在取尺寸/裁剪前——带 orientation 的 JPEG 先摆正，
        #   使 start_x/start_y/end_x/end_y 与用户目视方向一致；修复前
        #   exif_transpose 在 crop 之后才执行，按原始未摆正坐标裁剪后整体
        #   旋转 → 返回区域与用户期望的视觉区域不符） ──
        try:
            from PIL import ImageOps
            img = ImageOps.exif_transpose(img)
        except Exception:
            pass  # 摆正失败（异常图）→ 原样继续（防御，幂等：_normalize_srgb 再次 exif_transpose 为 no-op）

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
            return f"(操作失败: {e})"

        # ── 4. 规范化到 8-bit sRGB 单帧（对齐 DSH） ──────
        try:
            img = _normalize_srgb(img)
        except ValueError:
            return _normalization_error(file_path)
        except Exception as e:
            return f"(规范化失败: {e})"

        # ── 5. 长边硬上限（DSH normalizedImageMaxDimension） ──
        limit = _clamp(self.max_dimension, _MIN_DIMENSION, _DSH_NORMALIZED_MAX_DIMENSION)
        img = _fit_dimension(img, limit)

        # ── 6. token 预算适配（软约束，防爆上下文） ──────
        if self.max_tokens > 0:
            img = _fit_multimodal_to_budget(img, self.max_tokens)

        # ── 7. 字节硬上限（DSH normalizedImageMaxBytes） ──
        img = _fit_to_byte_cap(img, _DSH_NORMALIZED_MAX_BYTES)

        # ── 元信息（精简：不再携带面向模型的技术尾注——「模式: 多模态(base64
        #   PNG)」「预计占用: … tokens」「如需细节…」此前会在轨迹 Trace 检查器
        #   「▸ 返回值」中显示为多余叶子行；图片本身经 image_url block 返回，
        #   模型天然可见，无需文本复述） ──
        fmt_name = (getattr(img, "format", None) or "PNG").upper()
        out_w, out_h = img.size
        meta = [
            f"图片: {file_path}",
            f"尺寸: {out_w}x{out_h} (宽x高)",
            f"原始尺寸: {width}x{height} (宽x高)",
        ]
        if region_changed:
            meta.append(f"区域: ({start_x},{start_y})-({end_x},{end_y})")
        meta.append(f"操作: {self.operation}")
        meta.append(f"格式: {fmt_name}")

        return self._build_multimodal_output(img, meta)

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

    # ── 输出构造（固定多模态） ───────────────────────────

    def _build_multimodal_output(self, img, meta: list[str]) -> str:
        """多模态输出：文本元信息 + image_url data URI content blocks。"""
        buf = io.BytesIO()
        try:
            img.save(buf, format="PNG")
        except Exception as e:
            return f"(编码失败: {e})"
        data = buf.getvalue()
        text = "\n".join(meta)
        # ★ 2026-08-22（轨迹 Trace 尾注噪音）：不再把「图片已编码为 base64
        #   PNG（data URI）随 content blocks 返回。」拼进返回文本——该句是给
        #   模型的技术提示，在 Trace 检查器「▸ 返回值」中显示为多余叶子行；
        #   data URI 图片本身随 image_url block 返回，模型天然可见，无需文本
        #   复述（跟踪显示更干净）。
        self.result_blocks = build_image_content_blocks(text, data, "image/png")
        return text
