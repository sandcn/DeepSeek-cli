"""截图模板匹配（不依赖控件树，按画面里的图标 / 局部图片定位）。

用途：界面上没有可枚举的控件（游戏、canvas、自绘界面）时，把一小块「模板
图片」（图标 / 按钮截图）在整幅截图里找出来，返回其位置与置信度，再把坐标
交给输入 op 点击——不必人工读图估像素。

算法（纯 Python，零第三方依赖）：

  1. **粗搜索**：在整幅截图上按 ``stride`` 步长滑动模板，用**抽样像素**快速
     估算差异（带早停：累计差异一旦超过阈值立即放弃该位置）；
  2. **精修**：对粗搜索得分最高的若干候选，在其邻域做**全像素**穷举，取精确
     最优位置；
  3. **多尺度**：可选在若干缩放比例上重复（模板与截图像素比例不一致时用）；
  4. **去重**：按得分排序后做非极大值抑制，避免同一目标返回多个重叠框。

匹配度量用「每像素三通道平均绝对差」（0 = 完全一致），``tolerance`` 为允许
的平均通道差上限（0..255）；得分 ``score = 1 - 平均差 / 255``。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .png_decode import DecodedImage, decode_png_file

logger = logging.getLogger(__name__)

#: 默认匹配容差（每像素平均通道差上限）。截图与模板像素比例一致时用较小值，
#: 有缩放 / 抗锯齿时可调大。
DEFAULT_MATCH_TOLERANCE = 25

#: 默认返回的最大匹配数
DEFAULT_MAX_RESULTS = 10

#: 单次匹配返回条数上限（防御误传超大值）
MAX_RESULTS_LIMIT = 100

#: 粗搜索的抽样像素数（约值）
_COARSE_SAMPLES = 400

#: 保留用于精修的候选数
_REFINE_CANDIDATES = 16

#: 非极大值抑制的重叠阈值（交并比超过该值的框视为同一目标）
_NMS_IOU = 0.3

#: 最小模板边长（像素）——过小的模板匹配噪声极大，直接拒绝
MIN_TEMPLATE_SIDE = 4


class ImageMatchError(ValueError):
    """模板匹配参数非法（模板为空 / 模板大于图像 / 容差越界等）。"""


@dataclass(frozen=True)
class TemplateMatch:
    """一次模板匹配结果（左上角原点与图像一致）。"""

    x: int
    y: int
    width: int
    height: int
    score: float
    scale: float = 1.0

    @property
    def center_x(self) -> int:
        return self.x + self.width // 2

    @property
    def center_y(self) -> int:
        return self.y + self.height // 2

    def to_dict(self) -> dict:
        return {
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
            "center_x": self.center_x,
            "center_y": self.center_y,
            "score": round(self.score, 4),
            "scale": round(self.scale, 3),
        }


def match_template_file(image_path: str, template_path: str, *,
                        tolerance: int = DEFAULT_MATCH_TOLERANCE,
                        max_results: int = DEFAULT_MAX_RESULTS,
                        min_scale: float = 1.0, max_scale: float = 1.0,
                        scale_steps: int = 1) -> list[TemplateMatch]:
    """从文件读取图像与模板并匹配。"""
    try:
        image = decode_png_file(image_path)
        template = decode_png_file(template_path)
    except OSError as exc:
        raise ImageMatchError(f"读取图片失败: {exc}") from exc
    return match_template(image, template, tolerance=tolerance,
                          max_results=max_results, min_scale=min_scale,
                          max_scale=max_scale, scale_steps=scale_steps)


def scale_list(min_scale: float, max_scale: float, steps: int) -> list[float]:
    """生成缩放比例列表（含端点，去重、升序）。

    Raises:
        ImageMatchError: 比例非法或步数非法。
    """
    for name, value in (("min_scale", min_scale), ("max_scale", max_scale)):
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ImageMatchError(f"{name} 必须是数值，当前: {value!r}")
        if value <= 0:
            raise ImageMatchError(f"{name} 必须为正数，当前: {value}")
    if min_scale > max_scale:
        raise ImageMatchError(
            f"min_scale（{min_scale}）不能大于 max_scale（{max_scale}）"
        )
    try:
        count = int(steps)
    except (TypeError, ValueError):
        raise ImageMatchError(f"scale_steps 必须是整数，当前: {steps!r}") from None
    if count < 1:
        raise ImageMatchError(f"scale_steps 必须 >= 1，当前: {count}")
    if count == 1:
        return [float(min_scale)]
    span = max_scale - min_scale
    values = [min_scale + span * index / (count - 1) for index in range(count)]
    unique: list[float] = []
    for value in values:
        if not any(abs(value - existing) < 1e-6 for existing in unique):
            unique.append(float(value))
    return unique


def match_template(image: DecodedImage, template: DecodedImage, *,
                   tolerance: int = DEFAULT_MATCH_TOLERANCE,
                   max_results: int = DEFAULT_MAX_RESULTS,
                   min_scale: float = 1.0, max_scale: float = 1.0,
                   scale_steps: int = 1) -> list[TemplateMatch]:
    """在 ``image`` 中查找 ``template``，返回按得分降序的匹配列表。

    Args:
        image: 被搜索的图像（通常是窗口截图解码结果）。
        template: 模板图像（小图）。
        tolerance: 允许的每像素平均通道差（0..255）。
        max_results: 最多返回的匹配数。
        min_scale / max_scale / scale_steps: 模板缩放搜索范围。

    Raises:
        ImageMatchError: 参数非法（模板过小 / 大于图像 / 容差越界）。
    """
    count = _validate(image, template, tolerance, max_results)
    results: list[TemplateMatch] = []
    for scale in scale_list(min_scale, max_scale, scale_steps):
        scaled = _resize(template, scale)
        if scaled.width > image.width or scaled.height > image.height:
            continue
        if scaled.width < MIN_TEMPLATE_SIDE or scaled.height < MIN_TEMPLATE_SIDE:
            continue
        results.extend(_match_single(image, scaled, scale, tolerance, count))
    if not results:
        return []
    results.sort(key=lambda item: item.score, reverse=True)
    return _suppress(results, count)


def _validate(image: DecodedImage, template: DecodedImage,
              tolerance: int, max_results: int) -> int:
    if template.width < MIN_TEMPLATE_SIDE or template.height < MIN_TEMPLATE_SIDE:
        raise ImageMatchError(
            f"模板过小（{template.width}x{template.height}）：边长需 >= "
            f"{MIN_TEMPLATE_SIDE} 像素，否则匹配噪声过大"
        )
    if template.width > image.width or template.height > image.height:
        raise ImageMatchError(
            f"模板（{template.width}x{template.height}）大于被搜索图像"
            f"（{image.width}x{image.height}）"
        )
    if not 0 <= int(tolerance) <= 255:
        raise ImageMatchError(f"tolerance 需在 0..255 之间，当前: {tolerance}")
    try:
        count = int(max_results)
    except (TypeError, ValueError):
        raise ImageMatchError(f"max_results 必须是整数，当前: {max_results!r}") from None
    if count < 1:
        raise ImageMatchError(f"max_results 必须 >= 1，当前: {count}")
    return min(count, MAX_RESULTS_LIMIT)


def _match_single(image: DecodedImage, template: DecodedImage, scale: float,
                  tolerance: int, max_results: int) -> list[TemplateMatch]:
    """单尺度匹配：粗搜索 → 精修 → 组装结果。"""
    tw, th = template.width, template.height
    iw, ih = image.width, image.height
    max_x = iw - tw
    max_y = ih - th
    # 步长不超过模板短边的 1/4：保证真实位置附近总有粗搜索点（精修再补足）
    stride = max(1, min(tw, th) // 4)
    samples = _sample_offsets(tw, th)
    # 粗搜索用略放宽的阈值（抽样本身有误差），避免漏掉真实位置
    coarse_limit = int(tolerance * 1.5) * 3 * len(samples)
    candidates = _coarse_scan(image, template, max_x, max_y, stride, samples,
                              coarse_limit)
    matches: list[TemplateMatch] = []
    seen: set[tuple[int, int]] = set()
    for coarse_x, coarse_y in candidates:
        best = _refine(image, template, coarse_x, coarse_y, stride, tolerance)
        if best is None:
            continue
        x, y, mean = best
        if (x, y) in seen:
            continue
        seen.add((x, y))
        matches.append(TemplateMatch(
            x=x, y=y, width=tw, height=th,
            score=max(0.0, 1.0 - mean / 255.0), scale=scale))
    matches.sort(key=lambda item: item.score, reverse=True)
    return matches[:max_results]


def _coarse_scan(image: DecodedImage, template: DecodedImage, max_x: int,
                 max_y: int, stride: int, samples, limit: int
                 ) -> list[tuple[int, int]]:
    """粗搜索：按 stride 滑动、抽样比较、带早停，返回得分最高的候选位置。"""
    iw = image.width
    pixels = image.rgb
    sampled = _coarse_template(template, samples, iw)
    best: list[tuple[int, int, int]] = []
    for y in range(0, max_y + 1, stride):
        row_base = y * iw
        for x in range(0, max_x + 1, stride):
            total = 0
            base = (row_base + x) * 3
            for offset, tr, tg, tb in sampled:
                index = base + offset
                total += abs(pixels[index] - tr)
                total += abs(pixels[index + 1] - tg)
                total += abs(pixels[index + 2] - tb)
                if total > limit:
                    break
            else:
                _push(best, x, y, total)
    best.sort(key=lambda item: item[2])
    return [(item[0], item[1]) for item in best[:_REFINE_CANDIDATES]]


def _push(best: list, x: int, y: int, mean: int) -> None:
    best.append((x, y, mean))
    if len(best) > _REFINE_CANDIDATES * 4:
        best.sort(key=lambda item: item[2])
        del best[_REFINE_CANDIDATES:]


def _refine(image: DecodedImage, template: DecodedImage, coarse_x: int,
            coarse_y: int, stride: int, tolerance: int):
    """在粗候选邻域内做全像素穷举，返回 ``(x, y, 每像素平均通道差)``。"""
    iw = image.width
    tw, th = template.width, template.height
    max_x = image.width - tw
    max_y = image.height - th
    pixels = image.rgb
    tpl = template.rgb
    limit = int(tolerance) * 3 * tw * th
    best = None
    start_x = max(0, coarse_x - stride)
    end_x = min(max_x, coarse_x + stride)
    start_y = max(0, coarse_y - stride)
    end_y = min(max_y, coarse_y + stride)
    for y in range(start_y, end_y + 1):
        for x in range(start_x, end_x + 1):
            total = 0
            for row in range(th):
                index = ((y + row) * iw + x) * 3
                tindex = row * tw * 3
                row_bytes = tw * 3
                segment = pixels[index:index + row_bytes]
                target = tpl[tindex:tindex + row_bytes]
                if segment == target:
                    continue
                for col in range(row_bytes):
                    total += abs(segment[col] - target[col])
                    if total > limit:
                        break
                if total > limit:
                    break
            if total > limit:
                continue
            mean = total / (3 * tw * th)
            if best is None or mean < best[2]:
                best = (x, y, mean)
    return best


def _coarse_template(template: DecodedImage, samples,
                     image_width: int) -> list[tuple[int, int, int, int]]:
    """按抽样偏移取出模板像素 ``(offset, r, g, b)``。

    ``offset`` 以**被搜索图像**的宽度换算（``dy * image_width + dx``），
    这样在图像里直接加基址即可命中同一相对位置。
    """
    pixels = template.rgb
    out: list[tuple[int, int, int, int]] = []
    for dx, dy in samples:
        source = (dy * template.width + dx) * 3
        offset = (dy * image_width + dx) * 3
        out.append((offset, pixels[source], pixels[source + 1], pixels[source + 2]))
    return out


def _sample_offsets(width: int, height: int) -> list[tuple[int, int]]:
    """生成模板内的抽样偏移（约 :data:`_COARSE_SAMPLES` 个，均匀分布）。"""
    total = width * height
    step = max(1, total // _COARSE_SAMPLES)
    offsets: list[tuple[int, int]] = []
    for index in range(0, total, step):
        offsets.append((index % width, index // width))
    if not offsets:
        offsets.append((0, 0))
    return offsets


def _resize(template: DecodedImage, scale: float) -> DecodedImage:
    """最近邻缩放模板（scale=1 原样返回）。"""
    if abs(scale - 1.0) < 1e-9:
        return template
    width = max(1, int(round(template.width * scale)))
    height = max(1, int(round(template.height * scale)))
    source = template.rgb
    sw = template.width
    out = bytearray(width * height * 3)
    for y in range(height):
        sy = min(int(y / scale), template.height - 1)
        row_base = sy * sw
        for x in range(width):
            sx = min(int(x / scale), sw - 1)
            src = (row_base + sx) * 3
            dst = (y * width + x) * 3
            out[dst:dst + 3] = source[src:src + 3]
    return DecodedImage(width, height, bytes(out))


def _suppress(matches: list[TemplateMatch], max_results: int) -> list[TemplateMatch]:
    """非极大值抑制：去掉与已选结果重叠过多的框。"""
    selected: list[TemplateMatch] = []
    for candidate in matches:
        if all(_iou(candidate, chosen) <= _NMS_IOU for chosen in selected):
            selected.append(candidate)
            if len(selected) >= max_results:
                break
    return selected


def _iou(a: TemplateMatch, b: TemplateMatch) -> float:
    left = max(a.x, b.x)
    top = max(a.y, b.y)
    right = min(a.x + a.width, b.x + b.width)
    bottom = min(a.y + a.height, b.y + b.height)
    if right <= left or bottom <= top:
        return 0.0
    inter = (right - left) * (bottom - top)
    union = a.width * a.height + b.width * b.height - inter
    return inter / union if union > 0 else 0.0


__all__ = [
    "DEFAULT_MATCH_TOLERANCE",
    "DEFAULT_MAX_RESULTS",
    "MAX_RESULTS_LIMIT",
    "ImageMatchError",
    "TemplateMatch",
    "match_template",
    "match_template_file",
    "scale_list",
]
