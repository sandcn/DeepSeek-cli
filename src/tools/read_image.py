"""read_image — 读取图像文件工具

功能：
- 读取本地图像（PNG/JPEG/GIF/BMP/WebP 等，Pillow 支持的格式）
- 分块读取：start_x/start_y/end_x/end_y 指定像素区域
  （类似 read_file 的行号范围——先读整体轮廓，再按区域放大看细节）
- 图像操作：灰度化 / 旋转 / 翻转 / 缩放（先裁剪区域，再应用操作）
- 返回格式（format 参数，默认 auto）：
  - auto：按当前模型能力自动选择
  - multimodal：OpenAI 兼容 image_url data URI content blocks
    （Anthropic 适配器自动转换为 image block），多模态模型直接看到图片
  - rgba_hex：RGBA 十六进制字符（每像素 #RRGGBBAA，保持图像形状按行输出）
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

# RGBA 十六进制模式最大边长（每像素 8 hex + 分隔，防止输出撑爆上下文）
_MAX_RGBA_DIMENSION = 64
# 多模态模式最大边长（base64 PNG 体积控制）
_MAX_MULTIMODAL_DIMENSION = 1024
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
                    "读取图像文件内容。支持分块读取（start_x/start_y/end_x/end_y "
                    "指定像素区域，类似 read_file 的行号范围）；支持对图像操作后返回"
                    "（operation：grayscale 灰度/rotate90/rotate180/rotate270/flip_h/"
                    "flip_v/scale 缩放，先裁剪区域再应用操作）。"
                    "返回格式（format，默认 auto）：模型支持多模态时返回 base64 图片"
                    "（多模态格式），否则返回 RGBA 十六进制字符（每像素 #RRGGBBAA，"
                    "按图像宽度逐行输出）。路径安全校验拒绝危险路径"
                    "（设备文件/系统关键路径等）。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "图像文件路径，支持相对路径或绝对路径（PNG/JPEG/GIF/BMP/WebP 等）。"
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
                            "default": 512,
                            "description": (
                                "返回图像最大边长（超出则等比缩小，防止输出过大）。"
                                "多模态模式上限 1024；RGBA 十六进制模式自动收紧到 64。"
                            )
                        },
                        "format": {
                            "type": "string",
                            "enum": ["auto", "multimodal", "rgba_hex"],
                            "description": (
                                "返回格式：auto=按当前模型能力自动选择（支持多模态返回图片，"
                                "否则 RGBA 十六进制字符）；multimodal=强制返回 base64 图片；"
                                "rgba_hex=强制返回 RGBA 十六进制字符。"
                            ),
                            "default": "auto"
                        }
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
            operation = "none"

        fmt = str(args.get("format") or "auto").strip().lower()
        if fmt not in ("auto", "multimodal", "rgba_hex"):
            fmt = "auto"

        return cls(
            path=path,
            start_x=args.get("start_x"),
            start_y=args.get("start_y"),
            end_x=args.get("end_x"),
            end_y=args.get("end_y"),
            operation=operation,
            scale_width=args.get("scale_width"),
            scale_height=args.get("scale_height"),
            max_dimension=args.get("max_dimension", 512),
            format=fmt,
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
        if arguments.get("start_x") is not None:
            extras.append(f"({arguments.get('start_x')},{arguments.get('start_y')})-"
                          f"({arguments.get('end_x')},{arguments.get('end_y')})")
        if extras:
            display += " " + " ".join(extras)
        return display

    def __init__(
        self,
        path: str,
        start_x=None, start_y=None, end_x=None, end_y=None,
        operation: str = "none",
        scale_width=None, scale_height=None,
        max_dimension: int = 512,
        # 参数名 format 与内置 format 重名（工具 schema 参数名约束，函数内
        # 不使用内置 format；保持命名与 schema 一致避免 from_args 映射歧义）
        format: str = "auto",
    ):
        super().__init__()
        validate_path_security(path)
        self.path = path
        self.start_x = start_x
        self.start_y = start_y
        self.end_x = end_x
        self.end_y = end_y
        self.operation = operation if operation in _SUPPORTED_OPERATIONS else "none"
        self.scale_width = scale_width
        self.scale_height = scale_height
        self.max_dimension = _to_int(max_dimension, 512)
        self.format = format if format in ("auto", "multimodal", "rgba_hex") else "auto"
        self.result_blocks = None  # 多模态 content blocks（execute 内设置）

    # ── 执行 ──────────────────────────────────────────

    async def execute(self) -> str:
        """异步读取图像（Pillow 同步解码放线程池）。"""
        return await asyncio.to_thread(self._execute_sync)

    def _execute_sync(self) -> str:
        """同步执行读取逻辑（在 asyncio.to_thread 中运行）。

        每次执行前重置 result_blocks——同一 Func 实例可能被多次调用
        （多模态成功后再失败/切换 rgba 模式），避免旧 blocks 残留被
        执行链路误包装为 ToolResult。
        """
        self.result_blocks = None
        file_path = self.path

        if not os.path.exists(file_path):
            return f"(文件不存在: {file_path})"

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
                img = img.copy()  # 复制后源文件可关闭，后续操作安全
        except Exception as e:
            return f"(读取失败: {e})"

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

        # ── 尺寸限制 ────────────────────────────────────
        use_multimodal = self._use_multimodal()
        if use_multimodal:
            limit = _clamp(self.max_dimension, 16, _MAX_MULTIMODAL_DIMENSION)
        else:
            # RGBA hex 输出体积大，自动收紧到 64
            limit = _clamp(min(self.max_dimension, _MAX_RGBA_DIMENSION), 16, _MAX_RGBA_DIMENSION)
        img = _fit_dimension(img, limit)

        # ── 元信息 ───────────────────────────────────────
        fmt_name = (img.format or "PNG").upper()
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
        meta.append(f"模式: {'多模态(base64 PNG)' if use_multimodal else 'RGBA 十六进制'}")

        if use_multimodal:
            return self._build_multimodal_output(img, meta)
        return self._build_rgba_output(img, meta)

    def _use_multimodal(self) -> bool:
        """决定是否返回多模态格式（base64 图片）。"""
        if self.format == "multimodal":
            return True
        if self.format == "rgba_hex":
            return False
        # auto：按当前模型能力（延迟读取，避免模块导入时绑定旧值）
        try:
            from ..config import MODEL as current_model
            return is_multimodal_model(current_model)
        except Exception:
            _logger.debug("读取 MODEL / 多模态判定失败，回退 RGBA hex", exc_info=True)
            return False

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

    # ── 输出构造 ──────────────────────────────────────

    def _build_rgba_output(self, img, meta: list[str]) -> str:
        """RGBA 十六进制字符输出：每像素 #RRGGBBAA，按图像宽度逐行。

        RGBA 文本体积随像素数线性增长（约 10 字符/像素），在 meta 中
        提示估算体积，引导模型按区域/降采样控制上下文占用。
        """
        try:
            rgba = img.convert("RGBA")
        except Exception as e:
            return f"(转换失败: {e})"
        w, h = rgba.size
        pixels = list(rgba.getdata())
        rows = []
        for y in range(h):
            row_px = pixels[y * w:(y + 1) * w]
            rows.append(" ".join("#%02X%02X%02X%02X" % p for p in row_px))
        approx_chars = w * h * 10
        lines = list(meta) + [
            f"注意: RGBA 输出约 {approx_chars} 字符，可缩小区域或降低 max_dimension 减少体积。",
            "像素数据（每行显示图像宽度个像素，格式 #RRGGBBAA）:",
        ]
        lines.extend(rows)
        return "\n".join(lines)

    def _build_multimodal_output(self, img, meta: list[str]) -> str:
        """多模态输出：文本元信息 + image_url data URI content blocks。"""
        buf = io.BytesIO()
        try:
            img.save(buf, format="PNG")
        except Exception as e:
            return f"(编码失败: {e})"
        data = buf.getvalue()
        text = "\n".join(meta)
        text += "\n图片已编码为 base64 PNG（data URI）随 content blocks 返回。"
        self.result_blocks = build_image_content_blocks(text, data, "image/png")
        return text


# ── 辅助函数 ──────────────────────────────────────────

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
