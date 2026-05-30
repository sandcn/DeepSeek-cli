"""emoji_map — Emoji 短代码 → Unicode 映射表。

从 engine.py 提取的纯数据模块，减少引擎文件约 200 行数据噪音。
"""

# ═══════════════════════════════════════════════════════════
# Emoji 短代码映射（精简可扩展）
# ═══════════════════════════════════════════════════════════

EMOJI_MAP: dict[str, str] = {
    ":smile:": "\U0001f60a", ":smiley:": "\U0001f603", ":happy:": "\U0001f604",
    ":wink:": "\U0001f609", ":blush:": "\U0001f60a", ":laugh:": "\U0001f606",
    ":joy:": "\U0001f602", ":cool:": "\U0001f60e", ":thinking:": "\U0001f914",
    ":sweat:": "\U0001f605", ":cry:": "\U0001f622", ":sad:": "\U0001f61e",
    ":angry:": "\U0001f620", ":heart_eyes:": "\U0001f60d", ":kiss:": "\U0001f618",
    ":shock:": "\U0001f62e", ":sleep:": "\U0001f634", ":grimacing:": "\U0001f62c",
    ":relieved:": "\U0001f60c", ":satisfied:": "\U0001f60b", ":stuck_out_tongue:": "\U0001f61b",
    ":sunglasses:": "\U0001f60e", ":smirk:": "\U0001f60f", ":unamused:": "\U0001f612",
    ":worried:": "\U0001f61f", ":frowning:": "\U0001f626", ":persevere:": "\U0001f623",
    ":confounded:": "\U0001f616", ":tired:": "\U0001f62b", ":weary:": "\U0001f629",
    ":thumbsup:": "\U0001f44d", ":thumbsdown:": "\U0001f44e", ":ok:": "\U0001f44c",
    ":clap:": "\U0001f44f", ":wave:": "\U0001f44b", ":pray:": "\U0001f64f",
    ":muscle:": "\U0001f4aa", ":point_up:": "\U0001f446", ":point_down:": "\U0001f447",
    ":point_left:": "\U0001f448", ":point_right:": "\U0001f449", ":fist:": "\u270a",
    ":raised_hand:": "\u270b", ":v:": "\u270c\ufe0f", ":crossed_fingers:": "\U0001f91e",
    ":handshake:": "\U0001f91d", ":writing_hand:": "\u270d\ufe0f", ":nail_care:": "\U0001f485",
    ":heart:": "\u2764\ufe0f", ":broken_heart:": "\U0001f494",
    ":fire:": "\U0001f525", ":star:": "\u2b50", ":sparkles:": "\u2728",
    ":rainbow:": "\U0001f308", ":sunny:": "\u2600\ufe0f", ":moon:": "\U0001f319",
    ":two_hearts:": "\U0001f495", ":sparkling_heart:": "\U0001f496", ":heartbeat:": "\U0001f493",
    ":yellow_heart:": "\U0001f49b", ":green_heart:": "\U0001f49a", ":blue_heart:": "\U0001f499",
    ":check:": "\u2705", ":x:": "\u274c", ":warning:": "\u26a0\ufe0f",
    ":info:": "\u2139\ufe0f", ":question:": "\u2753", ":exclamation:": "\u2757",
    ":tick:": "\u2714\ufe0f", ":cross:": "\u2716\ufe0f", ":plus:": "\u2795",
    ":minus:": "\u2796", ":heavy_check_mark:": "\u2714\ufe0f",
    ":recycle:": "\u267b\ufe0f", ":copyright:": "\u00a9\ufe0f", ":registered:": "\u00ae\ufe0f",
    ":arrow_up:": "\u2b06\ufe0f", ":arrow_down:": "\u2b07\ufe0f",
    ":arrow_left:": "\u2b05\ufe0f", ":arrow_right:": "\u27a1\ufe0f",
    ":arrow_forward:": "\u25b6\ufe0f", ":arrow_backward:": "\u25c0\ufe0f",
    ":bulb:": "\U0001f4a1", ":book:": "\U0001f4d6", ":computer:": "\U0001f4bb",
    ":bug:": "\U0001f41b", ":gear:": "\u2699\ufe0f", ":lock:": "\U0001f512",
    ":key:": "\U0001f511", ":mail:": "\U0001f4e7", ":phone:": "\U0001f4de",
    ":clock:": "\u23f0", ":calendar:": "\U0001f4c5", ":pencil:": "\u270f\ufe0f",
    ":memo:": "\U0001f4dd", ":folder:": "\U0001f4c1", ":file:": "\U0001f4c4",
    ":search:": "\U0001f50d", ":trash:": "\U0001f5d1\ufe0f", ":rocket:": "\U0001f680",
    ":hammer:": "\U0001f528", ":wrench:": "\U0001f527", ":link:": "\U0001f517",
    ":flag:": "\U0001f6a9", ":trophy:": "\U0001f3c6", ":medal:": "\U0001f947",
    ":gift:": "\U0001f381", ":party:": "\U0001f389", ":balloon:": "\U0001f388",
    ":target:": "\U0001f3af", ":camera:": "\U0001f4f7",
    ":sun:": "\u2600\ufe0f", ":cloud:": "\u2601\ufe0f", ":umbrella:": "\u2602\ufe0f",
    ":snowflake:": "\u2744\ufe0f", ":zap:": "\u26a1", ":tornado:": "\U0001f32a\ufe0f",
    ":ocean:": "\U0001f30a", ":droplet:": "\U0001f4a7",
    ":apple:": "\U0001f34e", ":banana:": "\U0001f34c", ":coffee:": "\u2615",
    ":tea:": "\U0001f375", ":beer:": "\U0001f37a", ":pizza:": "\U0001f355",
    ":dog:": "\U0001f415", ":cat:": "\U0001f408", ":mouse:": "\U0001f401",
    ":hamster:": "\U0001f439", ":rabbit:": "\U0001f407", ":fox:": "\U0001f98a",
    ":bear:": "\U0001f43b", ":panda:": "\U0001f43c", ":lion:": "\U0001f981",
    ":tiger:": "\U0001f405", ":monkey:": "\U0001f412",
    ":soccer:": "\u26bd", ":basketball:": "\U0001f3c0", ":football:": "\U0001f3c8",
    ":baseball:": "\u26be", ":tennis:": "\U0001f3be",
    ":car:": "\U0001f697", ":taxi:": "\U0001f695", ":bus:": "\U0001f68c",
    ":train:": "\U0001f686", ":airplane:": "\u2708\ufe0f", ":helicopter:": "\U0001f681",
    ":ship:": "\U0001f6a2", ":bicycle:": "\U0001f6b2",
    ":tada:": "\U0001f389", ":package:": "\U0001f4e6",
    ":bell:": "\U0001f514", ":robot:": "\U0001f916",
    ":brain:": "\U0001f9e0", ":chart:": "\U0001f4ca",
    ":clipboard:": "\U0001f4cb", ":mag:": "\U0001f50d",
    ":speech:": "\U0001f4ac",
    # ── 短代码扩展 (2026-05-22) ──
    ":grin:": "\U0001f601",          # 😁
    ":smile_cat:": "\U0001f638",     # 😸
    ":100:": "\U0001f4af",           # 💯
    ":eyes:": "\U0001f440",          # 👀
    ":hourglass:": "\u23f3",         # ⏳
    ":star2:": "\U0001f31f",         # 🌟
    ":white_check_mark:": "\u2705",  # ✅
    ":unlock:": "\U0001f513",        # 🔓
    ":email:": "\U0001f4e7",         # 📧
    ":music:": "\U0001f3b5",         # 🎵
    ":movie:": "\U0001f3ac",         # 🎬
    ":art:": "\U0001f3a8",           # 🎨
}


def resolve_emoji(text: str) -> str:
    """将文本中的 Emoji 短代码替换为实际 Emoji 字符。

    字符级扫描，无正则表达式：逐字符遍历 text，
    遇到 : 时收集后续合法名称字符，若 :name: 在 EMOJI_MAP 中则替换。
    """
    result: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        if text[i] == ':':
            j = i + 1
            name_start = j
            while j < n and (text[j].isalnum() or text[j] in '_-+'):
                j += 1
            if j > name_start and j < n and text[j] == ':':
                full = text[i:j + 1]
                if full in EMOJI_MAP:
                    result.append(EMOJI_MAP[full])
                    i = j + 1
                    continue
        result.append(text[i])
        i += 1
    return ''.join(result)
