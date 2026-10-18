"""表现层数据表 — 纯数据定义（Emoji / 上下标 / 配色 / 键位表等）。

从 ``src.presentation_data`` 抽出（该模块同时承载数据定义与注册表机制，
1391 行中约 780 行为纯数据字面量）。抽出后：

- 本模块**只有数据**（无注册表、无 threading、无副作用），便于检索与比对；
- ``src.presentation_data`` 保留注册表/访问器，并按名 re-export 全部常量
  （``from src.presentation_data import CONFIG_ENTRY_DESC_MAP`` 等旧路径不变）。

数据表由清单中的独立插件条目（``presentation_data_*``）显式注册，可被
Profile/Bundle 声明，也可被 Patch/Overlay 按 id 单独禁用或整表覆盖。

本模块为**叶子模块**（仅依赖标准库），供 ``src.renderer`` 与 ``src.tui``
消费，避免引入重依赖。
"""

from __future__ import annotations

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
    ":grin:": "\U0001f601",
    ":smile_cat:": "\U0001f638",
    ":100:": "\U0001f4af",
    ":eyes:": "\U0001f440",
    ":hourglass:": "\u23f3",
    ":star2:": "\U0001f31f",
    ":white_check_mark:": "\u2705",
    ":unlock:": "\U0001f513",
    ":email:": "\U0001f4e7",
    ":music:": "\U0001f3b5",
    ":movie:": "\U0001f3ac",
    ":art:": "\U0001f3a8",
    # ── 补充：GitHub / GFM 常用短代码（TUI 流式渲染 emoji 短代码补全）──
    ":+1:": "\U0001f44d", ":-1:": "\U0001f44e",
    ":information_source:": "\u2139\ufe0f", ":heavy_multiplication_x:": "\u2716\ufe0f",
    ":ok_hand:": "\U0001f44c", ":raised_hands:": "\U0001f64c",
    ":punch:": "\U0001f44a", ":victory:": "\u270c\ufe0f",
    ":sob:": "\U0001f62d", ":sweat_smile:": "\U0001f605", ":rofl:": "\U0001f923",
    ":smiling_imp:": "\U0001f608", ":neutral_face:": "\U0001f610",
    ":no_mouth:": "\U0001f636", ":flushed:": "\U0001f633", ":dizzy_face:": "\U0001f635",
    ":poop:": "\U0001f4a9", ":ghost:": "\U0001f47b", ":alien:": "\U0001f47d",
    ":heartpulse:": "\U0001f497", ":cupid:": "\U0001f498", ":purple_heart:": "\U0001f49c",
    ":orange_heart:": "\U0001f9e1", ":black_heart:": "\U0001f5a4", ":white_heart:": "\U0001f90d",
    ":construction:": "\U0001f6a7", ":tools:": "\U0001f6e0\ufe0f",
    ":shield:": "\U0001f6e1\ufe0f", ":triangular_flag_on_post:": "\U0001f6a9",
    ":stopwatch:": "\u23f1\ufe0f", ":alarm_clock:": "\u23f0", ":hourglass_flowing_sand:": "\u23f3",
    ":chart_with_upwards_trend:": "\U0001f4c8",
    ":chart_with_downwards_trend:": "\U0001f4c9", ":bar_chart:": "\U0001f4ca",
    ":page_facing_up:": "\U0001f4c4", ":bookmark:": "\U0001f516",
    ":paperclip:": "\U0001f4ce", ":pushpin:": "\U0001f4cc",
    ":large_blue_circle:": "\U0001f535", ":red_circle:": "\U0001f534",
    ":green_circle:": "\U0001f7e2", ":yellow_circle:": "\U0001f7e1",
    ":white_circle:": "\u26aa", ":black_circle:": "\u26ab",
    ":arrow_upper_right:": "\u2197\ufe0f", ":arrow_lower_right:": "\u2198\ufe0f",
    ":arrows_counterclockwise:": "\U0001f504", ":repeat:": "\U0001f501",
    ":no_entry:": "\u26d4", ":no_entry_sign:": "\U0001f6ab",
    ":thought_balloon:": "\U0001f4ad", ":speech_balloon:": "\U0001f4ac",
    ":desktop_computer:": "\U0001f5a5\ufe0f", ":keyboard:": "\u2328\ufe0f",
    ":printer:": "\U0001f5a8\ufe0f", ":floppy_disk:": "\U0001f4be",
    ":globe_with_meridians:": "\U0001f310", ":earth_asia:": "\U0001f30f",
    ":seedling:": "\U0001f331", ":four_leaf_clover:": "\U0001f340",
    ":maple_leaf:": "\U0001f341", ":cherry_blossom:": "\U0001f338",
    ":rose:": "\U0001f339", ":sunflower:": "\U0001f33b",
    ":t-rex:": "\U0001f996", ":whale:": "\U0001f433", ":unicorn:": "\U0001f984",
    ":dart:": "\U0001f3af", ":video_game:": "\U0001f3ae", ":dvd:": "\U0001f4c0",
    ":microphone:": "\U0001f3a4", ":headphones:": "\U0001f3a7",
    ":pencil2:": "\u270f\ufe0f", ":straight_ruler:": "\U0001f4cf",
    ":triangular_ruler:": "\U0001f4d0", ":closed_lock_with_key:": "\U0001f510",
    ":mega:": "\U0001f4e3", ":loudspeaker:": "\U0001f4e2",
    ":inbox_tray:": "\U0001f4e5", ":outbox_tray:": "\U0001f4e4",
    ":satellite:": "\U0001f4e1", ":signal_strength:": "\U0001f4f6",
    ":battery:": "\U0001f50b", ":electric_plug:": "\U0001f50c",
    ":spider_web:": "\U0001f578\ufe0f", ":spider:": "\U0001f577\ufe0f",
    ":bee:": "\U0001f41d", ":ant:": "\U0001f41c", ":snail:": "\U0001f40c",
    ":turtle:": "\U0001f422", ":snake:": "\U0001f40d", ":dragon:": "\U0001f409",
    ":crown:": "\U0001f451", ":gem:": "\U0001f48e", ":ring:": "\U0001f48d",
    ":moneybag:": "\U0001f4b0", ":credit_card:": "\U0001f4b3",
    ":coffin:": "\u26b0\ufe0f", ":skull:": "\U0001f480", ":skull_and_crossbones:": "\u2620\ufe0f",
    ":hospital:": "\U0001f3e5", ":school:": "\U0001f3eb", ":house:": "\U0001f3e0",
    ":office:": "\U0001f3e2", ":factory:": "\U0001f3ed", ":bank:": "\U0001f3e6",
    ":sunrise:": "\U0001f305", ":city_sunrise:": "\U0001f307",
    ":milky_way:": "\U0001f30c", ":comet:": "\u2604\ufe0f",
    ":sos:": "\U0001f198", ":white_flag:": "\U0001f3f3\ufe0f",
    ":beginner:": "\U0001f530", ":top:": "\U0001f51d", ":soon:": "\U0001f51c",
    ":new:": "\U0001f195", ":free:": "\U0001f193", ":id:": "\U0001f194",
    ":hash:": "#\ufe0f\u20e3", ":asterisk:": "*\ufe0f\u20e3",
    ":speaker:": "\U0001f508", ":mute:": "\U0001f507",
}

# ── 上下标 / 圈号 Unicode 表 ─────────────────────────────

SUBSCRIPT_MAP: dict[str, str] = {
    "0": "₀", "1": "₁", "2": "₂", "3": "₃", "4": "₄",
    "5": "₅", "6": "₆", "7": "₇", "8": "₈", "9": "₉",
    "a": "ₐ", "e": "ₑ", "h": "ₕ", "i": "ᵢ", "j": "ⱼ",
    "k": "ₖ", "l": "ₗ", "m": "ₘ", "n": "ₙ", "o": "ₒ",
    "p": "ₚ", "r": "ᵣ", "s": "ₛ", "t": "ₜ", "u": "ᵤ",
    "v": "ᵥ", "x": "ₓ",
    "+": "₊", "-": "₋", "(": "₍", ")": "₎",
}

SUPERSCRIPT_MAP: dict[str, str] = {
    "0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴",
    "5": "⁵", "6": "⁶", "7": "⁷", "8": "⁸", "9": "⁹",
    "+": "⁺", "-": "⁻", "(": "⁽", ")": "⁾",
    "a": "ᵃ", "b": "ᵇ", "c": "ᶜ", "d": "ᵈ", "e": "ᵉ",
    "f": "ᶠ", "g": "ᵍ", "h": "ʰ", "i": "ⁱ", "j": "ʲ",
    "k": "ᵏ", "l": "ˡ", "m": "ᵐ", "n": "ⁿ", "o": "ᵒ",
    "p": "ᵖ", "r": "ʳ", "s": "ˢ", "t": "ᵗ", "u": "ᵘ",
    "v": "ᵛ", "w": "ʷ", "x": "ˣ", "y": "ʸ", "z": "ᶻ",
}

CIRCLED_DIGITS: list[str] = [
    "①", "②", "③", "④", "⑤", "⑥", "⑦", "⑧", "⑨", "⑩",
    "⑪", "⑫", "⑬", "⑭", "⑮", "⑯", "⑰", "⑱", "⑲", "⑳",
]

# ── HTML 块标签配色 / 列表符号 ───────────────────────────

HTML_TAG_COLORS: dict[str, str] = {"div": "blue", "pre": "green", "table": "yellow"}

BULLETS: list[str] = ["\u2022", "\u25e6", "\u25aa"]

#: 渲染器嵌套列表符号表（按嵌套深度取符号；与行内 ``bullet`` 表语义不同——
#: 后者用于行内无序列表前缀，本表用于块级嵌套列表缩进符号）。
NESTED_BULLET_SYMBOLS: list[str] = ["\u2022", "\u25e6", "\u25aa", "\u25b8", "\u25b9", "\u25c6"]

# ── 轨迹视图 KIND / STATUS 表现 ──────────────────────────

TRACE_KIND: dict[str, dict] = {
    "tools": {"icon": "\U0001F9F0", "name": "工具列表", "fg": 214},
    "system": {"icon": "\u2699", "name": "系统", "fg": 110},
    "user": {"icon": "\U0001F464", "name": "用户", "fg": 39},
    "reasoning": {"icon": "\U0001F4AD", "name": "思考", "fg": 242},
    "content": {"icon": "\U0001F4AC", "name": "回答", "fg": 45},
    "tool": {"icon": "\u26A1", "name": "工具", "fg": 214},
    "subagent": {"icon": "\U0001F916", "name": "子代理", "fg": 75},
    "context": {"icon": "\U0001F4C4", "name": "上下文", "fg": 110},
}

TRACE_STATUS: dict[str, dict] = {
    "running": {"icon": "\u25cf", "fg": 208},
    "done": {"icon": "\u2714", "fg": 41},
    "fail": {"icon": "\u2716", "fg": 196},
    "error": {"icon": "\u2716", "fg": 196},
}

# ── 主 Agent 运行模式文本 / 样式 ─────────────────────────

MODE_TEXT: dict[str, str] = {
    "empty": "空模式",
    "simple": "简单模式",
    "standard": "标准模式",
}

MODE_STYLE_FG: dict[str, int] = {"empty": 178, "simple": 45, "standard": 242}

# ── 工具显示名映射（工具注册名 → UI 显示名） ──────────────
# UI 显示一律取工具注册名的 PascalCase；新增工具时在此补一行
# （tests/test_tool_display_name_pascal.py 校验「映射完整 + 值 == PascalCase」）。

TOOL_DISPLAY_NAME_MAP: dict[str, str] = {
    "read": "Read",
    "read_image": "ReadImage",
    "write": "Write",
    "edit": "Edit",
    "str_replace_editor": "StrReplaceEditor",
    "file_editor": "FileEditor",
    "bash": "Bash",
    "execute_command": "ExecuteCommand",
    "bash_opt": "BashOpt",
    "subagent": "Subagent",
    "subagent_opt": "SubagentOpt",
    "grep": "Grep",
    "glob": "Glob",
    "cp": "Cp",
    "mv": "Mv",
    "rm": "Rm",
    "mkdir": "Mkdir",
    "user_select": "UserSelect",
    "web_search": "WebSearch",
    "web_fetch": "WebFetch",
    "ls": "Ls",
    "skill": "Skill",
}

# ── 告示块（Admonition）样式表 ────────────────────────────

ADMONITION_STYLE_MAP: dict[str, dict[str, str]] = {
    "NOTE":      {"color": "blue",       "icon": "ℹ️",  "label": "NOTE"},
    "TIP":       {"color": "green",      "icon": "💡",  "label": "TIP"},
    "WARNING":   {"color": "yellow",     "icon": "⚠️",  "label": "WARNING"},
    "CAUTION":   {"color": "red",        "icon": "⚡",  "label": "CAUTION"},
    "IMPORTANT": {"color": "magenta",    "icon": "❗",  "label": "IMPORTANT"},
    "INFO":      {"color": "cyan",       "icon": "ℹ️",  "label": "INFO"},
    "SUCCESS":   {"color": "green",      "icon": "✅",  "label": "SUCCESS"},
    "QUESTION":  {"color": "bright_blue","icon": "❓",  "label": "QUESTION"},
    "BUG":       {"color": "red",        "icon": "🐛",  "label": "BUG"},
    "DANGER":    {"color": "red",        "icon": "🔥",  "label": "DANGER"},
    "CITE":      {"color": "bright_black","icon": "📖",  "label": "CITE"},
}

# ── Spinner 动画预设（预设名 → 帧串） ──────────────────────

SPINNER_PRESET_FRAMES: dict[str, str] = {
    "dots": "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏",
    "dots2": "⣾⣽⣻⢿⡿⣟⣯⣷",
    "dots3": "⠋⠙⠚⠞⠖⠦⠴⠲⠳⠓",
    "dots4": "⠄⠆⠇⠋⠙⠸⠰⠠⠰⠸⠙⠋⠇⠆",
    "dots5": "⠋⠙⠚⠒⠂⠂⠒⠲⠴⠦⠖⠒⠐⠐⠒⠓⠋",
    "dots6": "⠁⠉⠙⠚⠒⠂⠂⠒⠲⠴⠤⠄⠄⠤⠴⠲⠒⠂⠂⠒⠚⠙⠉⠁",
    "dots7": "⠈⠉⠋⠓⠒⠐⠐⠒⠖⠦⠤⠠⠠⠤⠦⠖⠒⠐⠐⠒⠓⠋⠉⠈",
    "dots8": "⠁⠁⠉⠙⠚⠒⠂⠂⠒⠲⠴⠤⠄⠄⠤⠠⠠⠤⠦⠖⠒⠐⠐⠒⠓⠋⠉⠈⠈",
    "dots9": "⢹⢺⢼⣸⣇⡧⡗⡏",
    "dots10": "⢄⢂⢁⡁⡈⡐⡠",
    "dots11": "⠁⠂⠄⡀⢀⠠⠐⠈",
    "line": "─╼╾╴╶",
    "line2": "⠂⠒⠐⠈⠁⠉⠐⠒⠂",
    "pipe": "┤┘┴└├┌┬┐",
    "simpleDots": "⠂⠄⠆⠇⠋⠙⠸⠰⠠⠰⠸⠙⠋⠇⠆⠄",
    "simpleDotsScrolling": "⠈⠐⠠⢀⡀⢄⡂⡆⡇⡏⡟⡿⢿⠻⠽⠾⢾⣀⣠⣄⣆⣇⣏⣟⣿",
    "bar": "▁▃▄▅▆▇█▇▆▅▄▃",
    "vertical": "▁▂▃▄▅▆▇█▇▆▅▄▃▂",
    "grow": "▁▂▃▄▅▆▇█",
    "growHorizontal": "▏▎▍▌▋▊▉█",
    "arrow": "←↖↑↗→↘↓↙",
    "moon": "🌑🌒🌓🌔🌕🌖🌗🌘",
    "dotsClassic": "⠁⠂⠄⡀⢀⠠⠐⠈",
    "shark": "▐▌▐▌",
}

# ── 行内 Spinner 默认帧序列（braille：10 帧 30Hz 推进 ~0.33s 循环） ──

INLINE_SPINNER_FRAMES: str = "\u280b\u2819\u2839\u2838\u283c\u2834\u2826\u2827\u2807\u280f"

# ── 配置项说明映射（写回键名 → 中文说明） ──────────────────

CONFIG_ENTRY_DESC_MAP: dict[str, str] = {
    # ── 核心配置 ──
    "MODEL_PROFILES": "模型档案列表（name/model/base_url/api_key/provider/temperature/reasoning_effort；**模型列表唯一来源**，/models 界面增删改选并按模型设置温度与思考级别）",
    "REASONING_EFFORT": "全局默认思考级别（low/medium/high/max；/models 中可按模型覆盖）",
    "TEMPERATURE": "全局默认温度（0.0~2.0，越高越随机；/models 中可按模型覆盖）",
    "THEME": "UI 配色主题（dark/light/high-contrast/nord/dracula/gruvbox/tokyo-night/catppuccin）",
    # ── 数值配置 ──
    "MAX_CONTEXT_CHARS": "上下文最大字符数",
    "MAX_OUTPUT_CHARS": "单次输出最大字符数",
    "MAX_RETRIES": "API 调用最大重试次数",
    "EMPTY_RESPONSE_MAX_RETRIES": "空响应自动重发上限（0=无限重试直到拿到回答或 ESC 中断）",
    "RETRY_BASE_SEC": "重试基础间隔（秒）",
    "MAX_SESSION_MESSAGES": "会话消息数上限（0=无限制）",
    "KEEP_RECENT_MESSAGES": "压缩时保留的最近消息数",
    "MAX_CONTEXT_TOKENS": "上下文最大 tokens",
    "MODEL_CONTEXT_TOKENS": "模型上下文窗口（tokens，上下文使用率分母）",
    "SUMMARY_TOKEN_BUDGET": "摘要 token 预算",
    "AUTO_FORCE_COMPRESS_THRESHOLD": "自动强制压缩阈值（tokens，超过即全量压缩）",
    # ── 布尔配置 ──
    "ENABLE_NOTIFICATIONS": "启用系统通知",
    "NOTIFY_ON_CHAT_COMPLETION": "聊天完成时通知",
    # ── 复合配置 ──
    "TOKEN_PRICES": "token 价格表（input/output/input_cache_hit，$/M）",
    "MULTIMODAL_MODELS": "多模态模型列表（小写子串匹配，read_image 据此返回图片）",
    "IMAGE_UPLOAD_OPTIMIZE": "上传前图片优化（折叠旧图+压缩大图，缓解多图请求卡顿）",
    "IMAGE_UPLOAD_KEEP_RECENT": "上传时保留的最近图片数（更早的图片替换为文本占位，0=不折叠）",
    "IMAGE_UPLOAD_MAX_DIMENSION": "上传图片长边上限（像素，超过则降采样后再上传）",
    "IMAGE_UPLOAD_QUALITY": "上传图片 JPEG 压缩质量（1~100，越高越清晰体积越大）",
    "MULTIMODAL_IMAGE_TOKEN_PATCH": "图片视觉 token 分块边长（ceil(w/patch)*ceil(h/patch)，默认 28）",
    "MULTIMODAL_IMAGE_TOKEN_DEFAULT": "无法读取尺寸的图片（URL/解码失败）每图占用 token（默认 800）",
    "MCP_SERVERS": "MCP 外部工具服务器列表（name/transport/command 或 url）",
    "TUI_DROP_PATH_NORMALIZE": "拖动文件到输入框时规范化路径（去引号/转义/file URI/Windows→POSIX）",
    # ── HTTP 性能配置（嵌套路径） ──
    "HTTP_CONNECT_TIMEOUT": "HTTP 连接超时（秒）",
    "HTTP_READ_TIMEOUT": "HTTP 读取超时（秒）",
    "HTTP_WRITE_TIMEOUT": "HTTP 写入超时（秒）",
    "HTTP_MAX_CONNECTIONS": "HTTP 连接池最大连接数",
    "HTTP_MAX_CONNECTIONS_PER_HOST": "HTTP 单主机最大连接数",
    "HTTP_KEEP_ALIVE_TIMEOUT": "HTTP 保持连接超时（秒）",
    "HTTP_ENABLE_POOL": "启用 HTTP 连接池",
    "HTTP_ENABLE_HTTP2": "启用 HTTP/2",
    # ── 额外顶层键 ──
    "skills": "技能子系统配置（enabled/auto_load 等）",
}

# ── 枚举选择型配置项候选（写回键 → [(值, 说明), ...]） ─────

CONFIG_ENTRY_OPTION_MAP: dict[str, tuple[tuple[str, str], ...]] = {
    "THEME": (
        ("dark", "暗色主题"),
        ("light", "亮色主题"),
        ("high-contrast", "高对比主题"),
        ("nord", "Nord 主题（冷色调）"),
        ("dracula", "Dracula 主题（糖果色）"),
        ("gruvbox", "Gruvbox 主题（暖色复古）"),
        ("tokyo-night", "Tokyo Night 主题（深蓝夜色）"),
        ("catppuccin", "Catppuccin 主题（柔和粉紫）"),
    ),
    "REASONING_EFFORT": (
        ("low", "低——最快响应，思考最少"),
        ("medium", "中——平衡速度与深度"),
        ("high", "高——更深入思考"),
        ("max", "最大——最充分思考"),
    ),
}

# ── 轨迹记录种类顺序 / 块种类映射 ─────────────────────────

TRACE_KIND_ORDER_DATA: list = [
    "tools", "system", "user", "reasoning", "content", "tool", "subagent", "context",
]

TRACE_BLOCK_KIND_MAP: dict = {
    "user": "user",
    "reasoning": "reasoning",
    "content": "content",
    "tool": "tool",
    "subagent": "subagent",
    "parse_info": "context",
    "notification": "context",
    "error": "system",
    "write_line": "system",
}

# ── 消息角色图标 ──────────────────────────────────────────

MESSAGE_ROLE_ICON_MAP: dict = {
    "user": "\u25cf",       # ●
    "assistant": "\u25c6",  # ◆
    "tool": "\u2699",       # ⚙
}

# ── 边框样式字符表 / 自定义边框缺省 ───────────────────────

BORDER_CHARS_MAP: dict = {
    "single": ("┌", "┐", "└", "┘", "─", "│"),
    "double": ("╔", "╗", "╚", "╝", "═", "║"),
    "round": ("╭", "╮", "╰", "╯", "─", "│"),
    "bold": ("┏", "┓", "┗", "┛", "━", "┃"),
    "classic": ("+", "+", "+", "+", "-", "|"),
    "dashed": ("┌", "┐", "└", "┘", "┄", "┆"),
    "singleDouble": ("╓", "╖", "╙", "╜", "═", "│"),
    "doubleSingle": ("╒", "╕", "╘", "╛", "─", "║"),
}

BORDER_OBJECT_DEFAULT: dict = {
    "topLeft": "┌", "top": "─", "topRight": "┐",
    "left": "│",
    "bottomLeft": "└", "bottom": "─", "bottomRight": "┘",
    "right": "│",
}

# ── 运行期默认值表（计费单价 / 指标百分位 / UI 默认参数） ──
#
# 「一切皆插件」：此前散落在各模块的默认常量（telemetry 默认定价、
# metrics 默认百分位、配置项截断宽度、项目摘要 token 上限、输入提示符、
# Divider 默认宽度、工具卡兜底配色、代码块兜底边框）上移为数据表，
# 可按 Patch/Overlay 覆盖或禁用（禁用后消费方回退模块内兜底字面量）。

BILLING_DEFAULT_DATA: dict = {
    "input_per_1m": 0.55,
    "output_per_1m": 2.19,
}

METRIC_DEFAULTS_DATA: dict = {
    "percentiles": [50, 90, 95, 99],
}

UI_DEFAULTS_DATA: dict = {
    "prompt": "> ",
    "truncate_width": 48,
    "divider_width": 40,
    "summary_max_tokens": 8000,
    "tool_fallback_fg": 242,
    "tool_fallback_breath": [242, 252],
    # 工具卡满宽背景色（256 色号）——「工具卡整行占满终端宽度」：
    # 标题行/内容行/省略行右侧以该背景色空格填充至终端宽度（2026-10-05）。
    "tool_card_bg": 236,
    "codeblock_border": ["┌", "┐", "└", "┘", "─", "│"],
    # 头显示工具（head 语义）：输出超阈值时保留前 N 行 + 后置「… 后 N 行省略」。
    # ★ 2026-10-13 用户需求：ls 由头显示改为尾显示——目录列表看结尾更实用，
    #   卡片省略从「后置『… 后 N 行省略』」改为「前置『… 前 N 行省略』」。
    "tool_head_tools": ["read"],
    "tool_head_lines": 3,
    # 尾显示工具（tail 语义）：输出超阈值时保留后 N 行 + 前置「… 前 N 行省略」。
    # ★ 2026-10-10 用户需求：grep / glob 由头显示改为尾显示——卡片省略从
    #   「后置『… 后 N 行省略』」改为「前置『… 前 N 行省略』」。
    "tool_tail_tools": ["bash", "execute_command", "grep", "glob", "ls"],
    # 尾显示工具保留行数（bash/execute_command/grep/glob/ls 共用）。
    "bash_output_tail_lines": 3,
    "tool_incremental_threshold": 64,
    "role_labels": {
        "system": "系统提示词",
        "user": "用户",
        "assistant": "助手",
        "tool": "工具结果",
    },
    "internal_prefill_cmds": ["/editmsg", "/deitmsg", "/retry"],
    "summary_core_max_items": 4,
    "summary_core_max_len": 50,
    "summary_tech_max_items": 3,
    "summary_truncate_length": 300,
}

# ── 模型名称匹配模式表（推理模型 / V4 系列检测） ──────────────

MODEL_PATTERNS_DATA: dict = {
    "reasoner_patterns": ["reasoner"],
    "v4_prefixes": ["deepseek-v4", "deepseek-flash"],
}

# ── 语义色槽位表（主题/组件配色单一真源） ────────────────────
#
# 「一切皆插件」：语义色（accent/dim/sep/time/token/speed/tool_ok/... 以及
# subagent 面板的 running/done/fail/... 槽位）不再是各模块的自有硬编码
# 色号，而是本表的条目；`src/tui/_const._SEMANTIC_COLOR` 实时委托本表，
# 主题/Palette 与子代理面板配色据此派生（可按 Patch/Overlay 覆盖或禁用）。

SEMANTIC_COLOR_DATA: dict = {
    "accent": 45,
    "deep_cyan": 32,
    "dim": 242,
    "sep": 237,
    "time": 110,
    "token": 68,
    "speed": 214,
    "tool_ok": 41,
    "tool_fail": 196,
    "select_bg": 236,
    "select_fg": 15,
    "border": 23,
    "placeholder": 238,
    "running": 214,
    "done": 40,
    "fail": 196,
    "answering": 75,
    "parsing": 178,
    "batch": 140,
    "dimmer": 240,
    "dimmest": 238,
    "summary_dim": 245,
    "branch": 239,
}

# ── 渐变/呼吸动效参数表 ─────────────────────────────────────
#
# 顶部标题栏与欢迎屏的渐变停靠点、呼吸色域与周期登记为数据表，
# 可按 Patch/Overlay 覆盖或禁用（消费方经 ``gradient_param`` 实时查询）。

GRADIENT_STOPS_DATA: dict = {
    "title_stops": [45, 39, 141, 213],
    "welcome_stops": [45, 39, 141, 213],
    "welcome_brand": "DeepSeek CLI",
    "welcome_bullet": "\u203a",
    "header_dot": {"lo": 205, "hi": 219, "period": 6.0},
    "header_version": {"lo": 242, "hi": 252, "period": 8.0},
    "welcome_dot": {"lo": 45, "hi": 61, "period": 8.0},
}

# ── Shell / 终端检测表（系统提示词「当前命令行」章节） ────────

SHELL_DETECT_DATA: dict = {
    "shell_aliases": {
        "bash": "bash", "sh": "sh", "zsh": "zsh", "fish": "fish", "ksh": "ksh",
        "mksh": "ksh", "dash": "dash", "ash": "ash", "csh": "csh", "tcsh": "tcsh",
        "nu": "nushell", "nushell": "nushell", "xonsh": "xonsh", "elvish": "elvish",
        "pwsh": "powershell", "powershell": "powershell",
        "powershell_ise": "powershell", "cmd": "cmd", "busybox": "sh",
    },
    "terminal_env_rules": [
        ["WT_SESSION", "Windows Terminal"],
        ["TERM_PROGRAM", ""],
        ["TERMINAL_EMULATOR", ""],
        ["WEZTERM_PANE", "WezTerm"],
        ["KITTY_WINDOW_ID", "kitty"],
        ["ALACRITTY_WINDOW_ID", "Alacritty"],
        ["ALACRITTY_SOCKET", "Alacritty"],
        ["KONSOLE_VERSION", "Konsole"],
        ["VTE_VERSION", "VTE"],
        ["TMUX", "tmux"],
        ["STY", "screen"],
        ["TERMUX_VERSION", "Termux"],
    ],
    "term_program_map": {
        "apple_terminal": "Apple Terminal",
        "iterm.app": "iTerm2",
        "vscode": "VS Code",
        "mintty": "mintty",
        "wezterm": "WezTerm",
        "hyper": "Hyper",
        "ghostty": "Ghostty",
        "tabby": "Tabby",
        "windows_terminal": "Windows Terminal",
        "wt": "Windows Terminal",
    },
    "terminal_process_aliases": {
        "mintty": "mintty",
        "xterm": "xterm",
        "konsole": "Konsole",
        "gnome-terminal": "GNOME Terminal",
        "gnome-terminal-server": "GNOME Terminal",
        "kgx": "GNOME Console",
        "kitty": "kitty",
        "alacritty": "Alacritty",
        "wezterm": "WezTerm",
        "wezterm-gui": "WezTerm",
        "windowsterminal": "Windows Terminal",
        "wt": "Windows Terminal",
        "tmux": "tmux",
        "screen": "screen",
        "iterm2": "iTerm2",
        "qterminal": "QTerminal",
        "terminator": "Terminator",
        "tilix": "Tilix",
        "xfce4-terminal": "Xfce Terminal",
        "lxterminal": "LXTerminal",
        "mate-terminal": "MATE Terminal",
        "urxvt": "urxvt",
        "rxvt": "rxvt",
        "terminology": "Terminology",
    },
}

# ── HTTP 状态码 → 用户可操作提示表（api/errors） ──────────────

HTTP_ERROR_HINT_DATA: dict = {
    "400": "请求参数不合法",
    "401": "API 密钥无效或未设置，请在 /models 模型档案中检查 API 密钥",
    "403": "API 密钥无权访问（可能欠费或权限不足）",
    "404": "接口地址或模型不存在，请检查 BASE_URL 与模型名",
    "408": "请求超时",
    "422": "请求参数验证失败（如消息历史中 tool_calls 与 tool 响应不配对）",
    "425": "请求过早，请稍后重试",
    "429": "请求频率超限或额度不足",
    "500": "服务端内部错误",
    "502": "网关错误（上游服务不可用）",
    "503": "服务暂时不可用（过载或维护中）",
    "504": "网关超时",
}

# ── Badge 对比色度量表（ink Badge 前景自动对比） ──────────────

BADGE_METRICS_DATA: dict = {
    "fg_on_dark": 231,
    "fg_on_light": 232,
    "brightness_threshold": 150,
    "base_brightness": {
        "0": 0, "1": 139, "2": 146, "3": 178, "4": 93, "5": 158, "6": 170, "7": 192,
        "8": 128, "9": 255, "10": 255, "11": 255, "12": 255, "13": 255, "14": 255, "15": 255,
    },
    "ansi_levels": [0, 95, 135, 175, 215, 255],
}

# ── Kitty 键盘协议解析表（CSI u 增强键盘 / 事件类型） ─────────
KITTY_PROTOCOL_DATA: dict = {
    "modifier_bits": {
        "shift": 1, "alt": 2, "ctrl": 4, "super": 8, "hyper": 16, "meta": 32,
        "capsLock": 64, "numLock": 128,
    },
    "event_types": ["press", "repeat", "release"],
    #: US 布局 Shift 符号映射（base 字符 → 按下 Shift 后的字符）——增强键盘
    #: 协议（CSI u）下终端未上报 alternate key（``\x1b[47;2u`` 而非
    #: ``\x1b[47:63;2u``）时，输入解析按此推断实际字符（``/`` → ``?``、
    #: ``1`` → ``!`` 等）；字母大小写由解析器直接 ``upper()``（不入表）。
    "us_shift_map": {
        "`": "~", "1": "!", "2": "@", "3": "#", "4": "$", "5": "%",
        "6": "^", "7": "&", "8": "*", "9": "(", "0": ")",
        "-": "_", "=": "+", "[": "{", "]": "}", "\\": "|",
        ";": ":", "'": "\"", ",": "<", ".": ">", "/": "?",
    },
}

# ── Diff 渲染样式表（diff 文件头 / hunk / 行号 / 标记 / 行内背景） ──

DIFF_STYLE_DATA: dict = {
    "del_bg": 124,
    "add_bg": 28,
    "separator_width": 40,
    "file_old": {"fg": 210, "bold": True},
    "file_new": {"fg": 114, "bold": True},
    "hunk_bar": {"fg": 45, "dim": True},
    "num_del": {"fg": 167},
    "num_add": {"fg": 41},
    "mark_del": {"fg": 196, "bold": True},
    "mark_add": {"fg": 41, "bold": True},
}

# ── 轨迹视图（Trace）样式表（台账 / 检查器 / 树渲染共用） ─────

TRACE_STYLE_DATA: dict = {
    "title": {"fg": 45, "bold": True},
    "hint": {"fg": 242},
    "sep_row": {"fg": 238},
    "index": {"fg": 242},
    "time": {"fg": 110},
    "text": {"fg": 252},
    "dim": {"fg": 242},
    "sel_bg": {"bg": 237},
    "sel_mark": {"fg": 45, "bold": True},
    "section": {"fg": 110, "bold": True},
    "tree_key": {"fg": 75},
    "tree_val": {"fg": 252},
    "insp_bg": {"bg": 237},
    "search_bg": {"bg": 236},
    "search_cur_bg": {"bg": 25},
    "search_prompt": {"fg": 45, "bold": True},
    "search_query_max": 200,
    # ── 增强（2026-10-07）：失败高亮 / 状态提示 / 计数 / 帮助与统计面板 ──
    "error": {"fg": 196, "bold": True},
    "warn": {"fg": 214, "bold": True},
    "status": {"fg": 221},
    "count": {"fg": 214},
    "help_key": {"fg": 214},
    "help_group": {"fg": 110, "bold": True},
    "help_desc": {"fg": 252},
    "stats_label": {"fg": 110},
    "stats_value": {"fg": 252},
    "stats_bar": {"fg": 45},
    # ── 增强（2026-10-07 第二批）：记录标记 / 时间列 / 行号 / 内联展开 ──
    "mark": {"fg": 214, "bold": True},
    "time_abs": {"fg": 108},
    "line_number": {"fg": 240},
    "expanded": {"fg": 245},
    "expand_prefix": {"fg": 110},
    # ── 增强（2026-10-07 第三批）：耗时条形图 / 记录对比 / 轮次折叠 ──
    "time_bar": {"fg": 45},
    "time_bar_bg": {"fg": 238},
    "compare_label": {"fg": 214, "bold": True},
    "compare_same": {"fg": 242},
    "compare_diff": {"fg": 214},
    "turn_collapsed": {"fg": 108, "bold": True},
}

# ── 轨迹视图快捷键速查表（帮助面板内容，「一切皆插件」） ──────
# 每项：group=分组标题；keys=键位文本；desc=说明。渲染由
# ``src.tui.app.trace_help`` 负责（键列对齐 + 分色），可被 Patch/Overlay
# 整表替换或禁用（缺席时帮助面板回退空态提示）。

TRACE_KEYMAP_DATA: list = [
    {"group": "导航", "keys": "\u2191\u2193 / j k", "desc": "移动选择（检查器焦点移动光标行）"},
    {"group": "导航", "keys": "PgUp / PgDn", "desc": "整页翻页"},
    {"group": "导航", "keys": "Ctrl+D / Ctrl+U", "desc": "半页下翻 / 上翻"},
    {"group": "导航", "keys": "Home/End \u00b7 g/G", "desc": "首条 / 末条"},
    {"group": "导航", "keys": "N g \u00b7 N G", "desc": "跳到记录号 #N（数字 + g/G）"},
    {"group": "导航", "keys": "l / h \u00b7 \u2192 / \u2190", "desc": "焦点切到检查器 / 返回台账"},
    {"group": "标记", "keys": "m{a-z}", "desc": "在选中记录设置标记（a-z）"},
    {"group": "标记", "keys": "'{a-z}", "desc": "跳转到该标记所在记录"},
    {"group": "记录定位", "keys": "] / [", "desc": "下一个 / 上一个工具调用"},
    {"group": "记录定位", "keys": "e / E", "desc": "下一个 / 上一个失败记录"},
    {"group": "记录定位", "keys": "{ / }", "desc": "上一个 / 下一个轮次首条记录"},
    {"group": "记录定位", "keys": "t", "desc": "按记录种类过滤（循环切换，空=全部）"},
    {"group": "记录定位", "keys": "Enter", "desc": "进入子代理轨迹 / 工具列表"},
    {"group": "详情与树", "keys": "\u7a7a\u683c", "desc": "展开 / 收起光标所在树节点"},
    {"group": "详情与树", "keys": "zR / zM", "desc": "全部展开 / 全部折叠树"},
    {"group": "详情与树", "keys": "o", "desc": "就地展开 / 折叠选中记录详情（不切面板）"},
    {"group": "详情与树", "keys": "#", "desc": "检查器行号显示开关"},
    {"group": "详情与树", "keys": "r", "desc": "切换检查器原始文本 / 渲染显示"},
    {"group": "详情与树", "keys": "T", "desc": "时间列模式（关 / 绝对 / 相对）"},
    {"group": "轮次折叠", "keys": "za / zc / zo", "desc": "切换 / 折叠 / 展开当前轮次"},
    {"group": "轮次折叠", "keys": "zC / zO", "desc": "折叠 / 展开全部轮次"},
    {"group": "搜索", "keys": "/", "desc": "进入搜索输入（\u2191\u2193 回溯历史，回车执行）"},
    {"group": "搜索", "keys": "n / N / p", "desc": "下一个 / 上一个匹配"},
    {"group": "搜索", "keys": "v", "desc": "切换大小写敏感"},
    {"group": "搜索", "keys": "f", "desc": "切换过滤模式（台账只显示匹配记录）"},
    {"group": "面板", "keys": "i", "desc": "统计概览面板（token/耗时/成功率）"},
    {"group": "面板", "keys": "C", "desc": "记录对比（选两条记录并排对照）"},
    {"group": "面板", "keys": "?", "desc": "本帮助面板（? / Esc / q 关闭）"},
    {"group": "操作", "keys": "y", "desc": "复制内容（台账=整条记录 / 检查器=当前行）"},
    {"group": "操作", "keys": "x", "desc": "切换导出范围（全部/视图/失败/工具）"},
    {"group": "操作", "keys": "w / W", "desc": "按当前范围导出 Markdown / JSON"},
    {"group": "操作", "keys": "Esc / Ctrl+H", "desc": "返回主轨迹 / 关闭轨迹视图"},
]

# ── 插件视图快捷键速查表（``?`` 帮助面板内容，「一切皆插件」） ──
# 每项：group=分组标题；keys=键位文本；desc=说明。渲染由
# ``src.tui.app.plugin_view`` 负责，可被 Patch/Overlay 整表替换或禁用。

PLUGIN_KEYMAP_DATA: list = [
    {"group": "导航", "keys": "\u2191\u2193 / j k", "desc": "移动选择 / 详情滚动"},
    {"group": "导航", "keys": "PgUp / PgDn", "desc": "整页翻页"},
    {"group": "导航", "keys": "Home/End \u00b7 g/G", "desc": "首末"},
    {"group": "导航", "keys": "l / Enter \u00b7 h / \u2190", "desc": "进入详情 / 返回列表"},
    {"group": "搜索", "keys": "/", "desc": "搜索插件（回车执行，Esc 取消）"},
    {"group": "搜索", "keys": "n / N / p", "desc": "下一个 / 上一个匹配"},
    {"group": "搜索", "keys": "f", "desc": "过滤模式（只显示匹配插件）"},
    {"group": "搜索", "keys": "S", "desc": "按状态过滤（循环切换，空=全部）"},
    {"group": "搜索", "keys": "K", "desc": "按分类过滤（循环切换，空=全部）"},
    {"group": "面板", "keys": "r", "desc": "依赖关系视图（依赖/被依赖，Enter 跳转）"},
    {"group": "面板", "keys": "?", "desc": "本帮助面板（? / q / Esc 关闭）"},
    {"group": "操作", "keys": "y", "desc": "复制选中插件信息到剪贴板（OSC52）"},
    {"group": "操作", "keys": "w / W", "desc": "导出插件清单为 Markdown / JSON 文件"},
    {"group": "操作", "keys": "Esc / Ctrl+H", "desc": "关闭插件视图"},
]

# ── 配置中心快捷键速查表（``?`` 帮助面板内容，「一切皆插件」） ──
CONFIG_KEYMAP_DATA: list = [
    {"group": "导航", "keys": "\u2191\u2193 / j k", "desc": "移动选择 · 候选/条目导航"},
    {"group": "导航", "keys": "PgUp / PgDn", "desc": "整页翻页"},
    {"group": "导航", "keys": "Home/End \u00b7 g/G", "desc": "首末"},
    {"group": "导航", "keys": "[ / ]", "desc": "上一个 / 下一个配置分组"},
    {"group": "编辑", "keys": "Enter", "desc": "编辑选中项（选择 / 输入 / 子 JSON）"},
    {"group": "编辑", "keys": "r", "desc": "恢复选中项默认值"},
    {"group": "编辑", "keys": "u", "desc": "撤销上次编辑"},
    {"group": "编辑", "keys": "U", "desc": "撤销历史面板（回退到某历史点）"},
    {"group": "分组", "keys": "za / zc / zo", "desc": "切换 / 折叠 / 展开当前分组"},
    {"group": "分组", "keys": "zC / zO", "desc": "折叠 / 展开全部分组"},
    {"group": "搜索", "keys": "/", "desc": "搜索配置项（回车执行，Esc 取消）"},
    {"group": "搜索", "keys": "n / N / p", "desc": "下一个 / 上一个匹配"},
    {"group": "搜索", "keys": "f", "desc": "过滤模式（只显示匹配配置项）"},
    {"group": "面板", "keys": "?", "desc": "本帮助面板（? / q / Esc 关闭）"},
    {"group": "操作", "keys": "y", "desc": "复制选中项 key=value 到剪贴板（OSC52）"},
    {"group": "操作", "keys": "e", "desc": "导出当前配置为 JSON 文件"},
    {"group": "操作", "keys": "i", "desc": "从 JSON 文件导入配置（校验 + 确认）"},
    {"group": "操作", "keys": "Esc / Ctrl+H", "desc": "关闭配置中心"},
]

# ── 模型选择器快捷键速查表（``?`` 帮助面板内容，「一切皆插件」） ──
MODEL_KEYMAP_DATA: list = [
    {"group": "选择", "keys": "\u2191\u2193 / j k", "desc": "移动选择（表单中移动字段）"},
    {"group": "选择", "keys": "PgUp / PgDn", "desc": "整页翻页"},
    {"group": "选择", "keys": "Home/End \u00b7 g/G", "desc": "首末"},
    {"group": "选择", "keys": "Enter", "desc": "应用选中模型（表单中编辑字段）"},
    {"group": "管理", "keys": "a", "desc": "新增模型档案（提供商 → 模型名 → 密钥 → 温度/思考级别）"},
    {"group": "管理", "keys": "c", "desc": "复制选中档案为副本（改名后 s 保存）"},
    {"group": "管理", "keys": "e", "desc": "编辑选中模型档案"},
    {"group": "管理", "keys": "d", "desc": "删除选中模型档案"},
    {"group": "管理", "keys": "s", "desc": "表单保存（新增/编辑/复制）"},
    {"group": "管理", "keys": "r", "desc": "刷新模型列表（重载配置）"},
    {"group": "搜索", "keys": "/", "desc": "搜索模型（回车执行，Esc 取消）"},
    {"group": "搜索", "keys": "n / N / p", "desc": "下一个 / 上一个匹配"},
    {"group": "搜索", "keys": "f", "desc": "过滤模式（只显示匹配模型）"},
    {"group": "输入", "keys": "\u2191 / \u2193", "desc": "模型名候选：切换该提供商内置模型（也可直接输入自定义名）"},
    {"group": "输入", "keys": "温度字段", "desc": "输入 0.0~2.0（如 0.7）；留空沿用全局默认"},
    {"group": "输入", "keys": "思考级别字段", "desc": "Enter 后 \u2191\u2193 选择 low/medium/high/max 或「沿用全局默认」"},
    {"group": "输入", "keys": "\u2190 \u2192", "desc": "字段输入：光标左右移动"},
    {"group": "输入", "keys": "Home/End \u00b7 Ctrl+A/Ctrl+E", "desc": "字段输入：光标到行首 / 行尾"},
    {"group": "输入", "keys": "Delete / Backspace", "desc": "字段输入：删除光标处 / 前一个字符"},
    {"group": "输入", "keys": "Ctrl+U", "desc": "字段输入：清空当前字段"},
    {"group": "面板", "keys": "?", "desc": "本帮助面板（? / q / Esc 关闭）"},
    {"group": "操作", "keys": "y", "desc": "复制选中模型信息到剪贴板（OSC52）"},
    {"group": "操作", "keys": "Esc / Ctrl+H", "desc": "关闭模型选择器"},
]

__all__ = [
    "EMOJI_MAP",
    "SUBSCRIPT_MAP",
    "SUPERSCRIPT_MAP",
    "CIRCLED_DIGITS",
    "HTML_TAG_COLORS",
    "BULLETS",
    "NESTED_BULLET_SYMBOLS",
    "TRACE_KIND",
    "TRACE_STATUS",
    "MODE_TEXT",
    "MODE_STYLE_FG",
    "TOOL_DISPLAY_NAME_MAP",
    "ADMONITION_STYLE_MAP",
    "SPINNER_PRESET_FRAMES",
    "INLINE_SPINNER_FRAMES",
    "CONFIG_ENTRY_DESC_MAP",
    "CONFIG_ENTRY_OPTION_MAP",
    "TRACE_KIND_ORDER_DATA",
    "TRACE_BLOCK_KIND_MAP",
    "MESSAGE_ROLE_ICON_MAP",
    "BORDER_CHARS_MAP",
    "BORDER_OBJECT_DEFAULT",
    "BILLING_DEFAULT_DATA",
    "METRIC_DEFAULTS_DATA",
    "UI_DEFAULTS_DATA",
    "MODEL_PATTERNS_DATA",
    "SEMANTIC_COLOR_DATA",
    "GRADIENT_STOPS_DATA",
    "SHELL_DETECT_DATA",
    "HTTP_ERROR_HINT_DATA",
    "BADGE_METRICS_DATA",
    "KITTY_PROTOCOL_DATA",
    "DIFF_STYLE_DATA",
    "TRACE_STYLE_DATA",
    "TRACE_KEYMAP_DATA",
    "PLUGIN_KEYMAP_DATA",
    "CONFIG_KEYMAP_DATA",
    "MODEL_KEYMAP_DATA",
]
