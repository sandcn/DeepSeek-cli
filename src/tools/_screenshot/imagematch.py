"""截图模板匹配（不依赖控件树，按画面里的图标 / 局部图片定位）。

用途：界面上没有可枚举的控件（游戏、canvas、自绘界面）时，把一小块「模板
图片」（图标 / 按钮截图）在整幅截图里找出来，返回其位置与置信度，再把坐标
交给输入 op 点击——不必人工读图估像素。

算法（纯 Python，零第三方依赖）：

  1. **模板画像**：统计模板主色，标出**前景像素**（与主色差异明显的可见
     内容）；前景占比过低（近乎纯色模板）时退化为纯统计匹配；
  2. **粗搜索**：在整幅截图上按 ``stride`` 步长滑动模板，用**前景抽样像素**
     快速估算差异（带早停）。前景度量对「结构错位」敏感：模板里的大片同色
     背景不再把无关位置顶进候选，真实位置附近才有低分；
  3. **精修**：对粗搜索得分最高的若干候选，在其邻域做**全前景像素**穷举，
     取精确最优位置；
  4. **多尺度**：可选在若干缩放比例上重复（模板与截图像素比例不一致时用）；
  5. **校验**：命中位置的全像素平均差与**前景平均差**都必须落在
     ``tolerance`` 内，否则视为不匹配丢弃（纯色模板跳过前景校验）；
  6. **去重**：按得分排序后做非极大值抑制，避免同一目标返回多个重叠框。

匹配度量用「每像素三通道平均绝对差」（0 = 完全一致），``tolerance`` 为允许
的平均通道差上限（0..255）；得分 ``score = 1 - 平均差 / 255``。

前景校验解决的是「模板含大片纯色背景」的经典误命中：只看全图平均差时，
空白背景区域的「文字像素全部错位」误差会被大片背景稀释成很小的平均值
（误命中得高分），而前景平均差只统计模板的可见内容，把这类位置判为不匹配。
"""

from __future__ import annotations

import logging
from collections import Counter
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
_COARSE_SAMPLES = 64

#: 粗搜索「快速预筛」的抽样点数：这么多个抽样点全部不匹配的位置直接淘汰
#: （真实位置附近至少有部分前景点对齐，因此可用它极快地过滤大片背景）
_COARSE_FAST_SAMPLES = 8

#: 粗搜索早停允许的不匹配抽样点占比上限（抽样比较有噪声，留足余量）
_COARSE_MISMATCH_RATIO = 0.85

#: 粗搜索的像素级匹配门槛（容差很小时也给下限，容忍抗锯齿 / 采样噪声）
_COARSE_MIN_PIXEL_TOLERANCE = 8

#: 保留用于精修的候选数
_REFINE_CANDIDATES = 32

#: 候选分散选择时最多考察的评分条数（防止极端情况下遍历过慢）
_CANDIDATE_SCAN_LIMIT = 4000

#: 非极大值抑制的重叠阈值（交并比超过该值的框视为同一目标）
_NMS_IOU = 0.3

#: 最小模板边长（像素）——过小的模板匹配噪声极大，直接拒绝
MIN_TEMPLATE_SIDE = 4

#: 前景像素判定门槛：与模板主色的每通道最大差超过该值即视为「可见内容」
_FOREGROUND_DELTA = 24

#: 前景像素数量 / 占比低于这些阈值时视为「近乎纯色模板」（跳过前景校验）
_MIN_FOREGROUND_PIXELS = 16
_MIN_FOREGROUND_RATIO = 0.02

#: 粗搜索步长上限：步长越小越不容易漏掉真实位置（代价是更多位置参与比较）。
#: 取 2 可保证真实位置附近总有网格点只错位 1 像素，前景内容仍能部分对齐。
_MAX_COARSE_STRIDE = 2

#: 粗搜索位置数预算：超过则自动放大步长，避免大图 / 大模板匹配过慢
_MAX_COARSE_POSITIONS = 150000


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


@dataclass(frozen=True)
class _TemplateProfile:
    """模板结构画像：前景像素序号（``y * width + x``）与像素总数。

    前景像素 = 与模板主色差异明显的像素，代表模板的「可见内容」（图标笔画、
    文字、边框等）。``structured`` 为 False 表示模板近乎纯色：此时结构校验
    没有意义，匹配退化为纯统计（平均通道差）语义。
    """

    foreground: tuple
    total_pixels: int

    @property
    def structured(self) -> bool:
        """模板是否有足够前景内容（近乎纯色时为 False）。"""
        count = len(self.foreground)
        if count < _MIN_FOREGROUND_PIXELS:
            return False
        return count >= self.total_pixels * _MIN_FOREGROUND_RATIO

    def indices(self, total_pixels: int) -> tuple:
        """参与结构比较的像素序号（无结构时为全部像素）。"""
        if self.structured:
            return self.foreground
        return tuple(range(total_pixels))


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
    """单尺度匹配：模板画像 → 粗搜索 → 精修 → 结构校验。"""
    tw, th = template.width, template.height
    max_x = image.width - tw
    max_y = image.height - th
    profile = _analyze_template(template)
    stride = _coarse_stride(tw, th, max_x, max_y)
    samples = _sample_indices(profile, tw * th)
    sampled = _coarse_template(template, samples, image.width, tw)
    pixel_tolerance = max(int(tolerance), _COARSE_MIN_PIXEL_TOLERANCE)
    candidates = _coarse_scan(image, sampled, max_x, max_y, stride,
                              pixel_tolerance)
    matches: list[TemplateMatch] = []
    seen: set = set()
    for coarse_x, coarse_y in candidates:
        best = _refine(image, template, profile, coarse_x, coarse_y,
                       stride, pixel_tolerance)
        if best is None:
            continue
        x, y, foreground_mean = best
        if (x, y) in seen:
            continue
        seen.add((x, y))
        if profile.structured and foreground_mean > tolerance:
            # 结构不一致：模板的可见内容没有对上（典型是大片背景处的误命中）
            continue
        mean = _mean_channel_diff(image, template, x, y)
        if mean > tolerance:
            continue
        matches.append(TemplateMatch(
            x=x, y=y, width=tw, height=th,
            score=max(0.0, 1.0 - mean / 255.0), scale=scale))
    matches.sort(key=lambda item: item.score, reverse=True)
    return matches[:max_results]


def _coarse_stride(template_width: int, template_height: int,
                   max_x: int, max_y: int) -> int:
    """粗搜索步长：不超过模板短边的 1/8，且受位置数预算约束。"""
    stride = max(1, min(template_width, template_height) // 8)
    stride = min(stride, _MAX_COARSE_STRIDE)
    while stride < 128:
        positions = (max_x // stride + 1) * (max_y // stride + 1)
        if positions <= _MAX_COARSE_POSITIONS:
            break
        stride += 1
    return stride


def _analyze_template(template: DecodedImage) -> _TemplateProfile:
    """统计模板主色并标出前景像素（与主色差异明显的可见内容）。"""
    total = template.width * template.height
    if total <= 0:
        return _TemplateProfile((), 0)
    pixels = template.rgb
    buckets = Counter()
    for index in range(total):
        base = index * 3
        buckets[(pixels[base] >> 3, pixels[base + 1] >> 3,
                 pixels[base + 2] >> 3)] += 1
    if not buckets:
        return _TemplateProfile((), total)
    dominant = buckets.most_common(1)[0][0]
    # 用量化桶中心还原主色（量化误差最多 7，不影响前景判定）
    key_r, key_g, key_b = ((channel << 3) | 4 for channel in dominant)
    foreground: list = []
    for index in range(total):
        base = index * 3
        delta = max(abs(pixels[base] - key_r),
                    abs(pixels[base + 1] - key_g),
                    abs(pixels[base + 2] - key_b))
        if delta > _FOREGROUND_DELTA:
            foreground.append(index)
    return _TemplateProfile(tuple(foreground), total)


def _sample_indices(profile: _TemplateProfile, total_pixels: int) -> tuple:
    """从前景像素中抽样（无结构时从全部像素抽样），约 :data:`_COARSE_SAMPLES` 个。"""
    pool = profile.indices(total_pixels)
    count = len(pool)
    if count <= 0:
        return ()
    step = max(1, count // _COARSE_SAMPLES)
    return tuple(pool[index] for index in range(0, count, step))


def _coarse_template(template: DecodedImage, samples, image_width: int,
                     template_width: int) -> list:
    """按抽样序号取出模板像素 ``(offset, r, g, b)``。

    ``offset`` 以**被搜索图像**的宽度换算（``dy * image_width + dx``），
    这样在图像里直接加基址即可命中同一相对位置。
    """
    pixels = template.rgb
    out: list = []
    for index in samples:
        dy, dx = divmod(index, template_width)
        source = index * 3
        out.append((
            (dy * image_width + dx) * 3,
            pixels[source], pixels[source + 1], pixels[source + 2],
        ))
    return out


def _coarse_scan(image: DecodedImage, sampled, max_x: int, max_y: int,
                 stride: int, pixel_tolerance: int) -> list:
    """粗搜索：按 stride 滑动、用前景抽样像素做**不匹配计数**并带早停。

    不匹配计数的好处是「结构错位」会立刻暴露：模板里的文字 / 图标没有对齐
    时，对应前景抽样点几乎全判为不匹配；而按平均差排序时，大片同色背景会
    把这类位置的误差稀释掉，导致真实位置被无关位置挤出候选。

    早停分两级：前 :data:`_COARSE_FAST_SAMPLES` 个抽样点全部不匹配即淘汰
    （真实位置附近总有一部分前景点对齐，用这一条即可极快地过滤大片背景），
    其后按不匹配占比上限 :data:`_COARSE_MISMATCH_RATIO` 淘汰。
    """
    iw = image.width
    pixels = image.rgb
    total_samples = len(sampled)
    hard_limit = max(1, int(total_samples * _COARSE_MISMATCH_RATIO))
    fast_limit = min(_COARSE_FAST_SAMPLES, total_samples)
    scored: list = []
    for y in range(0, max_y + 1, stride):
        row_base = y * iw
        for x in range(0, max_x + 1, stride):
            base = (row_base + x) * 3
            mismatch = 0
            total = 0
            checked = 0
            for offset, tr, tg, tb in sampled:
                index = base + offset
                dr = pixels[index] - tr
                if dr < 0:
                    dr = -dr
                dg = pixels[index + 1] - tg
                if dg < 0:
                    dg = -dg
                db = pixels[index + 2] - tb
                if db < 0:
                    db = -db
                total += dr + dg + db
                if (dr > pixel_tolerance or dg > pixel_tolerance
                        or db > pixel_tolerance):
                    mismatch += 1
                checked += 1
                if checked <= fast_limit:
                    if mismatch >= fast_limit:
                        break
                elif mismatch > hard_limit:
                    break
            else:
                scored.append((mismatch, total, x, y))
    return _pick_candidates(scored, stride)


def _pick_candidates(scored: list, stride: int) -> list:
    """按「不匹配点最少 → 差异和最小」排序，取彼此分散的若干候选位置。

    分散（同一区域只保留得分最好的一个）能避免一个显眼目标占满候选名额，
    让更多区域有机会进入精修。
    """
    scored.sort(key=lambda item: (item[0], item[1]))
    picked: list = []
    gap = max(1, stride)
    for position, item in enumerate(scored):
        if position >= _CANDIDATE_SCAN_LIMIT:
            break
        _, _, x, y = item
        if any(abs(x - px) < gap and abs(y - py) < gap
               for _, _, px, py in picked):
            continue
        picked.append(item)
        if len(picked) >= _REFINE_CANDIDATES:
            break
    return [(item[2], item[3]) for item in picked]


def _refine(image: DecodedImage, template: DecodedImage,
            profile: _TemplateProfile, coarse_x: int, coarse_y: int,
            radius: int, pixel_tolerance: int):
    """在粗候选邻域内按前景像素穷举，返回 ``(x, y, 前景平均通道差)``。

    ``pixel_tolerance`` 只用于早停（避免在明显不可能的位置上算完全部前景
    像素），取值不低于 :data:`_COARSE_MIN_PIXEL_TOLERANCE`，因此「接近但
    不完全一致」的真实位置不会被误淘汰——最终是否算匹配由调用方的
    ``tolerance`` 与前景校验决定。

    返回 None 表示邻域内没有位置的前景差落在早停阈值内。
    """
    iw = image.width
    tw, th = template.width, template.height
    max_x = iw - tw
    max_y = image.height - th
    pixels = image.rgb
    tpl = template.rgb
    offsets = _pixel_offsets(profile.indices(tw * th), tw, iw)
    limit = int(pixel_tolerance) * 3 * len(offsets)
    best = None
    x_end = min(max_x, coarse_x + radius)
    y_end = min(max_y, coarse_y + radius)
    for y in range(max(0, coarse_y - radius), y_end + 1):
        row_base = y * iw
        for x in range(max(0, coarse_x - radius), x_end + 1):
            base = (row_base + x) * 3
            total = 0
            for source_offset, target_offset in offsets:
                index = base + source_offset
                total += abs(pixels[index] - tpl[target_offset])
                total += abs(pixels[index + 1] - tpl[target_offset + 1])
                total += abs(pixels[index + 2] - tpl[target_offset + 2])
                if total > limit:
                    break
            else:
                mean = total / (3 * len(offsets))
                if best is None or mean < best[2]:
                    best = (x, y, mean)
    return best


def _pixel_offsets(indices, template_width: int, image_width: int) -> tuple:
    """把像素序号换算为 ``(图像内字节偏移, 模板内字节偏移)``，供逐像素比较。"""
    return tuple(
        (((index // template_width) * image_width + index % template_width) * 3,
         index * 3)
        for index in indices
    )


def _mean_channel_diff(image: DecodedImage, template: DecodedImage,
                       x: int, y: int) -> float:
    """命中位置的**全像素**每像素平均通道差（得分与容差判定用）。"""
    iw = image.width
    tw, th = template.width, template.height
    pixels = image.rgb
    tpl = template.rgb
    total = 0
    for row in range(th):
        base = ((y + row) * iw + x) * 3
        target = row * tw * 3
        row_bytes = tw * 3
        segment = pixels[base:base + row_bytes]
        reference = tpl[target:target + row_bytes]
        if segment == reference:
            continue
        for col in range(row_bytes):
            total += abs(segment[col] - reference[col])
    return total / (3 * tw * th)


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


def _suppress(matches: list, max_results: int) -> list:
    """非极大值抑制：去掉与已选结果重叠过多的框。"""
    selected: list = []
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
