"""终端显示宽度计算与 Rich Text 转义标记处理。

★ P1-2（单一真源）：``cjk_display_width`` 与 ``expand_tabs`` 已委托
``src._text_width``（Layer 0；被 ``tui._width`` / ``renderer`` /
``config.view_model`` 共享）——本模块保留 renderer 侧公开 API 与
``_ZERO_WIDTH_CHARS`` 兼容导出，内部实现不再与其它层重复。

历史背景：此前 ``cjk_display_width`` 与 ``src.tui._width.wcswidth_simple``
是**两套独立实现**（区间表靠「注释同源约束」手工同步），多次出现漂移
（H1 双宽度区间对齐 / BUG-25 零宽集合对齐 / P1 CJK 兼容表意补充等修复均
源于此）。收敛到 ``src._text_width`` 后漂移风险消除。

口径（与 ``src._text_width`` 一致）：
  - ASCII 可打印 ``0x20-0x7E`` → 1
  - 控制字符 ``0x00-0x1F`` / ``0x7F-0x9F``（含 ``\\t``）→ 0
  - ANSI 转义序列（整段）→ 0
  - CJK / 全角 / emoji 宽符号 → 2
  - 组合标记 / 零宽字符 → 0
  - 其他 → 1

依赖约束：本模块属 renderer 层，仅依赖 Layer 0 的 ``src._text_width``
与标准库，不依赖 tui 层（架构分层保持）。
"""

from __future__ import annotations

from src._text_width import (
    char_width,
    expand_tabs,
    string_width,
    zero_width_codepoints,
)

#: 零宽字符集合（兼容导出；渲染侧历史 API）。由 ``src._text_width`` 的
#: ``ZERO_WIDTH_RANGES`` 区间表派生——与 tui 侧零宽判定同源（单一真源）。
_ZERO_WIDTH_CHARS = zero_width_codepoints()


def cjk_display_width(s: str) -> int:
    """计算字符串的终端显示宽度（CJK/全角/emoji 宽 2、零宽 0、其他 1）。

    ★ P1-2：委托 ``src._text_width``（唯一真源）。相较旧本地实现两处口径
    修正（与 tui 侧完全对齐）：① 控制字符（``\\t``/ESC 等）计 0（旧实现走
    ``else`` 分支计 1）；② ANSI 转义序列整段计 0（旧实现对序列正文逐字符
    计宽）。修正后同文本在 renderer 与 tui 测量结果恒一致。

    ★ 性能（超长单行）：**单字符**输入走 ``char_width``（有界缓存 + ASCII
    算术快路径）——``wrap_line`` / ``truncate_line`` 逐字符测宽是流式渲染
    热路径，修复前每次都进 ``string_width`` 的循环（非 ASCII 字符触发一次
    正则 match + 四次区间二分），超长行换行由 O(n) 次重活变为 dict 命中。

    Args:
        s: 输入字符串（调用方通常已剥离 ANSI；含 ANSI 时本函数亦正确跳过）。

    Returns:
        显示宽度（整数）。
    """
    if len(s) == 1:
        return char_width(s)
    return string_width(s)


__all__ = ["cjk_display_width", "expand_tabs", "_ZERO_WIDTH_CHARS"]
