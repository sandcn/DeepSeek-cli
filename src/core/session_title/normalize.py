"""会话标题文本处理 —— 清洗、UTF-8 安全截断、确定性回退派生。

对应 DeepSeek Harness ``dsh-session-title`` 的 ``normalize``：

- 接受标题前剥离终端控制序列、方向性/不可见控制符与非空白 C0/C1 控制符；
- 空白（含 Unicode 行分隔符）压成单个空格——标题按行渲染（会话列表 /
  ``chat.py session list`` / OSC 窗口标题），多行会把列表格式拆坏；
- 按**字节**上限截断时绝不切断 Unicode 码点（宁可短一个字符，也不产出
  半个代理项导致后续 UTF-8 编码抛错）；
- 确定性回退：取第一条合格用户消息开头若干词，同时受词数、CJK 字符数与
  字节上限约束（中英混排各有合理长度）。

本模块是标题文本处理的**单一真源**：``chat_msgs.normalize_title`` 委托
``sanitize_title``，回退派生统一走 ``derive_fallback_title``。
"""

from __future__ import annotations

import re

from ..._unicode_text import sanitize_surrogates

#: ANSI CSI 序列（``ESC [ ... 终止符``）——终端颜色/光标控制。
_CSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
#: ANSI OSC 序列（``ESC ] ... BEL`` 或 ``ESC ] ... ST``）——窗口标题等。
_OSC_RE = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
#: 其余双字符 ESC 序列（``ESC c`` / ``ESC (B`` 等）。
_ESC_RE = re.compile(r"\x1b[@-_][0-?]*[ -/]*")

#: 方向性与不可见格式控制符：零宽空格/连接符、双向嵌入/覆盖、词连接符、
#: BOM。复制粘贴的模型输出里常见，会让标题「看起来一样、实际不相等」。
_BIDI_INVISIBLE_RE = re.compile(
    "[\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u2069\ufeff]"
)
#: 非空白 C0/C1 控制符（\t \n \r 等空白控制符交给空白压缩处理）。
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
#: 所有空白（含 Unicode 行/段分隔符与 NEL）。
_WHITESPACE_RE = re.compile(r"[\s\u0085\u2028\u2029]+")

#: CJK 字符范围（中日韩统一表意文字 + 假名 + 谚文；含扩展 A/B 区）。
_CJK_RE = re.compile(
    "[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af"
    "\U00020000-\U0002fa1f]"
)

#: 截断省略号（单个字符，UTF-8 占 3 字节——按字节截断时须预留）。
ELLIPSIS = "\u2026"


def sanitize_title(text: object) -> str:
    """标题清洗：剥离控制序列/不可见符 → 空白压缩 → 去首尾空白 → 代理项清洗。

    Args:
        text: 任意标题来源文本（``None`` / 非字符串按 ``str()`` 处理）。

    Returns:
        可直接写入会话文件、渲染到终端、写入 OSC 窗口标题的单行标题。
    """
    if text is None:
        return ""
    raw = "" if text == "" else str(text)
    if not raw:
        return ""
    # 顺序要紧：先剥 ESC 序列（内部含 ``[``/``]``，压空白后再剥会残留碎片），
    # 再剥不可见符与残余控制符，最后统一压缩空白。
    raw = _OSC_RE.sub("", raw)
    raw = _CSI_RE.sub("", raw)
    raw = _ESC_RE.sub("", raw)
    raw = _BIDI_INVISIBLE_RE.sub("", raw)
    raw = _WHITESPACE_RE.sub(" ", raw)
    raw = _CONTROL_RE.sub("", raw)
    raw = raw.strip()
    # 孤立 UTF-16 代理项无法以 UTF-8 编码：写入会话文件 / OSC 序列前必须清洗。
    return sanitize_surrogates(raw)


def utf8_len(text: str) -> int:
    """文本的 UTF-8 字节长度（不可编码字符按替换字节计，永不抛错）。"""
    return len(str(text or "").encode("utf-8", "replace"))


def truncate_utf8(text: str, max_bytes: int) -> str:
    """按 UTF-8 字节上限截断，且绝不切断多字节字符（不产生半个码点）。

    Args:
        text: 待截断文本。
        max_bytes: 字节上限（``<= 0`` 返回空串）。

    Returns:
        字节长度不超过 ``max_bytes`` 的前缀。
    """
    if max_bytes <= 0:
        return ""
    data = str(text or "").encode("utf-8", "replace")
    if len(data) <= max_bytes:
        return str(text or "")
    cut = data[:max_bytes]
    while cut:
        try:
            return cut.decode("utf-8")
        except UnicodeDecodeError as exc:
            # 只回退「尾部不完整的多字节序列」；中间出现非法字节说明输入本身
            # 有问题（极少见），直接返回窗口内容，避免丢更多信息。
            if exc.reason != "unexpected end of data":
                return cut.decode("utf-8", "replace")
            cut = cut[: exc.start]
    return ""


def clip_cjk(text: str, max_cjk_characters: int) -> str:
    """按「第 N 个 CJK 字符处」截断（非 CJK 字符不计数，但随上下文保留）。

    中文标题没有空格分词，词数上限对整段中文等于 1 词——所以中文长度要单独
    用字符数约束（dsh ``targetCjkCharacters`` / ``fallback`` 同理）。

    Args:
        text: 待截断文本。
        max_cjk_characters: CJK 字符上限（``<= 0`` 不限制）。

    Returns:
        截断后的前缀（截断发生在第 ``max_cjk_characters`` 个 CJK 字符之后）。
    """
    if max_cjk_characters <= 0:
        return text
    count = 0
    for index, char in enumerate(text):
        if _CJK_RE.match(char):
            count += 1
            if count > max_cjk_characters:
                return text[:index]
    return text


def count_cjk(text: str) -> int:
    """统计文本中的 CJK 字符数（日文假名 / 谚文 / 汉字）。"""
    return len(_CJK_RE.findall(str(text or "")))


def derive_fallback_title(
    text: object,
    *,
    max_words: int = 8,
    cjk_characters: int = 12,
    max_bytes: int = 96,
) -> str:
    """确定性回退标题：第一条合格用户消息开头若干词/字。

    规则（与 dsh 回退语义对齐，中文另加字符上限）：

    1. 清洗为单行文本；
    2. 若 CJK 字符数超过 ``cjk_characters``，先按第 N 个 CJK 字符截断；
    3. 再按空白分词截到 ``max_words`` 个词；
    4. 最后按 ``max_bytes`` 字节上限截断（预留省略号字节，不切断码点）；
    5. 发生任何截断则追加 ``…``，最终字节数仍不超过 ``max_bytes``。

    空白或空串返回空串（调用方按「无标题」处理，不写占位符）。
    """
    cleaned = sanitize_title(text)
    if not cleaned:
        return ""
    limit_bytes = max(0, int(max_bytes))
    if limit_bytes == 0:
        return ""
    ellipsis_bytes = utf8_len(ELLIPSIS)

    candidate = clip_cjk(cleaned, int(cjk_characters))
    words = candidate.split(" ")
    if max_words > 0 and len(words) > max_words:
        candidate = " ".join(words[: int(max_words)])
    truncated = candidate != cleaned

    # 截断后仍可能超字节上限（长英文单词 / 长中文串）：按字节安全截断。
    budget = limit_bytes - ellipsis_bytes if truncated or utf8_len(candidate) > limit_bytes else limit_bytes
    if utf8_len(candidate) > budget:
        candidate = truncate_utf8(candidate, max(0, budget))
        truncated = True
    candidate = candidate.rstrip()
    if not candidate:
        return ""
    return f"{candidate}{ELLIPSIS}" if truncated else candidate


__all__ = [
    "ELLIPSIS",
    "clip_cjk",
    "count_cjk",
    "derive_fallback_title",
    "sanitize_title",
    "truncate_utf8",
    "utf8_len",
]
