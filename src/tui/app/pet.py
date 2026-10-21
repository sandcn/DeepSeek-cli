"""蓝色大肥鱼宠物浮窗（``/pet``）— 无边框 / 透明背景 / 行内裁剪。

用户需求（2026-10-22）：

  - 用一个**浮动窗口**显示蓝色大肥鱼宠物（娘化萌版：大眼、腮红、发饰）；
  - 宠物在窗口里做各种搞怪动作：跳来跳去、左右摇摆、冲刺、转圈、唱歌、
    眨眼、睡觉，并随机冒泡说些可爱的话；
  - **窗口无框**（``border=False``），**只显示行内容**：背景透明
    （``background=None``），每行按实际绘制字符拆段渲染——空白列不写入
    画布，底层内容不会被矩形背景抹掉；行宽天然可以大小不一。

实现约束：

  - 内容组件是普通函数组件，每帧从 ``time.monotonic()`` 计算姿态
    （不维护可变状态）——浮窗声明 ``refresh_interval=1/30``，空闲时由会话
    按 30fps 强制重建，既保留动画又避免 60Hz 全量重建；
  - 关闭不占用 Esc（Esc 在 TUI 中承担中断/清空输入语义），用 ``/pet off``
    或再次 ``/pet`` 收回；
  - 窗口 id 固定 ``pet``，重复打开 = 替换（动画从新起点开始）。
"""

from __future__ import annotations

import math
import time
from collections.abc import Iterable

from src.tui._width import truncate_width, wcswidth_simple
from src.tui.core.style import Style
from src.tui.ink import BOX, TEXT, StyledRun, h

from .floating import FloatingWindowSpec

#: 浮窗 id（``/pet off`` / 重复打开按此替换）
PET_WINDOW_ID = "pet"

#: 舞台尺寸（列 / 行）。舞台固定宽度，宠物在其中左右/上下移动——
#: 窗口宽度不随动作抖动；未绘制区域保持透明。
PET_STAGE_WIDTH = 30
PET_STAGE_HEIGHT = 12

#: 宠物精灵尺寸与在舞台中的基准位置。
PET_SPRITE_WIDTH = 24
PET_SPRITE_HEIGHT = 9
PET_SPRITE_TOP = 2
PET_SPRITE_LEFT = (PET_STAGE_WIDTH - PET_SPRITE_WIDTH) // 2

#: 左右移动上限（保证精灵不越出舞台）。
PET_MAX_DX = PET_STAGE_WIDTH - PET_SPRITE_WIDTH - PET_SPRITE_LEFT

#: 动画刷新间隔：30fps（内容依赖时间，但无需 60fps 全帧重建）。
PET_REFRESH_INTERVAL = 1.0 / 30.0

# ── 配色（256 色；固定蓝色系，不随主题变化——宠物形象是内容的一部分） ──
_S_BODY = Style(fg=39, bold=True)          # 亮蓝身体
_S_BODY_DEEP = Style(fg=33, bold=True)     # 深蓝（尾鳍阴影）
_S_OUTLINE = Style(fg=25, bold=True)       # 深蓝描边
_S_BELLY = Style(fg=117)                    # 浅蓝肚皮
_S_FIN = Style(fg=45)                       # 青色胸鳍
_S_EYE = Style(fg=231, bold=True)           # 白色大眼
_S_EYE_DIZZY = Style(fg=226, bold=True)     # 转圈眼（黄色）
_S_MOUTH = Style(fg=203, bold=True)         # 粉色小嘴
_S_BLUSH = Style(fg=211, bold=True)         # 腮红
_S_BOW = Style(fg=213, bold=True)           # 发饰
_S_BUBBLE_BORDER = Style(fg=51)             # 气泡边框（青）
_S_BUBBLE_TEXT = Style(fg=231, bold=True)   # 气泡文字
_S_NOTE = Style(fg=220, bold=True)          # 音符
_S_Z = Style(fg=111, bold=True)             # 睡觉 z
_S_SPARK = Style(fg=226, bold=True)         # 星星
_S_DUST = Style(fg=117)                     # 落地尘埃
_S_HEART = Style(fg=211, bold=True)         # 爱心
_S_MOTION = Style(fg=45)                    # 冲刺速度线

#: 单元格 = (字符, 样式)；空单元格固定为 (" ", None)。
Cell = tuple[str, Style | None]
_EMPTY: Cell = (" ", None)

#: 会冒出的搞怪台词（短句，避免撑宽窗口）。
PET_LINES: tuple[str, ...] = (
    "咕噜咕噜～",
    "本鱼是美少女！",
    "跳跳跳！",
    "摸摸头嘛～",
    "饿了…有小鱼干吗？",
    "才不是胖！是圆润！",
    "尾巴甩甩～",
    "今天也要加油鸭！",
    "看我空中转体三周半！",
    "不许戳我肚子！",
    "泡泡吐给你～",
    "最喜欢你啦～",
)

#: 动作表：(名称, 时长秒)。循环播放；同一时刻只处于一个动作。
PET_ACTIONS: tuple[tuple[str, float], ...] = (
    ("idle", 2.4),
    ("jump", 1.6),
    ("wiggle", 1.8),
    ("dash", 2.0),
    ("spin", 1.4),
    ("sing", 2.2),
    ("wink", 1.6),
    ("sleep", 2.8),
)

PET_CYCLE = sum(duration for _name, duration in PET_ACTIONS)

#: 气泡显示周期：每 8 秒露头 2.8 秒（开局立即说一句）。
PET_BUBBLE_PERIOD = 8.0
PET_BUBBLE_VISIBLE = 2.8


def _set(grid: list[list[Cell]], x: int, y: int, ch: str, style: Style | None) -> None:
    """安全写单元格（越界忽略）。"""
    if 0 <= y < len(grid) and 0 <= x < len(grid[0]):
        grid[y][x] = (ch, style)


def _build_base_sprite() -> list[list[Cell]]:
    """构建宠物基础精灵（9 行 × 24 列，未做镜像/位移）。

    造型：圆滚滚的蓝色鱼身 + 右侧扇形尾巴 + 左侧胸鳍 + 头顶小花 +
    大眼、腮红、小嘴（娘化萌版）。
    """
    w, h = PET_SPRITE_WIDTH, PET_SPRITE_HEIGHT
    grid: list[list[Cell]] = [[_EMPTY for _ in range(w)] for _ in range(h)]
    cx, cy = 10, 5
    rx, ry = 7.5, 3.8

    def inside_ellipse(x: int, y: int) -> bool:
        dx = (x - cx) / rx
        dy = (y - cy) / ry
        return dx * dx + dy * dy <= 1.0

    body = {
        (x, y)
        for y in range(h)
        for x in range(w)
        if inside_ellipse(x, y)
    }
    # 右侧尾鳍：向右侧张开的扇形（尖端连在鱼身右侧）。
    tail = {
        (x, y)
        for y in range(2, 9)
        for x in range(17, 17 + max(0, 5 - abs(y - 5)))
        if 0 <= x < w
    }
    solid = body | tail
    # 先铺尾巴（身体后绘制会覆盖连接处，避免描边割裂）。
    for x, y in tail:
        _set(grid, x, y, "█", _S_BODY_DEEP)
    # 鱼身填充 + 肚皮。
    for x, y in body:
        _set(grid, x, y, "█", _S_BODY)
    for x, y in body:
        if y >= cy + 1 and abs(x - cx) <= 4:
            _set(grid, x, y, "▒", _S_BELLY)
    # 描边：只画朝向外部的边（身体与尾巴相接处不描边）。
    for x, y in solid:
        if any((x + dx, y + dy) not in solid for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))):
            _set(grid, x, y, "▓", _S_OUTLINE)
    # 左侧胸鳍（像小手）。
    for x, y in ((0, 6), (0, 7), (0, 8), (1, 6), (1, 7), (1, 8), (2, 6), (2, 7)):
        _set(grid, x, y, "▒", _S_FIN)
    # 头顶小花（娘化发饰）。
    _set(grid, cx - 3, 0, "✿", _S_BOW)
    # 眼睛（大眼）与睫毛。
    _set(grid, cx - 4, cy - 1, "◕", _S_EYE)
    _set(grid, cx + 4, cy - 1, "◕", _S_EYE)
    _set(grid, cx - 5, cy - 2, "╱", _S_OUTLINE)
    _set(grid, cx + 5, cy - 2, "╲", _S_OUTLINE)
    # 腮红。
    for x in (cx - 7, cx - 6, cx + 6, cx + 7):
        _set(grid, x, cy + 1, "╱", _S_BLUSH)
    # 小嘴。
    _set(grid, cx, cy + 2, "ω", _S_MOUTH)
    return grid


_BASE_SPRITE: tuple[tuple[Cell, ...], ...] = tuple(
    tuple(row) for row in _build_base_sprite()
)

#: 镜像时的字符替换（左右对称的符号保持不变）。
_MIRROR_CHARS = {
    "╱": "╲",
    "╲": "╱",
    "╭": "╮",
    "╮": "╭",
    "╰": "╯",
    "╯": "╰",
    "◀": "▶",
    "▶": "◀",
}


def _mirror_sprite(sprite: list[list[Cell]]) -> list[list[Cell]]:
    """水平镜像精灵（冲刺掉头 / 转圈用）。"""
    mirrored: list[list[Cell]] = []
    for row in sprite:
        new_row: list[Cell] = []
        for ch, style in reversed(row):
            new_row.append((_MIRROR_CHARS.get(ch, ch), style))
        mirrored.append(new_row)
    return mirrored


def _face_sprite(eyes: tuple[str, str], mouth: str) -> list[list[Cell]]:
    """复制基础精灵并替换眼睛 / 嘴型。"""
    sprite = [list(row) for row in _BASE_SPRITE]
    left_style = _S_EYE_DIZZY if eyes[0] == "@" else _S_EYE
    right_style = _S_EYE_DIZZY if eyes[1] == "@" else _S_EYE
    _set(sprite, 6, 4, eyes[0], left_style)
    _set(sprite, 14, 4, eyes[1], right_style)
    _set(sprite, 10, 7, mouth, _S_MOUTH)
    return sprite


def _action_at(t: float) -> tuple[str, float]:
    """返回 ``(动作名, 动作内进度 0..1)``。"""
    if PET_CYCLE <= 0:
        return "idle", 0.0
    phase = max(0.0, float(t)) % PET_CYCLE
    for name, duration in PET_ACTIONS:
        if phase < duration:
            return name, phase / duration if duration > 0 else 0.0
        phase -= duration
    return PET_ACTIONS[-1][0], 0.0


def action_window(name: str) -> tuple[float, float]:
    """返回指定动作在循环中的 ``(起始时间, 时长)``（测试/调试用）。

    Raises:
        ValueError: 动作名不存在。
    """
    start = 0.0
    for action_name, duration in PET_ACTIONS:
        if action_name == name:
            return start, duration
        start += duration
    raise ValueError(f"未知宠物动作: {name!r}")


def _bubble_text(t: float) -> str:
    """按时间决定是否说台词（每 8 秒说 2.8 秒）。"""
    if PET_BUBBLE_PERIOD <= 0:
        return ""
    phase = max(0.0, float(t)) % PET_BUBBLE_PERIOD
    if phase >= PET_BUBBLE_VISIBLE:
        return ""
    index = (int(t // PET_BUBBLE_PERIOD) * 5 + 3) % len(PET_LINES)
    return PET_LINES[index]


class PetPose:
    """某一时刻的宠物姿态（纯数据，便于单元测试）。"""

    __slots__ = ("action", "progress", "eyes", "mouth", "dx", "dy", "flip", "effect", "line")

    def __init__(
        self,
        action: str,
        progress: float,
        eyes: tuple[str, str],
        mouth: str,
        *,
        dx: int = 0,
        dy: int = 0,
        flip: bool = False,
        effect: str = "",
        line: str = "",
    ) -> None:
        self.action = action
        self.progress = progress
        self.eyes = eyes
        self.mouth = mouth
        self.dx = int(dx)
        self.dy = int(dy)
        self.flip = bool(flip)
        self.effect = effect
        self.line = line

    def __repr__(self) -> str:  # pragma: no cover - 调试友好
        return (
            f"PetPose(action={self.action!r}, progress={self.progress:.2f}, "
            f"eyes={self.eyes!r}, mouth={self.mouth!r}, dx={self.dx}, dy={self.dy}, "
            f"flip={self.flip}, effect={self.effect!r})"
        )


def pet_pose_at(t: float, *, bubble: bool = True) -> PetPose:
    """计算 ``t`` 秒时的姿态（时间从宠物出现时开始计）。

    动作规则（全部为确定性时间函数，便于测试与复现）：

      - ``idle``：缓慢上下浮动，偶发眨眼；
      - ``jump``：按正弦曲线上跳 0~2 行，空中眯眼、张嘴，落地扬尘；
      - ``wiggle``：左右快速摇摆，身体两侧冒小波浪；
      - ``dash``：在舞台内左右冲刺，掉头时镜像，带速度线；
      - ``spin``：原地快速左右翻转 + 头晕眼 + 星星；
      - ``sing``：闭眼唱歌，右侧飘出音符；
      - ``wink``：单眼眨眼，冒出爱心；
      - ``sleep``：闭眼下沉，头顶飘 z。
    """
    action, progress = _action_at(t)
    line = _bubble_text(t) if bubble else ""
    p = max(0.0, min(1.0, progress))

    if action == "jump":
        height = math.sin(math.pi * p)
        dy = -int(round(2.0 * height))
        eyes = ("^", "^") if height > 0.25 else ("◕", "◕")
        mouth = "▽" if height > 0.25 else "ω"
        effect = "dust" if p > 0.82 else ("spark" if height > 0.55 else "")
        return PetPose(action, p, eyes, mouth, dy=dy, effect=effect, line=line)

    if action == "wiggle":
        dx = 1 if int(p * 10) % 2 == 0 else -1
        return PetPose(action, p, ("◕", "◕"), "ω", dx=dx, effect="wiggle", line=line)

    if action == "dash":
        wave = math.sin(2.0 * math.pi * p)
        dx = int(round(wave * PET_MAX_DX))
        flip = math.cos(2.0 * math.pi * p) < 0
        return PetPose(
            action, p, ("^", "^"), "o", dx=dx, flip=flip,
            effect="motion", line=line,
        )

    if action == "spin":
        flip = int(p * 8) % 2 == 0
        return PetPose(
            action, p, ("@", "@"), "o", flip=flip,
            effect="spark", line=line,
        )

    if action == "sing":
        dy = -1 if int(p * 4) % 2 else 0
        return PetPose(
            action, p, ("^", "^"), "o", dy=dy, effect="note", line=line,
        )

    if action == "wink":
        return PetPose(
            action, p, ("◕", "-"), "ω", effect="heart", line=line,
        )

    if action == "sleep":
        dy = 1 if p > 0.25 else 0
        return PetPose(
            action, p, ("∪", "∪"), "ω", dy=dy, effect="z", line=line,
        )

    # idle：每 2.2 秒上下浮一次，每 3.6 秒眨一次眼。
    bob = -1 if (t % 2.2) >= 1.1 else 0
    blink = (t % 3.6) < 0.12
    eyes = ("-", "-") if blink else ("◕", "◕")
    return PetPose(action, p, eyes, "ω", dy=bob, line=line)


def _apply_effect(stage: list[list[Cell]], effect: str, t: float, pose: PetPose) -> None:
    """把动作特效画到舞台（独立于宠物精灵，便于做出漂浮感）。"""
    w = len(stage[0]) if stage else 0
    h = len(stage)

    def put(x: int, y: int, ch: str, style: Style) -> None:
        if 0 <= x < w and 0 <= y < h:
            stage[y][x] = (ch, style)

    if effect == "dust":
        for offset, ch in ((4, "·"), (8, "·"), (13, "✧"), (18, "·"), (22, "·")):
            put(offset + (int(t * 12) % 2), PET_STAGE_HEIGHT - 1, ch, _S_DUST)
    elif effect == "spark":
        for x, y in ((4, 2), (24, 3), (6, 6), (23, 6)):
            put(x, y + (int(t * 8) % 2), "✦", _S_SPARK)
    elif effect == "note":
        drift = int(t * 5) % 3
        put(25, 5 - drift, "♪", _S_NOTE)
        put(27, 3 + drift, "♫", _S_NOTE)
    elif effect == "heart":
        put(14, 1 + (int(t * 4) % 2), "♥", _S_HEART)
        put(17, 2 + (int(t * 3) % 2), "♥", _S_HEART)
    elif effect == "z":
        drift = int(t * 3) % 3
        put(24, 4 - drift, "z", _S_Z)
        put(26, 2 + drift, "Z", _S_Z)
        put(27, 6 - drift, "z", _S_Z)
    elif effect == "motion":
        for x, y in ((0, 6), (0, 7), (0, 8), (28, 6), (28, 7), (28, 8)):
            put(x, y, "≋", _S_MOTION)
    elif effect == "wiggle":
        offset = 1 if pose.dx > 0 else 0
        put(1 + offset, 5, "∼", _S_MOTION)
        put(26 - offset, 8, "∼", _S_MOTION)


def _compose_grid(pose: PetPose, t: float) -> list[list[Cell]]:
    """合成完整内容网格（舞台固定 12 行，气泡叠在舞台顶部）。

    气泡与宠物共用一个固定高度的舞台：气泡占顶部 3 行，宠物在气泡显示时
    下移到其下方，避免「窗口高度增加 → 顶部被视口裁掉、气泡永远看不见」。
    """
    stage: list[list[Cell]] = [
        [_EMPTY for _ in range(PET_STAGE_WIDTH)]
        for _ in range(PET_STAGE_HEIGHT)
    ]
    sprite = _face_sprite(pose.eyes, pose.mouth)
    if pose.flip:
        sprite = _mirror_sprite(sprite)
    top = PET_SPRITE_TOP + pose.dy
    if pose.line:
        top = max(top, 3)  # 气泡占 0..2 行，宠物从其下方开始
    for y, row in enumerate(sprite):
        for x, cell in enumerate(row):
            if cell[0] == " ":
                continue
            tx = x + PET_SPRITE_LEFT + pose.dx
            ty = y + top
            if 0 <= tx < PET_STAGE_WIDTH and 0 <= ty < PET_STAGE_HEIGHT:
                stage[ty][tx] = cell
    _apply_effect(stage, pose.effect, t, pose)
    if pose.line:
        _draw_bubble(stage, pose.line)
    return stage


def _draw_bubble(stage: list[list[Cell]], text: str) -> None:
    """把三行气泡写到舞台顶部（空白处保持透明）。"""
    max_text = max(4, PET_STAGE_WIDTH - 4)
    text = truncate_width(str(text or ""), max_text)
    width = max(10, min(PET_STAGE_WIDTH, wcswidth_simple(text) + 4))
    left = max(0, (PET_STAGE_WIDTH - width) // 2)
    if not stage or len(stage) < 3:
        return
    for x in range(width):
        top_ch = "╭" if x == 0 else ("╮" if x == width - 1 else "─")
        bottom_ch = "╰" if x == 0 else ("╯" if x == width - 1 else "─")
        stage[0][left + x] = (top_ch, _S_BUBBLE_BORDER)
        stage[2][left + x] = (bottom_ch, _S_BUBBLE_BORDER)
    stage[1][left] = ("│", _S_BUBBLE_BORDER)
    stage[1][left + width - 1] = ("│", _S_BUBBLE_BORDER)
    # 文本从边框内第二列开始（与边框之间留 1 列透明空隙）。
    x = left + 2
    for ch in text:
        if x >= left + width - 1:
            break
        stage[1][x] = (ch, _S_BUBBLE_TEXT)
        x += max(1, wcswidth_simple(ch))


def _cells_to_runs(cells: Iterable[Cell]) -> list[StyledRun]:
    """把连续单元格合并为同样式的 run（减少 TEXT 节点内的 run 数）。"""
    runs: list[StyledRun] = []
    buffer: list[str] = []
    current: Style | None = None
    for ch, style in cells:
        if buffer and style is not current:
            runs.append(StyledRun("".join(buffer), current))
            buffer = []
        current = style
        buffer.append(ch)
    if buffer:
        runs.append(StyledRun("".join(buffer), current))
    return runs


def _row_element(key: str, row: list[Cell]) -> object:
    """把一行单元格渲染为行 BOX（只绘制非空白段，空白列透出底层内容）。"""
    children: list = []
    index = 0
    prev_end = 0
    row_len = len(row)
    while index < row_len:
        while index < row_len and row[index][0] == " ":
            index += 1
        if index >= row_len:
            break
        start = index
        cells: list[Cell] = []
        while index < row_len and row[index][0] != " ":
            cells.append(row[index])
            index += 1
        runs = _cells_to_runs(cells)
        gap = max(0, start - prev_end)
        children.append(h(TEXT, {
            "key": f"{key}-seg{start}",
            "marginLeft": gap,
            "height": 1,
            "styled": runs,
        }))
        prev_end = start + sum(run.width for run in runs)
    return h(BOX, {
        "key": key,
        "flexDirection": "row",
        "height": 1,
    }, children)


def PetFloatingWindow(props: dict) -> object:  # noqa: N802 - React Ink 组件命名
    """宠物浮窗内容组件（每帧按时间计算姿态）。

    Props（由 ``FloatingWindow`` 注入）：
        width: 内容可用宽度（列）——舞台宽度取 ``min(30, width)``。
        started_at: 动画起点（``time.monotonic()`` 时间戳；由规格闭包注入）。
        now: 可选当前时间（测试注入；生产按 ``time.monotonic()``）。
        window / model / geometry / height: 浮窗标准 props（本组件不使用）。
    """
    try:
        avail = int(props.get("width", PET_STAGE_WIDTH))
    except (TypeError, ValueError, OverflowError):
        avail = PET_STAGE_WIDTH
    stage_w = max(8, min(PET_STAGE_WIDTH, avail))
    started = props.get("started_at", 0.0)
    now = props.get("now")
    if now is None:
        now = time.monotonic()
    try:
        t = max(0.0, float(now) - float(started))
    except (TypeError, ValueError, OverflowError):
        t = 0.0
    pose = pet_pose_at(t)
    rows = _compose_grid(pose, t)
    if stage_w != PET_STAGE_WIDTH:
        rows = _clamp_rows(rows, stage_w)
    elements = [
        _row_element(f"pet-row-{index}", row)
        for index, row in enumerate(rows)
    ]
    return h(BOX, {
        "key": "pet-body",
        "flexDirection": "column",
        "width": stage_w,
    }, elements)


def _clamp_rows(rows: list[list[Cell]], width: int) -> list[list[Cell]]:
    """窄终端下裁剪到可用宽度（保留左侧内容）。"""
    if width >= PET_STAGE_WIDTH:
        return rows
    return [row[:width] for row in rows]


def pet_window_spec(started_at: float | None = None) -> FloatingWindowSpec:
    """构建宠物浮窗规格：无边框、透明背景、内容自适应宽度、底部上方展开。

    Args:
        started_at: 动画起点（``time.monotonic()``；None = 当前时刻）。
    """
    t0 = time.monotonic() if started_at is None else float(started_at)

    def _render(props: dict) -> object:
        return PetFloatingWindow({**props, "started_at": t0})

    return FloatingWindowSpec(
        id=PET_WINDOW_ID,
        render=_render,
        # 高于 /loop（100），避免两个浮窗重叠时宠物被盖住。
        priority=120,
        # 内容自适应宽度：行宽大小不一，窗口不为空白列铺背景。
        width=None,
        x="right",
        bottom="bottom_area",
        border=False,
        background=None,
        padding_x=0,
        padding_y=0,
        # Esc 在 TUI 中承担中断/清空输入语义，宠物不用 Esc 关闭
        #（用 /pet off 或再次 /pet 收回）。
        close_on_escape=False,
        # 时间驱动动画：30fps 重建一帧（空闲态允许帧复用）。
        refresh_interval=PET_REFRESH_INTERVAL,
    )


__all__ = [
    "PET_ACTIONS",
    "PET_BUBBLE_PERIOD",
    "PET_BUBBLE_VISIBLE",
    "PET_CYCLE",
    "PET_LINES",
    "PET_MAX_DX",
    "PET_REFRESH_INTERVAL",
    "PET_SPRITE_HEIGHT",
    "PET_SPRITE_WIDTH",
    "PET_STAGE_HEIGHT",
    "PET_STAGE_WIDTH",
    "PET_WINDOW_ID",
    "PetFloatingWindow",
    "PetPose",
    "action_window",
    "pet_pose_at",
    "pet_window_spec",
]
