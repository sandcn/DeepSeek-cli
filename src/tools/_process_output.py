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

修复策略（流式、逐行自适应，不做有状态的全局猜测）：
  1. **UTF-8 优先**：现代工具输出 UTF-8（最常见且无歧义），先严格解码；
     Windows CJK 代码页下与 GBK 「双重合法」的短行（如「系统」）按
     :func:`_ambiguous_local_decode` 的规则消解为本地代码页；
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
import os
import sys

from .._text_width import codepoint_width

__all__ = [
    "OutputLineDecoder",
    "decode_output",
    "default_output_encodings",
]


# ── 本机候选编码 ──────────────────────────────────────

def _windows_code_page(getter: str) -> int:
    """读取 Windows 代码页（``GetConsoleOutputCP`` / ``GetOEMCP`` / ``GetACP``）。

    非 Windows、无控制台（``GetConsoleOutputCP`` 返回 0）或 API 不可用时
    返回 0——调用方据此跳过该候选。
    """
    if os.name != "nt":
        return 0
    try:
        import ctypes

        value = int(getattr(ctypes.windll.kernel32, getter)())
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

    if os.name == "nt":
        for getter in ("GetConsoleOutputCP", "GetOEMCP", "GetACP"):
            _add(_code_page_encoding(_windows_code_page(getter)))
    try:
        _add(locale.getpreferredencoding(False))
    except Exception:  # pragma: no cover - locale 查询异常（极端环境）
        pass
    try:
        _add(sys.getfilesystemencoding())
    except Exception:  # pragma: no cover - 同上
        pass
    return tuple(encodings)


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

#: GBK 双字节被误读为 UTF-8 双字节序列时落到的码点区间（Greek..NKo）。
#: 合法 UTF-8 的 Latin-1 / Latin Extended（``café`` / ``naïve``）不在其中
#: ——避免把正常西欧文本改判成本地代码页。
_AMBIGUOUS_SCRIPT_MIN = 0x0370
_AMBIGUOUS_SCRIPT_MAX = 0x07FF


def _is_cjk_codepage(encoding: str) -> bool:
    """编码名是否为 CJK 多字节代码页（cp932/936/949/950、GBK、Big5 等）。"""
    name = encoding.lower().replace("_", "").replace("-", "").replace(" ", "")
    return name.startswith(_CJK_CODEPAGE_PREFIXES)


def _contains_wide_char(text: str) -> bool:
    """文本是否含宽字符（CJK / 全角 / emoji，显示宽度 2）。"""
    return any(codepoint_width(ord(ch)) == 2 for ch in text)


def _all_non_ascii_in_ambiguous_scripts(text: str) -> bool:
    """非 ASCII 字符是否全部落在 U+0370-U+07FF（希腊/西里尔/希伯来/阿拉伯等）。

    该区间正是「GBK 双字节 → UTF-8 双字节」误读的产物区间（如「系统」
    ``CF B5 CD B3`` → ``ϵͳ``）。Latin-1 / Latin Extended 与 3 字节以上的
    码点不在其中，避免误伤合法 UTF-8 文本。
    """
    for ch in text:
        cp = ord(ch)
        if cp < 0x80:
            continue
        if not (_AMBIGUOUS_SCRIPT_MIN <= cp <= _AMBIGUOUS_SCRIPT_MAX):
            return False
    return True


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
) -> "str | None":
    """消解「UTF-8 与本地 CJK 代码页双重合法」的歧义（返回本地解码或 None）。

    中文 Windows 上 ``cmd`` 输出的 GBK 短行可能恰好也是合法 UTF-8：例如
    「系统」= ``CF B5 CD B3`` → UTF-8 解出希腊字母 ``ϵͳ``。此时按以下规则
    改判本地 CJK 代码页（返回其解码结果），否则返回 None（保留 UTF-8）：

      1. UTF-8 文本含宽字符（CJK/全角/emoji）→ 真 UTF-8，不改判；
      2. UTF-8 字节流含 3/4 字节序列 → 真 UTF-8，不改判；
      3. UTF-8 的非 ASCII 字符不全是「歧义文字」（Greek..NKo）→ 不改判
         （保护 ``café`` 等合法西欧文本）；
      4. 本地 CJK 代码页能严格解码且解出宽字符 → 判为本地代码页输出。
    """
    if utf8_text.isascii():
        return None
    if _contains_wide_char(utf8_text):
        return None
    if _utf8_has_wide_sequence(raw):
        return None
    if not _all_non_ascii_in_ambiguous_scripts(utf8_text):
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


def decode_output(raw: bytes, encodings: "tuple[str, ...] | list[str] | None" = None) -> str:
    """把子进程输出字节解码为文本（编码自适应 + 宽字符误读修复）。

    在 :func:`_decode_output` 的自适应解码之上，再修复一类**上游**损坏：
    UTF-16 输出被按单字节 / UTF-8 解码（Windows 上 ``wsl.exe`` 等），
    详见 :func:`_repair_wide_text`。

    Args:
        raw: 子进程输出字节（可含多行 / 不完整行）。
        encodings: 候选编码（默认 :func:`default_output_encodings`；测试可注入）。

    Returns:
        解码后的文本（永不抛 ``UnicodeDecodeError``）。
    """
    return _repair_wide_text(_decode_output(raw, encodings))


def _decode_output(raw: bytes, encodings: "tuple[str, ...] | list[str] | None" = None) -> str:
    """按编码自适应把字节解码为文本（:func:`decode_output` 的内核）。

    解码顺序：
      1. 明确 BOM（UTF-32 → UTF-8 → UTF-16；UTF-32LE 的 BOM 前两字节与
         UTF-16LE 重叠，故先判 UTF-32）；
      2. 无 BOM 但呈 UTF-16 字节分布（``wmic`` 等宽字符输出）；
      3. UTF-8 严格解码（现代工具的主流编码；与本地 CJK 代码页双重合法
         的短行按 :func:`_ambiguous_local_decode` 消歧）；
      4. 依次尝试本机候选编码（见 :func:`default_output_encodings`）；
      5. 仍失败 → 本机编码 ``errors='replace'`` 兜底。

    Args:
        raw: 子进程输出字节（可含多行 / 不完整行）。
        encodings: 候选编码（默认 :func:`default_output_encodings`；测试可注入）。

    Returns:
        解码后的文本（永不抛 ``UnicodeDecodeError``）。
    """
    if not raw:
        return ""
    candidates = tuple(encodings) if encodings else default_output_encodings()

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
        local_text = _ambiguous_local_decode(raw, utf8_text, candidates)
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

    def __init__(self, encodings: "tuple[str, ...] | list[str] | None" = None) -> None:
        """初始化。

        Args:
            encodings: 候选编码（默认 :func:`default_output_encodings`）。
        """
        self._encodings: tuple[str, ...] = (
            tuple(encodings) if encodings else default_output_encodings()
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
                lines.append(decode_output(bytes(self._byte_buffer), self._encodings))
                self._byte_buffer.clear()
            return lines
        if self._wide_encoding is not None:
            try:
                self._text_buffer += self._wide_decoder.decode(b"", True)
            except Exception:  # pragma: no cover - 增量解码器收尾异常（防御）
                pass
            lines.extend(self._split_text(""))
            if self._text_buffer:
                lines.append(self._text_buffer)
                self._text_buffer = ""
            return lines
        if self._byte_buffer:
            lines.append(decode_output(bytes(self._byte_buffer), self._encodings))
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
                decode_output(bytes(self._byte_buffer[:newline + 1]), self._encodings)
            )
            del self._byte_buffer[:newline + 1]
        return lines
