"""子进程输出解码 — 跨平台编码自适应（bash 工具卡片 Windows 乱码修复）。

问题根因（用户报障「bash 的 toolcard 在 Windows cmd / PowerShell 下有时候
显示乱码」）：bash 工具把子进程的原始字节一律 ``decode('utf-8',
errors='replace')``。Windows 原生环境下 ``asyncio.create_subprocess_shell``
经 ``cmd.exe``（``COMSPEC``）执行命令——cmd 内建命令（``dir`` / ``echo`` /
``chcp`` 等）与多数控制台程序把 stdout 写成**当前控制台代码页**的字节
（简体中文 Windows 为 cp936/GBK；日文 cp932、韩文 cp949、繁体 cp950……），
并非 UTF-8。这些字节按 UTF-8 解码必然失败，被替换成 U+FFFD（``�``）——
工具卡片里就是成片的「乱码」。PowerShell 5.1 重定向输出同样跟随控制台
代码页；PowerShell 7 / Node.js / ripgrep / ``PYTHONUTF8`` 下的 Python 则输出
UTF-8——因此**同一个终端里不同命令的编码可能不同**，必须逐行自适应。

Cygwin / MSYS2 场景（用户报障「在 Cygwin 下执行 bash 会有机率乱码」）：
这类环境的 ``os.name`` 是 ``posix``、``sys.platform`` 是 ``cygwin``/``msys``，
但内核是 Windows——**Cygwin 程序按 ``locale`` 输出 UTF-8，原生 Windows
程序（``ipconfig`` / ``driverquery`` 等按 ANSI 代码页输出的工具）输出
cp936**，同一条命令流里两种编码都可能出现。修复前的消歧规则把「落在
``U+0370–U+07FF`` 的合法 UTF-8 行」一律改判成本地代码页：真 UTF-8 的希腊
字母（``α`` / ``μ``）、西里尔文（``Привет``）被改成「伪」「袩褉懈胁械褌」；
反过来落在 ``U+0080–U+036F`` 的 GBK 误读行（``ʲô`` / ``ɸѡ``）又原样留下。
现改为「误读特征 + 环境主编码」双判据（见 :func:`_ambiguous_local_decode`、
:func:`prefer_local_output`），Cygwin 下真 UTF-8 不再被改成中文。

修复策略（流式、逐行自适应，不做有状态的全局猜测）：
  1. **UTF-8 优先**：现代工具输出 UTF-8（最常见且无歧义），先严格解码；
     与 CJK 代码页「双重合法」的行（如 GBK「系统」=``CF B5 CD B3`` 恰好
     也是合法 UTF-8，解出 ``ϵͳ``）按 :func:`_ambiguous_local_decode` 消解：
     句内出现「误读特征」（IPA / 修饰符 / 组合符号 / 古体希腊 / 科普特 /
     古体西里尔等正常输出几乎不会出现的码位，或一行横跨多个书写系统）→
     判为本机代码页；否则按 :func:`prefer_local_output` 给出的**环境主编码**
     裁决（Cygwin / MSYS2 / POSIX 跟随 ``locale``，原生 Windows 跟随控制台
     代码页）；
  2. **本机代码页回退**：UTF-8 失败时依次尝试本机候选编码——Windows 取
     控制台输出代码页（``GetConsoleOutputCP``）、OEM 代码页（``GetOEMCP``）、
     ANSI 代码页（``GetACP``）；POSIX 取 ``locale`` 首选编码；
  3. **兜底绝不抛异常**：全部失败时用本机编码 ``errors='replace'`` 解码
     （保留可见部分，不因个别损坏字节丢掉整行）；
  4. **宽字符流**：识别 BOM（UTF-8/UTF-16/UTF-32）与无 BOM 的 UTF-16 流
     （``wmic`` 等按 UTF-16LE 输出），整块增量解码后再按 ``\\n`` 切行——
     按字节切分会把双字节编码拆坏（``\\n`` 后的 ``\\x00`` 变成下一行首）。

依赖约束：仅标准库 + Layer 0 的 ``src._text_width``（宽字符判定，零上层
依赖）；不得 import 任何上层模块（本模块被 tools 层多处复用）。
"""

from __future__ import annotations

import codecs
import locale
import logging
import os
import sys

from .._text_width import codepoint_width

logger = logging.getLogger(__name__)

__all__ = [
    "CHILD_STDIO_ENCODING",
    "OutputLineDecoder",
    "decode_output",
    "default_output_encodings",
    "prefer_local_output",
    "windows_ansi_codepage",
]


#: bash 工具为**子进程**统一设置的 stdio 编码：包装脚本把
#: ``[Console]::InputEncoding`` / ``[Console]::OutputEncoding`` / ``$OutputEncoding``
#: 设为无 BOM UTF-8，并设置 ``PYTHONIOENCODING=utf-8`` / ``PYTHONUTF8=1``
#: （见 ``bash._powershell_encoding_line``）；POSIX 下同样是 UTF-8。
#:
#: ``bash_opt op=stdin`` 的默认文本编码必须与之一致：后台任务里的 Python /
#: Node / PowerShell 都按 UTF-8 读 stdin，按系统 ANSI 代码页（中文 Windows
#: = cp936）写入的中文会被当成非法字节（实测 Python 3.11：
#: ``UnicodeDecodeError: 'utf-8' codec can't decode byte 0xd6``）。
CHILD_STDIO_ENCODING = "utf-8"


# ── 本机候选编码 ──────────────────────────────────────

def _is_windows_like() -> bool:
    """是否能读取 Windows 代码页（原生 Windows / Cygwin / MSYS2）。"""
    return os.name == "nt" or (sys.platform or "").startswith(("cygwin", "msys"))


def _windows_code_page(getter: str) -> int:
    """读取 Windows 代码页（``GetConsoleOutputCP`` / ``GetOEMCP`` / ``GetACP``）。

    非 Windows、无控制台（``GetConsoleOutputCP`` 返回 0）或 API 不可用时
    返回 0——调用方据此跳过该候选。

    ★ Cygwin / MSYS2：``os.name`` 是 ``posix``，没有 ``ctypes.windll``，
    但内核是 Windows，``ctypes.CDLL("kernel32.dll")`` 可正常调用。修复前
    只认 ``os.name == 'nt'``，导致 Cygwin 下候选编码只有 UTF-8：原生程序
    输出的 GBK（``cmd /c type``、旧版控制台工具、GBK 文件）全部变成
    ``����``，且「系统」这类与 UTF-8 双重合法的短行被误读成 ``ϵͳ``。
    """
    if not _is_windows_like():
        return 0
    try:
        import ctypes

        loader = getattr(ctypes, "WinDLL", None)
        if loader is not None:
            lib = loader("kernel32", use_last_error=True)
        else:
            lib = ctypes.CDLL("kernel32.dll")
        value = int(getattr(lib, getter)())
    except Exception:  # pragma: no cover - 极端环境（ctypes/kernel32 不可用）
        return 0
    return value if value > 0 else 0


def _code_page_encoding(code_page: int) -> str | None:
    """Windows 代码页 → Python 编码名（未知/不可用代码页返回 None）。"""
    if code_page <= 0:
        return None
    # 特殊代码页：UTF-8（65001）与 UTF-16（1200/1201）——Python 无同名
    # ``cpNNNN`` 编解码器别名，须显式映射。
    special = {65001: "utf-8", 1200: "utf-16-le", 1201: "utf-16-be"}
    if code_page in special:
        return special[code_page]
    name = f"cp{code_page}"
    try:
        codecs.lookup(name)
    except LookupError:
        return None
    return name


def windows_ansi_codepage() -> int:
    """系统 ANSI 代码页（``GetACP``；非 Windows / 取不到返回 0）。

    供 ``bash_opt`` 的 ``op=stdin`` 默认编码与诊断使用。Cygwin / MSYS2
    下同样可读（内核是 Windows）。
    """
    return _windows_code_page("GetACP")


def default_output_encodings() -> tuple[str, ...]:
    """返回子进程输出解码的候选编码（顺序即优先级，UTF-8 恒在首位）。

    Windows 依次追加：控制台输出代码页 → OEM 代码页 → ANSI 代码页 →
    ``locale`` 首选编码（通常与 ANSI 相同，自动去重）。POSIX 追加
    ``locale`` 首选编码与文件系统编码（``LANG=C`` 时可能仅 ASCII）。
    """
    encodings: list[str] = ["utf-8"]
    seen = {"utf-8"}

    def _add(name: "str | None") -> None:
        if not name:
            return
        lowered = name.lower()
        if lowered in seen:
            return
        seen.add(lowered)
        encodings.append(name)

    if _is_windows_like():
        for getter in ("GetConsoleOutputCP", "GetOEMCP", "GetACP"):
            _add(_code_page_encoding(_windows_code_page(getter)))
    try:
        _add(locale.getpreferredencoding(False))
    except Exception:  # pragma: no cover - locale 查询异常（极端环境）
        logger.debug("locale 首选编码查询失败", exc_info=True)
    try:
        _add(sys.getfilesystemencoding())
    except Exception:  # pragma: no cover - 同上
        logger.debug("文件系统编码查询失败", exc_info=True)
    return tuple(encodings)


def prefer_local_output() -> bool:
    """环境的主输出编码是否「本机代码页」（而非 UTF-8）。

    只用于消解**无法从句内证据判定**的行——即同时满足「UTF-8 严格解码
    成功」与「本机 CJK 代码页严格解码成功」的「双重合法」行（GBK
    ``CF B5 CD B3``「系统」按 UTF-8 解出 ``ϵͳ``）。这类行没有字节级证据，
    只能按环境的主编码裁决：

      - **原生 Windows**：主编码 = 控制台输出代码页（``chcp`` 决定，先取
        ``GetConsoleOutputCP``，取不到再退 OEM / ANSI）。``cmd`` 内建命令与
        多数控制台程序按它输出字节，因此非 65001 时返回 ``True``（按本机
        代码页解释，保持既有行为）；控制台已是 65001 时返回 ``False``
        （此时原生程序本身就写 UTF-8）；
      - **Cygwin / MSYS2 / 其它 POSIX**：主编码 = ``locale`` 首选编码。
        Cygwin 程序按 locale 输出（本项目环境为 ``zh_CN.utf-8`` → UTF-8），
        原生 Windows 程序的 GBK 字节已经由「UTF-8 严格解码失败 → 本机代码页
        回退」路径处理；因此 UTF-8 locale 下返回 ``False``——双合法行按
        UTF-8 解释，不再把真 UTF-8 的希腊 / 西里尔 / 带重音拉丁文本改判成
        GBK（Cygwin 下「α」曾被改成「伪」）。

    Returns:
        True 表示双合法行应判为本机代码页输出；False 表示应保留 UTF-8。
    """
    if os.name == "nt":
        code_page = (
            _windows_code_page("GetConsoleOutputCP")
            or _windows_code_page("GetOEMCP")
            or _windows_code_page("GetACP")
        )
        encoding = _code_page_encoding(code_page)
        return not (encoding and _is_utf8_name(encoding))
    try:
        preferred = locale.getpreferredencoding(False) or ""
    except Exception:  # pragma: no cover - locale 查询异常（极端环境）
        logger.debug("locale 首选编码查询失败", exc_info=True)
        return True
    # 取不到 locale 信息（空串）时按「本机代码页」处理，保持既有行为
    return not (preferred and _is_utf8_name(preferred))


# ── 单块字节 → 文本 ──────────────────────────────────

def _utf16_without_bom(raw: bytes) -> "str | None":
    """无 BOM 时按 NUL 字节分布猜测 UTF-16 字节序（猜不出返回 None）。

    UTF-16LE 的 ASCII 文本形如 ``h\\x00e\\x00...``（奇数位大量 0）；
    UTF-16BE 形如 ``\\x00h\\x00e...``（偶数位大量 0）。要求样本足够长且
    分布显著，避免把普通二进制/含 NUL 的文本误判为宽字符流。
    """
    sample = raw[:512]
    if len(sample) < 8:
        return None
    even = sample[0::2]
    odd = sample[1::2]
    count = min(len(even), len(odd))
    if count < 4:
        return None
    even_zeros = even[:count].count(0)
    odd_zeros = odd[:count].count(0)
    if odd_zeros * 2 >= count and even_zeros * 4 <= count:
        return "utf-16-le"
    if even_zeros * 2 >= count and odd_zeros * 4 <= count:
        return "utf-16-be"
    return None


def _is_utf8_name(encoding: str) -> bool:
    """编码名是否为 UTF-8 族（``utf-8`` / ``utf8`` / ``utf-8-sig``）。"""
    return encoding.lower().replace("_", "-").startswith("utf-8")


#: CJK 多字节代码页前缀（归一化：小写、去掉 ``_``/``-``）。
#: 这些代码页的字节流与 UTF-8 双字节序列存在「双重合法」歧义（见
#: :func:`_ambiguous_local_decode`），需要额外消解；单字节代码页不存在
#: 同类歧义（UTF-8 多字节序列按单字节代码页解码必为 Latin-1 乱码）。
_CJK_CODEPAGE_PREFIXES: tuple[str, ...] = (
    "cp932", "cp936", "cp949", "cp950", "cp1361",
    "gbk", "gb2312", "gb18030", "big5", "johab",
    "eucjp", "euckr", "shiftjis", "sjis",
)

#: 「本机代码页被按 UTF-8 误读」的特征码位区间——正常输出几乎不会出现：
#:
#:   - ``U+0250–U+02FF``：IPA 扩展 + 间距修饰符（``ʲ`` / ``ɸ``）；
#:   - ``U+0300–U+036F``：组合附加符号（正常文本只跟在字母后，见
#:     :func:`_embedded_in_ascii_word`，孤立出现即误读）；
#:   - ``U+0370–U+038F``：古体希腊字母（``ͳ``，现代希腊文不用）；
#:   - ``U+03D0–U+03FF``：变体希腊 / 科普特衍生（``ϵ``）；
#:   - ``U+0460–U+052F``：古体西里尔 / 西里尔补充。
#:
#: GBK 双字节字符（首字节 ``C2–DF``）按 UTF-8 误读后必然落在
#: ``U+0080–U+07FF``，其中相当一部分会落进上述区间——命中即判定为本机
#: 代码页输出。真希腊 / 西里尔 / 带重音拉丁文本用的是现代字母
#: （``U+0390–U+03CE`` / ``U+0400–U+045F`` / ``U+00C0–U+017F``），不命中。
_MOJIBAKE_RANGES: tuple[tuple[int, int], ...] = (
    (0x0250, 0x02FF),
    (0x0300, 0x036F),
    (0x0370, 0x038F),
    (0x03D0, 0x03FF),
    (0x0460, 0x052F),
)

#: ``U+0080–U+07FF`` 按书写系统分块（用于「一行横跨多个书写系统 = 误读」
#: 的判定）。拉丁诸块（Latin-1 补充 + Ext-A + Ext-B）合并为 ``latin``：
#: 同属拉丁书写系统，西欧 / 中欧文本混用（``café`` / ``Zürich`` / ``ș``）
#: 是常态，不能算「横跨多个系统」。
_SCRIPT_BLOCKS: tuple[tuple[int, int, str], ...] = (
    (0x0080, 0x024F, "latin"),
    (0x0250, 0x02AF, "ipa"),
    (0x02B0, 0x02FF, "modifier"),
    (0x0300, 0x036F, "combining"),
    (0x0370, 0x03FF, "greek"),
    (0x0400, 0x052F, "cyrillic"),
    (0x0530, 0x058F, "armenian"),
    (0x0590, 0x05FF, "hebrew"),
    (0x0600, 0x06FF, "arabic"),
    (0x0700, 0x074F, "syriac"),
    (0x0750, 0x077F, "arabic-supplement"),
    (0x0780, 0x07BF, "thaana"),
    (0x07C0, 0x07FF, "nko"),
)


def _is_cjk_codepage(encoding: str) -> bool:
    """编码名是否为 CJK 多字节代码页（cp932/936/949/950、GBK、Big5 等）。"""
    name = encoding.lower().replace("_", "").replace("-", "").replace(" ", "")
    return name.startswith(_CJK_CODEPAGE_PREFIXES)


def _contains_wide_char(text: str) -> bool:
    """文本是否含宽字符（CJK / 全角 / emoji，显示宽度 2）。"""
    return any(codepoint_width(ord(ch)) == 2 for ch in text)


def _script_group(char: str) -> str:
    """返回非 ASCII 字符所属的书写系统块名（未登记区间返回 ``other``）。"""
    cp = ord(char)
    for start, end, name in _SCRIPT_BLOCKS:
        if start <= cp <= end:
            return name
    return "other"


def _embedded_in_ascii_word(text: str, index: int) -> bool:
    """该位置的非 ASCII 字符是否紧贴 ASCII 字母 / 数字（真实文本特征）。

    ``café`` / ``Zürich`` / ``25 °C`` / ``2μs`` 里的重音字符、符号、希腊
    字母都嵌在 ASCII 词或数字之间；GBK 被误读时整段是连续非 ASCII（中文
    没有词间空格），不会紧贴 ASCII。用于把「孤立在 ASCII 词里的非 ASCII
    字符」排除出误读判定——它们几乎一定是真 UTF-8。
    """
    for neighbor in (index - 1, index + 1):
        if 0 <= neighbor < len(text):
            char = text[neighbor]
            if char.isascii() and char.isalnum():
                return True
    return False


def _suspicious_non_ascii(text: str) -> list[str]:
    """取出「可能来自本机代码页误读」的非 ASCII 字符（跳过词内重音等）。

    只对**UTF-8 合法、且全部为 2 字节序列**（``U+0080–U+07FF``）的文本有
    意义——这两个前提由调用方 :func:`_ambiguous_local_decode` 保证。
    """
    return [
        char for index, char in enumerate(text)
        if ord(char) > 0x7F and not _embedded_in_ascii_word(text, index)
    ]


def _is_mojibake_codepoint(char: str) -> bool:
    """码位是否落在「本机代码页被误读成 UTF-8」的特征区间。"""
    cp = ord(char)
    return any(start <= cp <= end for start, end in _MOJIBAKE_RANGES)


def _looks_like_local_mojibake(suspicious: list[str]) -> bool:
    """这些非 ASCII 字符是否呈现「本机代码页被误读」的特征。

    判定（任一命中即成立）：

      1. 含特征区间的码位（IPA / 修饰符 / 组合符号 / 古体希腊 / 科普特 /
         古体西里尔）——正常输出几乎不会出现；
      2. 横跨两个以上书写系统（如「ɸѡ」= IPA + 西里尔、「ʲô」= 修饰符 +
         拉丁）——真文本极少在一行内混排多种冷门书写系统。

    单书写系统且不含特征码位的行（真希腊文 ``Γειά σου``、GBK「系统」被
    误读成的 ``ϵͳ``——后者命中特征区间）交给环境主编码裁决，见
    :func:`_ambiguous_local_decode`。
    """
    if not suspicious:
        return False
    if any(_is_mojibake_codepoint(char) for char in suspicious):
        return True
    return len({_script_group(char) for char in suspicious}) >= 2


def _has_non_latin_script(suspicious: list[str]) -> bool:
    """是否含拉丁书写系统以外的字符（纯拉丁文本一律按真 UTF-8 处理）。"""
    return any(_script_group(char) != "latin" for char in suspicious)


def _utf8_has_wide_sequence(raw: bytes) -> bool:
    """UTF-8 字节流是否含 3/4 字节序列（CJK/emoji 的强信号）。

    GBK 双字节按 UTF-8 误读时通常落在 2 字节序列（U+0080-U+07FF）；
    真 UTF-8 的中文/日文/韩文/emoji 必然出现 3/4 字节序列。调用方保证
    ``raw`` 已通过 UTF-8 严格解码，故此处只需按首字节长度跳过。
    """
    index = 0
    length = len(raw)
    while index < length:
        lead = raw[index]
        if lead < 0x80:
            index += 1
        elif lead < 0xE0:
            index += 2
        else:
            return True
    return False


def _ambiguous_local_decode(
    raw: bytes,
    utf8_text: str,
    candidates: tuple[str, ...],
    prefer_local: bool,
) -> "str | None":
    """消解「UTF-8 与本地 CJK 代码页双重合法」的歧义（返回本地解码或 None）。

    CJK 代码页的短行可能恰好也是合法 UTF-8：例如 GBK「系统」=
    ``CF B5 CD B3`` → UTF-8 解出希腊字母 ``ϵͳ``。这类行没有字节级证据，
    按以下顺序判定（返回本地解码 → 判为本机代码页；返回 None → 保留 UTF-8）：

      1. UTF-8 文本含宽字符（CJK/全角/emoji）→ 真 UTF-8，不改判（GBK 双字节
         误读不可能产生宽字符）；
      2. UTF-8 字节流含 3/4 字节序列 → 真 UTF-8，不改判（GBK 双字节误读只
         产生 2 字节序列）；
      3. 去掉「嵌在 ASCII 词里的孤立字符」（``café`` / ``25 °C`` / ``2μs``）
         后无剩余 → 真 UTF-8，不改判；
      4. 剩余字符呈现误读特征（命中特征码位区间，或横跨多个书写系统）→
         判为本机代码页（见 :func:`_looks_like_local_mojibake`）；
      5. 否则按环境主编码裁决：``prefer_local`` 为真且含非拉丁字符 → 判为
         本机代码页；纯拉丁文本始终按真 UTF-8 处理（保护 ``ÆØÅ`` 等）；
      6. 判为本机代码页时，只有在本地 CJK 代码页**严格**解码且解出宽字符
         才接受（否则保留 UTF-8，避免把正常文本换成乱码）。

    Args:
        raw: 原始字节（已确认可按 UTF-8 严格解码）。
        utf8_text: ``raw`` 的 UTF-8 解码结果。
        candidates: 候选编码（本机代码页回退顺序）。
        prefer_local: 环境主编码是否本机代码页（见 :func:`prefer_local_output`）。
    """
    if utf8_text.isascii():
        return None
    if _contains_wide_char(utf8_text):
        return None
    if _utf8_has_wide_sequence(raw):
        return None
    suspicious = _suspicious_non_ascii(utf8_text)
    if not suspicious:
        return None
    if not (_looks_like_local_mojibake(suspicious)
            or (prefer_local and _has_non_latin_script(suspicious))):
        return None
    for encoding in candidates:
        if _is_utf8_name(encoding) or not _is_cjk_codepage(encoding):
            continue
        try:
            local_text = raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
        if _contains_wide_char(local_text):
            return local_text
    return None


#: 判定「UTF-16 被按单字节 / UTF-8 解码」的 NUL 占比阈值
#: （NUL 个数 / 非 NUL 字符数；正常 UTF-8 文本远低于该值）
_NUL_REPAIR_RATIO = 1 / 3


def _repair_wide_text(text: str) -> str:
    """修复「UTF-16 输出被上游按单字节 / UTF-8 解码」造成的 NUL 夹字符。

    背景（实测：Windows 上 ``wsl.exe -l -v``）：``wsl.exe`` 按 UTF-16LE 输出，
    PowerShell 5.1 的 native 管道按 **UTF-8** 解码它——每个 ASCII 字符后面
    多出一个 ``\\x00``，UTF-16 BOM（``FF FE``）变成两个 ``U+FFFD``。工具拿到
    的已经是这段文本（原始字节不可逆），但 NUL 的**位置规律**完整保留了原文：
    去掉 NUL 后就是正确内容。

    触发条件（保守，避免误伤正常文本）：

      - 文本含 ``\\x00``，且 NUL 个数 >= 非 NUL 字符数的 1/3——真 UTF-8 文本
        几乎不含 NUL；达到该比例只可能是宽字符流被按单字节解码；
      - 去掉 NUL 后仍有内容（全是 NUL 的输入原样返回）。

    去掉 NUL 后顺带剥离开头连续的 ``U+FFFD``（被误读的 UTF-16 BOM）。

    Args:
        text: 已解码文本。

    Returns:
        修复后的文本；未命中条件时原样返回。
    """
    if not text or "\x00" not in text:
        return text
    stripped = text.replace("\x00", "")
    if not stripped:
        return text
    if len(stripped) < 4:
        # 极短文本（1~3 个字符夹 NUL）无法可靠区分「宽字符流」与「二进制片段」，
        # 保持原样（宽字符流的最小样本远大于此，如 wsl -l -v 的整行）
        return text
    if len(text) - len(stripped) < max(1, len(stripped) * _NUL_REPAIR_RATIO):
        return text
    return stripped.lstrip("\ufffd")


def decode_output(
    raw: bytes,
    encodings: "tuple[str, ...] | list[str] | None" = None,
    *,
    prefer_local: "bool | None" = None,
) -> str:
    """把子进程输出字节解码为文本（编码自适应 + 宽字符误读修复）。

    在 :func:`_decode_output` 的自适应解码之上，再修复一类**上游**损坏：
    UTF-16 输出被按单字节 / UTF-8 解码（Windows 上 ``wsl.exe`` 等），
    详见 :func:`_repair_wide_text`。

    Args:
        raw: 子进程输出字节（可含多行 / 不完整行）。
        encodings: 候选编码（默认 :func:`default_output_encodings`；测试可注入）。
        prefer_local: 「双重合法」行的裁决偏好（默认按
            :func:`prefer_local_output` 判定环境主编码；测试可注入）。

    Returns:
        解码后的文本（永不抛 ``UnicodeDecodeError``）。
    """
    return _repair_wide_text(_decode_output(raw, encodings, prefer_local=prefer_local))


def _decode_output(
    raw: bytes,
    encodings: "tuple[str, ...] | list[str] | None" = None,
    *,
    prefer_local: "bool | None" = None,
) -> str:
    """按编码自适应把字节解码为文本（:func:`decode_output` 的内核）。

    解码顺序：
      1. 明确 BOM（UTF-32 → UTF-8 → UTF-16；UTF-32LE 的 BOM 前两字节与
         UTF-16LE 重叠，故先判 UTF-32）；
      2. 无 BOM 但呈 UTF-16 字节分布（``wmic`` 等宽字符输出）；
      3. UTF-8 严格解码（现代工具的主流编码；与本地 CJK 代码页双重合法
         的行按 :func:`_ambiguous_local_decode` 消歧）；
      4. 依次尝试本机候选编码（见 :func:`default_output_encodings`）；
      5. 仍失败 → 本机编码 ``errors='replace'`` 兜底。

    Args:
        raw: 子进程输出字节（可含多行 / 不完整行）。
        encodings: 候选编码（默认 :func:`default_output_encodings`；测试可注入）。
        prefer_local: 「双重合法」行的裁决偏好（默认按
            :func:`prefer_local_output` 判定环境主编码；测试可注入）。

    Returns:
        解码后的文本（永不抛 ``UnicodeDecodeError``）。
    """
    if not raw:
        return ""
    candidates = tuple(encodings) if encodings else default_output_encodings()
    if prefer_local is None:
        prefer_local = prefer_local_output()

    # 1) BOM 明确：UTF-32 必须先判（其 LE BOM 以 UTF-16LE BOM 开头）
    if raw.startswith(codecs.BOM_UTF32_LE) or raw.startswith(codecs.BOM_UTF32_BE):
        return raw.decode("utf-32", "replace")
    if raw.startswith(codecs.BOM_UTF8):
        return raw.decode("utf-8-sig", "replace")
    if raw.startswith(codecs.BOM_UTF16_LE) or raw.startswith(codecs.BOM_UTF16_BE):
        return raw.decode("utf-16", "replace")

    # 2) 无 BOM 的 UTF-16：含 NUL 且字节分布符合宽字符（ASCII UTF-16 文本
    #    也是合法 UTF-8——不先判会解出夹着 \x00 的「乱码」）。
    if b"\x00" in raw:
        wide = _utf16_without_bom(raw)
        if wide is not None:
            try:
                return raw.decode(wide)
            except UnicodeDecodeError:
                pass

    # 3) UTF-8 优先；但在 Windows CJK 代码页下存在「双重合法」歧义——
    #    GBK 短行（如「系统」）可能恰好也是合法 UTF-8（希腊字母 ϵͳ），
    #    此时按 _ambiguous_local_decode 的规则改判本地代码页。
    try:
        utf8_text = raw.decode("utf-8")
    except UnicodeDecodeError:
        utf8_text = None
    if utf8_text is not None:
        local_text = _ambiguous_local_decode(
            raw, utf8_text, candidates, prefer_local)
        return local_text if local_text is not None else utf8_text

    # 4) 本机代码页（跳过 UTF-8 族——已尝试过）
    for encoding in candidates:
        if _is_utf8_name(encoding):
            continue
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue

    # 5) 兜底：本机编码 replace（跳过 UTF-8 族，避免又把代码页字节变 U+FFFD）
    fallback = next(
        (item for item in candidates if not _is_utf8_name(item)),
        "utf-8",
    )
    try:
        return raw.decode(fallback, "replace")
    except LookupError:  # pragma: no cover - 候选编码名非法（防御）
        return raw.decode("utf-8", "replace")


# ── 流式按行解码 ─────────────────────────────────────

class OutputLineDecoder:
    """把子进程字节流解码为文本行（编码自适应 + 宽字符流支持）。

    与 ``StreamReader.readline()`` 的差异：不做字节级 ``readuntil``，而是
    **先按编码解码、再按 ``\\n`` 切行**——UTF-16/UTF-32 流按字节切分会把
    双字节字符和 ``\\n`` 拆坏（``\\n`` 后的 ``\\x00`` 落到下一行行首）。
    普通单字节/UTF-8/GBK 流走「字节缓冲 + 按 ``b'\\n'`` 切行 + 逐行解码」
    的快路径（``b'\\n'`` 不会出现在 UTF-8/GBK 的多字节字符内部，切分安全）。

    用法::

        decoder = OutputLineDecoder()
        for line in decoder.feed(chunk):   # 完整行（含行尾 \\n）
            ...
        for line in decoder.flush():       # EOF 残留（无行尾 \\n）
            ...

    行内容语义与旧实现一致：含 ``\\n`` 的完整行原样保留换行符；EOF 残留
    无换行符。编码判定逐行进行（同一流内不同命令/程序混排 UTF-8 与本地
    代码页时各自正确）。
    """

    def __init__(
        self,
        encodings: "tuple[str, ...] | list[str] | None" = None,
        *,
        prefer_local: "bool | None" = None,
    ) -> None:
        """初始化。

        Args:
            encodings: 候选编码（默认 :func:`default_output_encodings`）。
            prefer_local: 「双重合法」（UTF-8 与本地 CJK 代码页都能严格
                解码）的行按哪个编码解释（默认按
                :func:`prefer_local_output` 判定环境主编码）。每条输出流
                解析一次——原生 Windows 的控制台代码页（``chcp``）可能在
                会话期间变化，按流采样比进程级缓存更贴近实际输出。
        """
        self._encodings: tuple[str, ...] = (
            tuple(encodings) if encodings else default_output_encodings()
        )
        self._prefer_local: bool = (
            prefer_local_output() if prefer_local is None else bool(prefer_local)
        )
        self._byte_buffer = bytearray()
        self._text_buffer = ""
        self._wide_encoding: "str | None" = None
        self._wide_decoder = None
        self._detected = False

    @property
    def encodings(self) -> tuple[str, ...]:
        """本解码器使用的候选编码（调试/测试用）。"""
        return self._encodings

    @property
    def prefer_local(self) -> bool:
        """「双重合法」行是否按本机代码页解释（调试/测试用）。"""
        return self._prefer_local

    def feed(self, chunk: bytes) -> list[str]:
        """喂入一块字节，返回其中已完整的行（含行尾 ``\\n``，可能为空）。"""
        if not chunk:
            return []
        if not self._detected:
            # 样本不足且尚无换行：继续累积（宽字符/UTF-16 判定需要足够样本，
            # 少量字节先判会误判）。普通 UTF-8/GBK 行一旦出现 ``\\n`` 即可判定。
            if len(self._byte_buffer) + len(chunk) < 8 and b"\n" not in chunk \
                    and b"\n" not in self._byte_buffer:
                self._byte_buffer.extend(chunk)
                return []
            self._byte_buffer.extend(chunk)
            chunk = bytes(self._byte_buffer)
            self._byte_buffer.clear()
            self._detect(chunk)
        if self._wide_encoding is not None:
            return self._feed_wide(chunk)
        return self._feed_bytes(chunk)

    def flush(self) -> list[str]:
        """流结束：返回残留的不完整行（无行尾 ``\\n``）。"""
        lines: list[str] = []
        if not self._detected:
            # 全程不足 8 字节：按普通字节流解码（UTF-8/本地代码页自适应）
            if self._byte_buffer:
                lines.append(decode_output(
                    bytes(self._byte_buffer), self._encodings,
                    prefer_local=self._prefer_local))
                self._byte_buffer.clear()
            return lines
        if self._wide_encoding is not None:
            try:
                self._text_buffer += self._wide_decoder.decode(b"", True)
            except Exception:  # pragma: no cover - 增量解码器收尾异常（防御）
                logger.debug("宽字符流收尾解码失败", exc_info=True)
            lines.extend(self._split_text(""))
            if self._text_buffer:
                lines.append(self._text_buffer)
                self._text_buffer = ""
            return lines
        if self._byte_buffer:
            lines.append(decode_output(
                bytes(self._byte_buffer), self._encodings,
                prefer_local=self._prefer_local))
            self._byte_buffer.clear()
        return lines

    # ── 内部实现 ──

    def _detect(self, chunk: bytes) -> None:
        """首块数据编码探测：BOM 优先，其次无 BOM 的 UTF-16 字节分布。"""
        self._detected = True
        head = bytes(chunk[:4])
        if head.startswith(codecs.BOM_UTF32_LE) or head.startswith(codecs.BOM_UTF32_BE):
            self._start_wide("utf-32")
        elif head.startswith(codecs.BOM_UTF8):
            self._start_wide("utf-8-sig")
        elif head.startswith(codecs.BOM_UTF16_LE) or head.startswith(codecs.BOM_UTF16_BE):
            self._start_wide("utf-16")
        else:
            variant = _utf16_without_bom(chunk)
            if variant is not None:
                self._start_wide(variant)

    def _start_wide(self, encoding: str) -> None:
        """切换到宽字符流模式（整块增量解码，按 ``\\n`` 在文本层切行）。"""
        self._wide_encoding = encoding
        self._wide_decoder = codecs.getincrementaldecoder(encoding)("replace")

    def _feed_wide(self, chunk: bytes) -> list[str]:
        """宽字符流：增量解码（跨块保留半个码元）后按 ``\\n`` 切行。"""
        try:
            text = self._wide_decoder.decode(chunk, False)
        except Exception:  # pragma: no cover - 增量解码器异常（防御）
            # 退化回逐行字节解码（宁可个别行乱码，也不能让整条流读不出来）
            self._wide_encoding = None
            self._wide_decoder = None
            self._text_buffer = ""
            return self._feed_bytes(chunk)
        return self._split_text(text)

    def _split_text(self, text: str) -> list[str]:
        """把解码文本按 ``\\n`` 切行（完整行保留行尾换行符）。"""
        if text:
            self._text_buffer += text
        lines: list[str] = []
        while True:
            newline = self._text_buffer.find("\n")
            if newline < 0:
                break
            lines.append(self._text_buffer[:newline + 1])
            self._text_buffer = self._text_buffer[newline + 1:]
        return lines

    def _feed_bytes(self, chunk: bytes) -> list[str]:
        """单字节/UTF-8/GBK 快路径：字节缓冲按 ``b'\\n'`` 切行后逐行解码。"""
        self._byte_buffer.extend(chunk)
        lines: list[str] = []
        while True:
            newline = self._byte_buffer.find(b"\n")
            if newline < 0:
                break
            lines.append(
                decode_output(
                    bytes(self._byte_buffer[:newline + 1]), self._encodings,
                    prefer_local=self._prefer_local)
            )
            del self._byte_buffer[:newline + 1]
        return lines
