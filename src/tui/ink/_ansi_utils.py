"""ANSI 转义工具 — 剥离 / 检测 / 视觉宽度 / ASCII 快路径判定。

模块边界（2026-08-05 架构优化）：从 ``ink/helpers.py`` 拆分——ANSI 转义
序列处理为独立职责（纯文本工具，无 StyledRun/Line 依赖），供 ``_runs_utils``
（换行/截断前剥离或识别）与 ``helpers`` 门面共享。
"""

from __future__ import annotations

import re

from src._ansi_text import ANSI_RE as _ANSI_RE
from src._ansi_text import has_ansi as has_ansi  # noqa: F401 — 全项目单一真源 re-export
from src._ansi_text import strip_ansi as strip_ansi  # noqa: F401 — 全项目单一真源 re-export
from src.tui._width import wcswidth_simple

# ANSI 转义序列（SGR 颜色/属性 + 光标控制）
# ★ 收敛（2026-10）：正则与 ``strip_ansi`` / ``has_ansi`` 的真源移至
#   ``src._ansi_text``（tui / renderer / clawbot 三层唯一实现），本模块保留
#   ``_ANSI_RE`` 名（兼容历史导入）并按名 re-export 上述函数。
#   覆盖范围沿用此前的「最完整版本」：CSI 参数字节 ``\x20-\x3F``（ECMA-48
#   中间字节全范围，含真彩 ``\x1b[38:2::255:0:0m`` 的 ``:``、DECSTR ``\x1b[!p``、
#   DA 响应 ``\x1b[>c``）+ 最终字节 ``[@-~]``（含终端键序列 ``\x1b[3~``），
#   与 ``_width._skip_ansi_at`` 的 CSI 分支范围一致。

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
