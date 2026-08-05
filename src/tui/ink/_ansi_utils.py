"""ANSI 转义工具 — 剥离 / 检测 / 视觉宽度 / ASCII 快路径判定。

模块边界（2026-08-05 架构优化）：从 ``ink/helpers.py`` 拆分——ANSI 转义
序列处理为独立职责（纯文本工具，无 StyledRun/Line 依赖），供 ``_runs_utils``
（换行/截断前剥离或识别）与 ``helpers`` 门面共享。
"""

from __future__ import annotations

import re

from src.tui._width import wcswidth_simple

# ANSI 转义序列（SGR 颜色/属性 + 光标控制）
# ★ BUG-33（review 方向）：CSI 参数范围扩展为 ``[\x20-\x3F]``——ECMA-48 中间
#   字节全范围（数字/分号/冒号/问号/空格 + ``!"#$%&'()*+,-./``），与
#   ``_width._skip_ansi_at`` 的 CSI 分支（``0x20 <= ord(c) <= 0x3F``）完全对齐：
#   含真彩冒号格式 ``\x1b[38:2::255:0:0m`` 的 ``:``、DECSTR ``\x1b[!p`` 的 ``!``、
#   DA 响应 ``\x1b[>c`` 的 ``>``；最终字节 ``[@-~]``（0x40-0x7E，含终端键序列
#   ``\x1b[3~`` 的 ``~``）——修复前 ``\x1b[!p``/``\x1b[>c`` 等中间字节序列残留
#   字符被宽度测量计宽导致行宽虚高，且与 _width._skip_ansi_at 剥离范围分裂。
_ANSI_RE = re.compile(
    r"\x1b\[[\x20-\x3F]*[@-~]"
    r"|\x1b\][^\x07\x1b]*(\x07|\x1b\\)"
    r"|\x1b[@-Z\\-_]"
)

# 光标控制序列（CUP 绝对定位 + DECRC/SCRC 光标恢复）——统一供
# ``_stdout_tracker`` 数据流顺序解析底部栏过滤（row/col 命名组）。
# 与 ``_ANSI_RE``（全量剥离）分工：本正则保留 row/col 分组语义，仅服务
# 光标控制序列解析（非纯剥离）。方向1 步骤2：三套 ANSI 正则收敛——
# ``_CONTROL_SEQ_RE`` 语义迁移至此（组名/匹配范围不变）。
cursor_control_re = re.compile(
    r"\x1b\[(?P<row>\d+);(?P<col>\d+)H"  # CUP
    r"|\x1b8"                              # DECRC
    r"|\x1b\[u"                            # SCRC
)


def strip_ansi(text: str) -> str:
    """剥离 ANSI 转义序列，返回纯文本。"""
    return _ANSI_RE.sub("", text)


def has_ansi(text: str) -> bool:
    """是否包含 ANSI 转义序列。"""
    return "\x1b" in text


def visual_width(text: str) -> int:
    """字符串显示宽度（先剥离 ANSI，再按 wcswidth_simple 测量）。"""
    return wcswidth_simple(strip_ansi(text))


def _is_plain_ascii_fast(text: str) -> bool:
    """文本是否全为可打印 ASCII 且无空格/换行（wrap 批量快路径前置条件）。

    判定：``isascii()`` 快速排除非 ASCII（C 级）；再逐字符检查码点在
    [0x21, 0x7E]（``!``..``~``）——排除空格 0x20（词边界断点）/换行 0x0A
    （强制换行）/其余控制字符。ASCII 可打印且无空格/换行时：每字符宽度
    恒 1、无词边界断点、无强制换行 → wrap 可按宽度直接切片（免逐字符
    wcswidth_simple + tuple 展开）。

    Args:
        text: 待检查文本（非空）。

    Returns:
        True — 全为可打印 ASCII（无空格/换行/控制字符）。
    """
    if not text.isascii():
        return False
    for ch in text:
        c = ord(ch)
        if c < 0x21 or c > 0x7E:
            return False
    return True


__all__ = [
    "_ANSI_RE",
    "cursor_control_re",
    "strip_ansi",
    "has_ansi",
    "visual_width",
    "_is_plain_ascii_fast",
]
