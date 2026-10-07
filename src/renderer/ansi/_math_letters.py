"""_math_letters — Unicode 数学字母数字符号（``\\mathbb`` / ``\\mathcal`` 等）。

LaTeX 的「数学字母族」命令（``\\mathbb{R}`` / ``\\mathcal{F}`` /
``\\mathfrak{g}`` / ``\\mathscr{L}`` / ``\\mathsf{A}`` / ``\\mathtt{x}`` /
``\\mathbf{v}`` / ``\\boldsymbol{v}``）在终端中以 Unicode 数学字母数字符号
呈现——比单纯变色更接近印刷排版（真源为 Unicode Math Alphanumeric
Symbols 区块 + Letterlike Symbols 中的历史空洞字符）。

设计：每个族给出「大写 26 字母 + 小写 26 字母」两张字符串表（含空洞字符
的实际字形，不依赖码点偏移，避免误用未分配码点），数字表可选。
``to_math_alphabet`` 仅在该族可完整表示时才转换（否则返回 ``None``，调用方
回退普通样式文本，内容不丢）。
"""

from __future__ import annotations

# ── 各字母族字形表（顺序：A..Z / a..z） ─────────────────────

_BOLD_UPPER = "𝐀𝐁𝐂𝐃𝐄𝐅𝐆𝐇𝐈𝐉𝐊𝐋𝐌𝐍𝐎𝐏𝐐𝐑𝐒𝐓𝐔𝐕𝐖𝐗𝐘𝐙"
_BOLD_LOWER = "𝐚𝐛𝐜𝐝𝐞𝐟𝐠𝐡𝐢𝐣𝐤𝐥𝐦𝐧𝐨𝐩𝐪𝐫𝐬𝐭𝐮𝐯𝐰𝐱𝐲𝐳"

_ITALIC_UPPER = "𝐴𝐵𝐶𝐷𝐸𝐹𝐺𝐻𝐼𝐽𝐾𝐿𝑀𝑁𝑂𝑃𝑄𝑅𝑆𝑇𝑈𝑉𝑊𝑋𝑌𝑍"
_ITALIC_LOWER = "𝑎𝑏𝑐𝑑𝑒𝑓𝑔ℎ𝑖𝑗𝑘𝑙𝑚𝑛𝑜𝑝𝑞𝑟𝑠𝑡𝑢𝑣𝑤𝑥𝑦𝑧"

_BOLD_ITALIC_UPPER = "𝑨𝑩𝑪𝑫𝑬𝑭𝑮𝑯𝑰𝑱𝑲𝑳𝑴𝑵𝑶𝑷𝑸𝑹𝑺𝑻𝑼𝑽𝑾𝑿𝒀𝒁"
_BOLD_ITALIC_LOWER = "𝒂𝒃𝒄𝒅𝒆𝒇𝒈𝒉𝒊𝒋𝒌𝒍𝒎𝒏𝒐𝒑𝒒𝒓𝒔𝒕𝒖𝒗𝒘𝒙𝒚𝒛"

_SCRIPT_UPPER = "𝒜ℬ𝒞𝒟ℰℱ𝒢ℋℐ𝒥𝒦ℒℳ𝒩𝒪𝒫𝒬ℛ𝒮𝒯𝒰𝒱𝒲𝒳𝒴𝒵"
_SCRIPT_LOWER = "𝒶𝒷𝒸𝒹ℯ𝒻ℊ𝒽𝒾𝒿𝓀𝓁𝓂𝓃ℴ𝓅𝓆𝓇𝓈𝓉𝓊𝓋𝓌𝓍𝓎𝓏"

_BOLD_SCRIPT_UPPER = "𝓐𝓑𝓒𝓓𝓔𝓕𝓖𝓗𝓘𝓙𝓚𝓛𝓜𝓝𝓞𝓟𝓠𝓡𝓢𝓣𝓤𝓥𝓦𝓧𝓨𝓩"
_BOLD_SCRIPT_LOWER = "𝓪𝓫𝓬𝓭𝓮𝓯𝓰𝓱𝓲𝓳𝓴𝓵𝓶𝓷𝓸𝓹𝓺𝓻𝓼𝓽𝓾𝓿𝔀𝔁𝔂𝔃"

_FRAKTUR_UPPER = "𝔄𝔅ℭ𝔇𝔈𝔉𝔊ℌℑ𝔍𝔎𝔏𝔐𝔑𝔒𝔓𝔔ℜ𝔖𝔗𝔘𝔙𝔚𝔛𝔜ℨ"
_FRAKTUR_LOWER = "𝔞𝔟𝔠𝔡𝔢𝔣𝔤𝔥𝔦𝔧𝔨𝔩𝔪𝔫𝔬𝔭𝔮𝔯𝔰𝔱𝔲𝔳𝔴𝔵𝔶𝔷"

_BOLD_FRAKTUR_UPPER = "𝕬𝕭𝕮𝕯𝕰𝕱𝕲𝕳𝕴𝕵𝕶𝕷𝕸𝕹𝕺𝕻𝕼𝕽𝕾𝕿𝖀𝖁𝖂𝖃𝖄𝖅"
_BOLD_FRAKTUR_LOWER = "𝖆𝖇𝖈𝖉𝖊𝖋𝖌𝖍𝖎𝖏𝖐𝖑𝖒𝖓𝖔𝖕𝖖𝖗𝖘𝖙𝖚𝖛𝖜𝖝𝖞𝖟"

_DOUBLE_UPPER = "𝔸𝔹ℂ𝔻𝔼𝔽𝔾ℍ𝕀𝕁𝕂𝕃𝕄ℕ𝕆ℙℚℝ𝕊𝕋𝕌𝕍𝕎𝕏𝕐ℤ"
_DOUBLE_LOWER = "𝕒𝕓𝕔𝕕𝕖𝕗𝕘𝕙𝕚𝕛𝕜𝕝𝕞𝕟𝕠𝕡𝕢𝕣𝕤𝕥𝕦𝕧𝕨𝕩𝕪𝕫"

_SANS_UPPER = "𝖠𝖡𝖢𝖣𝖤𝖥𝖦𝖧𝖨𝖩𝖪𝖫𝖬𝖭𝖮𝖯𝖰𝖱𝖲𝖳𝖴𝖵𝖶𝖷𝖸𝖹"
_SANS_LOWER = "𝖺𝖻𝖼𝖽𝖾𝖿𝗀𝗁𝗂𝗃𝗄𝗅𝗆𝗇𝗈𝗉𝗊𝗋𝗌𝗍𝗎𝗏𝗐𝗑𝗒𝗓"

_SANS_BOLD_UPPER = "𝗔𝗕𝗖𝗗𝗘𝗙𝗚𝗛𝗜𝗝𝗞𝗟𝗠𝗡𝗢𝗣𝗤𝗥𝗦𝗧𝗨𝗩𝗪𝗫𝗬𝗭"
_SANS_BOLD_LOWER = "𝗮𝗯𝗰𝗱𝗲𝗳𝗴𝗵𝗶𝗷𝗸𝗹𝗺𝗻𝗼𝗽𝗾𝗿𝘀𝘁𝘂𝘃𝘄𝘅𝘆𝘇"

_MONO_UPPER = "𝙰𝙱𝙲𝙳𝙴𝙵𝙶𝙷𝙸𝙹𝙺𝙻𝙼𝙽𝙾𝙿𝚀𝚁𝚂𝚃𝚄𝚅𝚆𝚇𝚈𝚉"
_MONO_LOWER = "𝚊𝚋𝚌𝚍𝚎𝚏𝚐𝚑𝚒𝚓𝚔𝚕𝚖𝚗𝚘𝚙𝚚𝚛𝚜𝚝𝚞𝚟𝚠𝚡𝚢𝚣"

# ── 数字表（无该族数字符号时保持 ASCII 数字） ───────────────

_DIGITS_BOLD = "𝟎𝟏𝟐𝟑𝟒𝟓𝟔𝟕𝟖𝟗"
_DIGITS_DOUBLE = "𝟘𝟙𝟚𝟛𝟜𝟝𝟞𝟟𝟠𝟡"
_DIGITS_SANS = "𝟢𝟣𝟤𝟥𝟦𝟧𝟨𝟩𝟪𝟫"
_DIGITS_SANS_BOLD = "𝟬𝟭𝟮𝟯𝟰𝟱𝟲𝟳𝟴𝟵"
_DIGITS_MONO = "𝟶𝟷𝟸𝟹𝟺𝟻𝟼𝟽𝟾𝟿"

_ASCII_UPPER = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_ASCII_LOWER = "abcdefghijklmnopqrstuvwxyz"
_ASCII_DIGITS = "0123456789"


def _table(upper: str, lower: str, digits: str | None = None) -> dict[str, str]:
    """由字形表构造「ASCII → 数学字母」映射（字母必须等长 26）。"""
    m: dict[str, str] = {}
    for i, ch in enumerate(_ASCII_UPPER):
        if i < len(upper):
            m[ch] = upper[i]
    for i, ch in enumerate(_ASCII_LOWER):
        if i < len(lower):
            m[ch] = lower[i]
    if digits:
        for i, ch in enumerate(_ASCII_DIGITS):
            if i < len(digits):
                m[ch] = digits[i]
    return m


#: 字母族名（LaTeX 命令名 / 别名）→ ASCII 映射表
ALPHABET_TABLES: dict[str, dict[str, str]] = {
    "mathbb": _table(_DOUBLE_UPPER, _DOUBLE_LOWER, _DIGITS_DOUBLE),
    "Bbb": _table(_DOUBLE_UPPER, _DOUBLE_LOWER, _DIGITS_DOUBLE),
    "mathds": _table(_DOUBLE_UPPER, _DOUBLE_LOWER, _DIGITS_DOUBLE),
    "mathcal": _table(_SCRIPT_UPPER, _SCRIPT_LOWER),
    "cal": _table(_SCRIPT_UPPER, _SCRIPT_LOWER),
    "mathscr": _table(_SCRIPT_UPPER, _SCRIPT_LOWER),
    "EuScript": _table(_SCRIPT_UPPER, _SCRIPT_LOWER),
    "mathpzc": _table(_SCRIPT_UPPER, _SCRIPT_LOWER),
    "mathfrak": _table(_FRAKTUR_UPPER, _FRAKTUR_LOWER),
    "frak": _table(_FRAKTUR_UPPER, _FRAKTUR_LOWER),
    "mathbffrak": _table(_BOLD_FRAKTUR_UPPER, _BOLD_FRAKTUR_LOWER),
    "mathbf": _table(_BOLD_UPPER, _BOLD_LOWER, _DIGITS_BOLD),
    "boldsymbol": _table(_BOLD_ITALIC_UPPER, _BOLD_ITALIC_LOWER),
    "bm": _table(_BOLD_ITALIC_UPPER, _BOLD_ITALIC_LOWER),
    "mathbfit": _table(_BOLD_ITALIC_UPPER, _BOLD_ITALIC_LOWER),
    "mathboldsf": _table(_SANS_BOLD_UPPER, _SANS_BOLD_LOWER, _DIGITS_SANS_BOLD),
    "mathsf": _table(_SANS_UPPER, _SANS_LOWER, _DIGITS_SANS),
    "mathtt": _table(_MONO_UPPER, _MONO_LOWER, _DIGITS_MONO),
    "mathit": _table(_ITALIC_UPPER, _ITALIC_LOWER),
    "mathnormal": _table(_ITALIC_UPPER, _ITALIC_LOWER),
    "mathcalbold": _table(_BOLD_SCRIPT_UPPER, _BOLD_SCRIPT_LOWER),
}


def to_math_alphabet(text: str, family: str) -> str | None:
    """ASCII 文本 → 指定数学字母族字形。

    空格原样保留；任一非空格字符无法映射（如中文、希腊字母、标点）时返回
    ``None``，调用方回退普通样式渲染（保证内容不丢、不产生错误字形）。
    """
    table = ALPHABET_TABLES.get(family)
    if not table or not text:
        return None
    out: list[str] = []
    for ch in text:
        if ch == " ":
            out.append(ch)
            continue
        mapped = table.get(ch)
        if mapped is None:
            return None
        out.append(mapped)
    return "".join(out)


__all__ = ["ALPHABET_TABLES", "to_math_alphabet"]
