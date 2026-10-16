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

依赖约束：本模块仅依赖标准库，供任何层导入（包括要求零 Rich 的 TUI ANSI
渲染路径）。
"""

from __future__ import annotations

import re

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


__all__ = ["ANSI_RE", "strip_ansi", "has_ansi"]
