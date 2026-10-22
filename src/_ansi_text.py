"""ANSI 转义序列处理 — 全项目单一真源（Layer 0，无第三方依赖）。

历史上 ``tui`` / ``renderer`` / ``clawbot`` 三层各持一份 ``strip_ansi`` 与
私有正则，覆盖范围互不相同（``clawbot`` 仅剥 CSI，``renderer`` 剥 CSI+OSC+
双字符转义，``tui`` 的 CSI 参数范围最宽），同一段文本经不同路径清洗后结果
可能不一致。本模块收敛为唯一实现：

- CSI：``ESC [`` + 参数字节 ``0x20-0x3F`` + 最终字节 ``0x40-0x7E``
  （ECMA-48；覆盖 ``\\x1b[!p``、``\\x1b[>c``、真彩 ``\\x1b[38:2::255:0:0m``、
  终端键序列 ``\\x1b[3~`` 等此前易残留的形态）；
- OSC：``ESC ]`` … ``BEL`` 或 ``ESC \\``；
- 双字符转义：``ESC`` + ``0x40-0x5F``（含 ``ESC \\_`` 等）。

依赖约束：本模块仅依赖标准库，供任何层导入（包括要求零第三方渲染依赖的 TUI ANSI
渲染路径）。
"""

from __future__ import annotations

import re

from ._unicode_text import sanitize_surrogates

#: 全量 ANSI 转义序列正则（ECMA-48 CSI + OSC + 双字符转义）。
ANSI_RE = re.compile(
    r"\x1b\[[\x20-\x3F]*[@-~]"
    r"|\x1b\][^\x07\x1b]*(\x07|\x1b\\)"
    r"|\x1b[@-Z\\-_]"
)


def strip_ansi(text: str) -> str:
    """剥离 ANSI 转义序列，返回纯文本。

    非字符串（``None``）输入按空串处理——调用方（如 ClawBot 渲染）常直接
    传入可能缺失的字段。
    """
    if not text:
        return ""
    if isinstance(text, str):
        return ANSI_RE.sub("", text)
    return ANSI_RE.sub("", str(text))


def has_ansi(text: str) -> bool:
    """文本是否含 ANSI 转义引导字节（快速判定，不做完整匹配）。"""
    return bool(text) and "\x1b" in text


#: SGR（Select Graphic Rendition）序列——**唯一允许进入渲染文本**的 ANSI 形态
#: （只改样式，零宽、无光标动作）。``\x1b[0;31m`` 等组合参数同样命中。
SGR_RE = re.compile(r"\x1b\[[0-9;]*m")

#: 渲染文本控制字符过滤表：C0（``0x00-0x1F``，仅保留制表符）与 DEL/C1
#: （``0x7F-0x9F``）全部删除。这些字符宽度按 0 计，但终端会**真的执行**动作
#: （``\x0b``/``\x0c`` 换行、``\x08`` 退格、``\x07`` 响铃、``\r`` 回行首），
#: 使渲染器「光标/物理缓冲记账」与实际终端脱节 → 整屏错位一行。制表符保留
#: 交给下游 ``expand_tabs`` 统一展开（它的列推进是宽度感知的）。
_RENDER_CONTROL_TRANSLATION = {
    cp: None for cp in range(0x20) if cp != 0x09
}
_RENDER_CONTROL_TRANSLATION.update({cp: None for cp in range(0x7F, 0xA0)})

#: 同上，但保留 ``\n``（``StyledRun`` 等文本单元可能先承载多行文本，
#: 由上游 ``wrap_*`` 按 ``\n`` 拆分——在拆分前删掉换行会把多行并成一行，
#: 破坏「固定高度 Text 裁剪」语义）。``\r`` 仍剔除（回行首会覆盖内容）。
_RENDER_CONTROL_TRANSLATION_KEEP_NEWLINE = dict(_RENDER_CONTROL_TRANSLATION)
_RENDER_CONTROL_TRANSLATION_KEEP_NEWLINE.pop(0x0A, None)


#: 渲染禁止的控制字符检测（C0 除 ``\t``、DEL、C1）——消毒快速路径用。
_CONTROL_DETECT_RE = re.compile(r"[\x00-\x08\x0a-\x1f\x7f-\x9f]")


def has_dangerous_control(text: str) -> bool:
    """文本是否含渲染禁止的控制字符（C0 除 ``\\t`` / DEL / C1）。

    供消毒快速路径判定：干净文本（绝大多数行）零额外处理；含 ESC 时
    还须经 ``strip_ansi``/``iter_sgr_text`` 处理（ESC 本身也命中本正则）。
    """
    return bool(text) and _CONTROL_DETECT_RE.search(text) is not None


def scrub_control_chars(text: str) -> str:
    """剔除渲染文本中的危险控制字符（保留 ``\\t``；不处理 ANSI 序列）。

    与 ``strip_ansi`` 配合使用：ANSI 序列整段剥离后，孤立 ESC 与其余 C0/C1
    控制字节由本函数删除（见 ``sanitize_control_text``）。
    """
    if not text:
        return ""
    return text.translate(_RENDER_CONTROL_TRANSLATION)


def sanitize_control_text(text: str, *, keep_newline: bool = False) -> str:
    """渲染文本消毒：剥离 ANSI 转义序列 + 删除危险控制字符（保留 ``\\t``）。

    ★ 2026-10-23（用户报障：bash 工具输出更容易把内容写到下屏分割线）：
    渲染文本只允许「可见字符 + SGR 样式」，任何其它控制序列/控制字节都会
    让终端执行光标或换屏动作，而宽度计算按 0 计——帧内容与真实屏幕从此
    错位（下一帧重绘才恢复，即用户看到的「闪一下」）。本函数是这类外部
    文本进入渲染模型前的统一消毒入口（工具输出 / markdown 渲染残差）。

    Args:
        text: 原始文本（None 按空串）。
        keep_newline: True 保留 ``\\n``（文本单元可能先承载多行、由上游
            ``wrap_*`` 按换行拆分，如 ``StyledRun``）；False（默认）按单行
            语义连 ``\\n`` 一并剔除（工具输出行 / 行内文本）。

    Returns:
        消毒后的文本（不含 ANSI 序列、``\\r`` 与其它 C0/C1；制表符保留；
        ``keep_newline=True`` 时保留 ``\\n``）。
    """
    cleaned = strip_ansi(text or "")
    table = (
        _RENDER_CONTROL_TRANSLATION_KEEP_NEWLINE
        if keep_newline else _RENDER_CONTROL_TRANSLATION
    )
    return cleaned.translate(table)


def iter_sgr_text(text: str):
    """按 SGR 切分文本并就地消毒（渲染入口统一实现，产出 ``(kind, 片段)``）。

    用途：把「已渲染的 ANSI 文本」转回样式化 run（工具输出 / diff 块等）。
    渲染文本只允许保留 SGR（零宽、无光标动作）：

      - ``("text", segment)``：可见文本（已剔除 C0/C1 危险控制字符，保留
        ``\\t`` 交给下游 ``expand_tabs``）；
      - ``("sgr", sequence)``：完整 SGR 序列（``\\x1b[...m``，含 ESC 与 ``m``）。

    非 SGR 的 ANSI 序列（光标移动/擦除/OSC/双字符转义）与孤立 ESC 一律
    丢弃——它们宽度按 0 计却会驱动终端动作，是「整屏错位一行」的根因。

    Args:
        text: 原始文本（None / 空串不产出任何项）。

    Yields:
        ``(kind, segment)`` 二元组（kind 为 ``"text"`` 或 ``"sgr"``）。
    """
    if not text:
        return
    pos = 0
    for m in ANSI_RE.finditer(text):
        if m.start() > pos:
            seg = scrub_control_chars(text[pos:m.start()])
            if seg:
                yield ("text", seg)
        pos = m.end()
        seq = m.group(0)
        if SGR_RE.fullmatch(seq):
            yield ("sgr", seq)
    if pos < len(text):
        seg = scrub_control_chars(text[pos:])
        if seg:
            yield ("text", seg)


#: 粘贴文本控制字符过滤表：保留 \t（0x09）/ \n（0x0a），删除其余 C0、DEL
#: 与 C1（0x7F-0x9F）。ANSI 转义先经 ``strip_ansi`` 整体剥离；孤立 ESC 等
#: 残余控制字节在此删除，避免进入输入缓冲/查询/配置字段后被原样写到终端。
_PASTE_CONTROL_TRANSLATION = {
    cp: None for cp in range(0x20) if cp not in (0x09, 0x0A)
}
_PASTE_CONTROL_TRANSLATION.update({cp: None for cp in range(0x7F, 0xA0)})

#: 单行粘贴过滤表（额外删除换行）。
_PASTE_SINGLE_LINE_TRANSLATION = dict(_PASTE_CONTROL_TRANSLATION)
_PASTE_SINGLE_LINE_TRANSLATION[0x0A] = None


def sanitize_paste(text: str, multiline: bool = True) -> str:
    """清洗粘贴/批量输入文本（剥离 ANSI + 删除控制字符）。

    Args:
        text: 原始文本（None 按空串）。
        multiline: True 保留 ``\\n``（多行输入缓冲）；False 删除换行
            （单行查询/配置字段）。

    Returns:
        清洗后的文本：ANSI 转义整段剥离、``\\r`` 删除、控制字节删除；
        制表符 ``\\t`` 保留（不产生终端控制效果）；孤立 UTF-16 代理项替换为
        U+FFFD（该码点无法以 UTF-8 编码，进入输入缓冲后会让会话保存 /
        下一次请求序列化抛 ``UnicodeEncodeError``）。
    """
    cleaned = sanitize_surrogates(strip_ansi(text).replace("\r", ""))
    if not multiline:
        cleaned = cleaned.replace("\n", "")
        return cleaned.translate(_PASTE_SINGLE_LINE_TRANSLATION)
    return cleaned.translate(_PASTE_CONTROL_TRANSLATION)


__all__ = [
    "ANSI_RE",
    "SGR_RE",
    "strip_ansi",
    "has_ansi",
    "scrub_control_chars",
    "has_dangerous_control",
    "sanitize_control_text",
    "iter_sgr_text",
    "sanitize_paste",
]
