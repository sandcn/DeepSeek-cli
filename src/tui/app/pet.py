"""蓝色大肥鱼宠物浮窗（``/pet``）— 爱吃大白饭、爱吃 tok 的干饭鱼娘。

用户需求（2026-10-22 更新）：

  - 保留蓝色大肥鱼娘形象，强化「爱吃大白饭」人设：饭碗、米粒、蒸汽、
    干饭、咀嚼、打嗝、吃撑躺平、追饭粒等动作与台词；
  - 更多动作（22 个）与更多对话（36 条通用台词 + 动作专属台词）；
  - 宠物固定在右侧 40 列浮窗内活动，不再全屏随机游走；
  - 「吃 tok」：浮窗层画出一排 tok 小吃并逐个吃掉（不改真实统计）；
  - 仍然**无边框、透明背景**：只显示实际绘制的行内容，空白列不写入画布，
    底层内容不会被矩形背景抹掉。

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
from typing import Optional

from src.tui._width import truncate_width, wcswidth_simple
from src.tui.core.style import Style
from src.tui.ink import BOX, TEXT, StyledRun, h

from .floating import FloatingWindowSpec

#: 浮窗 id（``/pet off`` / 重复打开按此替换）
PET_WINDOW_ID = "pet"

#: 舞台尺寸（列 / 行）。宠物固定在右侧 40 列浮窗内。
PET_STAGE_WIDTH = 40
PET_STAGE_HEIGHT = 16

#: 宠物精灵尺寸与在舞台中的基准位置（28×11，四周留出活动余量）。
PET_SPRITE_WIDTH = 28
PET_SPRITE_HEIGHT = 11
PET_SPRITE_TOP = 4
PET_SPRITE_LEFT = (PET_STAGE_WIDTH - PET_SPRITE_WIDTH) // 2

#: 左右移动上限（保证精灵不越出舞台；40-28-6 = 6 列）。
PET_MAX_DX = PET_STAGE_WIDTH - PET_SPRITE_WIDTH - PET_SPRITE_LEFT

#: 饭碗尺寸（14×3，绘制在精灵下方，不遮住表情）。
PET_BOWL_WIDTH = 14
PET_BOWL_HEIGHT = 3

#: 动画刷新间隔：30fps（内容依赖时间，但无需 60fps 全帧重建）。
PET_REFRESH_INTERVAL = 1.0 / 30.0

# ── 配色（256 色；固定蓝色系 + 白饭暖色，不随主题变化） ──
# ★ 带 bg 的样式用于身体/饭碗内部：终端每个字符单元格的背景默认是黑色，
#   半填充方块（▓/▒/░）和 `◕` 等字形若不铺底色，会在蓝身体上露出黑块。
#   空白单元格仍然不写入画布，透明背景语义不变。
_S_BODY = Style(fg=39, bg=39, bold=True)           # 亮蓝身体
_S_BODY_LIGHT = Style(fg=45, bg=45, bold=True)     # 高光青蓝
_S_BODY_DEEP = Style(fg=33, bg=33, bold=True)      # 深蓝身体阴影
_S_OUTLINE = Style(fg=45, bg=39, bold=True)        # 亮青蓝描边（避免黑块感）
_S_BELLY = Style(fg=195, bg=117)                    # 浅蓝肚皮
_S_BELLY_LIGHT = Style(fg=153, bg=117)              # 肚皮阴影（保持蓝色，不抢白饭）
_S_BELLY_DOT = Style(fg=195, bg=195)                # 肚皮小饭粒
_S_FIN = Style(fg=51, bg=39)                        # 青色胸鳍
_S_TAIL_FIN = Style(fg=51, bg=33)                   # 尾鳍高光
_S_EYE = Style(fg=231, bg=39, bold=True)            # 白色大眼
_S_EYE_DIZZY = Style(fg=226, bg=39, bold=True)      # 转圈眼（黄色）
_S_LASH = Style(fg=51, bg=39, bold=True)            # 睫毛（青色，避免黑块感）
_S_MOUTH = Style(fg=203, bg=39, bold=True)          # 粉色小嘴
_S_BLUSH = Style(fg=211, bg=39, bold=True)          # 腮红
_S_BOW = Style(fg=213, bg=39, bold=True)            # 发饰（贴在头顶）
_S_BUBBLE_BORDER = Style(fg=51)                      # 气泡边框（青）
_S_BUBBLE_TEXT = Style(fg=231, bold=True)            # 气泡文字
_S_NOTE = Style(fg=220, bold=True)                   # 音符
_S_Z = Style(fg=111, bold=True)                      # 睡觉 z
_S_SPARK = Style(fg=226, bold=True)                  # 星星
_S_DUST = Style(fg=117)                              # 落地尘埃
_S_HEART = Style(fg=211, bold=True)                  # 爱心
_S_MOTION = Style(fg=45)                             # 冲刺速度线
_S_RICE = Style(fg=231, bold=True)                   # 飘起的米粒
_S_HAIR_RICE = Style(fg=231, bg=39, bold=True)       # 米粒发饰（贴在头顶）
_S_RICE_SHADOW = Style(fg=252, bg=39)                # 米粒阴影
_S_BOWL = Style(fg=45, bg=236, bold=True)            # 饭碗描边
_S_BOWL_DEEP = Style(fg=31, bg=236)                  # 饭碗阴影
_S_BOWL_RICE = Style(fg=231, bg=236, bold=True)      # 碗里的白饭
_S_BOWL_RICE_SHADOW = Style(fg=252, bg=236)          # 米饭阴影
_S_STEAM = Style(fg=245)                             # 热气
_S_CRUMB = Style(fg=229)                             # 饭粒
_S_BURP = Style(fg=250)                              # 打嗝气团
_S_SWEAT = Style(fg=117, bold=True)                  # 运动汗滴
_S_TOK = Style(fg=220, bold=True)                    # 上屏 tok 小吃
_S_TOK_EATEN = Style(fg=242)                         # 已被吃掉的 tok 残影

#: 单元格 = (字符, 样式)；空单元格固定为 (" ", None)。
#  ★ 运行期别名不能用 ``X | None``（PEP 604 需 Python 3.10+，项目兼容
#    3.9）：模块导入时即求值会抛 TypeError，导致 /pet 被误报「界面不支持」。
Cell = tuple[str, Optional[Style]]
_EMPTY: Cell = (" ", None)


#: 通用台词（干饭、撒娇、沙雕、治愈混合；每句控制在气泡可用宽度内）。
PET_LINES: tuple[str, ...] = (
    "本鱼最爱大白饭！",
    "饭碗就是我的海～",
    "今天也要干三碗！",
    "摸摸头，再给口饭～",
    "咕噜咕噜…饭好了吗？",
    "白米饭香香的～",
    "本鱼是干饭美少女！",
    "才不是胖，是装满饭！",
    "饭粒掉了我会心疼！",
    "最喜欢你啦～",
    "要不要一起干饭？",
    "碗底也要舔干净！",
    "米饭是海的云朵～",
    "干饭鱼，出发！",
    "闻到饭香就游过来啦",
    "今天吃白饭还是白饭？",
    "肚子说它还能再装一碗",
    "饭勺是我的魔法棒！",
    "别抢我的大白饭！",
    "一口饭，一口幸福～",
    "鱼生大事，干饭第一",
    "饭碗空了，心也空了",
    "我还能再吃一碗！",
    "白饭配什么？配更多白饭",
    "吃饱了才有力气游泳",
    "饭香把困意都赶跑啦",
    "你吃饭了吗？要好好吃哦",
    "慢慢吃，别噎着～",
    "今天也辛苦啦，开饭吧",
    "饭碗端稳，快乐加倍",
    "本鱼宣布：现在是干饭时间",
    "一口一口，把烦恼吃掉",
    "饭粒粘在脸上也不管！",
    "吃饱饱，睡香香～",
    "明天也要一起吃白饭",
    "大白饭，永远的神！",
)

#: 动作专属台词（每个动作都有；气泡会根据当前动作选词）。
_ACTION_LINES: dict[str, tuple[str, ...]] = {
    "idle": ("饭碗端端好～", "本鱼在等开饭！", "白饭的香气…"),
    "jump": ("跳起来够饭勺！", "饭粒别跑！", "看我干饭跳！"),
    "wiggle": ("饭前扭一扭～", "饭碗跟着摇！", "干饭热身中！"),
    "dash": ("冲向大白饭！", "饭堂开门啦！", "干饭鱼冲刺！"),
    "spin": ("转个圈再吃！", "饭碗转圈圈～", "转晕了也要干饭！"),
    "sing": ("干饭歌，开唱！", "♪ 白饭白饭白饭 ♪", "吃饭要唱饭饭歌～"),
    "wink": (" wink～给饭就给！", "抛个媚眼换碗饭", "本鱼可爱吗？"),
    "sleep": ("吃饱了…困…", "梦里还有一碗饭…", "饭香伴我入睡～"),
    "eat": ("啊呜——大口干饭！", "这碗白饭太香啦！", "别看我，我要认真干饭"),
    "chew": ("嚼嚼嚼…幸福的味道", "腮帮子鼓鼓的！", "饭粒好甜呀～"),
    "burp": ("嗝～吃饱啦！", "本鱼还能再来一碗！", "打嗝都是饭香味"),
    "chase": ("饭粒别跑！", "追到你就吃掉！", "干饭鱼冲刺！"),
    "food_coma": ("吃撑了…动不了了…", "碗…还有半碗…", "躺平消化中…"),
    "dance": ("吃饱了跳个饭舞！", "饭碗举高高～", "干饭鱼之舞，开始！"),
    "stroll": ("饭后走一走～", "本鱼在巡饭堂！", "左看看，右看看…"),
    "sniff_tok": ("闻到 tok 的香味！", "这排 tok 能吃吗？", "tok…tok…好香！"),
    "tok_feast": ("啊呜——吃掉一排 tok！", "tok tok tok，真香！", "本鱼把 tok 当饭团！"),
    "happy_run": ("吃饱了跑两圈！", "干饭鱼，冲鸭！", "跑得快才抢得到饭！"),
    "peek": ("探头看看饭好了没", "有人带饭来了吗？", "本鱼在暗中观察…"),
    "stretch": ("伸个懒腰再干饭～", "饭前拉伸一下！", "本鱼要长高高！"),
    "beg": ("给口饭嘛～就一口！", "本鱼在用眼神讨饭", "饭碗空空，心也空空"),
    "nod": ("好吃好吃！", "点头同意再吃一碗！", "嗯嗯，白饭最棒！"),
}

#: 动作表：(名称, 时长秒)。循环播放；同一时刻只处于一个动作。
PET_ACTIONS: tuple[tuple[str, float], ...] = (
    ("idle", 2.4),
    ("eat", 2.6),
    ("jump", 1.6),
    ("chew", 1.8),
    ("wiggle", 1.8),
    ("dash", 2.0),
    ("burp", 1.6),
    ("spin", 1.4),
    ("sing", 2.2),
    ("chase", 2.2),
    ("wink", 1.6),
    ("food_coma", 2.6),
    ("dance", 2.0),
    ("stroll", 2.8),
    ("sniff_tok", 1.8),
    ("tok_feast", 3.0),
    ("happy_run", 2.2),
    ("peek", 1.6),
    ("stretch", 2.0),
    ("beg", 1.8),
    ("nod", 1.4),
    ("sleep", 2.8),
)

PET_CYCLE = sum(duration for _name, duration in PET_ACTIONS)

#: 气泡显示周期：每 7 秒露头 3 秒（比旧版更频繁）。
PET_BUBBLE_PERIOD = 7.0
PET_BUBBLE_VISIBLE = 3.0

#: 需要端饭碗的动作。
_BOWL_ACTIONS = frozenset({"eat", "chew", "burp", "food_coma", "dance", "beg"})


def _set(grid: list[list[Cell]], x: int, y: int, ch: str, style: Style | None) -> None:
    """安全写单元格（越界忽略）。"""
    if 0 <= y < len(grid) and 0 <= x < len(grid[0]):
        grid[y][x] = (ch, style)


def _build_base_sprite() -> list[list[Cell]]:
    """构建宠物基础精灵（11 行 × 28 列，未做镜像/位移）。

    造型：圆滚滚的蓝色鱼身 + 右侧扇形尾巴 + 左侧胸鳍 + 头顶小花 +
    米粒发饰 + 大眼、腮红、小嘴（娘化萌版）。身体使用亮蓝、青蓝高光、
    深蓝阴影和浅蓝肚皮，比旧版更有层次。
    """
    w, h = PET_SPRITE_WIDTH, PET_SPRITE_HEIGHT
    grid: list[list[Cell]] = [[_EMPTY for _ in range(w)] for _ in range(h)]
    cx, cy = 13, 6
    rx, ry = 9.6, 4.3

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
        for y in range(2, 10)
        for x in range(20, 20 + max(0, 7 - abs(y - 6)))
        if 0 <= x < w
    }
    solid = body | tail
    # 先铺尾巴（身体后绘制会覆盖连接处，避免描边割裂）。
    for x, y in tail:
        _set(grid, x, y, "█", _S_BODY_DEEP)
        if (x + y) % 3 == 0:
            _set(grid, x, y, "▓", _S_TAIL_FIN)
    # 鱼身填充：上部高光、下部阴影、肚皮渐层。
    for x, y in body:
        if y <= cy - 2 and x <= cx + 2:
            ch, style = "█", _S_BODY_LIGHT
        elif y >= cy + 2 and x >= cx + 2:
            ch, style = "▓", _S_BODY_DEEP
        else:
            ch, style = "█", _S_BODY
        _set(grid, x, y, ch, style)
    for x, y in body:
        if y >= cy + 1 and abs(x - cx) <= 6:
            if y >= cy + 3:
                _set(grid, x, y, "░", _S_BELLY_LIGHT)
            else:
                _set(grid, x, y, "▒", _S_BELLY)
    # 描边：只画朝向外部的边（身体与尾巴相接处不描边）。
    for x, y in solid:
        if any((x + dx, y + dy) not in solid for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))):
            _set(grid, x, y, "▓", _S_OUTLINE)
    # 左侧胸鳍（像小手），带一点高光。
    for x, y in ((0, 7), (0, 8), (0, 9), (1, 7), (1, 8), (1, 9), (2, 7), (2, 8)):
        _set(grid, x, y, "▒", _S_FIN)
    _set(grid, 1, 8, "█", _S_FIN)
    # 头顶小花（娘化发饰）+ 米粒发饰（贴在头顶，避免透明底露出黑块）。
    _set(grid, cx - 3, 3, "✿", _S_BOW)
    _set(grid, cx + 3, 3, "✦", _S_HAIR_RICE)
    _set(grid, cx + 4, 4, "·", _S_RICE_SHADOW)
    # 眼睛（大眼）与睫毛。
    _set(grid, cx - 5, cy - 1, "◕", _S_EYE)
    _set(grid, cx + 5, cy - 1, "◕", _S_EYE)
    _set(grid, cx - 6, cy - 2, "╱", _S_LASH)
    _set(grid, cx + 6, cy - 2, "╲", _S_LASH)
    # 腮红。
    for x in (cx - 9, cx - 8, cx + 8, cx + 9):
        _set(grid, x, cy + 1, "╱", _S_BLUSH)
    # 小嘴 + 肚皮小饭粒。
    _set(grid, cx, cy + 2, "ω", _S_MOUTH)
    _set(grid, cx + 3, cy + 3, "·", _S_BELLY_DOT)
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
    "≧": "≦",
    "≦": "≧",
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
    _set(sprite, 8, 5, eyes[0], left_style)
    _set(sprite, 18, 5, eyes[1], right_style)
    # 眼内高光：让眼睛看起来更大、更有神（不影响闭合眼型）。
    if eyes[0] not in ("-", "∪"):
        _set(sprite, 9, 5, "·", _S_EYE)
    if eyes[1] not in ("-", "∪"):
        _set(sprite, 17, 5, "·", _S_EYE)
    _set(sprite, 13, 8, mouth, _S_MOUTH)
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


def _bubble_text(t: float, action: str = "") -> str:
    """按时间决定是否说台词（每 7 秒说 3 秒）。

    台词优先从当前动作的专属台词池中选；动作没有专属台词时回退通用池。
    """
    if PET_BUBBLE_PERIOD <= 0:
        return ""
    phase = max(0.0, float(t)) % PET_BUBBLE_PERIOD
    if phase >= PET_BUBBLE_VISIBLE:
        return ""
    pool = _ACTION_LINES.get(action) or PET_LINES
    index = (int(t // PET_BUBBLE_PERIOD) * 7 + 3) % len(pool)
    return pool[index]


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


def _clamp_dx(dx: int) -> int:
    """把水平位移钳制在舞台活动范围内。"""
    return max(-PET_MAX_DX, min(PET_MAX_DX, int(dx)))


def pet_pose_at(t: float, *, bubble: bool = True) -> PetPose:
    """计算 ``t`` 秒时的姿态（时间从宠物出现时开始计）。

    动作规则（全部为确定性时间函数，便于测试与复现）：

      - ``idle``：缓慢上下浮动，偶发眨眼；
      - ``eat``：捧碗大口干饭，身体一顿一顿，饭碗冒热气；
      - ``jump``：按正弦曲线上跳 0~3 行，空中眯眼、张嘴，落地扬尘；
      - ``chew``：捧着饭碗咀嚼，腮帮子鼓鼓，嘴边掉饭粒；
      - ``wiggle``：左右快速摇摆，身体两侧冒小波浪；
      - ``dash``：在舞台内左右冲刺，掉头时镜像，带速度线；
      - ``burp``：吃饱打嗝，眼睛弯弯，嘴边冒气团；
      - ``spin``：原地快速左右翻转 + 头晕眼 + 星星；
      - ``sing``：闭眼唱歌，右侧飘出音符；
      - ``chase``：追着饭粒左右冲刺，带饭粒与速度线；
      - ``wink``：单眼眨眼，冒出爱心；
      - ``food_coma``：吃撑躺平，眼睛变成 ∪，头顶飘 z / 饭粒；
      - ``dance``：捧着饭碗跳舞，左右摇摆并冒出音符与星星；
      - ``sleep``：闭眼下沉，头顶飘 z。
    """
    action, progress = _action_at(t)
    line = _bubble_text(t, action) if bubble else ""
    p = max(0.0, min(1.0, progress))

    if action == "eat":
        bob = -1 if int(p * 6) % 2 else 0
        mouth = "▽" if int(p * 6) % 2 else "ω"
        eyes = ("^", "^") if p > 0.82 else ("◕", "◕")
        return PetPose(
            action, p, eyes, mouth, dy=bob,
            effect="steam_rice", line=line,
        )

    if action == "chew":
        mouth = "o" if int(p * 8) % 2 else "ω"
        return PetPose(
            action, p, ("◕", "◕"), mouth, dy=0,
            effect="crumb", line=line,
        )

    if action == "burp":
        eyes = ("^", "^") if p < 0.6 else ("-", "-")
        return PetPose(
            action, p, eyes, "o", dy=(-1 if p < 0.25 else 0),
            effect="burp", line=line,
        )

    if action == "chase":
        wave = math.sin(2.0 * math.pi * p)
        dx = _clamp_dx(int(round(wave * (PET_MAX_DX + 1))))
        flip = math.cos(2.0 * math.pi * p) < 0
        return PetPose(
            action, p, ("^", "^"), "o",
            dx=dx, dy=(-1 if abs(wave) > 0.72 else 0), flip=flip,
            effect="crumb_motion", line=line,
        )

    if action == "food_coma":
        return PetPose(
            action, p, ("∪", "∪"), "ω", dy=2,
            effect="full", line=line,
        )

    if action == "dance":
        dx = _clamp_dx(2 if int(p * 8) % 2 else -2)
        return PetPose(
            action, p, ("^", "^") if int(p * 6) % 2 else ("◕", "◕"),
            "ω", dx=dx, dy=(-1 if int(p * 8) % 2 else 0),
            flip=int(p * 8) % 4 < 2,
            effect="dance", line=line,
        )

    if action == "stroll":
        return PetPose(
            action, p, ("◕", "◕"), "ω",
            dy=(-1 if int(p * 4) % 2 else 0),
            flip=int(p * 4) % 4 < 2,
            effect="walk", line=line,
        )

    if action == "sniff_tok":
        return PetPose(
            action, p, ("◕", "◕"), "o", dy=-1,
            effect="sniff_tok", line=line,
        )

    if action == "tok_feast":
        return PetPose(
            action, p, ("^", "^"), "▽" if int(p * 8) % 2 else "ω",
            dy=(-1 if int(p * 6) % 2 else 0),
            effect="tok_feast", line=line,
        )

    if action == "happy_run":
        wave = math.sin(2.0 * math.pi * p)
        return PetPose(
            action, p, ("^", "^"), "o",
            dx=_clamp_dx(int(round(wave * PET_MAX_DX))),
            dy=(-1 if abs(wave) > 0.7 else 0),
            flip=math.cos(2.0 * math.pi * p) < 0,
            effect="happy_run", line=line,
        )

    if action == "peek":
        return PetPose(
            action, p, ("◉", "◉"), "ω",
            dy=(-2 if p < 0.55 else -1),
            effect="spark", line=line,
        )

    if action == "stretch":
        return PetPose(
            action, p, ("-", "-"), "o",
            dy=(-1 if int(p * 4) % 2 else 0),
            effect="stretch", line=line,
        )

    if action == "beg":
        return PetPose(
            action, p, ("◕", "◕"), "▽",
            dy=(-1 if int(p * 4) % 2 else 0),
            effect="heart", line=line,
        )

    if action == "nod":
        return PetPose(
            action, p, ("◕", "◕"), "ω",
            dy=(-1 if int(p * 6) % 2 else 0),
            effect="nod", line=line,
        )

    if action == "jump":
        height = math.sin(math.pi * p)
        dy = -int(round(3.0 * height))
        eyes = ("^", "^") if height > 0.25 else ("◕", "◕")
        mouth = "▽" if height > 0.25 else "ω"
        effect = "dust" if p > 0.82 else ("spark" if height > 0.55 else "")
        return PetPose(action, p, eyes, mouth, dy=dy, effect=effect, line=line)

    if action == "wiggle":
        dx = 1 if int(p * 10) % 2 == 0 else -1
        return PetPose(action, p, ("◕", "◕"), "ω", dx=dx, effect="wiggle", line=line)

    if action == "dash":
        wave = math.sin(2.0 * math.pi * p)
        dx = _clamp_dx(int(round(wave * PET_MAX_DX)))
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


def _draw_bowl(
    stage: list[list[Cell]], x: int, y: int, *, flip: bool = False, full: bool = True,
) -> None:
    """在舞台 ``(x, y)`` 处画饭碗（``full=False`` 时是空碗）。"""
    rows = (
        (
            "   ╭▁▃▅▆▃▁╮   ",
            "   │▒▒▒▒▒▒│   ",
            "   ╰──────╯   ",
        )
        if full else (
            "   ╭──────╮   ",
            "   │░░░░░░│   ",
            "   ╰──────╯   ",
        )
    )
    for dy, row in enumerate(rows):
        chars = list(row)
        if flip:
            chars.reverse()
        for dx, ch in enumerate(chars):
            if ch == " ":
                continue
            if ch in "╭╮╰╯─│":
                style = _S_BOWL
            elif ch in "▁▃▅▆▒":
                style = _S_BOWL_RICE
            elif ch == "░":
                style = _S_BOWL_DEEP
            else:
                style = _S_BOWL_RICE_SHADOW
            _set(stage, x + dx, y + dy, _MIRROR_CHARS.get(ch, ch) if flip else ch, style)


def _apply_effect(
    stage: list[list[Cell]], effect: str, t: float, pose: PetPose, left: int = 0,
) -> None:
    """把动作特效画到舞台（``left`` = 精灵左边界，特效随宠物移动）。"""
    w = len(stage[0]) if stage else 0
    h = len(stage)

    def put(x: int, y: int, ch: str, style: Style) -> None:
        if 0 <= x < w and 0 <= y < h:
            stage[y][x] = (ch, style)

    if effect == "dust":
        for offset, ch in ((4, "·"), (9, "·"), (15, "✧"), (22, "·"), (28, "·")):
            put(left + offset + (int(t * 12) % 2), PET_STAGE_HEIGHT - 1, ch, _S_DUST)
    elif effect == "spark":
        for x, y in ((4, 2), (28, 3), (6, 7), (30, 7)):
            put(left + x, y + (int(t * 8) % 2), "✦", _S_SPARK)
    elif effect == "note":
        drift = int(t * 5) % 3
        put(left + 27, 6 - drift, "♪", _S_NOTE)
        put(left + 30, 4 + drift, "♫", _S_NOTE)
    elif effect == "heart":
        put(left + 12, 1 + (int(t * 4) % 2), "♥", _S_HEART)
        put(left + 16, 2 + (int(t * 3) % 2), "♥", _S_HEART)
    elif effect == "z":
        drift = int(t * 3) % 3
        put(left + 26, 5 - drift, "z", _S_Z)
        put(left + 29, 3 + drift, "Z", _S_Z)
        put(left + 31, 7 - drift, "z", _S_Z)
    elif effect == "motion":
        for x, y in ((-1, 7), (-1, 8), (-1, 9), (28, 7), (28, 8), (28, 9)):
            put(left + x, y, "≋", _S_MOTION)
    elif effect == "happy_run":
        for x, y in ((-1, 7), (-1, 8), (-1, 9), (28, 7), (28, 8), (28, 9)):
            put(left + x, y, "≋", _S_MOTION)
        put(left + 24, 3 + (int(t * 6) % 2), "✦", _S_SPARK)
        put(left + 4, 2 + (int(t * 5) % 2), "✦", _S_SPARK)
        put(left + 26, 5 + (int(t * 4) % 2), "·", _S_SWEAT)
    elif effect == "wiggle":
        offset = 1 if pose.dx > 0 else 0
        put(left - 1 + offset, 6, "∼", _S_MOTION)
        put(left + 28 - offset, 9, "∼", _S_MOTION)
    elif effect == "steam_rice":
        # 饭碗上方的热气和飘起的饭粒（饭碗在精灵侧下方）。
        drift = int(t * 4) % 3
        put(left + 10, 7 - drift, "≈", _S_STEAM)
        put(left + 13, 6 + drift, "≈", _S_STEAM)
        put(left + 16, 7 - drift, "≈", _S_STEAM)
        put(left + 6, 5 + drift, "·", _S_RICE)
        put(left + 19, 6 - drift, "·", _S_RICE)
    elif effect == "crumb":
        drift = int(t * 8) % 3
        put(left + 18, 9 + drift, "·", _S_CRUMB)
        put(left + 21, 10 - drift, "·", _S_CRUMB)
        put(left + 15, 11 - drift, "·", _S_CRUMB)
        put(left + 24, 9 + drift, "·", _S_CRUMB)
    elif effect == "crumb_motion":
        drift = int(t * 8) % 3
        put(left + 14, 10 - drift, "·", _S_CRUMB)
        put(left + 24, 9 + drift, "·", _S_CRUMB)
        for x, y in ((-1, 7), (-1, 9), (28, 7), (28, 9)):
            put(left + x, y, "≋", _S_MOTION)
    elif effect == "burp":
        drift = int(t * 4) % 3
        put(left + 21, 8 - drift, "°", _S_BURP)
        put(left + 23, 7 + drift, "°", _S_BURP)
        put(left + 25, 8 - drift, "~", _S_BURP)
    elif effect == "dance":
        drift = int(t * 5) % 3
        put(left + 7, 5 - drift, "♪", _S_NOTE)
        put(left + 27, 6 + drift, "♫", _S_NOTE)
        put(left + 10, 9 - drift, "✦", _S_SPARK)
        put(left + 26, 10 + drift, "✦", _S_SPARK)
    elif effect == "full":
        # 吃撑躺平：头顶飘 z、脚边散落饭粒。
        drift = int(t * 3) % 3
        put(left + 23, 5 - drift, "z", _S_Z)
        put(left + 26, 3 + drift, "Z", _S_Z)
        put(left + 4, 13, "·", _S_CRUMB)
        put(left + 7, 14, "·", _S_CRUMB)
        put(left + 22, 13, "·", _S_CRUMB)
    elif effect == "walk":
        # 漫步：脚边小尘土 + 左右看的小星星。
        wobble = int(t * 6) % 2
        put(left + 5 + wobble, PET_STAGE_HEIGHT - 1, "·", _S_DUST)
        put(left + 22 - wobble, PET_STAGE_HEIGHT - 1, "·", _S_DUST)
        put(left + (3 if pose.flip else 24), 5, "✧", _S_SPARK)
    elif effect == "sniff_tok":
        # 嗅 tok：鼻子前浮着一小串 tok。
        for index, ch in enumerate("tok tok"):
            put(left + 22 + index, 8 + (index % 2), ch, _S_TOK)
    elif effect == "tok_feast":
        # 一排 tok 小吃，按进度逐个被吃掉（吃掉后留小残影）。
        total = 6
        eaten = min(total, max(0, int(pose.progress * total * 1.15)))
        base_x = max(0, left + 1)
        for index in range(total):
            x = base_x + index * 4
            if index < eaten:
                put(x, 3, "·", _S_TOK_EATEN)
                put(x + 1, 3, "·", _S_TOK_EATEN)
            else:
                put(x, 3, "t", _S_TOK)
                put(x + 1, 3, "o", _S_TOK)
                put(x + 2, 3, "k", _S_TOK)
        for x, y in ((left + 20, 9), (left + 23, 10), (left + 18, 11)):
            put(x, y, "·", _S_CRUMB)
        put(left + 24, 4 + (int(t * 5) % 2), "✦", _S_SPARK)
        put(left + 25, 8, "·", _S_SWEAT)
    elif effect == "stretch":
        put(left + 4, 2 + (int(t * 4) % 2), "✦", _S_SPARK)
        put(left + 24, 2 + (int(t * 3) % 2), "✦", _S_SPARK)
        put(left + 14, 1, "·", _S_SPARK)
    elif effect == "nod":
        put(left + 14, 2 + (int(t * 8) % 2), "✦", _S_SPARK)



def _compose_grid(
    pose: PetPose, t: float, width: int | None = None,
) -> list[list[Cell]]:
    """合成完整内容网格（舞台固定 16 行，宽度默认 40）。

    宠物固定在舞台中心附近（叠加动作自身的 ``dx``），不再随机游走；
    ``width`` 仅用于单测/窄终端适配。
    """
    try:
        stage_w = max(8, int(width if width is not None else PET_STAGE_WIDTH))
    except (TypeError, ValueError, OverflowError):
        stage_w = PET_STAGE_WIDTH
    stage: list[list[Cell]] = [
        [_EMPTY for _ in range(stage_w)]
        for _ in range(PET_STAGE_HEIGHT)
    ]
    base_left = max(0, (stage_w - PET_SPRITE_WIDTH) // 2)
    tx0 = base_left + pose.dx
    tx0 = max(0, min(tx0, max(0, stage_w - PET_SPRITE_WIDTH)))
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
            tx = x + tx0
            ty = y + top
            if 0 <= tx < stage_w and 0 <= ty < PET_STAGE_HEIGHT:
                stage[ty][tx] = cell
    # 饭碗画在精灵侧下方：不压住嘴巴（居中时容易被看成「大白牙」）。
    if pose.action in _BOWL_ACTIONS:
        if pose.action == "food_coma":
            bowl_x = min(tx0 + 18, stage_w - PET_BOWL_WIDTH)
            bowl_y = top + 7
        else:
            if pose.flip:
                bowl_x = max(0, tx0 + 1)
            else:
                bowl_x = min(tx0 + 14, stage_w - PET_BOWL_WIDTH)
            bowl_y = top + 9
        _draw_bowl(
            stage, max(0, bowl_x), bowl_y, flip=pose.flip,
            full=pose.action not in {"burp", "food_coma"},
        )
    _apply_effect(stage, pose.effect, t, pose, left=tx0)
    if pose.line:
        _draw_bubble(stage, pose.line, center=tx0 + PET_SPRITE_WIDTH // 2)
    return stage


def _draw_bubble(
    stage: list[list[Cell]], text: str, center: int | None = None,
) -> None:
    """把三行气泡写到舞台顶部（默认居中，可跟随宠物 ``center``）。"""
    stage_w = len(stage[0]) if stage else PET_STAGE_WIDTH
    max_text = max(1, stage_w - 4)
    text = truncate_width(str(text or ""), max_text)
    width = max(1, min(stage_w, max(10, wcswidth_simple(text) + 4)))
    anchor = stage_w // 2 if center is None else int(center)
    left = max(0, min(anchor - width // 2, stage_w - width))
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


def _visible_height(geometry) -> int:
    """从运行时 geometry 读取可见高度（0 = 首帧/不可见/无信息）。"""
    if not isinstance(geometry, dict) or not geometry.get("visible"):
        return 0
    try:
        return max(0, int(geometry.get("height", 0) or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def PetFloatingWindow(props: dict) -> object:  # noqa: N802 - React Ink 组件命名
    """宠物浮窗内容组件（每帧按时间计算姿态）。

    Props（由 ``FloatingWindow`` 注入）：
        width: 内容可用宽度（列）——舞台最多取 40 列，宠物固定在右侧浮窗内。
        started_at: 动画起点（``time.monotonic()`` 时间戳；由规格闭包注入）。
        now: 可选当前时间（测试注入；生产按 ``time.monotonic()``）。
        geometry: 运行时矩形——可见高度不足 16 行时从舞台底部裁剪，
            优先保留身体、表情和饭碗（避免窗口顶部越界把脸裁掉）。
        window / model / height: 浮窗标准 props（本组件不使用）。
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
    rows = _compose_grid(pose, t, width=stage_w)
    # ★ 小终端 / 启动欢迎页空间不足时：从舞台底部保留可见行，并去掉会被
    #   裁掉的气泡（避免只露出半截气泡边框）。geometry 由上一帧布局写入，
    #   下一帧即稳定；空间恢复后自动回到完整 16 行。
    visible_h = _visible_height(props.get("geometry"))
    if 0 < visible_h < len(rows):
        pose = pet_pose_at(t, bubble=False)
        rows = _compose_grid(pose, t, width=stage_w)[-visible_h:]
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
    """窄终端下裁剪到可用宽度（保留左侧内容；兼容旧测试/调用）。"""
    if width >= PET_STAGE_WIDTH:
        return rows
    return [row[:width] for row in rows]


def pet_window_spec(started_at: float | None = None) -> FloatingWindowSpec:
    """构建宠物浮窗规格：无边框、透明背景、40 列宽度、底部上方展开。

    宠物固定在右侧浮窗内（不再全屏随机游走）；``priority=120`` 高于
    ``/loop``，与循环浮窗同开时由 ``ChatUIConsumer`` 把循环浮窗让到左侧。

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
        # 内容自适应宽度（左右活动范围仍限制在 40 列浮窗内）。
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
